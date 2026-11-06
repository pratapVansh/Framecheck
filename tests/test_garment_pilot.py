import numpy as np
import pytest

from scripts.garment_pilot import load_shirt_model, mask_metrics, prepare_segformer_input, reference_summary, scoped_dip


def test_mask_metrics_distinguish_missing_regions_from_wrong_pixels():
    reference = np.zeros((10, 10), bool)
    reference[2:8, 2:8] = True
    missing = reference.copy()
    missing[2:5] = False
    metrics = mask_metrics(missing, reference)
    assert metrics == {"iou": 0.5, "missed_reference_fraction": 0.5, "outside_reference_fraction": 0.0}
    oversized = np.ones((10, 10), bool)
    assert mask_metrics(oversized, reference)["outside_reference_fraction"] == pytest.approx(0.64)
    empty = mask_metrics(np.zeros((10, 10), bool), reference)
    assert empty["missed_reference_fraction"] == 1 and empty["outside_reference_fraction"] is None


def test_product_mask_never_changes_whole_photo_measurements(product):
    left = np.zeros(product.shape[:2], bool)
    left[12:68, 15:30] = True
    right = np.zeros(product.shape[:2], bool)
    right[12:68, 30:45] = True
    first = scoped_dip(product, left, mask_reliable=True)
    second = scoped_dip(product, right, mask_reliable=True)
    assert first["whole_photo"] == second["whole_photo"]
    assert first["product"]["median_luminance"] != second["product"]["median_luminance"]
    assert (first["native_width"], first["native_height"]) == (60, 80)
    assert first["verdict"] is None


def test_unverified_or_tiny_mask_cannot_create_a_quality_verdict(product):
    mask = np.ones(product.shape[:2], bool)
    guarded = scoped_dip(product, mask)
    assert guarded["product"] is None and guarded["whole_photo"]["usable_pixels"] > 32
    mask[:] = False
    tiny = scoped_dip(product, mask, mask_reliable=True)
    assert tiny["product"]["reason"] == "insufficient_pixels" and tiny["verdict"] is None
    with pytest.raises(ValueError, match="dimensions"):
        scoped_dip(product, np.zeros((3, 3), bool))


def test_missing_checkpoint_stays_blocked_without_network_or_random_weights(tmp_path):
    predict, report = load_shirt_model(tmp_path)
    assert predict is None
    assert report["status"] == "blocked_missing_checkpoint" and len(report["missing_files"]) == 3


def test_checkpoint_preprocessing_preserves_rgb_channels_and_normalization():
    rgb = np.full((3, 5, 3), [255, 0, 128], np.uint8)
    config = {"size": 8, "do_resize": True, "do_normalize": True,
              "image_mean": [0.5, 0.25, 0.0], "image_std": [0.5, 0.25, 1.0], "resample": 2}
    pixels = prepare_segformer_input(rgb, config)
    assert pixels.shape == (1, 3, 8, 8) and pixels.dtype == np.float32 and pixels.flags.c_contiguous
    np.testing.assert_allclose(pixels[0, :, 3, 3], [1.0, -1.0, 128 / 255], rtol=1e-6)
    config["image_std"][0] = 0
    with pytest.raises(ValueError, match="normalization"):
        prepare_segformer_input(rgb, config)


def test_verified_reference_scores_never_pool_ai_or_unverified_masks():
    rows = []
    for provenance, verified, score in [("Human corrected", True, 0.9), ("AI polygon", False, 0.1), ("Detailed polygon; reviewer unknown", False, 0.4)]:
        rows.append({"reference_mask": "mask.png", "reference_provenance": provenance,
                     "reference_human_verified": verified, "baseline_reference_metrics": {"iou": score},
                     "pretrained_reference_metrics": None})
    rows.append({"reference_mask": None, "reference_human_verified": True})
    summary = reference_summary(rows)
    assert summary["human_verified"]["reference_photos"] == 1
    assert summary["human_verified"]["baseline"]["mean_iou"] == 0.9
    assert summary["ai_reviewed"]["baseline"]["mean_iou"] == 0.1
    assert summary["unverified"]["baseline"]["mean_iou"] == 0.4
    assert summary["human_verified"]["pretrained"]["mean_iou"] is None
    assert reference_summary([])["human_verified"]["baseline"]["mean_iou"] is None
