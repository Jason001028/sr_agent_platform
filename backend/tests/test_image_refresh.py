from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from backend.services import image_refresh, preview_jpg
from backend.api import paths
from backend.tests import allowed_roots_env


@pytest.fixture
def scene(tmp_path, monkeypatch):
    for k, v in allowed_roots_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv('SR_PREVIEWS_ROOT', str(tmp_path / 'previews'))
    directory = tmp_path / 'scene'; directory.mkdir()
    source = directory / 'PAN.tif'
    Image.new('I;16', (16, 16), 100).save(source)
    task = {'task_id': 1, 'job_id': 1, 'status': 'COMPLETED', 'finished_at': 1,
            'params': {'lq_path': directory.as_posix(), 'repair': True}}
    with mock.patch('backend.api.app._run_dataroot', return_value=str(directory)), mock.patch.object(image_refresh, 'sr_contract', return_value={'ok': True, 'skip': False, 'output': str(source)}):
        yield tmp_path, source, task


def test_no_badge_before_preview_and_real_revision_changes(scene):
    root, source, task = scene
    pending = image_refresh.discover(task, root)
    assert pending['state'] == 'preparing'
    jpg = paths.preview_jpg_for(source, root)
    preview_jpg.ensure_preview_jpg(source, jpg, div=4)
    ready = image_refresh.discover(task, root)
    assert ready['state'] == 'ready'
    assert image_refresh.preview_bytes(task, root, ready['version']).startswith(b'\xff\xd8')
    Image.new('I;16', (16, 16), 200).save(source)
    updated = image_refresh.discover(task, root)
    assert updated['state'] == 'preparing'
    assert updated['version'] != ready['version']
    with pytest.raises(ValueError):
        image_refresh.preview_bytes(task, root, ready['version'])


def test_failed_and_skipped_contracts_never_become_new_images(scene):
    root, source, task = scene
    assert image_refresh.discover({**task, 'status': 'FAILED'}, root)['state'] == 'unavailable'
    with mock.patch.object(image_refresh, 'sr_contract', return_value={'ok': True, 'skip': True, 'output': str(source)}):
        assert image_refresh.discover(task, root)['state'] == 'unavailable'


def test_discovery_never_bakes_or_decodes_tiff(scene):
    root, _, task = scene
    with mock.patch.object(preview_jpg, 'ensure_preview_jpg', side_effect=AssertionError('no baking')), mock.patch.object(preview_jpg, 'build_preview_pixels', side_effect=AssertionError('no pixel decode')):
        assert image_refresh.discover(task, root)['state'] == 'preparing'
