"""services/preview_clear.py —— 场景库「清除缓存」的删除判据。

这是**唯一会主动删生产数据目录里文件**的模块，所以判据每一条都要有正反用例：

* 该删的：带规则戳的 `<stem>_preview.jpg` 与改名前的 `<stem>.preview.jpg`；
* 不该删的：没有戳的同名件（别人手放的）、场景源自己（目录名以 `_preview` 结尾
  时 `is_scene_file` 与缓存件同名）、符号链接、子目录里的东西（不递归）；
* 删不掉的：权限 / 被占用 → 计入 failed，**其余文件照删**（逐条尽力）。

顺带钉 `preview_jpg.has_rule_stamp` —— 判据的基石，只认前缀不认完整戳形态。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from backend.services import preview_clear
from backend.services.preview_jpg import (has_rule_stamp, preview_div_of,
                                           write_preview_jpg)


def make_preview(path: Path, div: int = 2) -> Path:
    """造一份**带规则戳**的预览（= 本平台烤出来的）。"""
    write_preview_jpg(path, np.zeros((8, 8), dtype=np.uint8), div=div)
    return path


def make_foreign_jpg(path: Path) -> Path:
    """造一份**没有规则戳**的同名 JPG（= 盘阵上别人手放的显示件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (8, 8)).save(path, format="JPEG")
    return path


class ClearBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve() / "SCENE_A"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()


class TestIsPreviewName(unittest.TestCase):
    def test_both_shapes_and_case(self):
        for name in ("SC.tif_preview.jpg", "PAN_NOSR_preview.jpg",
                     "PAN_PREVIEW.JPG", "SC.tif.preview.jpg", "x.Preview.Jpg"):
            self.assertTrue(preview_clear.is_preview_name(name), name)

    def test_rejects_everything_else(self):
        for name in ("SC.tif", "preview.jpg", "x_preview.tif", "x.preview.tiff",
                     "SC_meta.xml", "_preview.jpg.jpeg"):
            self.assertFalse(preview_clear.is_preview_name(name), name)


class TestHasRuleStamp(ClearBase):
    def test_stamped_vs_foreign(self):
        stamped = make_preview(self.dir / "SC_preview.jpg", div=2)
        foreign = make_foreign_jpg(self.dir / "OTHER_preview.jpg")
        self.assertTrue(has_rule_stamp(stamped))
        # 同一份文件按档位读得出来：两个判据同源
        self.assertEqual(preview_div_of(stamped), 2)
        self.assertFalse(has_rule_stamp(foreign))
        self.assertIsNone(preview_div_of(foreign))

    def test_accepts_v1_era_stamp(self):
        """v1 的戳给不出档位（`preview_div_of` 返回 None），但它确实是烤出来的，
        清缓存时要认 —— 判前缀而不是判完整形态的理由就在这。"""
        p = self.dir / "OLD_preview.jpg"
        Image.new("L", (8, 8)).save(p, format="JPEG",
                                    comment=b"srprev:8192+linear2")
        self.assertTrue(has_rule_stamp(p))
        self.assertIsNone(preview_div_of(p))

    def test_unreadable_is_false(self):
        broken = self.dir / "BROKEN_preview.jpg"
        broken.write_bytes(b"not a jpeg")
        self.assertFalse(has_rule_stamp(broken))
        self.assertFalse(has_rule_stamp(self.dir / "missing.jpg"))


class TestClearDirPreviews(ClearBase):
    def test_removes_both_names_keeps_foreign(self):
        new = make_preview(self.dir / "SC_preview.jpg")
        legacy = make_preview(self.dir / "SC.preview.jpg", div=4)
        foreign = make_foreign_jpg(self.dir / "MANUAL_preview.jpg")
        src = self.dir / "SC.tif"
        src.write_bytes(b"raster")
        meta = self.dir / "SC_meta.xml"
        meta.write_text("<x/>", encoding="utf-8")

        r = preview_clear.clear_dir_previews(self.dir)

        self.assertEqual(sorted(r["removed"]), sorted([new.name, legacy.name]))
        self.assertFalse(new.exists())
        self.assertFalse(legacy.exists())
        self.assertTrue(foreign.is_file(), "没有规则戳的同名件不该删")
        self.assertTrue(src.is_file())
        self.assertTrue(meta.is_file())
        self.assertEqual([n for n, _ in r["skipped"]], [foreign.name])
        self.assertIn("规则戳", r["skipped"][0][1])
        self.assertEqual(r["failed"], [])

    def test_non_preview_files_are_not_even_reported(self):
        """场景目录里躺着一堆 `.tif` / `_meta.xml` —— 它们不该出现在明细里，
        否则失败清单会被噪声淹掉。"""
        (self.dir / "SC.tif").write_bytes(b"raster")
        (self.dir / "SC_meta.xml").write_text("<x/>", encoding="utf-8")
        r = preview_clear.clear_dir_previews(self.dir)
        self.assertEqual(r["removed"], [])
        self.assertEqual(r["skipped"], [])
        self.assertEqual(r["failed"], [])

    def test_scene_source_is_never_deleted(self):
        """目录名以 `_preview` 结尾时，场景源 `<目录名>.jpg` 的名字**恰好**
        也是 `*_preview.jpg`。给它也写上规则戳（模拟「有人烤过这个文件」），
        此时只剩 is_scene_file 一道锁 —— 这道锁必须结实。"""
        d = Path(self._tmp.name).resolve() / "SITE_preview"
        d.mkdir()
        (d / "SITE_preview_meta.xml").write_text("<x/>", encoding="utf-8")
        source = make_preview(d / "SITE_preview.jpg")

        r = preview_clear.clear_dir_previews(d)

        self.assertTrue(source.is_file(), "场景源被删了 —— 这是最严重的误删")
        self.assertEqual(r["removed"], [])
        self.assertEqual([n for n, _ in r["skipped"]], [source.name])
        self.assertIn("场景源", r["skipped"][0][1])

    def test_does_not_recurse(self):
        sub = self.dir / "Debug"
        sub.mkdir()
        deep = make_preview(sub / "SC_preview.jpg")
        self.assertEqual(preview_clear.clear_dir_previews(self.dir)["removed"], [])
        self.assertTrue(deep.is_file(), "子目录（Debug/）里的东西不归这一层管")

    def test_missing_dir_is_empty_result(self):
        r = preview_clear.clear_dir_previews(self.dir / "no-such-dir")
        self.assertEqual((r["removed"], r["skipped"], r["failed"]), ([], [], []))

    def test_idempotent(self):
        make_preview(self.dir / "SC_preview.jpg")
        self.assertEqual(len(preview_clear.clear_dir_previews(self.dir)["removed"]), 1)
        r2 = preview_clear.clear_dir_previews(self.dir)
        self.assertEqual(r2["removed"], [])
        self.assertEqual(r2["failed"], [])

    def test_one_failure_does_not_stop_the_rest(self):
        """一份删不掉（占用 / 权限）不能拖累同目录另一份。"""
        doomed = make_preview(self.dir / "A_preview.jpg")
        fine = make_preview(self.dir / "B_preview.jpg")
        real_unlink = Path.unlink

        def flaky(p, *a, **kw):
            if p.name == doomed.name:
                raise PermissionError("拒绝访问")
            return real_unlink(p, *a, **kw)

        with mock.patch.object(Path, "unlink", flaky):
            r = preview_clear.clear_dir_previews(self.dir)

        self.assertEqual(r["removed"], [fine.name])
        self.assertFalse(fine.exists())
        self.assertTrue(doomed.is_file())
        self.assertEqual([n for n, _ in r["failed"]], [doomed.name])
        self.assertIn("PermissionError", r["failed"][0][1])

    def test_unlistable_dir_counts_as_failure(self):
        """目录读不了（服务账号没权限）不能报成「无需清除」—— 后者会让用户
        以为盘上本来就没什么可清的。"""
        make_preview(self.dir / "SC_preview.jpg")
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError("拒绝")):
            r = preview_clear.clear_dir_previews(self.dir)
        self.assertEqual(r["removed"], [])
        self.assertEqual([n for n, _ in r["failed"]], [self.dir.name])
        self.assertIn("无法列举", r["failed"][0][1])

    def test_symlink_is_skipped(self):
        outside = Path(self._tmp.name).resolve() / "outside.jpg"
        make_preview(outside)
        link = self.dir / "LINK_preview.jpg"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("本机不允许建符号链接（Windows 需开发者模式/管理员）")
        r = preview_clear.clear_dir_previews(self.dir)
        self.assertEqual(r["removed"], [])
        self.assertTrue(outside.is_file())
        self.assertIn("符号链接", r["skipped"][0][1])


if __name__ == "__main__":
    unittest.main()
