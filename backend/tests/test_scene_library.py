import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.paths import scene_id, scene_id_abs
from backend.services.scene_library import install
from backend.services.store import Store
from backend.tests import allowed_roots_env


@pytest.fixture
def library(tmp_path):
    root = tmp_path / 'scenes'
    root.mkdir()
    src = root / 'a.tif'
    Image.new('L', (16, 16)).save(src)
    app = FastAPI()
    app.state.store = Store(str(tmp_path / 'library.db'))
    install(app, root, lambda src, parent, root: {'id': scene_id_abs(src), 'name': src.name},
            lambda src: {'W': 16, 'H': 16})
    with patch.dict(os.environ, allowed_roots_env(root)), TestClient(app) as client:
        yield client, app, src
    app.state.store.close()


def entry(id, **tags):
    return {'scene_id': id, 'tags': tags}


def test_persistence_merge_and_shared_clients(library):
    client, app, src = library
    assert client.post('/api/scene-library', json={'entries': [entry(scene_id('a.tif'), content=['water'])]}).status_code == 200
    assert client.post('/api/scene-library', json={'entries': [entry(scene_id_abs(src), content=['water', 'road'], custom=['batch1'])]}).json()['count'] == 1
    app.state.store.close()
    with TestClient(app) as second:
        rows = second.get('/api/scene-library').json()['results']
        assert len(rows) == 1
        assert rows[0]['tags']['content'] == ['water', 'road']
        assert rows[0]['tags']['custom'] == ['batch1']
        assert rows[0]['W'] == 16
    assert src.exists()


@pytest.mark.parametrize('tags', [{'other':['a']}, {'content':'water'}, {'custom':['']},
                                {'custom':[' '*3]}, {'custom':['x'*41]}])
def test_reject_invalid_tags_atomically(library, tags):
    client, app, src = library
    assert client.post('/api/scene-library', json={'entries': [entry(scene_id_abs(src), **tags)]}).status_code == 422
    assert app.state.store.list_library_entries() == []


def test_invalid_source_prevents_whole_batch(library):
    client, app, src = library
    body = {'entries': [entry(scene_id_abs(src), purpose=['train']), entry(scene_id('../outside.tif'))]}
    assert client.post('/api/scene-library', json=body).status_code == 422
    assert app.state.store.list_library_entries() == []


def test_changed_whitelist_and_missing_source_hidden(library):
    client, app, src = library
    client.post('/api/scene-library', json={'entries':[entry(scene_id_abs(src))]})
    src.unlink()
    assert client.get('/api/scene-library').json() == {'results':[], 'unavailable':1, 'hidden': 0}
