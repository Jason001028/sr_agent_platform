"""阶段5 API 契约测试（api-contract.md §5.3 验收清单）。

Env 在 create_app() 前注入（SR_AGENT_DB / SR_LLM_MOCK / SR_SLURM_FAKE / …），
TestClient 内存驱动。覆盖：
  工具 manifest + 直调；chat 新建/历史/SSE 事件序列/并发 409/404；
  queue list/submit/幂等复用/cancel/events 广播；masks 落原图目录 + 白名单。
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.platform import (_Subscriber, _broadcast, _queue_state,
                                  chat_send)
from backend.services import slurm
from backend.services.run_sr import task_fingerprint

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_LLM_MOCK",
         "SR_SLURM_FAKE", "SR_SLURM_FAKE_T_MS", "SR_SLURM_WORK_DIR",
         "SR_QUEUE_POLL_SEC", "SR_SANDBOX_ROOT", "SR_EXECUTOR",
         "SR_LOCKED_DIR", "SR_LOCAL_GPU", "SR_SUFFIX_DEFAULT")


def sse_events(text: str) -> list[dict]:
    """Parse `data: {json}\n\n` SSE frames off a stream body."""
    events = []
    for block in text.split("\n\n"):
        line = block.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def touch_tif(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")          # mask 端点只要求文件存在（W/H 来自 body）
    return p


class PlatformBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._apps: list = []
        self._saved = {k: os.environ.get(k) for k in _ENVS}
        slurm._fake_reset()
        self._set_env()

    def _make_app(self):
        app = create_app()
        self._apps.append(app)
        return app

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

    def _set_env(self):
        os.environ["SR_AGENT_DB"] = os.path.join(self._tmp.name, "db.sqlite")
        os.environ["SR_SLURM_WORK_DIR"] = os.path.join(self._tmp.name, "work")

    def client(self) -> TestClient:
        return TestClient(self._make_app())

    def app_client(self):
        app = self._make_app()
        return app, TestClient(app)


class TestTools(PlatformBase):
    def test_manifest_lists_four_tools(self):
        c = self.client()
        r = c.get("/api/tools")
        self.assertEqual(r.status_code, 200)
        tools = r.json()["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(names, {"run_sr", "search_scenes", "sr_job_status",
                                 "fix_bad_lines"})
        for t in tools:
            self.assertIn("description", t)
            self.assertIn("parameters", t)

    def test_direct_call_returns_ok_dict_http_200(self):
        c = self.client()                       # 无盘阵根 → search 走 fake
        r = c.post("/api/tools/search_scenes", json={})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["source"], "fake")
        self.assertIsNone(body["error"])

    def test_unknown_tool_404(self):
        r = self.client().post("/api/tools/ghost_tool", json={})
        self.assertEqual(r.status_code, 404)

    def test_non_object_body_400(self):
        r = self.client().post("/api/tools/search_scenes",
                               content=b"[1,2,3]",
                               headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400)

    def test_param_validation_err_result_not_transport(self):
        # fix_bad_lines 缺参 → err()（HTTP 仍 200，业务结果 ok=false）
        c = self.client()
        r = c.post("/api/tools/fix_bad_lines", json={"input_path": "x.tif"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])


class TestChat(PlatformBase):
    def _set_env(self):
        super()._set_env()
        os.environ["SR_LLM_MOCK"] = "1"
        os.environ.pop("SR_SCENES_ROOT", None)     # fake 场景（确定性 12 个）

    def test_create_and_list_session(self):
        c = self.client()
        r = c.post("/api/chat/sessions")
        self.assertEqual(r.status_code, 201)
        sid = r.json()["session_id"]
        self.assertEqual(len(sid), 32)
        sessions = c.get("/api/chat/sessions").json()["sessions"]
        self.assertEqual(sessions[0]["session_id"], sid)

    def test_messages_unknown_session_404(self):
        r = self.client().get("/api/chat/sessions/ghost/messages")
        self.assertEqual(r.status_code, 404)

    def test_send_streams_full_event_sequence(self):
        c = self.client()
        sid = c.post("/api/chat/sessions").json()["session_id"]
        r = c.post(f"/api/chat/sessions/{sid}/messages",
                   json={"content": "看看盘阵上有什么场景"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"].split(";")[0],
                         "text/event-stream")
        events = sse_events(r.text)
        types = [e["type"] for e in events]
        # mock LLM 固定脚本（§5.1）：先 tool_call search_scenes 再总结
        self.assertEqual(types,
                         ["turn_start", "tool_call", "tool_result",
                          "assistant", "turn_done"])
        self.assertEqual(events[1]["name"], "search_scenes")
        self.assertEqual(events[1]["args"], {})
        self.assertTrue(events[2]["ok"])
        self.assertIn("mock 模型", events[3]["content"])
        self.assertIn("mock 模型", events[4]["content"])

    def test_history_projection_after_turn(self):
        c = self.client()
        sid = c.post("/api/chat/sessions").json()["session_id"]
        c.post(f"/api/chat/sessions/{sid}/messages", json={"content": "hi"})
        body = c.get(f"/api/chat/sessions/{sid}/messages").json()
        self.assertEqual(body["session_id"], sid)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        tool = body["messages"][2]
        self.assertEqual(tool["tool_name"], "search_scenes")
        self.assertTrue(tool["ok"])
        final = body["messages"][3]
        self.assertIn("mock 模型", final["content"])
        self.assertIsNone(final["tool_calls"])

    def test_empty_content_400_and_bad_json_400(self):
        c = self.client()
        sid = c.post("/api/chat/sessions").json()["session_id"]
        self.assertEqual(
            c.post(f"/api/chat/sessions/{sid}/messages",
                   json={"content": "  "}).status_code, 400)
        self.assertEqual(
            c.post(f"/api/chat/sessions/{sid}/messages",
                   content=b"{not json",
                   headers={"content-type": "application/json"}).status_code,
            400)

    def test_busy_session_returns_409(self):
        """同会话并发回合 → 409。TestClient 会缓冲完整流式响应（首请求已跑完
        才返回），HTTP 层无法制造重叠；故直接在持有会话锁时调用 chat_send 端点
        验证其 409 守卫（会话正忙 → 拒绝第二个回合）。"""
        app = self._make_app()
        sid = app.state.store.create_session()

        class _Req:
            def __init__(self, app):
                self.app = app

            async def body(self):
                return b'{"content": "hi"}'

        async def scenario():
            lock = asyncio.Lock()
            await lock.acquire()
            app.state.chat_locks[sid] = lock          # 首个回合占用中
            with self.assertRaises(HTTPException) as cm:
                await chat_send(sid, _Req(app))
            self.assertEqual(cm.exception.status_code, 409)
            self.assertIn("正忙", cm.exception.detail)

        asyncio.run(scenario())

    def test_tools_direct_call_skip_session(self):
        # 直调工具不落会话：tools 调用后 sessions 仍为空
        c = self.client()
        r = c.post("/api/tools/search_scenes", json={})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(c.get("/api/chat/sessions").json()["sessions"], [])


class TestQueue(PlatformBase):
    SCENE = "JL1KF02B03_xxx_L1_PAN"                  # 场景目录名 = SR 的 DatarootLQ

    def _set_env(self):
        super()._set_env()
        os.environ["SR_SLURM_FAKE"] = "1"
        # 每 stage 的停留时间。**不能太小**：`_poll_state` 每 30ms 采一次样，而
        # `test_submit_progresses_to_completed` 断言它**亲眼看到过** PENDING 与
        # RUNNING —— stage 若短到两次采样之间就整个走完，采样会直接从 SUBMITTING
        # 跳到 COMPLETED，断言随机失败。原先 120ms 在整包 300+ 用例的负载下就会漏采
        # （2026-09-14 全量跑复现两次、单跑通过），抬到 500ms 给足采样裕度；本类
        # 因此慢约 1s，换来的是不再随机红。
        os.environ["SR_SLURM_FAKE_T_MS"] = "500"
        os.environ["SR_QUEUE_POLL_SEC"] = "60"       # 关闭 lifespan 轮询干扰
        # 真实场景目录 + 目录里已有的掩码：POST /api/queue 现在要求
        # <lq_path>/<目录名>_mask.tif 在场（plan §4.3），不再接受「不给掩码」。
        self.scene_dir = Path(self._tmp.name) / self.SCENE
        self.scene_dir.mkdir(parents=True, exist_ok=True)
        self.mask_file = touch_tif(
            self.scene_dir / f"{self.SCENE}_mask.tif")
        self.LQ = str(self.scene_dir)

    def _submit(self, c, **over):
        body = {"lq_path": self.LQ, "suffix": "t"}
        body.update(over)
        return c.post("/api/queue", json=body)

    def _poll_state(self, c, tid, timeout=4.0):
        states = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            tasks = c.get("/api/queue").json()["tasks"]
            st = next(t["state"] for t in tasks if t["task_id"] == tid)
            if not states or states[-1] != st:
                states.append(st)
            if st == "COMPLETED":
                return states
            time.sleep(0.03)
        return states

    def test_empty_list(self):
        c = self.client()
        self.assertEqual(c.get("/api/queue").json()["tasks"], [])

    def test_submit_progresses_to_completed(self):
        app, c = self.app_client()
        r = self._submit(c)
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["status"], "SUBMITTED")
        self.assertEqual(body["state"], "SUBMITTING")
        self.assertIsInstance(body["job_id"], int)
        states = self._poll_state(c, body["task_id"])
        self.assertIn("PENDING", states)
        self.assertIn("RUNNING", states)
        self.assertEqual(states[-1], "COMPLETED")
        db = app.state.store.get_sr_task_by_id(body["task_id"])
        self.assertEqual(db["status"], "COMPLETED")
        self.assertEqual(db["job_id"], body["job_id"])

    def test_duplicate_submit_reuses_completed(self):
        c = self.client()
        first = self._submit(c).json()
        self._poll_state(c, first["task_id"])
        second = self._submit(c).json()
        self.assertEqual(second["status"], "RESUMED_COMPLETED")
        self.assertEqual(second["state"], "COMPLETED")
        self.assertEqual(second["job_id"], first["job_id"])   # 未重复提交
        self.assertEqual(len(c.get("/api/queue").json()["tasks"]), 1)

    def test_submit_says_the_run_writes_in_place(self):
        # 无沙箱 = SR 就地写场景目录并把输入改名 *_NOSR.tif：提交响应必须自己说清
        # 楚，别让操作者从别处推断（工作单 §4.2 最后一条）。
        body = self._submit(self.client()).json()
        self.assertTrue(body["in_place"])
        self.assertIn("_NOSR.tif", body["notice"])
        self.assertIn(self.LQ, body["notice"])

    def test_no_in_place_notice_when_sandboxed(self):
        os.environ["SR_SANDBOX_ROOT"] = "/DiskArray/tmp/sbx"
        self.addCleanup(os.environ.pop, "SR_SANDBOX_ROOT", None)
        body = self._submit(self.client()).json()
        self.assertNotIn("in_place", body)
        self.assertNotIn("notice", body)

    def test_list_queue_shows_params_subset(self):
        c = self.client()
        self._submit(c, sr_scale=3, mask_path="/DiskArray/x_mask.tif")
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["params"]["lq_path"], self.LQ)
        self.assertEqual(t["params"]["mask_path"], "/DiskArray/x_mask.tif")
        self.assertEqual(t["params"]["sr_scale"], 3)
        self.assertEqual(t["params"]["suffix"], "t")
        self.assertIn("config_xml", t)

    def test_run_dataroot_is_the_sandbox_copy_when_configured(self):
        # the product lands in the copy, not in the path the user typed — the
        # row has to say where the job actually ran.
        os.environ["SR_SANDBOX_ROOT"] = "/DiskArray/tmp/sbx"
        self.addCleanup(os.environ.pop, "SR_SANDBOX_ROOT", None)
        c = self.client()
        self._submit(c)
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["params"]["lq_path"], self.LQ)      # what was asked for
        self.assertTrue(t["run_dataroot"].startswith("/DiskArray/tmp/sbx/"))
        # only the basename survives the copy — the SC step derives its input
        # name (`<目录名>.tif`) from the directory's own name. (The sandbox path
        # is built POSIX-style: it is a path on the array server, not on this
        # Windows box the test happens to run on.)
        self.assertEqual(t["run_dataroot"].replace("\\", "/").split("/")[-1],
                         self.LQ.replace("\\", "/").split("/")[-1])

    def test_run_dataroot_defaults_to_lq_path(self):
        c = self.client()
        self._submit(c)
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["run_dataroot"], self.LQ)

    def test_cancel_pending_job(self):
        os.environ["SR_SLURM_FAKE_T_MS"] = "600000"    # 停在 PENDING/RUNNING
        c = self.client()
        r = self._submit(c)
        tid = r.json()["task_id"]
        cancel = c.post(f"/api/queue/{tid}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertTrue(cancel.json()["cancelled"])
        self.assertEqual(cancel.json()["state"], "FAILED")   # CANCELLED→FAILED
        st = c.get("/api/queue").json()["tasks"][0]["state"]
        self.assertEqual(st, "FAILED")

    def test_cancel_unknown_task_404(self):
        c = self.client()
        self.assertEqual(c.post("/api/queue/9999/cancel").status_code, 404)

    def test_cancel_interrupted_task_400(self):
        app, c = self.app_client()
        params = {"lq_path": self.LQ, "mask_path": None, "sr_scale": 2,
                  "suffix": "t", "gpu": 0, "cloud_limit": 80,
                  "delete_ori": False, "grid_align": True,
                  "options_yml": None}
        app.state.store.put_sr_task(task_fingerprint(params), params,
                                    status="new", job_id=None)
        tid = app.state.store.list_sr_tasks()[0]["task_id"]
        r = c.post(f"/api/queue/{tid}/cancel")
        self.assertEqual(r.status_code, 400)
        self.assertIn("job_id", r.json()["detail"])

    def test_cancel_without_a_local_record_is_409(self):
        # sr-api 重启后，本进程没有该 job 的记录（local_exec._JOBS 为空）而子进程可能
        # 还在就地写 lq_path。200 + cancelled=false 会被读成"已经停下了"，操作员据此
        # 重新提交就会有两个 SR 进程同写一个目录 —— 必须报错并给出下一步。
        app, c = self.app_client()
        params = {"lq_path": self.LQ, "mask_path": None, "sr_scale": 2,
                  "suffix": "t", "gpu": 0, "cloud_limit": 80,
                  "delete_ori": False, "grid_align": True,
                  "options_yml": None}
        app.state.store.put_sr_task(task_fingerprint(params), params,
                                    status="new", job_id=987654)
        tid = app.state.store.list_sr_tasks()[0]["task_id"]
        r = c.post(f"/api/queue/{tid}/cancel")
        self.assertEqual(r.status_code, 409)
        self.assertIn("987654", r.json()["detail"])
        self.assertIn("ps -ef", r.json()["detail"])

    def test_delete_ori_rejected_400(self):
        # 原型期禁用：SR 会删原图/就地覆盖且本机模式无沙箱，误开不可恢复。
        r = self._submit(self.client(), delete_ori=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("delete_ori", r.json()["detail"])

    def test_bad_path_rejected_400(self):
        c = self.client()
        self.assertEqual(
            self._submit(c, lq_path="relative/path.tif").status_code, 400)
        self.assertEqual(
            self._submit(c, lq_path="<fake>/x.tif").status_code, 400)
        self.assertEqual(self._submit(c, lq_path="").status_code, 400)

    def test_slurm_unavailable_422(self):
        os.environ.pop("SR_SLURM_FAKE", None)         # 无 sbatch + 未开 fake
        c = self.client()
        r = self._submit(c)
        self.assertEqual(r.status_code, 422)
        self.assertIn("slurm not available", r.json()["detail"])

    # ---- plan §4.3: locked directory / derived mask / non-empty suffix ------

    def test_derived_mask_is_used_when_the_request_omits_it(self):
        # 目录里已有 <目录名>_mask.tif → 提交不带 mask_path 也照跑，且参数里
        # 落的就是这个文件（规则式推导，不给用户手填的机会）。
        c = self.client()
        r = self._submit(c)                                  # no mask_path
        self.assertEqual(r.status_code, 201)
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(Path(t["params"]["mask_path"]).name,
                         f"{self.SCENE}_mask.tif")
        self.assertEqual(Path(t["params"]["mask_path"]).parent,
                         Path(self.LQ))

    def test_missing_mask_is_400_not_a_silent_full_image_run(self):
        # 缺掩码必须报错：静默退化成全图超分，用户会以为掩码生效了。
        self.mask_file.unlink()
        r = self._submit(self.client())
        self.assertEqual(r.status_code, 400)
        self.assertIn("缺少掩码文件", r.json()["detail"])

    def test_explicit_mask_path_still_wins(self):
        c = self.client()
        r = self._submit(c, mask_path=str(self.mask_file))
        self.assertEqual(r.status_code, 201)
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["params"]["mask_path"], str(self.mask_file))

    def test_locked_dir_accepts_its_own_path_and_rejects_others(self):
        os.environ["SR_LOCKED_DIR"] = self.LQ
        c = self.client()
        self.assertEqual(self._submit(c).status_code, 201)
        # 同值但带尾斜杠 → 仍算同一个目录（两侧都 realpath 归一）
        self.assertEqual(self._submit(c, lq_path=self.LQ + "/").status_code, 201)
        other = os.path.join(self._tmp.name, "another_scene")
        Path(other).mkdir()
        r = self._submit(c, lq_path=other)
        self.assertEqual(r.status_code, 400)
        self.assertIn("锁死", r.json()["detail"])

    def test_unset_locked_dir_keeps_accepting_any_directory(self):
        os.environ.pop("SR_LOCKED_DIR", None)
        self.assertEqual(self._submit(self.client()).status_code, 201)

    def test_suffix_defaults_to_sr_when_omitted_or_empty(self):
        # 空后缀会把输出名变成输入名（SR 随即改名输入），所以空值一律取默认值。
        os.environ.pop("SR_SUFFIX_DEFAULT", None)
        c = self.client()
        for body_suffix in (None, ""):                       # null 与 "" 都要落默认
            with self.subTest(suffix=body_suffix):
                self.assertEqual(
                    self._submit(c, suffix=body_suffix).status_code, 201)
                t = c.get("/api/queue").json()["tasks"][0]
                self.assertEqual(t["params"]["suffix"], "sr")

    def test_suffix_whitelist(self):
        c = self.client()
        for bad in ("a/b", "../x", "a b", "x" * 17, "suffix;rm", "掩码"):
            with self.subTest(suffix=bad):
                r = self._submit(c, suffix=bad)
                self.assertEqual(r.status_code, 400)
                self.assertIn("suffix", r.json()["detail"])
        for good in ("sr", "acc-d1", "t_2", "X" * 16):
            with self.subTest(suffix=good):
                self.assertEqual(
                    self._submit(c, suffix=good, sr_scale=2).status_code, 201)

    def test_events_broadcast_and_queue_state_mapping(self):
        # 广播机制：注册的订阅者收到 job_update 帧；_queue_state 覆盖 §3.3 映射
        app = self._make_app()

        async def scenario():
            q = asyncio.Queue()
            app.state.subscribers.add(
                _Subscriber(asyncio.get_running_loop(), q))
            _broadcast(app.state, {"type": "job_update", "task_id": 1,
                                   "state": "RUNNING", "prev_state": "PENDING"})
            payload = await asyncio.wait_for(q.get(), 1)
            self.assertTrue(payload.startswith("data: "))
            self.assertIn('"type": "job_update"', payload)

        asyncio.run(scenario())
        self.assertEqual(_queue_state({"active": True, "state": "PENDING"}),
                         "PENDING")
        self.assertEqual(_queue_state({"active": True, "state": "RUNNING"}),
                         "RUNNING")
        self.assertEqual(_queue_state({"active": False, "state": "COMPLETED"}),
                         "COMPLETED")
        self.assertEqual(_queue_state({"active": False, "state": "CANCELLED"}),
                         "FAILED")
        self.assertEqual(_queue_state({"active": None, "state": "UNKNOWN"}),
                         "UNKNOWN")


class TestMasks(PlatformBase):
    def _set_env(self):
        super()._set_env()
        os.environ["SR_SCENES_ROOT"] = os.path.join(self._tmp.name, "scenes")
        os.mkdir(os.environ["SR_SCENES_ROOT"])

    def _scene_row(self, c):
        scene = touch_tif(os.path.join(
            os.environ["SR_SCENES_ROOT"],
            "GF07A03_PMS01_20260722125045.tif"))
        rows = c.get("/api/scenes").json()["results"]
        return rows[0], scene

    def test_bake_mask_writes_tif_and_txt_into_scene_dir(self):
        c = self.client()
        row, scene = self._scene_row(c)
        poly = [{"label": "roi_1",
                 "points": [[5, 5], [40, 5], [40, 30], [5, 30]]}]
        r = c.post("/api/masks", json={
            "scene_id": row["id"], "polygons": poly, "W": 60, "H": 40})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        mask = Path(body["mask_path"])
        self.assertEqual(mask.parent, scene.parent)
        self.assertEqual(mask.name, scene.stem + "_mask.tif")
        self.assertTrue(mask.is_file())
        self.assertTrue(Path(body["mask_txt"]).is_file())
        self.assertEqual(body["lq_path"], str(scene.parent))
        draft = body["task_draft"]
        self.assertEqual(draft["lq_path"], body["lq_path"])
        self.assertEqual(draft["mask_path"], body["mask_path"])
        self.assertEqual(draft["sr_scale"], 2)
        self.assertFalse(draft["delete_ori"])
        self.assertTrue(draft["grid_align"])
        # mask 是 0/255 灰度（后端 0/1 → 0/255 归一）
        # 读回用 Pillow（与 test_mask.py 及生产消费链 cv2/util.read_img 一致）；
        # 不用 tifffile——老版 imagecodecs(2021) 解不动 Pillow 的 deflate TIF（环境缺陷，产物本身有效）。
        import numpy as np
        from PIL import Image
        arr = np.asarray(Image.open(body["mask_path"]))
        self.assertEqual(arr.dtype, np.uint8)
        self.assertEqual(sorted(np.unique(arr).tolist()), [0, 255])
        raw = Path(body["mask_txt"]).read_bytes()
        self.assertIn("掩膜".encode("utf-8"), raw)   # 参考格式全角表头
        self.assertIn(b"\r\n", raw)                  # CRLF（read_text 会吞掉）

    def test_traversal_and_unknown_scene_404(self):
        c = self.client()
        self._scene_row(c)
        from backend.api import paths
        for bad in (paths.scene_id("../outside.tif"),
                    paths.scene_id("no_such.tif")):
            r = c.post("/api/masks", json={
                "scene_id": bad,
                "polygons": [{"points": [[1, 1], [2, 1], [1, 2]]}],
                "W": 10, "H": 10})
            self.assertEqual(r.status_code, 404)

    def test_invalid_payload_400(self):
        c = self.client()
        row, _ = self._scene_row(c)
        base = {"scene_id": row["id"], "W": 60, "H": 40,
                "polygons": [{"points": [[0, 0], [5, 0], [5, 5]]}]}
        r = c.post("/api/masks", json={**base, "W": 0})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/masks", json={**base, "polygons": []})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/masks", json={**base,
                                       "polygons": [{"points": [[0, 0], [5, 0]]}]})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/masks", json={**base, "scene_id": ""})
        self.assertEqual(r.status_code, 400)

    def test_no_scenes_root_404(self):
        os.environ.pop("SR_SCENES_ROOT", None)
        c = self.client()
        r = c.post("/api/masks", json={
            "scene_id": "x",
            "polygons": [{"points": [[1, 1], [2, 1], [1, 2]]}],
            "W": 10, "H": 10})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
