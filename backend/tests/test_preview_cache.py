"""services/preview_cache.py —— 拖拽入口的临时预览缓存（1 天 TTL）。

覆盖两件事，两者都是「删错东西」的防线：

* **落点**：`tmp_preview_path` 只按「绝对路径的 sha256」定名、按当天分桶，
  且只 `mkdir` + `stat`，**绝不列举目录**（请求路径里能调它，见下）。
* **清理**：`purge_temp_previews` 只删「桶名是 ISO 日期、名 < 今天、且带本模块
  写的标记文件」的目录。数据盘上碰巧叫 `2026-09-16` 的目录、符号链接、根不合法
  —— 一个都不许碰。
"""

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from backend.services import preview_cache

TODAY = date(2026, 9, 18)


class PreviewCacheBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "tmp-previews"
        self._saved = os.environ.get("SR_TEMP_PREVIEWS_ROOT")
        os.environ["SR_TEMP_PREVIEWS_ROOT"] = str(self.root)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SR_TEMP_PREVIEWS_ROOT", None)
        else:
            os.environ["SR_TEMP_PREVIEWS_ROOT"] = self._saved
        self._tmp.cleanup()

    def bucket(self, day: date, marked: bool = True) -> Path:
        """造一个日期桶。`marked=False` 模拟「不是我们建的目录」。"""
        b = self.root / day.isoformat()
        b.mkdir(parents=True, exist_ok=True)
        if marked:
            (b / preview_cache.MARKER_NAME).touch()
        (b / "x.jpg").write_bytes(b"jpeg")
        return b


class TestTmpPreviewPath(PreviewCacheBase):
    def test_bucket_is_today_and_marker_written(self):
        p = preview_cache.tmp_preview_path("/disk/scene/SC.tif", now=TODAY)
        self.assertEqual(p.parent, self.root / "2026-09-18")
        self.assertEqual(p.suffix, ".jpg")
        self.assertTrue((p.parent / preview_cache.MARKER_NAME).is_file())

    def test_same_source_same_path_different_source_differs(self):
        a = preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY)
        b = preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY)
        c = preview_cache.tmp_preview_path("/disk/b.tif", now=TODAY)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_path_shape_independent(self):
        """`/disk/a.tif` 与 `\\disk\\a.tif` 这类同一文件的两种写法不保证归一 ——
        但必须**稳定**（同输入同输出），且哈希只吃路径、不吃 mtime：缓存新鲜度
        由 ensure_preview_jpg 自己判，这里不掺日期变量。"""
        p1 = preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY)
        p2 = preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY + timedelta(days=1))
        self.assertEqual(p1.name, p2.name)          # 文件名不含日期
        self.assertNotEqual(p1.parent, p2.parent)   # 桶名含日期

    def test_default_root_when_env_unset(self):
        os.environ.pop("SR_TEMP_PREVIEWS_ROOT", None)
        root = preview_cache.temp_previews_root()
        self.assertEqual(root.name, "sr-tmp-previews")
        self.assertTrue(root.is_absolute())

    def test_never_lists_directories(self):
        """请求路径里能调它（`/preview-tmp`），所以必须过「禁止扫盘」这关。

        桶**已存在**与**不存在**两条都要走一遍：前者只能靠 `exists()`，
        后者只能靠 `mkdir`。
        """
        preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY)     # 先建桶
        with ExitStack() as st:
            for name in ("rglob", "glob", "iterdir"):
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            hit = preview_cache.tmp_preview_path("/disk/a.tif", now=TODAY)
            self.assertTrue(hit.parent.is_dir())
            fresh = preview_cache.tmp_preview_path("/disk/z.tif",
                                                   now=TODAY + timedelta(days=1))
            self.assertFalse(fresh.exists())


class TestPurge(PreviewCacheBase):
    def test_deletes_only_expired_marked_buckets(self):
        old2, old1 = self.bucket(TODAY - timedelta(days=2)), self.bucket(TODAY - timedelta(days=1))
        today = self.bucket(TODAY)
        tomorrow = self.bucket(TODAY + timedelta(days=1))   # 时钟快了一天的机器
        unmarked = self.bucket(TODAY - timedelta(days=3), marked=False)
        loose = self.root / "evil"                          # 非日期目录
        loose.mkdir()
        odd = self.root / "2026-9-1"                        # 像日期但不合格式
        odd.mkdir()

        removed = preview_cache.purge_temp_previews(now=TODAY)

        self.assertEqual(sorted(removed), sorted([old1.name, old2.name]))
        self.assertFalse(old1.exists())
        self.assertFalse(old2.exists())
        for keep in (today, tomorrow, unmarked, loose, odd):
            self.assertTrue(keep.is_dir(), f"不该删 {keep}")

    def test_symlinked_bucket_is_skipped(self):
        """符号链接一律跳过：`rmtree` 不跟链接是常识，但这条链上任何一环判错
        都会删到根之外，所以显式挡一道。"""
        outside = Path(self._tmp.name).resolve() / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("别删我", encoding="utf-8")
        link = self.root / (TODAY - timedelta(days=1)).isoformat()
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("本机不允许建符号链接（Windows 需开发者模式/管理员）")

        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])
        self.assertTrue((outside / "precious.txt").is_file())

    def test_missing_root_is_noop(self):
        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])

    def test_refuses_symlinked_root(self):
        real = self.root
        real.mkdir(parents=True)
        link = real.parent / "linked-root"
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("本机不允许建符号链接")
        os.environ["SR_TEMP_PREVIEWS_ROOT"] = str(link)
        self.bucket(TODAY - timedelta(days=1))
        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])
        self.assertTrue((real / (TODAY - timedelta(days=1)).isoformat()).is_dir())

    def test_idempotent_and_swallows_errors(self):
        self.bucket(TODAY - timedelta(days=1))
        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), ["2026-09-17"])
        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])
        # 根指向一个文件（配置写错）：不抛，只 no-op
        os.environ["SR_TEMP_PREVIEWS_ROOT"] = str(self.root / "2026-09-17" / "x.jpg")
        self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])

    def test_rmtree_failure_does_not_raise(self):
        self.bucket(TODAY - timedelta(days=1))
        with mock.patch("backend.services.preview_cache.shutil.rmtree",
                        side_effect=OSError("占用中")):
            self.assertEqual(preview_cache.purge_temp_previews(now=TODAY), [])


if __name__ == "__main__":
    unittest.main()
