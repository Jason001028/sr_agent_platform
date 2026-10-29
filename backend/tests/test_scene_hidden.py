"""服务端隐藏集合（2026-10-28）。

场景库「清除选定 / 全部清除」与卡片垃圾篓不再只写本机 localStorage：后端把
**场景目录**记进 SQLite 的 scene_hidden 表，`/api/scenes`、`/api/scene-library`
与 Agent 的 search_scenes 共用同一份。这里钉的是编排层：

  * 清除成功 / 本来就没有缓存 → 隐藏；跳过 / 失败 → 不隐藏；
  * 隐藏键是场景目录（同一景的两行一起隐藏）；
  * 垃圾篓只隐藏、不删盘上文件；
  * 恢复后重新出现在检索结果里。
"""

import os
import unittest

from backend.api import paths
from backend.services.scene_search import dir_key

from .test_scene_clear_preview import (SCENE, ClearBase, TestLibraryRows,
                                       make_foreign_jpg)


class TestHiddenSet(ClearBase):
    def hidden(self, client=None):
        c = client or self.client()
        return c.get("/api/scenes/hidden").json()

    def test_cleared_scene_is_hidden_and_summary_says_so(self):
        self.src_preview()
        c = self.client()
        body = self.clear([self.manual_id()], client=c).json()
        self.assertEqual(body["summary"]["hidden"], 1)
        self.assertEqual(body["summary"]["hidden_total"], 1)
        items = self.hidden(c)["items"]
        self.assertEqual([i["scene_dir"] for i in items],
                         [dir_key(self.scene_dir)])

    def test_nothing_status_also_hides(self):
        """盘上本来就没有缓存：清不掉任何东西，但这一景同样该从检索里消失。"""
        body = self.clear([self.manual_id()]).json()
        self.assertEqual(body["results"][0]["status"], "nothing")
        self.assertEqual(body["summary"]["hidden_total"], 1)

    def test_skipped_and_failed_are_not_hidden(self):
        foreign = make_foreign_jpg(self.scene_dir / "MANUAL_preview.jpg")
        body = self.clear([self.manual_id()]).json()
        self.assertEqual(body["results"][0]["status"], "skipped")
        self.assertTrue(foreign.is_file())
        self.assertEqual(body["summary"]["hidden_total"], 0)

    def test_hide_endpoint_hides_without_deleting_anything(self):
        preview = self.src_preview()
        c = self.client()
        r = c.post("/api/scenes/hide", json={"ids": [self.manual_id()]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["hidden"], 1)
        self.assertEqual(body["hidden_total"], 1)
        self.assertEqual(body["hidden_ids"], [self.manual_id()])
        self.assertEqual(body["skipped"], [])
        self.assertTrue(preview.is_file(), "隐藏不是清除：盘上的预览 JPG 不动")
        self.assertTrue(self.inp.is_file())

    def test_hide_rejects_bad_payload(self):
        c = self.client()
        self.assertEqual(c.post("/api/scenes/hide", json={"ids": []}).status_code, 400)
        self.assertEqual(c.post("/api/scenes/hide", json={}).status_code, 400)
        r = c.post("/api/scenes/hide", json={"ids": ["no-such-id"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["hidden_total"], 0)
        self.assertEqual(r.json()["hidden_ids"], [], "跳过的 id 不算隐藏成功")
        self.assertTrue(r.json()["skipped"], "不可访问的 id 如实回报")

    def test_hidden_ids_lists_only_what_was_written(self):
        """前端按 hidden_ids 摘行：可访问的那条成功，坏 id 进 skipped 留在列表里。"""
        c = self.client()
        body = c.post("/api/scenes/hide",
                      json={"ids": [self.manual_id(), "no-such-id"]}).json()
        self.assertEqual(body["hidden_ids"], [self.manual_id()])
        self.assertEqual([s["id"] for s in body["skipped"]], ["no-such-id"])
        self.assertEqual(self.hidden(c)["count"], 1)

    def test_restore_brings_the_scene_back(self):
        c = self.client()
        c.post("/api/scenes/hide", json={"ids": [self.manual_id()]})
        r = c.post("/api/scenes/restore-hidden", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"restored": 1, "hidden_total": 0})
        self.assertEqual(self.hidden(c)["count"], 0)

    def test_restore_by_keys_only_drops_those(self):
        other = self.root / (SCENE + "_other")
        other.mkdir()
        (other / (SCENE + "_other.tif")).write_bytes(b"x")
        c = self.client()
        c.post("/api/scenes/hide", json={"ids": [self.manual_id(),
                                                 paths.scene_id_abs(other / (SCENE + "_other.tif"))]})
        self.assertEqual(self.hidden(c)["count"], 2)
        r = c.post("/api/scenes/restore-hidden",
                   json={"keys": [dir_key(self.scene_dir)]})
        self.assertEqual(r.json(), {"restored": 1, "hidden_total": 1})
        self.assertEqual([i["scene_dir"] for i in self.hidden(c)["items"]],
                         [dir_key(other)])


class TestHiddenFiltersSearch(TestLibraryRows):
    """/api/scenes 走 search_scenes 的 exclude_dirs；同一景的两行一起消失。"""

    def test_hidden_scene_disappears_from_scene_list(self):
        make_foreign_jpg(self.scene_dir / "MANUAL_preview.jpg")
        c = self.client()
        before = c.get("/api/scenes").json()
        self.assertEqual(before["count"], 1)
        self.assertEqual(before["hidden"], 0)

        c.post("/api/scenes/hide", json={"ids": [self.lib_id(SCENE + ".tif")]})

        after = c.get("/api/scenes").json()
        self.assertEqual(after["results"], [])
        self.assertEqual(after["count"], 0)
        self.assertEqual(after["hidden"], 1)

        c.post("/api/scenes/restore-hidden", json={})
        self.assertEqual(c.get("/api/scenes").json()["count"], 1)


if __name__ == "__main__":
    unittest.main()
