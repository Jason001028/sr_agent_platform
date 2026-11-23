import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from backend.services import mss_land
from backend.tests.test_mss_water import MSS, XML, geo, scene


@pytest.fixture
def execution_scene(scene, tmp_path, monkeypatch):
    pan, mss, xml = scene
    pixels = np.full((24, 40), 65, np.uint16)
    pixels[10, 31] = 65535
    tifffile.imwrite(pan, pixels, extratags=geo(1) + [(42113, 's', 0, '65535', False)], metadata=None)
    spectral = np.zeros((12, 20, 4), np.uint8)
    spectral[:, :8, 1], spectral[:, :8, 3] = 90, 20
    spectral[:, 8:10, 1], spectral[:, 8:10, 3] = 25, 50
    spectral[:, 10:, 1], spectral[:, 10:, 3] = 10, 100
    tifffile.imwrite(mss, spectral, photometric='rgb', extrasamples=['unspecified'],
                     extratags=geo(2), metadata=None)
    xml.write_text(XML.replace('<ProductInfo>',
                               f'<ProductInfo><SceneID>{MSS}</SceneID><ProductType>MSS</ProductType>'),
                   encoding='utf-8')
    path = tmp_path / 'profile.json'
    profile = dict(profile_id='synthetic', verification_ref='synthetic-control-points',
                   satellite='SAT', sensor='PMS', registration_method=mss_land.METHOD,
                   land_threshold=-0.2, water_threshold=0.3, shoreline_pan_pixels=1,
                   registration_error_pan_pixels=0.5)
    path.write_text(json.dumps(profile), encoding='utf-8')
    monkeypatch.setenv('SR_MSS_EXEC_PROFILE', str(path))
    return pan, mss, xml, path, profile


def test_constraint_rejects_water_uncertainty_and_pan_nodata(execution_scene, tmp_path):
    pan, _, _, profile_path, profile = execution_scene
    candidate = tmp_path / 'candidate.tif'
    mask = np.zeros((24, 40), np.uint8)
    mask[6:18, 5:35] = 255
    tifffile.imwrite(candidate, mask, compression='deflate', rowsperstrip=5, metadata=None)
    candidates = [{'candidate_id': 'water', 'bbox': [5, 6, 8, 12]},
                  {'candidate_id': 'shoreline', 'bbox': [17, 6, 28, 12]}]
    evidence = mss_land.build(pan, candidate, tmp_path / 'execution', candidates)
    classes = tifffile.imread(evidence['classification']['path'])
    final = tifffile.imread(evidence['final_mask']['path'])
    assert classes[9, 9] == mss_land.WATER
    assert classes[9, 18] == mss_land.UNCERTAIN
    assert classes[9, 31] == mss_land.LAND
    assert classes[10, 31] == mss_land.UNCERTAIN
    np.testing.assert_array_equal(final > 0, (mask > 0) & (classes == mss_land.LAND))
    assert evidence['stats']['excluded'] == evidence['stats']['clipped'] == 1
    assert evidence['stats']['executable'] == 1
    assert mss_land.build(pan, candidate, tmp_path / 'execution', candidates) == evidence
    final_path = Path(evidence['final_mask']['path'])
    final_path.write_bytes(b'changed')
    with pytest.raises(mss_land.WaterUnavailable, match='证据已变化'):
        mss_land.verify(pan, evidence)
    profile['shoreline_pan_pixels'] = 2
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    with pytest.raises(mss_land.WaterUnavailable, match='Profile 已变化'):
        mss_land.verify(pan, evidence)


@pytest.mark.parametrize("suffix", [".rpb", ".prj"])
def test_execution_blocks_unsupported_alignment_and_missing_profile(execution_scene, monkeypatch, suffix):
    pan, mss, _, _, _ = execution_scene
    sidecar = mss.with_suffix(suffix)
    sidecar.touch()
    with pytest.raises(mss_land.WaterUnavailable) as failed:
        mss_land.preflight(pan)
    assert failed.value.code == 'registration_required'
    sidecar.unlink()
    monkeypatch.delenv('SR_MSS_EXEC_PROFILE')
    with pytest.raises(mss_land.WaterUnavailable) as failed:
        mss_land.preflight(pan)
    assert failed.value.code == 'execution_profile'


def test_no_candidates_preserves_evidence_without_executable_pixels(execution_scene, tmp_path):
    pan, mss, _, _, _ = execution_scene
    evidence = mss_land.build(pan, None, tmp_path / 'execution', [])
    assert evidence['candidate_mask'] is None
    assert evidence['stats']['executable'] == evidence['stats']['executable_pixels'] == 0
    assert not tifffile.imread(evidence['final_mask']['path']).any()
    assert mss_land.verify(pan, evidence)['revision'] == evidence['revision']
    mss.unlink()
    with pytest.raises(mss_land.WaterUnavailable) as failed:
        mss_land.verify(pan, evidence)
    assert failed.value.code == 'missing_mss'

def test_missing_tifffile_becomes_raster_dependency_error(execution_scene, monkeypatch):
    import sys

    pan, *_ = execution_scene
    monkeypatch.setitem(sys.modules, 'tifffile', None)
    with pytest.raises(mss_land.WaterUnavailable, match='requirements-api.txt') as failed:
        mss_land.preflight(pan)
    assert failed.value.code == 'raster_dependency'
