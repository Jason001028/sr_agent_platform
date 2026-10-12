"""Tests for the in-job scene-prep script (SR_code/variants/sr_prep_scene.py).

That script lives outside the backend package and runs inside the batch job
under the SR interpreter (py3.6 + GDAL), so it is loaded by path via importlib
and tested in two halves:

  * everything that is plain filesystem or numpy work — pinned here on the dev
    machine;
  * the GDAL half (resampling a mask) — skipped when GDAL is absent, which it
    is on the Windows dev machine. Those cases exist for whoever runs this
    suite where the SR environment does.

The argv contract is pinned too: backend/services/run_sr.build_batch_script
writes that command line, and a rename here would only surface on the array.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
PREP_PATH = REPO_ROOT / "SR_code" / "variants" / "sr_prep_scene.py"


def load_prep():
    spec = importlib.util.spec_from_file_location("sr_prep_scene", PREP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = load_prep()

HAVE_GDAL = prep.gdal is not None


def write_tif(path, arr, geotransform=None, projection=None):
    """Write a one-band GeoTIFF with GDAL (test helper for the GDAL cases)."""
    gdal = prep.gdal
    height, width = arr.shape
    dtype = gdal.GDT_UInt16 if arr.dtype.itemsize == 2 else gdal.GDT_Byte
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path), width, height, 1, dtype, options=["COMPRESS=DEFLATE"])
    if geotransform:
        ds.SetGeoTransform(geotransform)
    if projection:
        ds.SetProjection(projection)
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None
    return path


class BackupTests(unittest.TestCase):
    """备份只在不存在时留 —— 作业被硬杀后重跑不能把原件盖掉。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.body = Path(self._tmp.name) / "PAN.tif"
        self.body.write_bytes(b"pre-run body\n")
        self.backup = Path(self._tmp.name) / "Debug" / "PAN.tif.srbak"

    def test_the_first_run_keeps_the_pre_run_body(self):
        self.backup.parent.mkdir()
        self.assertEqual(prep.keep_backup(str(self.body), str(self.backup)),
                         "kept")
        self.assertEqual(self.backup.read_bytes(), b"pre-run body\n")

    def test_an_existing_backup_is_never_overwritten(self):
        self.backup.parent.mkdir()
        self.backup.write_bytes(b"the real original\n")
        self.body.write_bytes(b"already landed 1x copy\n")
        self.assertEqual(prep.keep_backup(str(self.body), str(self.backup)),
                         "exists")
        self.assertEqual(self.backup.read_bytes(), b"the real original\n")


class LandSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_the_body_is_overwritten_and_the_source_stays(self):
        body = self.root / "PAN.tif"
        body.write_bytes(b"body\n")
        source = self.root / "PAN_NOSR.tif"
        source.write_bytes(b"un-superresolved\n")
        prep.land_source(str(body), str(source))
        self.assertEqual(body.read_bytes(), b"un-superresolved\n")
        # 拷贝，不是改名：归档那份原地不动。
        self.assertEqual(source.read_bytes(), b"un-superresolved\n")


class ResampleAxisTests(unittest.TestCase):
    """缩小取块内最大（ROI 不丢）、放大取最近邻。"""

    def test_shrinking_keeps_the_brightest_pixel_of_each_block(self):
        arr = np.zeros((1, 4), dtype=np.uint8)
        arr[0, 1] = 255                      # one ROI pixel inside the block
        out = prep._resample_axis(arr, 1, 2)
        self.assertEqual(out.shape, (1, 2))
        self.assertEqual(out.tolist(), [[255, 0]])

    def test_every_block_boundary_stays_inside_the_source(self):
        # floor(arange(n+1) * m/n) 的最后一个值可能溢出一格，clip 必须兜住。
        arr = np.arange(7, dtype=np.uint8).reshape(1, 7)
        for n in range(1, 8):
            with self.subTest(n=n):
                out = prep._resample_axis(arr, 1, n)
                self.assertEqual(out.shape, (1, n))

    def test_growing_takes_the_nearest_pixel(self):
        arr = np.array([[7, 9]], dtype=np.uint8)
        self.assertEqual(prep._resample_axis(arr, 1, 4).tolist(), [[7, 7, 9, 9]])

    def test_an_unchanged_axis_is_returned_as_is(self):
        arr = np.arange(4, dtype=np.uint8).reshape(2, 2)
        self.assertIs(prep._resample_axis(arr, 1, 2), arr)

    def test_a_masked_array_is_filled_with_background(self):
        # 有 nodata 的掩码：空洞在块内取最大之前先填成背景，不会被"提"成 ROI。
        arr = np.ma.masked_array(np.array([[9, 255]], dtype=np.uint8),
                                 mask=[[False, True]])
        filled = arr.filled(0)
        self.assertEqual(prep._resample_axis(filled, 1, 1).tolist(), [[9]])


@unittest.skipIf(HAVE_GDAL, "only meaningful where osgeo is absent")
class GdalMissingTests(unittest.TestCase):
    def test_resample_refuses_without_gdal(self):
        with self.assertRaises(IOError) as ctx:
            prep.resample_mask("m.tif", "s.tif", "o.tif")
        self.assertIn("GDAL", str(ctx.exception))


@unittest.skipUnless(HAVE_GDAL, "needs osgeo (SR host, not the dev machine)")
class ResampleMaskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_mask_at_the_source_size_is_copied_through(self):
        src = write_tif(self.root / "PAN_NOSR.tif", np.zeros((4, 5), np.uint16))
        mask = write_tif(self.root / "PAN_mask.tif",
                         np.full((4, 5), 255, np.uint16))
        out = self.root / "PAN_mask.tif.sr1x"
        self.assertEqual(prep.resample_mask(str(mask), str(src), str(out)),
                         "copied")
        self.assertEqual(out.read_bytes(), mask.read_bytes())

    def test_a_larger_mask_is_shrunk_to_the_source_size(self):
        src = write_tif(self.root / "PAN_NOSR.tif", np.zeros((4, 4), np.uint16))
        arr = np.zeros((8, 8), np.uint16)
        arr[1, 1] = 255                       # ROI pixel surviving the block max
        mask = write_tif(self.root / "PAN_mask.tif", arr)
        out = self.root / "PAN_mask.tif.sr1x"
        self.assertEqual(prep.resample_mask(str(mask), str(src), str(out)),
                         "resampled")
        read = prep.gdal.Open(str(out))
        self.assertEqual((read.RasterYSize, read.RasterXSize), (4, 4))
        self.assertEqual(int(read.GetRasterBand(1).ReadAsArray().max()), 255)

    def test_the_source_geotransform_is_carried_over(self):
        gt = (400000.0, 2.5, 0.0, 3000000.0, 0.0, -2.5)
        src = write_tif(self.root / "PAN_NOSR.tif", np.zeros((2, 2), np.uint16),
                        geotransform=gt)
        mask = write_tif(self.root / "PAN_mask.tif", np.zeros((4, 4), np.uint16))
        out = self.root / "PAN_mask.tif.sr1x"
        prep.resample_mask(str(mask), str(src), str(out))
        read = prep.gdal.Open(str(out))
        self.assertEqual(read.GetGeoTransform(), gt)

    def test_an_identity_geotransform_is_not_copied(self):
        # GDAL 的恒等 geotransform 等于"没有网格"；照抄过去会凭空给掩码一个网格。
        src = write_tif(self.root / "PAN_NOSR.tif", np.zeros((2, 2), np.uint16),
                        geotransform=prep._DEFAULT_GT)
        mask = write_tif(self.root / "PAN_mask.tif", np.zeros((4, 4), np.uint16))
        out = self.root / "PAN_mask.tif.sr1x"
        prep.resample_mask(str(mask), str(src), str(out))
        read = prep.gdal.Open(str(out))
        self.assertEqual(read.GetGeoTransform(), prep._DEFAULT_GT)


class MainTests(unittest.TestCase):
    """命令行契约 + 失败必须是非 0 退出（批脚本据此中止）。"""

    ARGS = ("--body", "--nosr", "--backup", "--mask", "--mask-1x")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.body = self.root / "PAN.tif"
        self.body.write_bytes(b"body\n")
        self.source = self.root / "PAN_NOSR.tif"
        self.source.write_bytes(b"un-superresolved\n")
        self.mask = self.root / "PAN_mask.tif"
        self.mask.write_bytes(b"mask\n")

    def argv(self, **over):
        values = {"--body": str(self.body), "--nosr": str(self.source),
                  "--backup": str(self.root / "Debug" / "PAN.tif.srbak"),
                  "--mask": str(self.mask),
                  "--mask-1x": str(self.root / "Debug" / "PAN_mask.tif.sr1x")}
        values.update(over)
        out = []
        for flag in self.ARGS:
            out += [flag, values[flag]]
        return out

    def test_every_flag_is_required(self):
        for flag in self.ARGS:
            with self.subTest(flag=flag):
                argv = list(self.argv())
                i = argv.index(flag)
                del argv[i:i + 2]                # drop the flag and its value
                with self.assertRaises(SystemExit):
                    prep.main(argv)

    def test_a_failure_returns_a_nonzero_exit_code(self):
        # 本体不在 → 备份那一步就抛，作业必须拿到非 0 才知道不该继续。
        with mock.patch.object(prep, "resample_mask") as resample:
            code = prep.main(self.argv(**{"--body": str(self.root / "nope.tif")}))
        self.assertEqual(code, prep.EXIT_PREP_FAIL)
        resample.assert_not_called()          # 备份/就位失败就不该走到掩码那步

    def test_the_debug_dir_is_created_for_the_scratch_files(self):
        with mock.patch.object(prep, "resample_mask") as resample:
            code = prep.main(self.argv())
        self.assertEqual(code, prep.EXIT_OK)
        self.assertTrue((self.root / "Debug").is_dir())
        self.assertEqual(self.body.read_bytes(), b"un-superresolved\n")
        self.assertEqual(self.source.read_bytes(), b"un-superresolved\n")
        resample.assert_called_once_with(str(self.mask), str(self.source),
                                         str(self.root / "Debug"
                                             / "PAN_mask.tif.sr1x"))


if __name__ == "__main__":
    unittest.main()
