"""Offline CPU garment loading shared by the pilot and opt-in locator."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

MODEL_ID = "mattmdjaga/segformer_b2_clothes"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_segformer_input(rgb, config):
    """Use the checkpoint's PIL resize/rescale/normalize recipe without Torchvision."""
    size = config["size"]
    if not isinstance(size, int) or size <= 0 or not config["do_resize"] or not config["do_normalize"]:
        raise ValueError("This pilot expects the supplied square resize and normalized RGB configuration.")
    mean = np.asarray(config["image_mean"], dtype=np.float32)
    std = np.asarray(config["image_std"], dtype=np.float32)
    if mean.shape != (3,) or std.shape != (3,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("RGB normalization requires three finite means and positive standard deviations.")
    resized = Image.fromarray(rgb).resize((size, size), resample=config["resample"])
    pixels = np.asarray(resized).astype(np.float32) * (1 / 255)
    return np.ascontiguousarray(((pixels - mean) / std).transpose(2, 0, 1)[None])


def load_shirt_model(folder):
    """Load local safe weights only; missing weights must never become fake predictions."""
    folder = Path(folder)
    required = [folder / name for name in ("config.json", "preprocessor_config.json", "model.safetensors")]
    if not all(path.is_file() for path in required):
        return None, {"status": "blocked_missing_checkpoint", "model_id": MODEL_ID,
                      "missing_files": [str(path) for path in required if not path.is_file()],
                      "download_policy": "Local files only; no model downloads or random-weight fallback."}
    import torch
    from transformers import SegformerForSemanticSegmentation

    torch.set_num_threads(4)
    device = "cpu"
    started = time.perf_counter()
    preprocessing = json.loads((folder / "preprocessor_config.json").read_text(encoding="utf-8"))
    model, loading = SegformerForSemanticSegmentation.from_pretrained(
        folder, local_files_only=True, use_safetensors=True, output_loading_info=True)
    if any(loading.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        raise ValueError("Checkpoint did not load completely: " + str(loading))
    loading = {key: sorted(value) if isinstance(value, set) else value for key, value in loading.items()}
    if model.config.id2label.get(4) != "Upper-clothes":
        raise ValueError("Checkpoint does not provide the expected Upper-clothes label at index 4.")
    model.to(device).eval()

    def predict(rgb, *, return_labels=False):
        pixels = torch.from_numpy(prepare_segformer_input(rgb, preprocessing)).to(device)
        with torch.inference_mode():
            logits = model(pixel_values=pixels).logits
            logits = torch.nn.functional.interpolate(logits, size=rgb.shape[:2], mode="bilinear", align_corners=False)
            labels = logits.argmax(dim=1)[0].cpu().numpy()
            mask = labels == 4
        if device == "cuda":
            torch.cuda.synchronize()
        return labels if return_labels else mask

    return predict, {"status": "loaded", "model_id": MODEL_ID, "device": device,
                     "parameters": sum(p.numel() for p in model.parameters()),
                     "load_ms": (time.perf_counter() - started) * 1000,
                     "torch_threads": 4, "input_size": preprocessing["size"],
                     "preprocessing": preprocessing, "processor_implementation": "Pillow/NumPy using checkpoint configuration; no Torchvision dependency",
                     "loading_info": loading,
                     "checkpoint_sha256": {p.name: digest(p) for p in required},
                     "limitation": "Semantic upper-clothes mask merges matching garments; it is not an instance selector."}


