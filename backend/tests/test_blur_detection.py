import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from backend.services.blur_detection import BlurConfig, analyze_texture


def test_identical_high_dn_and_invalid():
    a = np.random.default_rng(71).integers(50000, 65000, (128, 128)).astype(float)
    a[:8] = np.nan
    result = analyze_texture(a, a)
    assert not result["score"].any()
    assert np.isfinite(result["varianceRatio"]).all()
    assert result["maskAvailability"]["water"] == "unverified"


def test_local_blur_and_direction():
    a = np.random.default_rng(72).normal(size=(192, 192))
    b = a.copy()
    b[48:144, 48:144] = gaussian_filter(a, 2)[48:144, 48:144]
    result = analyze_texture(a, b)
    assert result["score"][64:128, 64:128].mean() > .4
    assert result["score"][:32].mean() < .05
    assert analyze_texture(b, a)["score"][64:128, 64:128].mean() < .05


def test_gain_and_flat():
    a = np.random.default_rng(73).normal(size=(96, 96))
    assert analyze_texture(a, a * .7 + 400)["score"].max() < .01
    assert not analyze_texture(np.ones((32, 32)), np.ones((32, 32)))["score"].any()


def test_validation():
    with pytest.raises(ValueError):
        BlurConfig(window=14)
    with pytest.raises(ValueError):
        analyze_texture(np.zeros((8, 8)), np.zeros((9, 8)))
    with pytest.raises(ValueError):
        analyze_texture(np.full((8, 8), np.nan), np.zeros((8, 8)))
