import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from backend.services.blur_detection import BlurConfig, analyze_texture, local_statistics, frequency_evidence, config_from_dict


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


@pytest.mark.parametrize('value', [True, '1', [], {}, float('inf'), 0, -1])
def test_calibration_rejects_non_numeric_or_invalid_values(value):
    with pytest.raises(ValueError, match='Calibration'):
        BlurConfig(calibration_scale=value)


@pytest.mark.parametrize('value', [[], False, 0, ''])
def test_empty_nonobject_options_are_not_silently_defaulted(value):
    with pytest.raises(ValueError, match='object'):
        config_from_dict(value)


def test_local_variance_is_stable_under_large_dc_offset_and_nodata():
    texture = np.random.default_rng(4).normal(size=(64, 64))
    valid = np.ones(texture.shape, bool)
    valid[20:25, 20:25] = False
    shifted = texture + 1e9
    shifted[~valid] = np.nan
    mean, variance, weight = local_statistics(shifted, valid, 15)
    expected_mean, expected, expected_weight = local_statistics(texture, valid, 15)
    np.testing.assert_allclose(variance, expected, atol=2e-8)
    np.testing.assert_allclose(mean - 1e9, expected_mean, atol=1e-7)
    np.testing.assert_array_equal(weight, expected_weight)


def test_frequency_preserves_native_frequencies_in_large_candidate():
    # 0.4 cycles/pixel aliases to 0.2 with the old stride=2 sampling.
    x = np.sin(2*np.pi*.4*np.arange(512))
    a = np.tile(x, (512, 1))
    b = gaussian_filter(a, 1)
    evidence = frequency_evidence(a, b, np.ones(a.shape, bool))
    assert evidence['sampleStride'] == 1
    assert evidence['cropShape'] == [256, 256]
    assert evidence['referenceHighFrequency'] > .99
    assert evidence['frequencyUnits'] == 'cycles-per-analysis-pixel'


def test_frequency_rechecks_validity_after_bounded_crop():
    a = np.ones((1024, 1024))
    valid = np.ones(a.shape, bool)
    valid[384:640, 384:640] = False
    result = frequency_evidence(a, a, valid)
    assert result['status'] == 'insufficient-data'


def test_refinement_can_inherit_scene_normalization_instead_of_normalizing_blur_away():
    ref=np.random.default_rng(31).normal(30000,3000,(512,512))
    target=ref.copy();target[96:416,96:416]=gaussian_filter(ref,2)[96:416,96:416]
    full=analyze_texture(ref,target)
    norm=full['normalization']
    local=analyze_texture(ref[176:336,176:336],target[176:336,176:336],
        config=BlurConfig(calibration_scale=norm['scale'],log_ratio_baseline=norm['logRatioBaseline']))
    np.testing.assert_allclose(local['score'][32:-32,32:-32],full['score'][208:304,208:304],atol=1e-6)
