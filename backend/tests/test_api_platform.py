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
         "SR_QUEUE_POLL_SEC")


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
    def _set_env(self):
        super()._set_env()
        os.environ["SR_SLURM_FAKE"] = "1"
        os.environ["SR_SLURM_FAKE_T_MS"] = "120"     # 每 stage 120ms
        os.environ["SR_QUEUE_POLL_SEC"] = "60"       # 关闭 lifespan 轮询干扰

    LQ = "/DiskArray/JL1KF02B03_xxx_L1_PAN"          # 绝对路径（fake 下不落盘外）

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

    def test_list_queue_shows_params_subset(self):
        c = self.client()
        self._submit(c, sr_scale=3, mask_path="/DiskArray/x_mask.tif")
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["params"]["lq_path"], self.LQ)
        self.assertEqual(t["params"]["mask_path"], "/DiskArray/x_mask.tif")
        self.assertEqual(t["params"]["sr_scale"], 3)
        self.assertEqual(t["params"]["suffix"], "t")
        self.assertIn("config_xml", t)

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
