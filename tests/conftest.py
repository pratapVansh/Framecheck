import numpy as np
import pytest

from inspector.images import png_bytes


@pytest.fixture
def product():
    """Procedural unit-test fixture, not a dataset sample or evaluation evidence."""
    image = np.full((80, 60, 3), 255, np.uint8)
    yy, xx = np.indices((56, 30))
    luminance = 60 + xx * 3 + ((yy // 7) % 2) * 30
    image[12:68, 15:45] = np.repeat(luminance[:, :, None], 3, axis=2)
    return image


@pytest.fixture
def product_bytes(product):
    return png_bytes(product)
