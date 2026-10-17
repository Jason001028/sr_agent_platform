import json
import tempfile
import unittest
from pathlib import Path

from backend.plugins.loader import discover_manifests
from backend.plugins.manifest import load_manifest


class TestPluginManifest(unittest.TestCase):
    def test_discovery_loads_valid_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'plugins'; (root / 'demo').mkdir(parents=True)
            (root / 'demo' / 'manifest.json').write_text(json.dumps({
                'id': 'demo', 'name': 'Demo', 'version': '1.0.0',
                'host_api_version': '1', 'enabled': True,
            }), encoding='utf-8')
            found, errors = discover_manifests(root)
            self.assertEqual([item.id for item in found], ['demo'])
            self.assertEqual(errors, [])

    def test_invalid_manifest_is_reported_without_stopping_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'plugins'; (root / 'bad').mkdir(parents=True)
            (root / 'bad' / 'manifest.json').write_text('{}', encoding='utf-8')
            found, errors = discover_manifests(root)
            self.assertEqual(found, [])
            self.assertEqual(len(errors), 1)

    def test_duplicate_ids_are_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'plugins'
            for name in ('a', 'b'):
                folder = root / name; folder.mkdir(parents=True)
                (folder / 'manifest.json').write_text(json.dumps({
                    'id': 'same', 'name': name, 'version': '1',
                    'host_api_version': '1', 'enabled': True,
                }), encoding='utf-8')
            found, errors = discover_manifests(root)
            self.assertEqual(len(found), 1)
            self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
