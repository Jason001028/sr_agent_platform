"""Repair orchestration tests use real TIFF bytes and a bounded fake GDAL API.

GPU inference and GDAL cubic interpolation need intranet acceptance; constant
NOSR images make the expected cubic background exact without those libraries.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import tifffile

from SR_code.variants import repair_sr as repair
from SR_code.variants import verify_sr_run as verifier
from backend.services.run_sr import build_config_xml, build_batch_script, repair_request, task_fingerprint
from backend.tests.test_scene_resolve import ResolveBase
from backend.tests.test_preview_jpg import make_strip_tif


class Raster:
    def __init__(self, path):
        self.pixels = tifffile.memmap(path)
        self.RasterYSize, self.RasterXSize = self.pixels.shape
        self.RasterCount = 1
        self.DataType = 1 if self.pixels.dtype == np.uint8 else 2

    def GetRasterBand(self, _):
        return self

    def GetMetadata(self, _):
        return {}

    def ReadAsArray(self, x, y, w, h):
        return np.array(self.pixels[y:y+h, x:x+w])

    def WriteArray(self, pixels, x=0, y=0):
        h, w = pixels.shape
        self.pixels[y:y+h, x:x+w] = pixels

    def FlushCache(self):
        self.pixels.flush()


class FakeGdal:
    GA_Update = 1
    GA_ReadOnly = 0
    GDT_Byte = 1

    def Open(self, path, _=0):
        return Raster(path)

    def GetDriverByName(self, _):
        return self

    def Create(self, path, width, height, bands, dtype):
        tifffile.imwrite(path, np.zeros((height, width), dtype=np.uint8 if dtype == 1 else np.uint16))
        return Raster(path)

    def Translate(self, path, source, width, height, **kwargs):
        source = source if isinstance(source, Raster) else Raster(source)
        rows = np.minimum(np.arange(height) * source.RasterYSize // height, source.RasterYSize - 1)
        cols = np.minimum(np.arange(width) * source.RasterXSize // width, source.RasterXSize - 1)
        tifffile.imwrite(path, np.array(source.pixels[np.ix_(rows, cols)]))
        return Raster(path)


class TestPanRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scene = Path(self.tmp.name) / 'A_B_20261009120519_200544503_101_0017_001_L1_PAN'
        self.scene.mkdir()
        self.pan = self.scene / 'PAN.tif'
        self.nosr = self.scene / (self.scene.name + '_NOSR.tif')
        self.mask = self.scene / 'PAN_mask.tif'
        tifffile.imwrite(self.pan, np.full((8, 8), 77, dtype=np.uint16))
        tifffile.imwrite(self.nosr, np.full((4, 4), 12, dtype=np.uint16))
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[3, 3] = 255
        tifffile.imwrite(self.mask, mask)
        self.meta = self.scene / (self.scene.name + '_meta.xml')
        self.meta.write_text('<meta><SolarAzimuth/></meta>', encoding='utf-8')
        self.opt = Path(self.tmp.name) / 'options.yml'
        self.opt.write_text('tiftype: .tif\nscale: 2\n', encoding='utf-8')
        self.config = Path(self.tmp.name) / 'config.xml'
        self.params = repair_request({'lq_path': str(self.scene), 'mask_path': str(self.mask),
                                      'suffix': '260318', 'sr_scale': 2, 'options_yml': str(self.opt)})
        self.config.write_text(build_config_xml(self.params), encoding='utf-8')
        self.gdal = FakeGdal()
        self.patch = patch.dict('sys.modules', {'verify_sr_run': verifier})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def inference(self, argv, size=(8, 8)):
        self.assertIn('--infer', argv)
        config = ET.parse(argv[-1]).getroot()
        private = Path(config.findtext('DatarootLQ'))
        self.assertNotEqual(private, self.scene)
        np.testing.assert_array_equal(tifffile.imread(private / 'PAN.tif'), tifffile.imread(self.nosr))
        mapped = tifffile.imread(config.findtext('MaskPath'))
        self.assertEqual(mapped.shape, (4, 4))
        self.assertEqual(mapped[1, 1], 255)  # A single PAN pixel survives reduction.
        self.assertIsNone(config.find('RepairMode'))
        tifffile.imwrite(private / 'PAN_260318.tif', np.full(size, 99, dtype=np.uint16))
        debug = private / 'Debug'
        debug.mkdir()
        (debug / 'PAN_SRLOG.txt').write_text('Run finished.\n')

    def run_repair(self, execute=None):
        return repair.run(self.config, 'unused-inference.py', gdal=self.gdal, execute=execute or self.inference)

    def test_mask_only_model_background_cubic_and_exact_file_size(self):
        old = self.pan.read_bytes()
        original = self.nosr.read_bytes()
        metadata = self.meta.read_bytes()
        receipt = self.run_repair()
        got = tifffile.imread(self.pan)
        expected = np.full((8, 8), 12, dtype=np.uint16)
        expected[3, 3] = 99
        np.testing.assert_array_equal(got, expected)
        self.assertEqual(self.pan.stat().st_size, len(old))
        self.assertEqual((self.scene / 'PAN_old_2.tif').read_bytes(), old)
        self.assertEqual(self.nosr.read_bytes(), original)
        self.assertEqual(self.meta.read_bytes(), metadata)
        self.assertFalse((self.scene / 'PAN_260318.tif').exists())
        verdict = verifier.check_contract(str(self.config), 0)
        self.assertTrue(verdict.ok, verdict.reason_text)
        self.assertEqual(Path(verdict.output), self.pan)
        from backend.services.goal_worker import sr_contract
        checked = sr_contract({'params': {'repair': True}, 'config_xml': str(self.config), 'job_id': 42},
                              {'exit_code': '0'})
        self.assertTrue(checked['ok'], checked['reasons'])
        self.assertEqual(receipt['width'], 8)
        with self.pan.open('ab') as stream:
            stream.write(b'tamper')
        self.assertFalse(verifier.check_contract(str(self.config), 0).ok)

    def test_missing_nosr_never_reads_pan_as_input(self):
        old = self.pan.read_bytes()
        self.nosr.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing repair input'):
            self.run_repair()
        self.assertEqual(self.pan.read_bytes(), old)

    def test_missing_mask_fails_preflight_without_publishing(self):
        old = self.pan.read_bytes()
        self.mask.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing repair input'):
            self.run_repair(lambda _: self.fail('inference must not run'))
        self.assertEqual(self.pan.read_bytes(), old)
        self.assertFalse((self.scene / 'PAN_old_2.tif').exists())

    def test_skipped_inference_is_not_a_successful_pan_repair(self):
        old = self.pan.read_bytes()

        def skipped(argv):
            root = ET.parse(argv[-1]).getroot()
            debug = Path(root.findtext('DatarootLQ')) / 'Debug'
            debug.mkdir()
            (debug / 'PAN_SRLOG.txt').write_text('Run skipped: cloud limit\n')

        with self.assertRaisesRegex(ValueError, 'no verified repair'):
            self.run_repair(skipped)
        self.assertEqual(self.pan.read_bytes(), old)
        self.assertFalse((self.scene / 'PAN_old_2.tif').exists())

    def test_existing_backup_is_preserved_without_running_inference(self):
        backup = self.scene / 'PAN_old_2.tif'
        backup.write_bytes(b'keep existing backup')
        with self.assertRaisesRegex(ValueError, 'Backup exists'):
            self.run_repair(lambda _: self.fail('inference must not run'))
        self.assertEqual(backup.read_bytes(), b'keep existing backup')

    def test_wrong_model_dimensions_does_not_publish(self):
        old = self.pan.read_bytes()
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            self.run_repair(lambda argv: self.inference(argv, size=(16, 16)))
        self.assertEqual(self.pan.read_bytes(), old)
        self.assertFalse((self.scene / 'PAN_old_2.tif').exists())

    def test_silent_inference_exit_is_not_success(self):
        old = self.pan.read_bytes()
        with self.assertRaisesRegex(ValueError, 'no verified repair'):
            self.run_repair(lambda _: None)
        self.assertEqual(self.pan.read_bytes(), old)

    def test_publish_failure_keeps_current_pan(self):
        old = self.pan.read_bytes()
        with patch.object(repair.os, 'replace', side_effect=OSError('publish denied')):
            with self.assertRaises(OSError):
                self.run_repair()
        self.assertEqual(self.pan.read_bytes(), old)
        self.assertFalse((self.scene / 'PAN_old_2.tif').exists())

    def test_repair_plan_and_script_and_mask_revision(self):
        tree = ET.fromstring(build_config_xml(self.params))
        self.assertEqual(tree.findtext('RepairNOSR'), self.nosr.as_posix())
        script = build_batch_script(str(self.config), self.tmp.name, repair=True)
        self.assertIn('repair_sr.py', script)
        self.assertIn('--sr-script', script)
        before = task_fingerprint(self.params)
        os.utime(self.mask, ns=(self.mask.stat().st_atime_ns, self.mask.stat().st_mtime_ns + 10000000))
        after = repair_request({k: v for k, v in self.params.items() if k != 'repair_revision'})
        self.assertNotEqual(before, task_fingerprint(after))

    def test_queued_mask_change_is_rejected_before_inference(self):
        os.utime(self.mask, ns=(self.mask.stat().st_atime_ns, self.mask.stat().st_mtime_ns + 10000000))
        with self.assertRaisesRegex(ValueError, 'Queued repair input changed'):
            self.run_repair(lambda _: self.fail('must not run'))

    def test_model_process_cannot_publish_after_source_changes(self):
        old = self.pan.read_bytes()
        def changed(argv):
            self.inference(argv)
            os.utime(self.nosr, ns=(self.nosr.stat().st_atime_ns, self.nosr.stat().st_mtime_ns + 10000000))
        with self.assertRaisesRegex(ValueError, 'input changed during inference'):
            self.run_repair(changed)
        self.assertEqual(self.pan.read_bytes(), old)
        self.assertFalse((self.scene / 'PAN_old_2.tif').exists())

    def test_empty_mask_is_rejected(self):
        tifffile.imwrite(self.mask, np.zeros((8, 8), dtype=np.uint8))
        self.params = repair_request({k: v for k, v in self.params.items() if k != 'repair_revision'})
        self.config.write_text(build_config_xml(self.params), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'mask is empty'):
            self.run_repair(lambda _: self.fail('must not run'))

    def test_existing_pan_without_matching_receipt_never_passes_verifier(self):
        verdict = verifier.check_contract(str(self.config), 0)
        self.assertFalse(verdict.ok)


class TestPanRepairSceneApi(ResolveBase):
    def test_current_pan_nosr_and_backup_identity(self):
        d = self.make_scene(tif=False)
        make_strip_tif(d, 'PAN.tif', 80, 40)
        make_strip_tif(d, d.name + '_NOSR.tif', 40, 20)
        make_strip_tif(d, 'PAN_old_2.tif', 80, 40)
        c = self.client()
        response = c.post('/api/scenes/resolve', json={'path': self.win_path(d)})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['resolved']['input_name'], 'PAN.tif')
        self.assertEqual((body['row']['W'], body['row']['H']), (80, 40))
        self.assertTrue(body['resolved']['mask_path'].endswith('/PAN_mask.tif'))
        siblings = c.get('/api/scenes/' + body['row']['id'] + '/siblings', params={'suffix': 'old'}).json()
        old = next(i for i in siblings['items'] if i['kind'] == 'product')
        nosr = next(i for i in siblings['items'] if i['kind'] == 'nosr')
        self.assertEqual(old['name'], 'PAN_old_2.tif')
        self.assertEqual(nosr['name'], d.name + '_NOSR.tif')
        self.assertNotEqual(old['id'], body['row']['id'])
