"""Native logit streaming must preserve full-resolution interpolation and argmax exactly."""
import pytest

torch = pytest.importorskip("torch")

from inspector.garments import _native_labels


@pytest.mark.parametrize("shape,size", [
    ((1, 18, 7, 11), (39, 83)),
    ((1, 18, 11, 7), (83, 39)),
    ((2, 5, 9, 13), (4, 7)),
    ((1, 1, 3, 5), (1, 17)),
    ((1, 18, 8, 8), (8, 8)),
])
def test_streaming_matches_full_native_logits(shape, size):
    logits = torch.randn(shape, generator=torch.Generator().manual_seed(73))
    expected = torch.nn.functional.interpolate(
        logits, size=size, mode="bilinear", align_corners=False).argmax(dim=1)
    actual = _native_labels(logits, size)
    assert torch.equal(actual, expected)
    assert torch.equal(actual == 4, expected == 4)


@pytest.mark.parametrize("special", ["ties", "nan", "infinity"])
def test_first_class_wins_ties_and_first_nan_matches_argmax(special):
    logits = torch.zeros(1, 6, 3, 5)
    logits[:, 2:4] = 1
    if special == "nan":
        logits[:, 3:5, 1, 2] = float("nan")
    elif special == "infinity":
        logits[:, 3:5] = float("inf")
    expected = torch.nn.functional.interpolate(
        logits, size=(17, 29), mode="bilinear", align_corners=False).argmax(dim=1)
    assert torch.equal(_native_labels(logits, (17, 29)), expected)
