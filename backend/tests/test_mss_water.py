import json

import numpy as np
import pytest
import tifffile
from PIL import Image

from backend.services.mss_water import (WaterProfile, WaterUnavailable, generate_water,
                                        ndwi_window, plan_water, resolve_mss)
from backend.tests import allowed_roots_env


PAN = 'SAT_PMS_20260101010101_200516571_101_0002_001_L1_PAN'
MSS = PAN[:-3] + 'MSS'
XML = '''<Metadata><ProductInfo><SatelliteID>SAT</SatelliteID><SensorID>PMS</SensorID>
<BandOrder>Blue,Green,Red,NIR</BandOrder></ProductInfo><ProcessInfo>
<AbsCalibrationGain>1,2,1,1</AbsCalibrationGain><AbsCalibrationBias>0,0,0,0</AbsCalibrationBias>
</ProcessInfo></Metadata>'''


def geo(scale=2, origin=100):
    return [(33550, 'd', 3, (scale, scale, 0), False), (33922, 'd', 6, (0, 0, 0, origin, 200, 0), False),
            (34735, 'H', 16, (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 32651), False)]


@pytest.fixture
def scene(tmp_path, monkeypatch):
    for name, value in allowed_roots_env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('SR_SCENES_ROOT', str(tmp_path))
    monkeypatch.setenv('SR_SCENE_PATH_TEMPLATE', (tmp_path / '{d}' / '{name}').as_posix())
    monkeypatch.setenv('SR_MSS_WATER_DIR', str(tmp_path / 'water-out'))
    monkeypatch.delenv('SR_MSS_WATER_THRESHOLD', raising=False)
    pan = tmp_path / PAN / 'PAN.tif'
    pan.parent.mkdir()
    tifffile.imwrite(pan, np.zeros((4, 8), dtype='uint8'), extratags=geo(1), metadata=None)
    mss = tmp_path / MSS / 'MSS.tif'
    mss.parent.mkdir()
    xml = mss.with_name(MSS + '_meta.xml')
    xml.write_text(XML, encoding='utf-8')
    return pan, mss, xml


def write_mss(path, dtype='uint8', planar='contig', *, spatial=None, samples=4):
    data = np.zeros((2, 4, samples), dtype=dtype)
    data[0, :, 1] = 100, 10, 0, 255
    data[0, :, 3 if samples == 4 else 2] = 25, 60, 0, 3
    if dtype == 'uint16':
        data = np.where(data == 255, 65535, data * 220).astype(dtype)
    if dtype == 'float32':
        data[1, 0, 1] = np.nan
        data[1, 1, 3] = np.inf
    nodata = '65535' if dtype == 'uint16' else '255'
    tags = (geo() if spatial is None else spatial) + [(42113, 's', 0, nodata, False)]
    tifffile.imwrite(path, np.moveaxis(data, -1, 0) if planar == 'separate' else data,
                     planarconfig=planar, photometric='rgb', extrasamples=['unspecified'] if samples == 4 else None,
                     rowsperstrip=1, extratags=tags, metadata=None,
                     byteorder='>' if dtype == 'uint16' else '<')


@pytest.mark.parametrize(('dtype', 'planar'), [('uint8', 'contig'), ('uint16', 'separate'), ('float32', 'contig')])
def test_products_keep_mss_grid_and_validity(scene, dtype, planar):
    pan, mss, xml = scene
    write_mss(mss, dtype, planar)
    original = {path: path.read_bytes() for path in scene}
    plan = plan_water(pan)
    result = generate_water(plan)
    output = plan[1]
    assert result == json.loads((output / 'metadata.json').read_text(encoding='utf-8'))
    assert generate_water(plan)['id'] == result['id']
    assert result['alignment']['status'] == 'aligned'
    assert result['version'] == 'mss-ndwi.v3'
    assert result['usage'] == 'viewer_preview_only'
    assert result['sources']['mss']['image_key'].startswith('scene-v1:')
    assert result['profile']['threshold'] == 0.3
    np.testing.assert_allclose(tifffile.imread(output / 'ndwi.tif')[0], [175 / 225, -0.5, np.nan, np.nan])
    np.testing.assert_array_equal(tifffile.imread(output / 'water.tif')[0], [1, 0, 255, 255])
    np.testing.assert_array_equal(tifffile.imread(output / 'valid.tif')[0], [1, 1, 0, 0])
    for asset in ('ndwi.tif', 'water.tif', 'valid.tif'):
        with tifffile.TiffFile(output / asset) as tif:
            assert tif.pages[0].shape == (2, 4)
            assert tif.pages[0].tags[33550].value == (2, 2, 0)
            assert tif.pages[0].tags[33922].value == (0, 0, 0, 100, 200, 0)
            assert tif.pages[0].tags[34735].value == geo()[2][3]
    with Image.open(output / 'overlay.png') as overlay:
        assert overlay.getpixel((0, 0))[3] == 128
        assert all(overlay.getpixel((col, 0))[3] == 0 for col in (1, 2, 3))
    assert all(path.read_bytes() == content for path, content in original.items())


def test_bias_and_profile_change_classification():
    args = (np.array([100, 0, np.nan]), np.array([60, 5, 10]), None, (1, 2, 1, 1), (0, -5, 0, 0))
    ndwi, water, valid = ndwi_window(*args, WaterProfile(threshold=0.6))
    np.testing.assert_allclose(ndwi, [135 / 255, np.nan, np.nan])
    assert water.tolist() == [0, 255, 255]
    assert valid.tolist() == [1, 0, 0]
    assert ndwi_window(*args, WaterProfile())[1].tolist() == [1, 255, 255]


def test_threshold_reproducible_from_float32_export_and_negative_radiance_invalid():
    threshold = float(np.float32(0.3))
    ndwi, water, valid = ndwi_window(np.array([13, 2]), np.array([8, 0]), None,
                                     (1, 1, 1, 1), (0, 0, 0, -1), WaterProfile(threshold=threshold))
    assert float(ndwi[0]) == threshold
    assert np.isnan(ndwi[1])
    assert water.tolist() == [1, 255]
    assert valid.tolist() == [1, 0]


@pytest.mark.parametrize(('change', 'code'), [
    ('<bad', 'invalid_xml'), ('<!DOCTYPE Metadata [<!ENTITY a "x">]>' + XML, 'invalid_xml'),
    (XML.replace('Blue,Green,Red,NIR', 'Blue,NIR,Red,Green'), 'band_mismatch'),
    (XML.replace('<SensorID>PMS', '<SensorID>PMS09'), 'sensor_mismatch'),
    (XML.replace('<ProductInfo>', f'<ProductInfo><SceneID>{MSS.replace("0002", "0003")}</SceneID>'), 'scene_mismatch'),
    (XML.replace('<ProductInfo>', '<ProductInfo><ProductType>PAN</ProductType>'), 'scene_mismatch'),
    (XML.replace('1,2,1,1', '1,NaN,1,1'), 'invalid_calibration'),
    (XML.replace('1,2,1,1', '1,0,1,1'), 'invalid_calibration'),
    (XML.replace('1,2,1,1', '1,1e308,1,1'), 'invalid_calibration'),
    (XML.replace('0,0,0,0', '0,0,0'), 'invalid_calibration'),
])
def test_bad_calibration_has_no_artifacts(scene, change, code):
    pan, mss, xml = scene
    write_mss(mss)
    xml.write_text(change, encoding='utf-8')
    plan = plan_water(pan)
    with pytest.raises(WaterUnavailable) as failed:
        generate_water(plan)
    assert failed.value.code == code
    assert not plan[1].exists()


def test_wrong_tiff_band_count(scene):
    pan, mss, _ = scene
    write_mss(mss, samples=3)
    with pytest.raises(WaterUnavailable, match='四个'):
        generate_water(plan_water(pan))


def test_joint_nodata_is_not_published_as_water(scene):
    pan, mss, _ = scene
    data = np.tile(np.array([0, 100, 0, 0], dtype=np.uint8), (2, 4, 1))
    tag = (42112, 's', 0, '<GDALMetadata><Item name="NODATA_VALUES">0 100 0 0</Item></GDALMetadata>', False)
    tifffile.imwrite(mss, data, photometric='rgb', extrasamples=['unspecified'],
                     extratags=geo() + [tag], metadata=None)
    plan = plan_water(pan)
    with pytest.raises(WaterUnavailable) as failed:
        generate_water(plan)
    assert failed.value.code == 'unsupported_nodata'
    assert not plan[1].exists()


def test_matching_identity_and_external_rpc_allow_native_preview_only(scene):
    pan, mss, xml = scene
    write_mss(mss)
    xml.write_text(XML.replace('<ProductInfo>', f'<ProductInfo><SceneID>{MSS}</SceneID><ProductType>MSS</ProductType>'),
                   encoding='utf-8')
    report = generate_water(plan_water(pan))
    assert report['xml_identity'] == {'scene_id': MSS, 'product_type': 'MSS'}
    assert report['usage'] == 'viewer_preview_only'
    mss.with_name(MSS.rsplit('_', 1)[0] + '_B3_rpc.txt').touch()
    plan = plan_water(pan)
    result = generate_water(plan)
    assert result['id'] != report['id']
    assert result['alignment']['status'] == 'unaligned'
    assert result['sources']['mss']['external_rpc'] == [MSS.rsplit('_', 1)[0] + '_B3_rpc.txt']
    assert 'overlay.png' not in result['assets']
    with Image.open(plan[1] / 'mss-preview.png') as preview:
        assert preview.getpixel((0, 0)) == (0, 189, 255, 255)
        assert preview.getpixel((2, 0)) == (118, 103, 84, 255)


@pytest.mark.parametrize('suffix', ['.prj', '.PRJ'])
def test_projection_sidecar_allows_native_preview_without_pan_overlay(scene, suffix):
    pan, mss, _ = scene
    write_mss(mss)
    original = plan_water(pan)
    projection = mss.with_suffix(suffix)
    projection.write_text('GEOGCS["WGS 84"]', encoding='ascii')
    plan = plan_water(pan)
    result = generate_water(plan)
    assert original[0] != plan[0]
    assert [name.casefold() for name in result['sources']['mss']['external_projection']] == [projection.name.casefold()]
    assert result['sources']['mss']['external_rpc'] == []
    assert result['alignment']['status'] == 'unaligned'
    assert 'mss-preview.png' in result['assets']
    assert 'overlay.png' not in result['assets']


@pytest.mark.parametrize('suffix', ['.msk', '.aux.xml', '.tfw'])
def test_non_rpc_sidecar_still_blocks_and_names_the_file(scene, suffix):
    pan, mss, _ = scene
    write_mss(mss)
    sidecar = mss.with_name(mss.name + suffix) if suffix != '.tfw' else mss.with_suffix(suffix)
    sidecar.touch()
    with pytest.raises(WaterUnavailable) as failed:
        plan_water(pan)
    assert failed.value.code == 'unsupported_spatial_metadata'
    assert sidecar.name in str(failed.value)


def test_exact_match_only_and_duplicate_matches_fail(scene):
    pan, mss, xml = scene
    write_mss(mss.with_name(MSS.replace('0002', '0003') + '.tif'))
    with pytest.raises(WaterUnavailable) as failed:
        resolve_mss(pan)
    assert failed.value.code == 'missing_mss'
    write_mss(mss)
    duplicate = pan.parent.parent / '02' / MSS
    duplicate.mkdir(parents=True)
    (duplicate / xml.name).write_bytes(xml.read_bytes())
    write_mss(duplicate / mss.name)
    with pytest.raises(WaterUnavailable) as failed:
        resolve_mss(pan)
    assert failed.value.code == 'ambiguous_mss'


def test_nosr(scene, tmp_path):
    pan, mss, xml = scene
    pan_segment = tmp_path / PAN.replace('_0002_', '_') / PAN
    mss_segment = tmp_path / MSS.replace('_0002_', '_') / MSS
    pan_segment.parent.mkdir()
    mss_segment.parent.mkdir()
    pan.parent.rename(pan_segment)
    write_mss(mss)
    mss.parent.rename(mss_segment)
    nosr = pan_segment / 'PAN_NOSR.tif'
    (pan_segment / pan.name).rename(nosr)
    assert resolve_mss(nosr)[1:3] == (mss_segment / mss.name, mss_segment / xml.name)


@pytest.mark.parametrize('spatial', [[], geo(origin=101), geo(scale=3)])
def test_unaligned_has_downloads_but_no_overlay(scene, spatial):
    pan, mss, _ = scene
    write_mss(mss, spatial=spatial)
    plan = plan_water(pan)
    result = generate_water(plan)
    assert result['alignment']['status'] == 'unaligned'
    assert '未完成空间对齐' in result['alignment']['detail']
    assert (plan[1] / 'water.tif').exists()
    assert not (plan[1] / 'overlay.png').exists()


def test_all_invalid_is_not_all_land(scene):
    pan, mss, _ = scene
    tifffile.imwrite(mss, np.zeros((4, 2, 3), dtype='uint16'), planarconfig='separate',
                     photometric='rgb', extrasamples=['unspecified'], metadata=None)
    plan = plan_water(pan)
    with pytest.raises(WaterUnavailable) as failed:
        generate_water(plan)
    assert failed.value.code == 'no_valid_pixels'
    assert not plan[1].exists()


def test_windows_remain_bounded_for_single_large_strip(scene, monkeypatch):
    from backend.services import mss_water

    pan, mss, _ = scene
    data = np.zeros((1080, 1920, 4), dtype='uint16')
    data[:, :, 1] = 100
    tifffile.imwrite(mss, data, planarconfig='contig', photometric='rgb',
                     extrasamples=['unspecified'], rowsperstrip=1080, metadata=None)
    original = mss_water.read_strip_span
    calls = []

    def sized(stream, offsets, lengths, strip, start, size, file_size):
        calls.append((strip, start, size))
        assert size <= 1920 * 4 * 2
        return original(stream, offsets, lengths, strip, start, size, file_size)

    monkeypatch.setattr(mss_water, 'read_strip_span', sized)
    result = generate_water(plan_water(pan))
    assert len(calls) == 1080 and calls[-1] == (0, 1079 * 1920 * 8, 1920 * 8)
    assert result['mss_grid']['water_pixels'] == 1920 * 1080


def test_api_returns_unavailability_and_fences_asset_revision(scene, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    from backend.api.paths import scene_id_abs

    monkeypatch.setenv('SR_AGENT_DB', str(tmp_path / 'test.db'))
    monkeypatch.setenv('SR_SLURM_FAKE', '1')
    monkeypatch.setenv('SR_LLM_MOCK', '1')
    pan, mss, _ = scene
    endpoint = '/api/scenes/' + scene_id_abs(pan) + '/mss-water'
    with TestClient(create_app()) as client:
        assert client.post(endpoint).json()['reason'] == 'missing_mss'
        write_mss(mss)
        document = client.post(endpoint).json()
        assert document['status'] == 'ready', document
        url = endpoint + '/' + document['id']
        assert client.get(url + '/overlay.png').headers['content-type'] == 'image/png'
        mss.with_suffix('.rpc').touch()
        native = client.post(endpoint).json()
        native_url = endpoint + '/' + native['id']
        assert client.get(native_url + '/mss-preview.png').headers['content-type'] == 'image/png'
        assert client.get(native_url + '/overlay.png').status_code == 404
        mss.with_suffix('.rpc').unlink()
        assert client.get(url + '/water.tif').status_code == 200
        assert client.get(url + '/metadata.json').json() == document
        assert client.get(url + '/PAN.tif').status_code == 404
        write_mss(mss, 'uint16')
        assert client.get(url + '/water.tif').status_code == 404

@pytest.mark.parametrize("value, accepted", [(" pms ", True), ("PMS09", False)])
def test_repeated_sensor_id_requires_consensus(scene, value, accepted):
    from backend.services.mss_water import read_calibration

    _, _, xml = scene
    xml.write_text(XML.replace("<ProcessInfo>", f"<ProcessInfo><SensorId>{value}</SensorId>"),
                   encoding="utf-8")
    if accepted:
        read_calibration(xml, "SAT", "PMS", MSS)
    else:
        with pytest.raises(WaterUnavailable) as failed:
            read_calibration(xml, "SAT", "PMS", MSS)
        assert failed.value.code == "sensor_mismatch"

def test_sensor_id_nested_in_metadata_wrapper(scene):
    from backend.services.mss_water import read_calibration

    _, _, xml = scene
    xml.write_text(XML.replace("<SensorID>PMS</SensorID>",
                               "<Acquisition><SENSORID>PMS</SENSORID></Acquisition>"),
                   encoding="utf-8")
    read_calibration(xml, "SAT", "PMS", MSS)


def test_missing_sensor_id_reports_selected_file_and_candidate_tag(scene):
    from backend.services.mss_water import read_calibration

    _, _, xml = scene
    xml.write_text(XML.replace("SensorID", "SensorName"), encoding="utf-8")
    with pytest.raises(WaterUnavailable) as failed:
        read_calibration(xml, "SAT", "PMS", MSS)
    assert xml.name in str(failed.value)
    assert "SensorName" in str(failed.value)

@pytest.mark.parametrize("sensor_tag", ["<Sensor>PMS</Sensor>", "<Sensor>PMS09</Sensor>"])
def test_production_sensor_tag_matches_scene(scene, sensor_tag):
    from backend.services.mss_water import read_calibration

    _, _, xml = scene
    xml.write_text(XML.replace("<SensorID>PMS</SensorID>", sensor_tag +
                               "<SensorWorkMode>MSS</SensorWorkMode>"), encoding="utf-8")
    if sensor_tag == "<Sensor>PMS</Sensor>":
        read_calibration(xml, "SAT", "PMS", MSS)
    else:
        with pytest.raises(WaterUnavailable, match="XML=PMS09"):
            read_calibration(xml, "SAT", "PMS", MSS)

def test_production_product_id_short_scene_and_fus_method(scene):
    from backend.services.mss_water import read_calibration

    _, _, xml = scene
    product = "JL1KF02B02_PMS04_20261025120913_200548603_102_0024_001_L1_MSS"
    short = product.rsplit("_", 3)[0]
    content = XML.replace("<ProductInfo>", f"<ProductInfo><ProductID>{product}</ProductID><SceneID>{short}</SceneID>")
    content = content.replace(">SAT<", ">JL1KF02B02<").replace("<SensorID>PMS</SensorID>", "<Sensor>PMS04</Sensor>")
    import re
    content = re.sub(r"<BandOrder>.*?</BandOrder>", "", content)
    content = content.replace("</ProductInfo>", "<FUSMethod>B-G-R-NIR</FUSMethod></ProductInfo>")
    xml.write_text(content, encoding="utf-8")
    bands, _, _, identity = read_calibration(xml, "JL1KF02B02", "PMS04", product)
    assert bands == ("BLUE", "GREEN", "RED", "NIR")
    assert identity == {"scene_id": short, "product_type": "MSS"}
    xml.write_text(content.replace(product, product.replace("_0024_", "_0025_")), encoding="utf-8")
    with pytest.raises(WaterUnavailable) as failed:
        read_calibration(xml, "JL1KF02B02", "PMS04", product)
    assert failed.value.code == "scene_mismatch"

def test_registration_probe_reports_grids_and_prj_without_decoding(scene, monkeypatch):
    from backend.probe_mss_registration import inspect_registration

    pan, mss, _ = scene
    write_mss(mss)
    monkeypatch.setattr(tifffile.TiffPage, "asarray",
                        lambda *args, **kwargs: pytest.fail("unexpected raster decode"))
    report = inspect_registration(pan)
    assert report["passes_current_alignment_rules"]
    projection = mss.with_suffix('.prj')
    projection.write_text('PROJCS["fixture"]', encoding='ascii')
    report = inspect_registration(pan)
    assert report["embedded_grids_match"]
    assert not report["passes_current_alignment_rules"]
    assert report["mss"]["sidecars"][0]["text"] == 'PROJCS["fixture"]'
    assert report["mss"]["tags"]["34735"]["value"][15] == 32651
    assert not (pan.parent.parent / "water-out").exists()

def test_probe_detects_production_lowercase_scene_rpc(scene):
    from backend.probe_mss_registration import inspect_registration

    pan, mss, _ = scene
    write_mss(mss)
    for raster in (pan, mss):
        raster.with_name(raster.parent.name + '_rpc.txt').write_text('LINE_OFF: 10 pixels', encoding='ascii')
    report = inspect_registration(pan)
    assert report['embedded_grids_match']
    assert not report['passes_current_alignment_rules']
    assert report['pan']['sidecars'][0]['name'] == pan.parent.name + '_rpc.txt'
    assert plan_water(pan)[3]['mss']['external_rpc'] == [mss.parent.name + '_rpc.txt']

def test_registration_probe_validates_numbered_rpc_fields():
    from backend.probe_mss_registration import RPC_SCALARS, RPC_COEFFICIENTS, _rpc_summary

    scalars = {key: 1 for key in RPC_SCALARS}
    scalars.update(LINE_OFF=3859.5, SAMP_OFF=3847.5)
    text = '\n'.join([f'{key}: {value}' for key, value in scalars.items()]
                     + [f'{key}: +1.0e-03' for key in RPC_COEFFICIENTS])
    report = _rpc_summary(text, 7695, 7719)
    assert report['status'] == 'complete'
    assert report['offset_minus_half_size'] == {'line': 0, 'sample': 0}
    assert _rpc_summary(text + '\nLINE_OFF: 8', 7695, 7719)['status'] == 'invalid_field'
    assert _rpc_summary('LINE_OFF: 10 pixels', 7695, 7719)['status'] == 'incomplete'

def test_rpc_sweep_measures_height_sensitivity_without_decoding(scene, monkeypatch):
    from backend.probe_mss_registration import RPC_COEFFICIENTS, rpc_sweep

    pan, mss, _ = scene
    write_mss(mss)
    for source, width, height, parallax in ((pan, 8, 4, 0.5), (mss, 4, 2, 0)):
        fields = dict.fromkeys(RPC_COEFFICIENTS, 0)
        fields.update(LINE_OFF=0, SAMP_OFF=0, LAT_OFF=30, LONG_OFF=113, HEIGHT_OFF=0,
                      LINE_SCALE=height, SAMP_SCALE=width, LAT_SCALE=1, LONG_SCALE=1,
                      HEIGHT_SCALE=100, LINE_NUM_COEFF_3=1, SAMP_NUM_COEFF_2=1,
                      LINE_DEN_COEFF_1=1, SAMP_DEN_COEFF_1=1, SAMP_NUM_COEFF_4=parallax)
        source.with_name(source.parent.name + '_rpc.txt').write_text(
            '\n'.join(f'{key}: {value}' for key, value in fields.items()), encoding='ascii')
    monkeypatch.setattr(tifffile.TiffPage, 'asarray', lambda *args, **kwargs: pytest.fail('unexpected decode'))
    report = rpc_sweep(pan, [0, 50])
    center = report['samples'][4]
    assert center['samples'][0]['pan_xy'] == pytest.approx((3, 1))
    assert center['pan_height_spread_px'] == pytest.approx(2)
    assert center['samples'][1]['mss_round_trip_error_px'] < 0.001
    assert report['usage'] == 'diagnostic_only'
