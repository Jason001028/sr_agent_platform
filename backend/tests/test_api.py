"""Tests for the REST API (backend/api/app.py) — scenes browse + lazy preview.

Env is injected per-test before create_app(); TestClient drives the app in
memory. Fixtures are uncompressed strip TIFFs under a temp SR_SCENES_ROOT.
"""

import io
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import paths
from backend.api.app import create_app
from backend.services.preview_jpg import rule_stamp, stamp_div


def make_scene(dirp, name, w=320, h=640):
    """造一个真机形态的场景目录，返回场景文件路径。

    真机布局是 <root>/…/<生产编号>/<生产编号>.tif，且同目录内有
    <生产编号>_meta.xml —— scene_search 的场景判据（is_scene_dir）。平铺的裸
    文件不会被列出。
    """
    stem = Path(name).stem
    d = Path(dirp) / stem
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    arr = (np.arange(w * h).reshape(h, w) % 65535).astype(np.uint16)
    tifffile.imwrite(p, arr, photometric="minisblack")
    (d / (stem + "_meta.xml")).write_text(
        '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
        encoding="utf-8")
    return p


def _unset(*names):
    for n in names:
        os.environ.pop(n, None)


class SceneListMixin(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k) for k in
                       ("SR_SCENES_ROOT", "SR_PREVIEWS_ROOT")}

    def tearDown(self):
        self._root.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def client(self, root):
        if root is None:
            _unset("SR_SCENES_ROOT")
        else:
            os.environ["SR_SCENES_ROOT"] = root
        _unset("SR_PREVIEWS_ROOT")
        return TestClient(create_app())


class TestScenesFakeFallback(SceneListMixin):
    def test_no_root_returns_fake_rows_with_dims(self):
        c = self.client(None)
        r = c.get("/api/scenes")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["source"], "fake")
        self.assertGreater(body["count"], 0)
        for row in body["results"]:
            self.assertTrue(row["fake"])
            self.assertIsInstance(row["W"], int)
            self.assertIsInstance(row["H"], int)
            self.assertGreater(row["W"], 0)
            self.assertIsNone(row["jpgUrl"])
            self.assertFalse(row["hasPreview"])
            self.assertIsNone(row["lq_path"])       # fake 无真实目录可关联

    def test_fake_filters_via_query(self):
        c = self.client(None)
        r = c.get("/api/scenes", params={"satellite": "GF07A03"})
        self.assertEqual(r.status_code, 200)
        rows = r.json()["results"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["satellite"], "GF07A03")

    def test_fake_sensor_filter(self):
        c = self.client(None)
        r = c.get("/api/scenes", params={"sensor": "PMS05"})
        self.assertEqual(r.status_code, 200)
        rows = r.json()["results"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["sensor"], "PMS05")


class TestScenesDisk(SceneListMixin):
    def test_disk_listing_with_dims_and_jpg_url(self):
        make_scene(self._root.name, "GF07A03_PMS01_20260722125045.tif",
                   320, 640)
        c = self.client(self._root.name)
        r = c.get("/api/scenes")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["source"], "disk")
        self.assertEqual(len(body["results"]), 1)
        row = body["results"][0]
        self.assertFalse(row["fake"])
        self.assertEqual((row["W"], row["H"]), (320, 640))
        self.assertEqual(
            row["rel"],
            "GF07A03_PMS01_20260722125045/GF07A03_PMS01_20260722125045.tif")
        self.assertEqual(
            row["jpgUrl"], "/disk-array/GF07A03_PMS01_20260722125045/"
                           "GF07A03_PMS01_20260722125045.preview.jpg")
        self.assertFalse(row["hasPreview"])
        # 上下文侧舱任务关联：lq_path = scene 文件父目录（= run_sr 目录语义）
        self.assertEqual(row["lq_path"], Path(os.path.realpath(
            os.path.join(self._root.name, "GF07A03_PMS01_20260722125045"))).as_posix())

    def test_disk_hdr_dims_preferred(self):
        root = self._root.name
        p = make_scene(root, "GF07A03_PMS01_20260722125045.tif", 40, 40)
        Path(p).with_suffix(".hdr").write_text(
            "samples = 4000\nlines = 3000\nbands = 1\n")
        c = self.client(root)
        row = c.get("/api/scenes").json()["results"][0]
        self.assertEqual((row["W"], row["H"]), (4000, 3000))

    def test_disk_nested_scene_gets_quoted_rel_url(self):
        root = Path(self._root.name)
        sub = root / "2026/07"
        sub.mkdir(parents=True)
        make_scene(str(sub), "KF02B04_PMS05_20260810120000.tif")
        c = self.client(str(root))
        row = c.get("/api/scenes").json()["results"][0]
        self.assertEqual(
            row["rel"], "2026/07/KF02B04_PMS05_20260810120000/"
                        "KF02B04_PMS05_20260810120000.tif")
        self.assertTrue(row["jpgUrl"].startswith("/disk-array/2026/07/"))
        self.assertEqual(row["lq_path"],
                         Path(os.path.realpath(
                             str(sub / "KF02B04_PMS05_20260810120000"))).as_posix())

    def test_abs_path_not_leaked(self):
        make_scene(self._root.name, "GF07A03_PMS01_20260722125045.tif")
        c = self.client(self._root.name)
        row = c.get("/api/scenes").json()["results"][0]
        # 绝不外泄**场景文件本身**的绝对路径；lq_path 只给父目录（不含文件名），
        # 且与 rel/jpgUrl 同语义（浏览器已能从 /api/queue 看到同款目录路径）。
        self.assertNotIn("path", row)
        self.assertNotIn(self._root.name, row["jpgUrl"])
        self.assertFalse(str(row["lq_path"]).endswith("GF07A03_PMS01_20260722125045.tif"))


class TestScenesImageSource(SceneListMixin):
    """§4.7：盘阵 .jpg/.jpeg 行 = 显示就绪图本身（无烘焙、jpgUrl 指源文件）。"""

    def make_jpg(self, name, w=40, h=30, dirp=None):
        """场景目录里的显示就绪 JPG（真机 <目录名>_meta.xml + <目录名>.jpg）。"""
        d = Path(dirp or self._root.name) / Path(name).stem
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        Image.fromarray(np.full((h, w), 128, dtype=np.uint8)).save(
            p, format="JPEG")
        (d / (Path(name).stem + "_meta.xml")).write_text(
            '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
            encoding="utf-8")
        return p

    def test_jpg_row_is_self_previewing(self):
        self.make_jpg("GF07A03_PMS01_20260722125045.jpg", 40, 30)
        c = self.client(self._root.name)
        body = c.get("/api/scenes").json()
        self.assertEqual(body["scanned"], 1)
        row = body["results"][0]
        self.assertEqual((row["W"], row["H"]), (40, 30))       # Pillow 头
        self.assertTrue(row["hasPreview"])                     # 无需烘焙
        self.assertTrue(row["jpgUrl"].endswith(".jpg"))
        self.assertNotIn(".preview.jpg", row["jpgUrl"])
        self.assertEqual(
            row["rel"],
            "GF07A03_PMS01_20260722125045/GF07A03_PMS01_20260722125045.jpg")
        self.assertEqual(row["lq_path"], Path(os.path.realpath(
            os.path.join(self._root.name, "GF07A03_PMS01_20260722125045"))).as_posix())

    def test_preview_endpoint_serves_the_source(self):
        p = self.make_jpg("KF02B04_PMS05_20260810120000.jpg")
        c = self.client(self._root.name)
        row = c.get("/api/scenes").json()["results"][0]
        r = c.get(f"/api/scenes/{row['id']}/preview")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, p.read_bytes())
        # 不给源是 JPG 的场景落 .preview.jpg 缓存
        self.assertFalse(Path(self._root.name, "KF02B04_PMS05_20260810120000",
                              "KF02B04_PMS05_20260810120000.preview.jpg").is_file())

    def test_baked_preview_cache_is_not_listed(self):
        make_scene(self._root.name, "GF07A03_PMS01_20260722125045.tif")
        c = self.client(self._root.name)
        scene = c.get("/api/scenes").json()["results"][0]
        self.assertEqual(c.get(f"/api/scenes/{scene['id']}/preview").status_code,
                         200)                       # 落盘 <stem>.preview.jpg
        body = c.get("/api/scenes").json()
        self.assertEqual(body["scanned"], 1)         # 缓存不新增行
        self.assertEqual(body["results"][0]["name"],
                         "GF07A03_PMS01_20260722125045")

    def test_tif_and_jpg_same_stem_are_two_rows(self):
        make_scene(self._root.name, "ZY302_MUX_20260805120000.tif")
        self.make_jpg("ZY302_MUX_20260805120000.jpg")
        rows = self.client(self._root.name).get("/api/scenes").json()["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["id"] for r in rows}), 2)   # id 互不相同
        self.assertEqual({r["name"] for r in rows}, {"ZY302_MUX_20260805120000"})


class TestPreview(SceneListMixin):
    def _disk_client_with_scene(self, w=320, h=640):
        make_scene(self._root.name, "GF07A03_PMS01_20260722125045.tif", w, h)
        return self.client(self._root.name)

    def test_generate_and_fetch_jpeg(self):
        c = self._disk_client_with_scene()
        scene = c.get("/api/scenes").json()["results"][0]
        r = c.get(f"/api/scenes/{scene['id']}/preview")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"))
        jpg = Path(self._root.name, "GF07A03_PMS01_20260722125045",
                   "GF07A03_PMS01_20260722125045.preview.jpg")
        self.assertTrue(jpg.is_file())

    def test_second_call_is_cached(self):
        c = self._disk_client_with_scene()
        scene = c.get("/api/scenes").json()["results"][0]
        jpg = Path(self._root.name, "GF07A03_PMS01_20260722125045",
                   "GF07A03_PMS01_20260722125045.preview.jpg")
        self.assertEqual(c.get(f"/api/scenes/{scene['id']}/preview").status_code,
                         200)
        mtime1 = jpg.stat().st_mtime
        os.utime(jpg, (mtime1, mtime1))
        self.assertEqual(c.get(f"/api/scenes/{scene['id']}/preview").status_code,
                         200)
        self.assertEqual(jpg.stat().st_mtime, mtime1)

    def test_has_preview_flips_after_generation(self):
        c = self._disk_client_with_scene()
        scene = c.get("/api/scenes").json()["results"][0]
        self.assertFalse(scene["hasPreview"])
        c.get(f"/api/scenes/{scene['id']}/preview")
        row = c.get("/api/scenes").json()["results"][0]
        self.assertTrue(row["hasPreview"])
        self.assertTrue(row["jpgUrl"].endswith(".preview.jpg"))

    def test_traversal_id_rejected(self):
        c = self._disk_client_with_scene()
        bad = paths.scene_id("../secret.tif")
        r = c.get(f"/api/scenes/{bad}/preview")
        self.assertEqual(r.status_code, 404)

    def test_outside_root_id_rejected(self):
        c = self._disk_client_with_scene()
        # 该 id 解码出根外的绝对路径 rel（./… 相对根外），白名单拒绝
        rel = "../outside/evil.tif"
        bad = paths.scene_id(rel)
        self.assertEqual(c.get(f"/api/scenes/{bad}/preview").status_code, 404)

    def test_nonexistent_scene_rejected(self):
        c = self._disk_client_with_scene()
        bad = paths.scene_id("no_such_scene.tif")
        self.assertEqual(c.get(f"/api/scenes/{bad}/preview").status_code, 404)

    def test_garbage_id_rejected(self):
        c = self._disk_client_with_scene()
        self.assertEqual(c.get("/api/scenes/%21%21%21/preview").status_code, 404)

    def test_preview_in_fake_mode_404(self):
        c = self.client(None)      # 无盘阵根
        self.assertEqual(c.get("/api/scenes/whatever/preview").status_code, 404)

    def test_health(self):
        c = self._disk_client_with_scene()
        r = c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["source"], "disk")


class TestPreviewDiv(SceneListMixin):
    """下采样档位：`?div=` 参数 + 行上的 `previewDiv` 回填。

    前端据 `previewDiv` 与当前档位比对来决定要不要重烤 —— 只看 `hasPreview`
    不够（它不认档位），这是「滑了滑块盘上却不动」那个哑火的根因。
    """

    NAME = "GF07A03_PMS01_20260722125045"

    def setUp(self):
        super().setUp()
        make_scene(self._root.name, self.NAME + ".tif", 320, 640)
        self.jpg = Path(self._root.name, self.NAME, self.NAME + ".preview.jpg")

    def _client(self):
        return self.client(self._root.name)

    def _row(self, c):
        return c.get("/api/scenes").json()["results"][0]

    def test_row_preview_div_is_none_before_baking(self):
        row = self._row(self._client())
        self.assertFalse(row["hasPreview"])
        self.assertIsNone(row["previewDiv"])

    def test_row_preview_div_reflects_the_baked_level(self):
        c = self._client()
        sid = self._row(c)["id"]
        c.get(f"/api/scenes/{sid}/preview?div=8")
        row = self._row(c)
        self.assertTrue(row["hasPreview"])
        self.assertEqual(row["previewDiv"], 8)
        # 换档位、原地重烤 → 行字段跟着变
        c.get(f"/api/scenes/{sid}/preview?div=2")
        self.assertEqual(self._row(c)["previewDiv"], 2)

    def test_legacy_stamp_reads_as_none(self):
        """v2 那代戳解不出 div → None → 前端按当前档位重烤一轮（惰性，预期内）。"""
        c = self._client()
        sid = self._row(c)["id"]
        self.jpg.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jpg, "wb") as f:
            Image.new("L", (16, 32)).save(
                f, format="JPEG", comment=rule_stamp(85, 2).replace(
                    b"div2", b"half"))
        os.utime(self.jpg, None)
        self.assertIsNone(self._row(c)["previewDiv"])

    def test_each_div_bakes_its_own_size_and_stamp(self):
        c = self._client()
        sid = self._row(c)["id"]
        for div, (w, h) in ((2, (160, 320)), (4, (80, 160)), (8, (40, 80)),
                            (16, (20, 40)), (32, (10, 20))):
            with self.subTest(div=div):
                r = c.get(f"/api/scenes/{sid}/preview?div={div}")
                self.assertEqual(r.status_code, 200, r.text)
                with Image.open(io.BytesIO(r.content)) as im:
                    self.assertEqual(im.size, (w, h))
                    self.assertEqual(stamp_div(im.info.get("comment")), div)

    def test_invalid_div_400(self):
        c = self._client()
        sid = self._row(c)["id"]
        for bad in (0, 1, 3, 64):
            with self.subTest(div=bad):
                self.assertEqual(
                    c.get(f"/api/scenes/{sid}/preview?div={bad}").status_code, 400)
        self.assertFalse(self.jpg.exists())

    def test_default_div_is_legacy_two(self):
        """缺 `div` 参数 = 逐字节等于换档位之前的行为（旧 dist 配新 backend 不乱套）。"""
        c = self._client()
        sid = self._row(c)["id"]
        r = c.get(f"/api/scenes/{sid}/preview")
        with Image.open(io.BytesIO(r.content)) as im:
            self.assertEqual(im.size, (160, 320))
            self.assertEqual(im.info.get("comment"), rule_stamp(85, 2))


if __name__ == "__main__":
    unittest.main()
