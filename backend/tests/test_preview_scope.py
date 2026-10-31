"""预览能力的来源边界 —— 盘阵外授权 TIFF 能看，但换不来生产权限。

`SR_PREVIEW_ROOTS` 是这次新增的**只读**前缀：`POST /api/scenes/resolve` 的
`{path}` 分支与 `GET /api/scenes/{id}/preview` 认它，提交 SR、写掩码、场景检索
与 siblings / 清除缓存仍只认 `SR_ALLOWED_ROOTS`。

夹具分三块：
  * array/   盘阵前缀（SR_ALLOWED_ROOTS），但**不**配 SR_SCENES_ROOT —— 覆盖
             「盘阵内、场景根外」这条既有手工路径。
  * ext/     盘阵外授权预览源（SR_PREVIEW_ROOTS）。
  * outside/ 两块都不在，用来钉 403。

预览缓存也分两处：盘阵内的仍落源同目录（既有行为），盘阵外的落
`SR_PREVIEW_CACHE_ROOT`，文件名带源路径哈希，按来源隔离。
"""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import app as app_module
from backend.api import paths
from backend.api.app import create_app
from backend.pathguard import (PathDeniedError, ensure_allowed,
                               ensure_preview_allowed, is_preview_only,
                               normalize_submit_path)
from backend.services.preview_jpg import rule_stamp
from backend.tests import allowed_roots_env

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_PREVIEW_ROOTS",
         "SR_PREVIEW_CACHE_ROOT", "SR_TEMP_PREVIEWS_ROOT", "SR_ALLOWED_ROOTS",
         "SR_DRIVE_MAP", "SR_SLURM_FAKE", "SR_EXECUTOR")


def make_tif(path: Path, w: int = 64, h: int = 32, *,
             overview: bool = False, flip: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = (np.arange(w * h).reshape(h, w) % 4000).astype(np.uint16)
    if flip:
        base = base[:, ::-1]
    if not overview:
        tifffile.imwrite(path, base, photometric="minisblack")
        return path
    with tifffile.TiffWriter(path) as tw:
        tw.write(base, subifds=1)
        tw.write(base[::2, ::2], subfiletype=1)
    return path


class PreviewScopeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._apps: list = []
        self._saved = {k: os.environ.get(k) for k in _ENVS}
        for k in _ENVS:
            os.environ.pop(k, None)
        self.root = Path(self._tmp.name).resolve()
        self.array = self.root / "array"
        self.ext = self.root / "ext"
        self.outside = self.root / "outside"
        self.cache = self.root / "preview-cache"
        for d in (self.array, self.ext, self.outside):
            d.mkdir(parents=True, exist_ok=True)
        env = allowed_roots_env(self.array)
        os.environ.update(env)
        os.environ["SR_PREVIEW_ROOTS"] = self.ext.as_posix()
        os.environ["SR_PREVIEW_CACHE_ROOT"] = self.cache.as_posix()
        os.environ["SR_AGENT_DB"] = (self.root / "test.db").as_posix()
        os.environ["SR_SLURM_FAKE"] = "1"

    def tearDown(self):
        for a in self._apps:
            try:
                a.state.store.close()
            except Exception:  # noqa: BLE001
                pass
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def client(self):
        app = create_app()
        self._apps.append(app)
        return TestClient(app)

    def resolve_path(self, c, p: Path):
        return c.post("/api/scenes/resolve", json={"path": p.as_posix()})


class TestPreviewRoots(PreviewScopeBase):
    def test_preview_root_is_not_submit_root(self):
        p = make_tif(self.ext / "only-preview.tif")
        self.assertEqual(
            ensure_preview_allowed(p.as_posix(), kind="file"), p)
        with self.assertRaises(PathDeniedError):
            ensure_allowed(p.as_posix(), kind="file")
        self.assertTrue(is_preview_only(p))
        with self.assertRaises(PathDeniedError):
            normalize_submit_path(p.as_posix())

    def test_array_path_is_not_preview_only(self):
        p = make_tif(self.array / "in-array.tif")
        self.assertFalse(is_preview_only(p))


class TestResolveExternalTiff(PreviewScopeBase):
    def test_external_tiff_opens_as_preview_only(self):
        p = make_tif(self.ext / "scan" / "external.tif")
        r = self.resolve_path(self.client(), p)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        row, res = body["row"], body["resolved"]
        self.assertTrue(row["id"].startswith("~"))
        self.assertIsNone(row["lq_path"], "lq_path 非空即等于开了 SR 提交入口")
        self.assertEqual((row["W"], row["H"]), (64, 32))
        self.assertFalse(res["sr_capable"])
        self.assertTrue(res["preview_only"])
        self.assertIsNone(res["mask_path"])
        self.assertEqual(res["capabilities"], {
            "preview": True, "roi": True, "agent": True,
            "mask_write": False, "sr_submit": False, "scene_search": False,
            "preview_only": True,
        })

    def test_external_preview_uses_isolated_cache(self):
        p = make_tif(self.ext / "scan" / "external.tif")
        before = (p.stat().st_size, p.stat().st_mtime_ns)
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        r = c.get(f"/api/scenes/{row['id']}/preview", params={"div": 4})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        with Image.open(io.BytesIO(r.content)) as im:
            self.assertEqual(im.size, (16, 8))
        cache = paths.generic_preview_path(p)
        self.assertTrue(cache.is_file(), "盘阵外预览要落隔离缓存根")
        self.assertEqual(cache.parent.resolve(), self.cache.resolve())
        self.assertFalse((p.parent / (p.stem + "_preview.jpg")).exists(),
                         "不往外部源目录写预览文件")
        self.assertEqual((p.stat().st_size, p.stat().st_mtime_ns), before,
                         "源 TIFF 不得被改写")
        first = cache.stat().st_mtime_ns
        again = c.get(f"/api/scenes/{row['id']}/preview", params={"div": 4})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(cache.stat().st_mtime_ns, first, "同档位应命中缓存")

    def test_external_cache_stays_isolated_when_scenes_root_is_set(self):
        """生产形态：SR_SCENES_ROOT 配了，盘阵外源仍不能落进场景镜像树。"""
        os.environ["SR_SCENES_ROOT"] = self.array.as_posix()
        p = make_tif(self.ext / "scan" / "external.tif")
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/preview").status_code, 200)
        self.assertTrue(paths.generic_preview_path(p).is_file())
        self.assertFalse((self.array / "scan").exists())

    def test_external_tiff_with_and_without_overview(self):
        """同一条外部预览链：有 Overview 走 Overview，没有就回退到条带。"""
        for overview in (False, True):
            with self.subTest(overview=overview):
                p = make_tif(self.ext / f"ov-{int(overview)}.tif",
                             w=256, h=128, overview=overview)
                c = self.client()
                row = self.resolve_path(c, p).json()["row"]
                r = c.get(f"/api/scenes/{row['id']}/preview",
                          params={"div": 4})
                self.assertEqual(r.status_code, 200, r.text)
                with Image.open(io.BytesIO(r.content)) as im:
                    self.assertEqual(im.size, (64, 32))

    def test_explicit_tiff_is_not_replaced_by_same_stem_other_ext(self):
        """`same.tiff` 显式打开时必须读它自己，不能落到 `same.tif` 上。

        `sibling_raster_path` 的候选顺序是 `.tif` 在前，只该给显示件 jpg 用。
        """
        d = self.ext / "same"
        make_tif(d / "same.tif", w=64, h=32)
        make_tif(d / "same.tiff", w=256, h=128, flip=True)
        c = self.client()
        row = self.resolve_path(c, d / "same.tiff").json()["row"]
        r = c.get(f"/api/scenes/{row['id']}/preview", params={"div": 4})
        self.assertEqual(r.status_code, 200, r.text)
        with Image.open(io.BytesIO(r.content)) as im:
            self.assertEqual(im.size, (64, 32), "读成了同名 .tif 的内容")

    def test_array_same_stem_two_extensions_keep_own_content(self):
        """盘阵内同名 `.tif` / `.tiff` 使用**各自的预览落点**，响应不串图。

        名字只由 stem 拼时，A 生成完、B 覆盖、A 的响应在发送前会读到 B 的像素。
        源身份戳管不住这个「生成到发送之间」的窗口 —— 落点必须按完整源身份分开。
        """
        d = self.array / "same"
        tif = make_tif(d / "same.tif", w=64, h=32)
        tiff = make_tif(d / "same.tiff", w=256, h=128, flip=True)
        jpg_tif = paths.preview_jpg_for(tif, None)
        jpg_tiff = paths.preview_jpg_for(tiff, None)
        self.assertNotEqual(jpg_tif, jpg_tiff,
                            "同 stem 两扩展名不能共用一份可覆盖缓存")
        c = self.client()
        for p, size in ((tiff, (64, 32)), (tif, (16, 8)), (tiff, (64, 32))):
            with self.subTest(path=p.name):
                row = self.resolve_path(c, p).json()["row"]
                r = c.get(f"/api/scenes/{row['id']}/preview",
                          params={"div": 4})
                self.assertEqual(r.status_code, 200, r.text)
                with Image.open(io.BytesIO(r.content)) as im:
                    self.assertEqual(im.size, size)
        self.assertTrue(jpg_tif.is_file())
        self.assertTrue(jpg_tiff.is_file())

    def test_endpoint_snapshot_survives_other_extension_overwrite(self):
        """端点返回后另一份同名源再生成，先前拿到的响应内容仍是自己的。"""
        d = self.array / "snap"
        tif = make_tif(d / "snap.tif", w=64, h=32)
        tiff = make_tif(d / "snap.tiff", w=256, h=128, flip=True)
        c = self.client()
        row_a = self.resolve_path(c, tif).json()["row"]
        row_b = self.resolve_path(c, tiff).json()["row"]
        a = c.get(f"/api/scenes/{row_a['id']}/preview", params={"div": 4})
        b = c.get(f"/api/scenes/{row_b['id']}/preview", params={"div": 4})
        self.assertEqual(b.status_code, 200)
        # /preview 现在回的是字节快照，不再指向一个会被后续请求覆盖的路径。
        self.assertEqual(a.headers.get("content-type"), "image/jpeg")
        with Image.open(io.BytesIO(a.content)) as im:
            self.assertEqual(im.size, (16, 8), "A 的响应被 B 覆盖成了 64×32")

    def test_snapshot_rejects_other_source_in_shared_cache(self):
        """受控交错：A 生成后、A 读快照前，B 覆盖同一落点 → A 仍拿到自己的像素。

        `_jpeg_snapshot` 只核对档位时，同档位的 B 会通过校验，A 返回 64×32。
        这里把落点强行指到同一路径（模拟名称冲突的极端情形），断言快照按源身份
        判废并重生成，A 的响应仍是 16×8，B 的仍是 64×32。
        """
        d = self.array / "race"
        a = make_tif(d / "a.tif", 64, 32)
        b = make_tif(d / "b.tif", 256, 128, flip=True)
        shared = d / "shared_preview.jpg"
        c = self.client()
        row_a = self.resolve_path(c, a).json()["row"]
        row_b = self.resolve_path(c, b).json()["row"]
        real_ensure = app_module.ensure_preview_jpg
        state = {"raced": False}

        def racing_ensure(source_path, jpg_path, div=2, quality=85):
            out = real_ensure(source_path, jpg_path, div=div, quality=quality)
            if not state["raced"]:
                state["raced"] = True
                # A 已生成、快照还没读：另一份源覆盖同一个落点
                real_ensure(str(b), str(shared), div=div, quality=quality)
            return out

        with mock.patch.object(paths, "preview_jpg_for", return_value=shared), \
                mock.patch.object(app_module, "ensure_preview_jpg",
                                  racing_ensure):
            ra = c.get(f"/api/scenes/{row_a['id']}/preview",
                       params={"div": 4})
            rb = c.get(f"/api/scenes/{row_b['id']}/preview",
                       params={"div": 4})
        self.assertEqual(ra.status_code, 200, ra.text)
        self.assertEqual(rb.status_code, 200, rb.text)
        with Image.open(io.BytesIO(ra.content)) as im:
            self.assertEqual(im.size, (16, 8), "A 的响应串成了 B 的像素")
        with Image.open(io.BytesIO(rb.content)) as im:
            self.assertEqual(im.size, (64, 32))

    def test_three_way_stem_collision_keeps_own_content_and_cache(self):
        """`same.tif` + `same.tiff` + `same_tif.tif` 三份并存时落点仍互不相同。

        只拼扩展名的身份段（`same_tif`）本身可以是另一份源的 stem：前者的冲突
        落点与第三份的原名正好相同。身份段必须含源路径哈希，三份源才能各用各
        的缓存；否则同档位的另一份源会通过快照校验（它只核对档位时）。
        """
        d = self.array / "collide"
        a = make_tif(d / "same.tif", 64, 32)
        b = make_tif(d / "same.tiff", 256, 128, flip=True)
        c3 = make_tif(d / "same_tif.tif", 128, 64)
        jpgs = [paths.preview_jpg_for(p, None) for p in (a, b, c3)]
        self.assertEqual(len(set(jpgs)), 3, "三份源的缓存落点必须互不相同")
        client = self.client()
        for p, size in {a: (16, 8), b: (64, 32), c3: (32, 16)}.items():
            row = self.resolve_path(client, p).json()["row"]
            r = client.get(f"/api/scenes/{row['id']}/preview",
                           params={"div": 4})
            self.assertEqual(r.status_code, 200, r.text)
            with Image.open(io.BytesIO(r.content)) as im:
                self.assertEqual(im.size, size, f"{p.name} 的响应串了源")
        self.assertTrue(all(j.is_file() for j in jpgs))
        self.assertEqual(len({j.read_bytes() for j in jpgs}), 3,
                         "三份缓存内容不能串源")

    def test_cached_preview_notices_replaced_source_with_preserved_mtime(self):
        """源被换成另一份内容、但 mtime 不动：仍要按源身份戳判废。"""
        p = make_tif(self.ext / "scan" / "rev.tif", w=64, h=32)
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        r = c.get(f"/api/scenes/{row['id']}/preview", params={"div": 4})
        self.assertEqual(r.status_code, 200, r.text)
        with Image.open(io.BytesIO(r.content)) as im:
            self.assertEqual(im.size, (16, 8))
        st = p.stat()
        make_tif(p, w=128, h=64)          # 同路径、同 mtime、不同尺寸
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        r2 = c.get(f"/api/scenes/{row['id']}/preview", params={"div": 4})
        self.assertEqual(r2.status_code, 200, r2.text)
        with Image.open(io.BytesIO(r2.content)) as im:
            self.assertEqual(im.size, (32, 16), "保留了旧缓存 / 旧内容")

    def test_jpg_sibling_raster_symlink_outside_is_rejected(self):
        """显示件换成同名栅格后，实际读取路径要重新过白名单（含符号链接）。"""
        d = self.ext / "link"
        d.mkdir(parents=True)
        secret = make_tif(self.outside / "secret.tif")
        jpg = d / "shot.jpg"
        Image.new("L", (8, 8)).save(jpg, format="JPEG")
        try:
            os.symlink(secret, d / "shot.tif")
        except (OSError, NotImplementedError):
            self.skipTest("本机不支持创建符号链接")
        r = self.client().get(
            f"/api/scenes/{paths.scene_id_abs(jpg)}/preview",
            params={"div": 4})
        self.assertEqual(r.status_code, 404, r.text)

    def test_same_stem_in_two_dirs_keeps_two_caches(self):
        a = make_tif(self.ext / "a" / "same.tif")
        b = make_tif(self.ext / "b" / "same.tif", flip=True)
        pa, pb = paths.preview_jpg_for(a, None), paths.preview_jpg_for(b, None)
        self.assertNotEqual(pa, pb, "同名不同源的缓存必须分开")
        c = self.client()
        ids = []
        for p in (a, b):
            row = self.resolve_path(c, p).json()["row"]
            ids.append(row["id"])
            self.assertEqual(
                c.get(f"/api/scenes/{row['id']}/preview").status_code, 200)
        self.assertNotEqual(ids[0], ids[1], "同名不同源的 id 必须分开")
        self.assertTrue(pa.is_file() and pb.is_file())
        self.assertNotEqual(pa.read_bytes(), pb.read_bytes(),
                            "缓存内容不能串源")

    def test_array_path_outside_scenes_root_keeps_source_side_cache(self):
        """既有手工路径零回归：盘阵前缀内、场景根外仍落源同目录。"""
        p = make_tif(self.array / "manual" / "keep.tif")
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/preview").status_code, 200)
        self.assertTrue((p.parent / (p.stem + "_preview.jpg")).is_file())
        self.assertFalse(paths.generic_preview_path(p).exists())

    def test_scene_dir_under_preview_root_is_not_resolved(self):
        """目录解析问的是「能不能提交」，所以盘阵外目录整条拒绝。

        盘阵外的预览源只按**单张 TIFF** 打开（见上一条用例）。目录一旦能解析，
        调用方就会把它当场景拿到 `dir` / `input` / `mask_path`，而这几样是生产
        语义 —— 即使目录里凑齐了 `<目录名>.tif` + meta.xml 也不给。
        """
        d = self.ext / "SCENE"
        d.mkdir(parents=True)
        make_tif(d / "SCENE.tif")
        (d / "SCENE_meta.xml").write_text("<x/>", encoding="utf-8")
        r = self.resolve_path(self.client(), d)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertNotIn("resolved", r.json())

    def test_outside_both_roots_403(self):
        p = make_tif(self.outside / "nope.tif")
        r = self.resolve_path(self.client(), p)
        self.assertEqual(r.status_code, 403, r.text)

    def test_preview_scope_does_not_open_business_endpoints(self):
        p = make_tif(self.ext / "scan" / "external.tif")
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        # 只读预览之外的端点仍按盘阵前缀解析：siblings / 清除缓存都拒绝。
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/siblings").status_code, 404)
        clear = c.post("/api/scenes/clear-preview", json={"ids": [row["id"]]})
        self.assertEqual(clear.status_code, 200)
        result = clear.json()["results"][0]
        self.assertEqual(result["status"], "skipped")
        # 只读的空间标识跟着预览范围放开（Agent 批注要稳定的图像身份）。
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/spatial-identity").status_code, 200)

    def test_sr_submit_rejects_external_path(self):
        p = make_tif(self.ext / "scan" / "external.tif")
        c = self.client()
        r = c.post("/api/queue", json={
            "lq_path": p.parent.as_posix(),
            "mask_path": (p.parent / "x_mask.tif").as_posix(),
        })
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("SR_ALLOWED_ROOTS", r.json()["detail"])


class TestRowPreviewValidation(PreviewScopeBase):
    """场景行不得把「文件存在」当成「缓存有效」。

    行上回 `hasPreview=true` + `previewDiv=N`，前端就会跳过 `/preview` 直接读静态
    JPG。若盘上那份是**没有源签名**的旧缓存、或源已经换过，展示出来的就是错图。
    行构造必须按实际源核对缓存签名，无效则让前端重生成。
    """

    def _library_scene(self, w=64, h=32):
        d = self.array / "SCENE1"
        p = make_tif(d / "SCENE1.tif", w, h)
        (d / "SCENE1_meta.xml").write_text("<x/>", encoding="utf-8")
        os.environ["SR_SCENES_ROOT"] = self.array.as_posix()
        return p

    def _row(self, c):
        return c.get("/api/scenes").json()["results"][0]

    def test_old_cache_without_source_stamp_is_not_advertised(self):
        p = self._library_scene()
        c = self.client()
        jpg = paths.preview_jpg_path(p, self.array)
        jpg.parent.mkdir(parents=True, exist_ok=True)
        Image.new("L", (16, 8)).save(
            jpg, format="JPEG", comment=rule_stamp(85, 4))
        os.utime(jpg, None)
        row = self._row(c)
        self.assertFalse(row["hasPreview"],
                         "旧戳没有 ;src=，不能以 hasPreview=true 通向静态 URL")
        self.assertIsNone(row["previewDiv"])
        self.assertIsNotNone(row["jpgUrl"])     # URL 仍给出，前端据此触发重生成
        # 打开一次（惰性重生成）之后，行才恢复为可用。
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/preview",
                  params={"div": 4}).status_code, 200)
        row2 = self._row(c)
        self.assertTrue(row2["hasPreview"])
        self.assertEqual(row2["previewDiv"], 4)
        self.assertIsNotNone(row2["srcRev"])

    def test_source_replacement_invalidates_row_cache(self):
        p = self._library_scene()
        c = self.client()
        row = self._row(c)
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/preview",
                  params={"div": 4}).status_code, 200)
        self.assertTrue(self._row(c)["hasPreview"])
        st = p.stat()
        make_tif(p, 128, 64)                  # 同路径、同 mtime、不同内容
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        row2 = self._row(c)
        self.assertFalse(row2["hasPreview"], "源换过就不能再展示旧缓存")
        self.assertIsNone(row2["previewDiv"])

    def test_manual_row_also_validates_source_stamp(self):
        p = make_tif(self.array / "manual" / "m.tif", 64, 32)
        c = self.client()
        row = self.resolve_path(c, p).json()["row"]
        self.assertEqual(
            c.get(f"/api/scenes/{row['id']}/preview",
                  params={"div": 4}).status_code, 200)
        jpg = paths.preview_jpg_for(p, None)
        Image.new("L", (16, 8)).save(
            jpg, format="JPEG", comment=rule_stamp(85, 4))
        os.utime(jpg, None)
        row2 = self.resolve_path(c, p).json()["row"]
        self.assertFalse(row2["hasPreview"])
        self.assertIsNone(row2["previewDiv"])


class TestIdScope(PreviewScopeBase):
    def test_abs_id_needs_explicit_preview_scope(self):
        p = make_tif(self.ext / "scan" / "external.tif")
        token = paths.scene_id_abs(p)
        with self.assertRaises(PathDeniedError):
            paths.scene_id_to_abs(token)
        self.assertEqual(
            paths.scene_id_to_abs(token, allow_preview=True), p)


if __name__ == "__main__":
    unittest.main()
