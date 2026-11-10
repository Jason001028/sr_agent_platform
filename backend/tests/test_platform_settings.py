"""Platform-level settings: server persistence + preview long-edge limit wiring."""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from backend.services import platform_settings
from backend.services.preview_jpg import (
    PREVIEW_TRUE_MAX_EDGE, cache_meta, ensure_preview_jpg, preview_max_edge,
    rule_stamp, source_revision)
from backend.services.store import Store


def make_tif(dirp, name, w, h):
    p = Path(dirp) / name
    arr = np.zeros((h, w), dtype=np.uint8)
    tifffile.imwrite(p, arr, photometric="minisblack")
    return p


class PlatformSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("SR_AGENT_DB")
        os.environ["SR_AGENT_DB"] = str(Path(self.tmp.name) / "db.sqlite")

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("SR_AGENT_DB", None)
        else:
            os.environ["SR_AGENT_DB"] = self.old_db
        self.tmp.cleanup()

    def test_defaults_and_round_trip_across_store_instances(self):
        self.assertEqual(platform_settings.load_settings(refresh=True),
                         {"preview_max_edge": 16384})
        platform_settings.save_settings({"preview_max_edge": 8192})
        path = os.environ["SR_AGENT_DB"]
        self.assertEqual(Store(path).list_platform_settings(),
                         {"preview_max_edge": 8192})
        self.assertEqual(platform_settings.load_settings(refresh=True),
                         {"preview_max_edge": 8192})

    def test_rejects_values_outside_the_three_options(self):
        for bad in (5000, 16383, 0, -4096, "8192", True, None):
            with self.subTest(value=bad):
                with self.assertRaises(platform_settings.SettingsError):
                    platform_settings.save_settings({"preview_max_edge": bad})
        with self.assertRaises(platform_settings.SettingsError):
            platform_settings.save_settings({"unknown": 1})
        self.assertEqual(platform_settings.load_settings(refresh=True),
                         {"preview_max_edge": 16384})

    def test_corrupt_or_unknown_saved_value_falls_back_to_default(self):
        store = Store(os.environ["SR_AGENT_DB"])
        db = store._db()
        db.execute("INSERT OR REPLACE INTO platform_settings VALUES (?, ?, ?)",
                   ("preview_max_edge", '"not-a-number"', 1.0))
        db.commit()
        store.close()
        self.assertEqual(platform_settings.load_settings(refresh=True),
                         {"preview_max_edge": 16384})

    def test_preview_generation_uses_the_configured_cap(self):
        source = make_tif(self.tmp.name, "wide.tif", 20000, 1)
        self.assertEqual(preview_max_edge(source, 1), PREVIEW_TRUE_MAX_EDGE)
        platform_settings.save_settings({"preview_max_edge": 4096})
        self.assertEqual(preview_max_edge(source, 1), 4096)
        platform_settings.save_settings({"preview_max_edge": 8192})
        self.assertEqual(preview_max_edge(source, 1), 8192)

    def test_cache_stamp_follows_the_cap_in_both_directions(self):
        jpg = Path(self.tmp.name) / "v_preview.jpg"
        Image.new("L", (320, 640)).save(
            jpg, format="JPEG", comment=rule_stamp(85, 2))
        self.assertIsNotNone(cache_meta(jpg))
        # 调低：戳里的上限与当前设置对不上 → 失效（这张图尺寸没超限，
        # 所以真正起作用的只有「戳里的上限」这一条）
        platform_settings.save_settings({"preview_max_edge": 4096})
        self.assertIsNone(cache_meta(jpg))
        # 服务端按新上限重生成（原地覆盖，戳换成 4096）→ 命中
        Image.new("L", (320, 640)).save(
            jpg, format="JPEG", comment=rule_stamp(85, 2))
        self.assertIsNotNone(cache_meta(jpg))
        # 调回：4096 口径的那份不能因为尺寸没超就被当成 16384 口径命中
        platform_settings.save_settings({"preview_max_edge": 16384})
        self.assertIsNone(cache_meta(jpg))
        # 按当前上限重新写戳 → 命中
        Image.new("L", (320, 640)).save(
            jpg, format="JPEG", comment=rule_stamp(85, 2))
        self.assertIsNotNone(cache_meta(jpg))

    def test_stamp_records_the_cap_not_the_effective_edge(self):
        """小图/浅档位时实际尺寸小于上限；戳必须记上限，否则每次打开都判缓存失效。"""
        source = make_tif(self.tmp.name, "small.tif", 2000, 1000)
        jpg = Path(self.tmp.name) / "small_preview.jpg"
        out = ensure_preview_jpg(source, jpg, div=2)
        self.assertEqual((out["w"], out["h"]), (1000, 500))
        with Image.open(out["path"]) as im:
            self.assertEqual(im.info.get("comment"),
                             rule_stamp(85, 2,
                                        source_rev=source_revision(source)))
        self.assertIsNotNone(cache_meta(Path(out["path"])))
        platform_settings.save_settings({"preview_max_edge": 4096})
        self.assertIsNone(cache_meta(Path(out["path"])))


class PlatformSettingsApiTest(unittest.TestCase):
    """HTTP contract used by every client machine."""

    def setUp(self):
        from backend.tests.test_api_platform import PlatformBase
        self.base = PlatformBase(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.tearDown)

    def test_get_put_and_validation(self):
        c = self.base.client()
        body = c.get("/api/settings").json()
        self.assertEqual(body["settings"], {"preview_max_edge": 16384})
        self.assertEqual(body["options"]["preview_max_edge"], [4096, 8192, 16384])

        r = c.put("/api/settings", json={"preview_max_edge": 8192})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["settings"]["preview_max_edge"], 8192)
        # 另一台机器 / 新请求读到的也是同一份
        self.assertEqual(
            c.get("/api/settings").json()["settings"]["preview_max_edge"], 8192)

        for bad in ({"preview_max_edge": 16383}, {"preview_max_edge": "4096"},
                    {"unknown": 1}, {}):
            with self.subTest(body=bad):
                self.assertEqual(
                    c.put("/api/settings", json=bad).status_code, 400)
        self.assertEqual(
            c.get("/api/settings").json()["settings"]["preview_max_edge"], 8192)


if __name__ == "__main__":
    unittest.main()
