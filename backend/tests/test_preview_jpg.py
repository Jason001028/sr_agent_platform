"""Tests for preview_jpg service (ENVI dims, TIFF probe, sparse sampling,
stretch, Pillow fallback, cache idempotency)."""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from backend.services.preview_jpg import (PREVIEW_JPG_QUALITY, PreviewError,
                                          build_preview_pixels, cache_hit,
                                          ensure_preview_jpg, probe_tiff,
                                          rule_stamp, scene_dims,
                                          write_preview_jpg)


def make_strip_tif(dirp, name, w, h, bits=16, dtype=None, **kw):
    """Uncompressed single-band strip TIFF with a determinable ramp."""
    p = Path(dirp) / name
    if dtype is None:
        dtype = np.uint16 if bits == 16 else np.uint8
    arr = (np.arange(w * h).reshape(h, w) % 4096).astype(dtype)
    if "photometric" not in kw:
        kw["photometric"] = "minisblack"
    tifffile.imwrite(p, arr, **kw)
    return p, arr


class TestSceneDims(unittest.TestCase):
    def test_envi_hdr_first(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "GF07A03_20260801.tif"
            p.write_bytes(b"")          # tif 可空：hdr 优先，不探测
            Path(d, "GF07A03_20260801.hdr").write_text(
                "samples = 3200\nlines = 2000\nbands = 1\n")
            self.assertEqual(scene_dims(str(p)),
                             {"W": 3200, "H": 2000})

    def test_no_hdr_probes_tiff(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 320, 640)
            self.assertEqual(scene_dims(str(p)), {"W": 320, "H": 640})

    def test_unreadable_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.tif"
            p.write_bytes(b"not a tiff")
            self.assertIsNone(scene_dims(str(p)))

    def test_non_tif_without_hdr(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.img"
            p.write_bytes(b"whatever")
            self.assertIsNone(scene_dims(str(p)))


class TestProbe(unittest.TestCase):
    def test_probe_strip_layout(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 800, 600)
            info = probe_tiff(str(p))
            self.assertEqual(info["W"], 800)
            self.assertEqual(info["H"], 600)
            self.assertEqual(info["bits"], 16)
            self.assertEqual(info["compression"], 1)
            self.assertFalse(info["tiled"])

    def test_probe_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PreviewError):
                probe_tiff(str(Path(d) / "nope.tif"))


class TestBuildPreview(unittest.TestCase):
    def test_half_scale(self):
        """规则 v2：各边严格取源图的 1/2（旧规则是长边封顶 8192）。"""
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 320, 640)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.dtype, np.uint8)
            self.assertEqual(px.shape, (320, 160))  # h, w

    def test_half_scale_wide_not_capped(self):
        # 12000 宽 → 6000（旧规则会封到 8192）；采样只读几行，fixture 很小
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "big.tif", 12000, 60)
            px = build_preview_pixels(str(p))
            h, w = px.shape
            self.assertEqual(w, 6000)
            self.assertEqual(h, 30)

    def test_explicit_max_edge_still_honoured(self):
        """显式传 max_edge 时不再按 1/2 推（测试与调用方都要能钉死尺寸）。"""
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 320, 640)
            px = build_preview_pixels(str(p), 160)
            self.assertEqual(px.shape, (160, 80))    # ps = 160/640 = 0.25
            px2 = build_preview_pixels(str(p), 1280)
            self.assertEqual(px2.shape, (640, 320))  # ps 封顶 1.0，不放大

    def test_parallel_path_matches_serial(self):
        """多线程读的结果必须与串行读**逐像素相同**（线程只许提速，不许改结果）。

        源 1024 行 → 1/2 后 512 行，恰好踩到 PARALLEL_MIN_ROWS，默认走并行；
        再把阈值顶到天上强制走串行，两边比。

        这条钉的是**分块覆盖**：少了/重了哪一段、或线程写错输出行号，都会红
        （已反证：把最后一段丢掉即失败）。它**不**负责读行逻辑本身 —— 两个分支
        共用同一个 read_rows 闭包，那里的 bug 会同时污染两边而互相抵消；读行的
        正确性由上面的斜坡 / 常量 / WhiteIsZero 等内容测试钉住。"""
        from unittest import mock

        from backend.services import preview_jpg as pj
        self.assertGreater(pj.READ_THREADS, 1)
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "tall.tif", 16, pj.PARALLEL_MIN_ROWS * 2)
            par = pj.build_preview_pixels(str(p))
            self.assertEqual(par.shape, (pj.PARALLEL_MIN_ROWS, 8))
            with mock.patch.object(pj, "PARALLEL_MIN_ROWS", 1 << 30):
                ser = pj.build_preview_pixels(str(p))
            self.assertTrue(np.array_equal(par, ser),
                            "并行读与串行读结果不一致")

    def test_const_all_zero_maps_black(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.tif"
            tifffile.imwrite(p, np.zeros((50, 50), dtype=np.uint16))
            px = build_preview_pixels(str(p))
            self.assertEqual(int(px.min()), 0)
            self.assertEqual(int(px.max()), 0)

    def test_const_mid_maps_gray(self):
        # 恒定值无 p2/p98 → 中间灰（128），避免整图溢出为纯黑/白
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.tif"
            tifffile.imwrite(p, np.full((50, 50), 5000, dtype=np.uint16))
            px = build_preview_pixels(str(p))
            self.assertEqual(int(px.min()), 128)
            self.assertEqual(int(px.max()), 128)

    def test_single_strip_scalar_tags(self):
        # 小图单条带：StripOffsets/StripByteCounts 以 count==1 标量存
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "tiny.tif", 16, 16)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.shape, (8, 8))

    def test_compressed_small_falls_back_to_pillow(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "z.tif", 64, 64,
                                  compression="deflate")
            px = build_preview_pixels(str(p))   # 小文件走 Pillow 兜底
            self.assertEqual(px.dtype, np.uint8)
            self.assertEqual(px.shape, (32, 32))   # 兜底同样吃 1/2 尺度

    def test_tiled_layout_rejected(self):
        # tile=(16,16) → sample_strips 报 tiled → Pillow 兜底仍成（小文件）
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "t.tif", 128, 128,
                                  tile=(32, 32), compression=None)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.shape, (64, 64))


class TestStretchEqual(unittest.TestCase):
    """直方图均衡（镜像前端 stretchMap mode='equal'）。

    与前端的一致性已用真实 tifDecode 逐像素核过（2026-09-17，7 个用例 0 差异，
    见 docs/status/current-question.md）；这里钉的是不依赖前端就能自查的语义。
    """

    def _stretch(self, arr):
        from backend.services.preview_jpg import stretch_equal
        return stretch_equal(arr)

    def test_ramp_is_monotonic_and_uses_full_range(self):
        arr = (np.arange(256 * 4).reshape(4, 256) % 256).astype(np.uint16)
        out = self._stretch(arr)
        self.assertEqual(out.dtype, np.uint8)
        row = out[0].astype(int)
        self.assertTrue(np.all(np.diff(row) >= 0), "斜坡均衡后应单调不减")
        self.assertEqual(row[-1], 255)
        # 注意 row[0] 不是 0：最小那一档的 CDF 计数包含它自己，所以起点是 1/256
        # 量级（四舍五入后 1）—— 这与前端 cdf[k]/total*255 一致。
        self.assertEqual(row[0], 1)

    def test_const_all_zero_maps_black(self):
        out = self._stretch(np.zeros((8, 8), dtype=np.uint16))
        self.assertEqual((int(out.min()), int(out.max())), (0, 0))

    def test_const_nonzero_maps_mid_gray(self):
        out = self._stretch(np.full((8, 8), 5000, dtype=np.uint16))
        self.assertEqual((int(out.min()), int(out.max())), (128, 128))

    def test_uses_min_max_not_percentile(self):
        """equal 用的是 min/max（linear2 才用 p2/p98）—— 极亮目标必须留在图上。"""
        arr = np.full((16, 16), 1000, dtype=np.uint16)
        arr[0, 0] = 60000
        out = self._stretch(arr)
        self.assertEqual(int(out.max()), 255)

    def test_rounds_like_uint8clampedarray(self):
        """前端写进 Uint8ClampedArray（四舍五入，ties-to-even）而不是截断。

        256 个灰度各出现 4 次 → cdf[0] = 4，lut[0] = 4/1024*255 = 0.996：
        四舍五入 → 1，截断 → 0。这一个像素就钉住了差别。"""
        arr = (np.arange(256 * 4).reshape(4, 256) % 256).astype(np.uint16)
        row = self._stretch(arr)[0].astype(int)
        self.assertEqual(row[0], 1, "0.996 应四舍五入到 1（截断会给 0）")
        self.assertEqual(list(row[:4]), [1, 2, 3, 4])


class TestBigPreviewNotMistakenForBomb(unittest.TestCase):
    """1/2 尺度会把「2.4 万像素级的源」烤成 1.5 亿像素，超过 Pillow 默认像素
    上限（8948 万）的 2 倍就会抛 DecompressionBombError —— 那会让 cache_hit
    的 Image.open 失败、缓存永远判不中，于是每次打开都重烤一遍，本次的优化
    全部抵消。这里不造 1.5 亿像素的真图（太慢），而是造一张**头里声明**了
    巨大尺寸的 JPEG：Pillow 的炸弹检查只看头，足够复现该失败。"""

    HUGE = 20000          # 20000² = 4 亿像素 > 2× 默认上限

    def _fake_huge_jpeg(self, path, w, h):
        """把小 JPEG 的 SOF0 宽高改成 w×h（只骗过头解析，不解码像素）。"""
        import io
        buf = io.BytesIO()
        Image.fromarray(np.zeros((8, 8), dtype=np.uint8)).save(
            buf, format="JPEG", comment=rule_stamp())
        data = bytearray(buf.getvalue())
        i = data.index(b"\xff\xc0")            # SOF0
        data[i + 5:i + 7] = h.to_bytes(2, "big")
        data[i + 7:i + 9] = w.to_bytes(2, "big")
        Path(path).write_bytes(bytes(data))

    def test_huge_preview_opens_without_bomb_error(self):
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d, "huge.preview.jpg")
            self._fake_huge_jpeg(jpg, self.HUGE, self.HUGE)
            with Image.open(jpg) as im:         # 默认上限下这里是 BombError
                self.assertEqual(im.size, (self.HUGE, self.HUGE))

    def test_huge_preview_cache_still_hits(self):
        """端到端：巨大的旧缓存 + mtime 比源新 → 必须判为命中，而不是重烤。"""
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            self._fake_huge_jpeg(jpg, self.HUGE, self.HUGE)
            future = p.stat().st_mtime + 100
            os.utime(jpg, (future, future))
            r = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r["status"], "cached")
            self.assertEqual((r["w"], r["h"]), (self.HUGE, self.HUGE))


class TestWhiteIsZero(unittest.TestCase):
    def test_photometric_zero_is_inverted(self):
        """photometric=0（WhiteIsZero）要比同数据的 minisblack 整体反相。"""
        with tempfile.TemporaryDirectory() as d:
            base = (np.arange(64 * 64).reshape(64, 64) % 4096).astype(np.uint16)
            fwd = Path(d) / "fwd.tif"
            inv = Path(d) / "inv.tif"
            tifffile.imwrite(fwd, base, photometric="minisblack")
            tifffile.imwrite(inv, base, photometric="miniswhite")
            a = build_preview_pixels(str(fwd))
            b = build_preview_pixels(str(inv))
            self.assertTrue(np.array_equal(b, 255 - a))


class TestEnsurePreviewJpg(unittest.TestCase):
    def test_generate_then_cache_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 320, 640)
            jpg = Path(d, "GF07A03_PMS01_20260722.preview.jpg")
            r1 = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r1["status"], "generated")
            self.assertEqual((r1["w"], r1["h"]), (160, 320))
            mtime1 = jpg.stat().st_mtime

            os.utime(jpg, (mtime1, mtime1))  # 归一化，便于比较
            r2 = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r2["status"], "cached")
            self.assertEqual(jpg.stat().st_mtime, mtime1)

            with open(jpg, "rb") as f:
                self.assertTrue(f.read(2).startswith(b"\xff\xd8"))  # JPEG SOI

    def test_generated_file_carries_rule_stamp(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            ensure_preview_jpg(str(p), str(jpg))
            with Image.open(jpg) as im:
                self.assertEqual(im.info.get("comment"), rule_stamp())

    def _old_cache(self, p: Path, jpg: Path, comment: bytes | None) -> None:
        """造一张「旧规则产物」：mtime 明确比源新，可选带某个旧规则戳。"""
        kw = {"comment": comment} if comment else {}
        Image.fromarray(np.zeros((64, 32), dtype=np.uint8)).save(
            jpg, format="JPEG", **kw)
        future = p.stat().st_mtime + 100
        os.utime(jpg, (future, future))

    def test_stampless_old_cache_regenerates(self):
        """升级前烤的图没有规则戳、mtime 还比源新 —— 必须重烤。

        这是改规则最容易踩的坑：只看 mtime 的话，真机上换包后旧图原地不动。"""
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            self._old_cache(p, jpg, None)
            r = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r["status"], "generated")
            self.assertEqual((r["w"], r["h"]), (32, 32))

    def test_other_rule_version_regenerates(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            self._old_cache(p, jpg, b"srprev:v1:8192+linear2:q90")
            self.assertEqual(ensure_preview_jpg(str(p), str(jpg))["status"],
                             "generated")

    def test_other_quality_regenerates(self):
        """质量也是规则的一部分：改了 quality，旧图同样要重烤。"""
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            self._old_cache(p, jpg, rule_stamp(90))
            self.assertEqual(ensure_preview_jpg(str(p), str(jpg))["status"],
                             "generated")

    def test_stale_source_regenerates(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 64, 64)
            jpg = Path(d, "GF07A03_PMS01_20260722.preview.jpg")
            ensure_preview_jpg(str(p), str(jpg))
            old = jpg.stat().st_mtime
            # 源更新（mtime 推后）→ 缓存视为过期
            old_mtime = p.stat().st_mtime
            os.utime(p, (old_mtime + 5, old_mtime + 5))
            r = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r["status"], "generated")
            self.assertNotEqual(jpg.stat().st_mtime, old)

    def test_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PreviewError):
                ensure_preview_jpg(str(Path(d) / "nope.tif"),
                                   str(Path(d) / "nope.preview.jpg"))


class TestSplitHelpers(unittest.TestCase):
    """`ensure_preview_jpg` 拆出来的两个半成品（`cache_hit` / `write_preview_jpg`）。

    拆的理由只有一个：产物急烤要在「像素已读出」与「落盘」之间插一次 stat 复核
    （同 suffix 重跑会覆盖同一个产物路径，不复核会把半截图永久留在盘上）。
    所以这里要钉的是**拆出来之后两边规则仍然一致** —— 缓存判据不能变，落盘的
    规则戳不能变。整条 `ensure_preview_jpg` 的行为由上面 `TestEnsurePreviewJpg`
    那一组钉着（这次重构一个用例都没改就全绿，就是它守住了）。
    """

    def test_write_preview_jpg_stamps_and_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d) / "sub" / "out.preview.jpg"    # 父目录不存在也要能落
            pixels = np.zeros((8, 16), dtype=np.uint8)
            out = write_preview_jpg(jpg, pixels, div=8)

            self.assertEqual((out["w"], out["h"]), (16, 8))
            self.assertEqual(out["path"], str(jpg))
            self.assertTrue(jpg.is_file())
            with Image.open(jpg) as im:
                self.assertEqual(im.info.get("comment"), rule_stamp(div=8))

    def test_write_preview_jpg_leaves_no_temp_file(self):
        """写盘走 mkstemp + os.replace（原子替换），临时件不能留在产出目录里。

        产物目录是**生产场景目录**，多出一个 `.tmp` 就是脏数据；而且这里原地覆盖
        同一份落点，留下半截临时件会让下一次读取拿到坏图。
        """
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d, "out.preview.jpg")
            write_preview_jpg(jpg, np.zeros((8, 16), dtype=np.uint8), div=4)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["out.preview.jpg"])

    def test_write_preview_jpg_overwrites_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d, "out.preview.jpg")
            write_preview_jpg(jpg, np.zeros((8, 16), dtype=np.uint8), div=4)
            write_preview_jpg(jpg, np.zeros((4, 8), dtype=np.uint8), div=16)
            with Image.open(jpg) as im:
                self.assertEqual(im.size, (8, 4), "旧的那份被换掉了")
                self.assertEqual(im.info.get("comment"), rule_stamp(div=16))

    def test_cache_hit_answers_the_rule_only(self):
        """**`cache_hit` 只管规则，不管新鲜度** —— 这一点必须钉住，因为拆出来的两个
        调用方各自补那一半：

        * `ensure_preview_jpg`：`dst.is_file() and dst.mtime >= src.mtime` 再问它；
        * 产物急烤（`_eager_bake_tick`）：同样的 mtime 判据，但**在读像素之前**问，
          省掉重读一遍 GB 级文件。

        谁要是以为 `cache_hit` 已经包含了新鲜度，就会漏掉自己那一半 —— 于是换过源
        之后缓存永远命中，界面滑了盘上不动。
        """
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 64, 64)
            jpg = Path(d, "s.preview.jpg")
            ensure_preview_jpg(str(p), str(jpg), div=4)

            hit = cache_hit(jpg, PREVIEW_JPG_QUALITY, 4)
            self.assertIsNotNone(hit)
            self.assertEqual((hit["w"], hit["h"]), (16, 16))

            self.assertIsNone(cache_hit(jpg, PREVIEW_JPG_QUALITY, 8),
                              "档位不同 → 落空（落点里那份是 ÷4）")
            self.assertIsNone(cache_hit(jpg, 90, 4), "质量不同 → 落空")

            # 源改新：cache_hit **照样命中**（它看不出来），新鲜度是调用方那一半。
            # 而 ensure 补上自己那一半之后确实重烤 —— 两句话合起来才是完整判据。
            fresh = p.stat().st_mtime + 100
            os.utime(p, (fresh, fresh))
            self.assertIsNotNone(cache_hit(jpg, PREVIEW_JPG_QUALITY, 4),
                                 "它只看戳，不看源")
            self.assertEqual(ensure_preview_jpg(str(p), str(jpg), div=4)["status"],
                             "generated", "ensure 补上了 mtime 那一半")

    def test_cache_hit_on_a_missing_file_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                cache_hit(Path(d) / "nope.preview.jpg", PREVIEW_JPG_QUALITY, 4))


if __name__ == "__main__":
    unittest.main()
