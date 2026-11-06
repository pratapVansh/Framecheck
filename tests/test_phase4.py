"""Reference-outline annotation checks use no production inspector predictions."""
import numpy as np

from scripts.finalize_phase4 import geometry_labels


def test_reference_geometry_accounts_for_incidental_layout_defects():
    mask = np.zeros((80, 60), bool)
    mask[16:64, 30:60] = True
    assert geometry_labels(mask) == dict(frame_coverage=False, placement=True, margins=True)


def test_one_pixel_margin_is_unknown_and_underfill_is_present():
    mask = np.zeros((80, 60), bool)
    mask[20:60, 1:21] = True
    assert geometry_labels(mask) == dict(frame_coverage=True, placement=True, margins=None)


def test_reference_geometry_keeps_rule_boundary_unknown():
    mask = np.zeros((80, 60), bool)
    mask[18:62, 15:45] = True
    assert geometry_labels(mask) == dict(frame_coverage=None, placement=False, margins=False)
