"""场景库「清除缓存」—— `POST /api/scenes/clear-preview` 与 `store.mark_preview_cleared`。

判据本身（删什么、不删什么）在 `test_preview_clear.py` 里逐条钉过了；这里钉的是
**编排**：id 解析与去重、按目录归并、库里标 `cleared`、广播、汇总形状，以及那条
只有把 store 与端点合起来看才成立的结论 —— **清完之后急烤不会把文件烤回来**。

Env 在 `create_app()` 之前注入。默认**不设** `SR_SCENES_ROOT`：手工行 id
（`~` + 绝对路径，查看器里手填盘阵路径进来的那些行）是这条功能不需要场景库根的
那条路；要测库行 / 镜像树的子类自己设。
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from backend.api import paths
from backend.api.app import _eager_bake_tick, create_app
from backend.api.platform import _Subscriber, _broadcast
from backend.services import slurm
from backend.services.preview_jpg import write_preview_jpg
from backend.services.run_sr import task_fingerprint
from backend.tests import allowed_roots_env
from backend.tests.test_preview_jpg import make_strip_tif

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_QUEUE_POLL_SEC",
         "SR_PRODUCT_PREVIEW_DIV", "SR_PRODUCT_PREVIEW_MAX_AGE_SEC",
         "SR_ALLOWED_ROOTS", "SR_DRIVE_MAP", "SR_SLURM_FAKE", "SR_BUNDLE_DIR",
         "SR_SLURM_WORK_DIR", "SR_EXECUTOR", "SR_SANDBOX_ROOT")

SCENE = "JL1KF02B03_20260917120000"
SUFFIX = "260318"


def make_preview(path, div=4):
    """造一份**带规则戳**的预览（= 本平台烤出来的），返回路径。"""
    write_preview_jpg(path, np.zeros((8, 8), dtype=np.uint8), div=div)
    return Path(path)


def make_foreign_jpg(path):
    """造一份**没有规则戳**的同名 JPG（= 盘阵上别人手放的显示件）。"""
    Image.new("L", (8, 8)).save(path, format="JPEG")
    return Path(path)


class ClearBase(unittest.TestCase):
    """一个真形态的场景目录 + 临时库 + 白名单。

    目录里放**真 strip tif**（不是空文件）：`build_preview_pixels` 与
    `scene_search` 的判据都在这条链上，空文件会让「烤回来了没有」这类断言假绿。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._apps: list = []
        self._saved = {k: os.environ.get(k) for k in _ENVS}
        slurm._fake_reset()
        for k in _ENVS:
            os.environ.pop(k, None)
        os.environ["SR_AGENT_DB"] = os.path.join(self._tmp.name, "db.sqlite")
        os.environ["SR_QUEUE_POLL_SEC"] = "60"      # 关掉后台轮询的时序干扰
        os.environ["SR_SLURM_WORK_DIR"] = os.path.join(self._tmp.name, "work")
        os.environ["SR_BUNDLE_DIR"] = os.path.join(self._tmp.name, "bundle")
        os.environ.update(allowed_roots_env(self._tmp.name))
        self.root = Path(self._tmp.name).resolve()
        # 场景目录直接摆在临时根下：SR_SCENES_ROOT 设成临时根时（子类）它就是
        # 一级场景目录，rel = `<SCENE>/<SCENE>.tif`。
        self.scene_dir = self.root / SCENE
        self.scene_dir.mkdir(parents=True)
        (self.scene_dir / (SCENE + "_meta.xml")).write_text(
            "<x/>", encoding="utf-8")
        self.inp, _ = make_strip_tif(self.scene_dir, SCENE + ".tif", 64, 32)
        self.LQ = self.scene_dir.as_posix()

    def tearDown(self):
        for a in self._apps:
            try:
                a.state.store.close()      # 释放 sqlite 句柄，Windows 才能删临时库
            except Exception:  # noqa: BLE001
                pass
        self._tmp.cleanup()
        slurm._fake_reset()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ---- 夹具 ---------------------------------------------------------------

    def _make_app(self):
        app = create_app()
        self._apps.append(app)
        return app

    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(self._make_app())

    def app_client(self):
        from fastapi.testclient import TestClient
        app = self._make_app()
        return app, TestClient(app)

    def clear(self, ids, client=None):
        c = client or self.client()
        return c.post("/api/scenes/clear-preview", json={"ids": ids})

    def manual_id(self, path=None) -> str:
        """手工行 id（`~` + base64url 绝对路径）—— 查看器手填路径进来的行。"""
        return paths.scene_id_abs(path or self.inp)

    def src_preview(self, name=None, div=4):
        return make_preview(self.scene_dir / (name or SCENE + "_preview.jpg"), div)

    def state_of(self, app, tid):
        row = app.state.store.get_sr_task_by_id(tid)
        return row["preview_state"], row["preview_note"]

    def add_row(self, app, *, suffix=SUFFIX, lq=None, status="COMPLETED") -> int:
        """在库里造一行；默认已跑到 COMPLETED 且钉了 finished_at（急烤的候选）。"""
        params = {"lq_path": lq if lq is not None else self.LQ, "mask_path": None,
                  "sr_scale": 2, "suffix": suffix, "gpu": 0, "cloud_limit": 80,
                  "delete_ori": False, "grid_align": True, "options_yml": None}
        fp = task_fingerprint(params)
        app.state.store.put_sr_task(fp, params, status="new", job_id=None)
        tid = app.state.store.get_sr_task(fp)["task_id"]
        if status == "COMPLETED":
            app.state.store.set_sr_task_state(tid, "COMPLETED", mark_finished=True)
        return tid


class TestClearSelected(ClearBase):
    def test_removes_stamped_previews_and_reports_foreign(self):
        src = self.src_preview()
        nosr = self.src_preview(SCENE + "_NOSR_preview.jpg")
        foreign = make_foreign_jpg(self.scene_dir / "MANUAL_preview.jpg")

        r = self.clear([self.manual_id()])

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["summary"]["cleared"], 1)
        self.assertEqual(body["summary"]["files"], 2)
        res = body["results"][0]
        self.assertEqual(res["status"], "cleared")
        self.assertEqual(res["dir"], self.LQ)
        self.assertEqual(sorted(x["name"] for x in res["removed"]),
                         sorted([src.name, nosr.name]))
        self.assertFalse(src.exists())
        self.assertFalse(nosr.exists())
        self.assertTrue(foreign.is_file(), "没有规则戳的同名件不该删")
        self.assertEqual([x["name"] for x in res["skipped"]], [foreign.name])
        self.assertIn("规则戳", res["skipped"][0]["reason"])
        self.assertEqual(res["failed"], [])
        # 结论仍是 cleared，但「有一份没删」必须写在 reason 里：用户看到「已清除」
        # 之后多半不会再展开明细，而没删掉的那份正是他该知道的事。
        self.assertIn("另有 1 个同名文件", res["reason"])

    def test_removed_entries_carry_their_dir(self):
        """明细里带目录：同一份文件名在场景目录与镜像树各有一份时得分得清。"""
        self.src_preview()
        res = self.clear([self.manual_id()]).json()["results"][0]
        self.assertEqual(res["removed"][0]["dir"], self.LQ)

    def test_nothing_when_no_cache_on_disk(self):
        r = self.clear([self.manual_id()])
        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["results"][0]
        self.assertEqual(res["status"], "nothing")
        self.assertIn("本来就没有", res["reason"])
        self.assertEqual(r.json()["summary"]["nothing"], 1)

    def test_scene_source_is_never_deleted(self):
        """目录名以 `_preview` 结尾时，场景源 `<目录名>.jpg` 的名字恰好也像预览件。"""
        d = self.root / "SITE_preview"
        d.mkdir()
        (d / "SITE_preview_meta.xml").write_text("<x/>", encoding="utf-8")
        source = make_preview(d / "SITE_preview.jpg")

        r = self.clear([self.manual_id(source)])

        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["results"][0]
        self.assertTrue(source.is_file(), "场景源被删了 —— 这是最严重的误删")
        self.assertIn("场景源", res["skipped"][0]["reason"])
        # 一个都没删掉 → **不能报成 `nothing`**（"盘上本来就没有"是假话，而且前端按
        # 结论决定行去留：报 nothing 会把这一行从列表摘掉，可盘上那份文件还在，
        # 重新检索又是「已生成」，看起来像清除没生效）。
        self.assertEqual(res["status"], "skipped")
        self.assertIn("一个都没删", res["reason"])

    def test_only_foreign_files_reports_skipped_not_nothing(self):
        """目录里只有「不是本平台烤的」同名件：既不删、也不谎称「本来就没有」。"""
        foreign = make_foreign_jpg(self.scene_dir / "别的工具_preview.jpg")

        r = self.clear([self.manual_id()])

        res = r.json()["results"][0]
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(r.json()["summary"]["nothing"], 0)
        self.assertEqual(r.json()["summary"]["skipped"], 1)
        self.assertTrue(foreign.is_file())

    def test_unlink_failure_is_reported_per_file(self):
        """一份删不掉（被占用 / 没权限）不该拖累同目录另一份，也不该整批 500。"""
        doomed = self.src_preview("A_preview.jpg")
        fine = self.src_preview("B_preview.jpg")
        real_unlink = Path.unlink

        def flaky(p, *a, **kw):
            if p.name == doomed.name:
                raise PermissionError("拒绝访问")
            return real_unlink(p, *a, **kw)

        with mock.patch.object(Path, "unlink", flaky):
            r = self.clear([self.manual_id()])

        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["results"][0]
        self.assertEqual(res["status"], "failed")
        self.assertIn("删不掉", res["reason"])
        self.assertEqual([x["name"] for x in res["removed"]], [fine.name])
        self.assertEqual([x["name"] for x in res["failed"]], [doomed.name])
        self.assertIn("PermissionError", res["failed"][0]["reason"])
        self.assertTrue(doomed.is_file())

    def test_bad_and_fake_ids_are_skipped_not_500(self):
        """坏 id / fake 行 / 白名单外 / 非字符串：一律进 skipped 带原因，不 5xx。"""
        ghost = paths.scene_id_abs(self.scene_dir / "ghost.tif")       # 文件不存在
        outside = paths.scene_id_abs(Path.home() / "nope.tif")         # 白名单之外
        ids = ["!!!not-base64!!!", outside, ghost, "", 42, None,
               "GF07A03_PMS01_20260722125045"]                        # 最后一个是 fake 行 id

        r = self.clear(ids)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["summary"]["failed"], 0)
        self.assertEqual(body["summary"]["cleared"], 0)
        # 空串 / 非字符串 / null 连「一条 id」都不算：它们进不了明细（用户发的
        # 是什么自己都不知道，报出来只会让人去猜），但既不是 500 也不是 failed。
        self.assertEqual(len(body["results"]), 4)
        for res in body["results"]:
            self.assertEqual(res["status"], "skipped")
            self.assertTrue(res["reason"], res)
        self.assertIn("不在允许的盘阵前缀内", body["results"][1]["reason"])
        self.assertIn("文件不存在", body["results"][2]["reason"])
        self.assertIn("盘阵根未配置", body["results"][3]["reason"])

    def test_ids_must_be_a_non_empty_list(self):
        for body in ({}, {"ids": []}, {"ids": None}, {"ids": "abc"}):
            r = self.client().post("/api/scenes/clear-preview", json=body)
            self.assertEqual(r.status_code, 400, body)

    def test_too_many_ids_is_400(self):
        r = self.clear([f"id{i}" for i in range(501)])
        self.assertEqual(r.status_code, 400)
        self.assertIn("500", r.json()["detail"])

    def test_duplicate_ids_are_cleared_once(self):
        src = self.src_preview()
        r = self.clear([self.manual_id(), self.manual_id()])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["results"]), 1, "同一个 id 只回一条结论")
        self.assertEqual(body["summary"]["files"], 1)
        self.assertFalse(src.exists())


class TestClearTaskRows(ClearBase):
    """库里的行怎么标 —— 这是「清了会不会被烤回来」的答案所在。"""

    def test_marks_all_rows_of_the_dir_and_blocks_eager_bake(self):
        app, c = self.app_client()
        prod, _ = make_strip_tif(self.scene_dir,
                                 f"{SCENE}_{SUFFIX}.tif", 64, 32)
        jpg = make_preview(prod.with_name(prod.stem + "_preview.jpg"))
        t1 = self.add_row(app, suffix="sr")
        t2 = self.add_row(app, suffix="sr2")        # 同目录、另一个 suffix

        r = self.clear([self.manual_id()], client=c)

        self.assertEqual(r.json()["results"][0]["status"], "cleared")
        self.assertFalse(jpg.exists())
        # **该目录的每一行都要标**：只标一行的话，另一行下一轮就被急烤认领回去。
        for tid in (t1, t2):
            state, note = self.state_of(app, tid)
            self.assertEqual(state, "cleared")
            self.assertTrue(note.startswith("cleared:"), note)

        _eager_bake_tick(app.state)
        self.assertFalse(jpg.exists(), "标了 cleared 之后急烤不该再把文件烤回来")

    def test_running_row_leaves_the_whole_scene_alone(self):
        """后台正在烤这一景 → 整景不动。删了也会被那次烘焙在几十秒后写回来。"""
        app, c = self.app_client()
        prod, _ = make_strip_tif(self.scene_dir,
                                 f"{SCENE}_{SUFFIX}.tif", 64, 32)
        jpg = make_preview(prod.with_name(prod.stem + "_preview.jpg"))
        tid = self.add_row(app)
        self.assertIsNotNone(app.state.store.claim_preview_bake(tid))   # → running

        r = self.clear([self.manual_id()], client=c)

        res = r.json()["results"][0]
        self.assertEqual(res["status"], "skipped")
        self.assertIn("正在烘焙", res["reason"])
        self.assertTrue(jpg.is_file(), "正在烤的这一景一个文件都不该动")
        self.assertEqual(self.state_of(app, tid)[0], "running",
                         "running 不能被盖成 cleared（盖了它就再也不写回了）")

    def test_unmatched_dir_only_deletes_files(self):
        """库里没有这一景的任务行（从没提交过 / 行已超扫描上限）→ 照样删文件。"""
        app, c = self.app_client()
        other = self.root / "OTHER_SCENE"
        other.mkdir()
        (other / "OTHER_SCENE_meta.xml").write_text("<x/>", encoding="utf-8")
        inp, _ = make_strip_tif(other, "OTHER_SCENE.tif", 64, 32)
        jpg = make_preview(other / "OTHER_SCENE_preview.jpg")
        tid = self.add_row(app)                     # 这一行属于**另一个**目录

        r = self.clear([self.manual_id(inp)], client=c)

        self.assertEqual(r.json()["results"][0]["status"], "cleared")
        self.assertFalse(jpg.exists())
        self.assertEqual(self.state_of(app, tid)[0], None, "别的场景的行不该被动")

    def test_broadcasts_cleared_to_queue_subscribers(self):
        app, c = self.app_client()
        self.src_preview()
        tid = self.add_row(app)

        async def scenario():
            q = asyncio.Queue()
            app.state.subscribers.add(
                _Subscriber(asyncio.get_running_loop(), q))
            r = c.post("/api/scenes/clear-preview",
                       json={"ids": [self.manual_id()]})
            self.assertEqual(r.status_code, 200, r.text)
            return await asyncio.wait_for(q.get(), 1)

        payload = asyncio.run(scenario())

        self.assertIn('"type": "preview_update"', payload)
        self.assertIn('"state": "cleared"', payload)
        self.assertIn(f'"task_id": {tid}', payload)


class TestMarkPreviewCleared(ClearBase):
    """`store.mark_preview_cleared` 的 CAS 语义（端点的第一条防线就在这）。"""

    def test_none_and_done_rows_are_markable(self):
        app, _ = self.app_client()
        fresh = self.add_row(app, suffix="a")                       # preview_state NULL
        done = self.add_row(app, suffix="b")
        app.state.store.set_preview_state(done, "done", "baked: x")

        self.assertTrue(app.state.store.mark_preview_cleared(fresh, "cleared: 测试"))
        self.assertTrue(app.state.store.mark_preview_cleared(done, "cleared: 测试"))

        self.assertEqual(self.state_of(app, fresh)[0], "cleared")
        self.assertEqual(self.state_of(app, done)[0], "cleared")

    def test_running_row_is_not_clobbered(self):
        app, _ = self.app_client()
        tid = self.add_row(app)
        app.state.store.claim_preview_bake(tid)                     # → running

        self.assertFalse(app.state.store.mark_preview_cleared(tid, "cleared: 测试"))

        self.assertEqual(self.state_of(app, tid)[0], "running")

    def test_unfinished_row_is_not_marked(self):
        """排队 / 运行中的行不标：那一行跑完后仍该自动烤预览（用户清的是旧缓存）。"""
        app, _ = self.app_client()
        params = {"lq_path": self.LQ, "suffix": "sr3"}
        fp = task_fingerprint(params)
        app.state.store.put_sr_task(fp, params, status="submitted", job_id=None)
        tid = app.state.store.get_sr_task(fp)["task_id"]

        self.assertFalse(app.state.store.mark_preview_cleared(tid, "cleared: 测试"))

        self.assertEqual(self.state_of(app, tid)[0], None)


class TestLibraryRows(ClearBase):
    """库行 id（`SR_SCENES_ROOT` 下的 rel 编码）与镜像树。"""

    def setUp(self):
        super().setUp()
        os.environ["SR_SCENES_ROOT"] = str(self.root)

    def lib_id(self, name) -> str:
        return paths.scene_id(f"{SCENE}/{name}")

    def test_two_rows_of_one_scene_clear_one_directory(self):
        """`<目录名>.tif` 与 `PAN.tif` 是同一景的两行 —— 目录只清一次，两份文件都走。"""
        make_strip_tif(self.scene_dir, "PAN.tif", 64, 32)
        a = self.src_preview()
        b = self.src_preview("PAN_preview.jpg")
        ids = [self.lib_id(SCENE + ".tif"), self.lib_id("PAN.tif")]

        r = self.clear(ids)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["summary"]["cleared"], 2, "两个 id 各自有结论")
        self.assertEqual(body["summary"]["dirs"], 1, "但只处理了一个目录")
        self.assertEqual(body["summary"]["files"], 2, "文件数按目录算，不按 id 累加")
        self.assertEqual(sorted(x["name"] for x in body["results"][0]["removed"]),
                         sorted([a.name, b.name]))
        self.assertEqual(body["results"][0]["removed"],
                         body["results"][1]["removed"], "两行共享同一批文件")
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())

    def test_clears_both_the_mirror_tree_and_the_scene_dir(self):
        """两条落点各有一份：惰性链落镜像树，拖入链 / 未超分那份恒落场景目录。"""
        os.environ["SR_PREVIEWS_ROOT"] = str(self.root / "_previews")
        c = self.client()
        iid = self.lib_id(SCENE + ".tif")

        r = c.get(f"/api/scenes/{iid}/preview", params={"div": 4})
        self.assertEqual(r.status_code, 200, r.text)
        mirror = self.root / "_previews" / SCENE / (SCENE + "_preview.jpg")
        self.assertTrue(mirror.is_file(), "惰性链的落点是镜像树")
        local = self.src_preview()                  # 拖入链那一份（场景目录）
        self.assertNotEqual(mirror, local)

        res = self.clear([iid], client=c).json()

        self.assertEqual(res["summary"]["cleared"], 1)
        self.assertEqual(res["summary"]["files"], 2, "两处各一份，都要清")
        self.assertFalse(mirror.exists())
        self.assertFalse(local.exists())
        self.assertTrue(mirror.parent.is_dir(), "空目录保留：镜像树骨架留着")


if __name__ == "__main__":
    unittest.main()
