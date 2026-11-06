from io import BytesIO
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000


class ImageInputError(ValueError):
    pass


def decode_image(data: bytes) -> tuple[np.ndarray, list[str]]:
    if not data or len(data) > MAX_BYTES:
        raise ImageInputError("Upload a nonempty JPEG or PNG file of at most 10 MB.")
    notes = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise ImageInputError("Decoded image must be JPEG or PNG.")
                if source.width * source.height > MAX_PIXELS:
                    raise ImageInputError("Image exceeds the 24 megapixel limit.")
                if getattr(source, "n_frames", 1) != 1:
                    raise ImageInputError("Animated images are not supported. Upload one still image.")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    oriented = Image.alpha_composite(background, rgba)
                    notes.append("Transparency was composited against white before inspection.")
                if source.mode in {"1", "L", "I", "F", "LA"}:
                    notes.append("Grayscale input was converted to RGB.")
                return np.array(oriented.convert("RGB")), notes
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageInputError("Cannot decode this image safely. Upload a valid JPEG or PNG.") from exc


def png_bytes(array: np.ndarray) -> bytes:
    output = BytesIO()
    Image.fromarray(array.astype(np.uint8)).save(output, format="PNG")
    return output.getvalue()
