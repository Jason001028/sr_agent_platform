"""阶段5 API 契约测试（api-contract.md §5.3 验收清单）。

Env 在 create_app() 前注入（SR_AGENT_DB / SR_LLM_MOCK / SR_SLURM_FAKE / …），
TestClient 内存驱动。覆盖：
  工具 manifest + 直调；chat 新建/历史/SSE 事件序列/并发 409/404；
  queue list/submit/幂等复用/cancel/events 广播；masks 落原图目录 + 白名单。
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.app import create_app, _bake_nosr_preview, _eager_bake_tick
from backend.api.platform import (_Subscriber, _broadcast, _queue_state,
                                  _task_state, chat_send)
from backend.tests import allowed_roots_env
from backend.tests.test_preview_jpg import make_strip_tif
from backend.services import run_sr as svc
from backend.services import slurm
from backend.services.run_sr import task_fingerprint
from backend.api import app as app_mod

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_LLM_MOCK",
         "SR_SLURM_FAKE", "SR_SLURM_FAKE_T_MS", "SR_SLURM_WORK_DIR",
         "SR_QUEUE_POLL_SEC", "SR_SANDBOX_ROOT", "SR_EXECUTOR",
         "SR_LOCKED_DIR", "SR_LOCAL_GPU", "SR_BUNDLE_DIR",
         "SR_ALLOWED_ROOTS", "SR_DRIVE_MAP",
         # 产物急烤的两个开关。用例会按需改它们，不还原就会漏到后面的用例上
         # （急烤是「按 env 现读」的，泄漏的效果正是随机烤/随机不烤）。
         "SR_PRODUCT_PREVIEW_DIV", "SR_PRODUCT_PREVIEW_MAX_AGE_SEC")


def write_bundle_suffix(bundle_dir, value, name=None):
    """Drop a stub of the SR team's config into a fake SR_BUNDLE_DIR.

    Only <Suffix> matters to the platform; the rest of the file's tags are the
    SR team's own test-run values and are deliberately not reproduced here.
    """
    name = name or svc.BUNDLE_SUFFIX_CONFIG_NAMES[0]
    os.makedirs(bundle_dir, exist_ok=True)
    path = os.path.join(bundle_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<?xml version='1.0' encoding='UTF-8'?>\n"
                f"<SFSR_Config><Suffix>{value}</Suffix></SFSR_Config>\n")
    return path


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
        # Point the bundle at an empty dir: the suffix default reads <Suffix> out
        # of the SR team's config there, so without this the suite would quietly
        # follow whatever SR_BUNDLE_DIR the developer's shell happens to export
        # (and on the dev box the real one is absent anyway, so the tests would
        # pass for the wrong reason — see test_suffix_defaults_* below).
        os.environ["SR_BUNDLE_DIR"] = os.path.join(self._tmp.name, "bundle")
        # 临时目录就是这些用例的"盘阵"：提交侧的白名单（SR_ALLOWED_ROOTS）
        # 默认只放行 /DiskArray，不配的话每个提交都会被 400 挡下。
        os.environ.update(allowed_roots_env(self._tmp.name))

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
        # 提交侧会把 lq_path 归一成盘阵 POSIX 形态（`W:\...` 与 `/DiskArray/...`
        # 必须落到同一个字符串，幂等才成立）。真机上是 Linux，两种写法本来就
        # 同串；开发机上才有区别，所以断言用归一形态。
        self.LQ_POSIX = self.scene_dir.as_posix()

    def _submit(self, c, **over):
        body = {"lq_path": self.LQ, "suffix": "t"}
        body.update(over)
        return c.post("/api/queue", json=body)

    def _task(self, c, task_id):
        """The queue row for one task_id (the POST reply carries no params)."""
        tasks = c.get("/api/queue").json()["tasks"]
        return next(t for t in tasks if t["task_id"] == task_id)

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
        # 无沙箱 = SR 就地写场景目录：输出按管线命名为 <输入名>_<suffix>.tif，输入 tif
        # 不改名也不删除（Suffix 恒非空），只有同名旧输出会被改名 *_NOSR.tif。提交响应
        # 必须自己说清楚，别让操作者从别处推断（工作单 §4.2 最后一条）。
        body = self._submit(self.client()).json()
        self.assertTrue(body["in_place"])
        self.assertIn(self.LQ_POSIX, body["notice"])
        self.assertIn("<输入名>_t.tif", body["notice"])   # 输出名按管线拼接
        self.assertIn("不改名也不删除", body["notice"])     # 输入不动
        self.assertIn("_NOSR.tif", body["notice"])        # 同名旧输出的去向

    def test_no_in_place_notice_when_sandboxed(self):
        os.environ["SR_SANDBOX_ROOT"] = "/DiskArray/tmp/sbx"
        self.addCleanup(os.environ.pop, "SR_SANDBOX_ROOT", None)
        body = self._submit(self.client()).json()
        self.assertNotIn("in_place", body)
        self.assertNotIn("notice", body)

    def test_list_queue_shows_params_subset(self):
        c = self.client()
        other_mask = self.scene_dir / "other_mask.tif"
        touch_tif(other_mask)
        self._submit(c, sr_scale=3, mask_path=str(other_mask))
        t = c.get("/api/queue").json()["tasks"][0]
        self.assertEqual(t["params"]["lq_path"], self.LQ_POSIX)
        self.assertEqual(t["params"]["mask_path"], other_mask.as_posix())
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
        self.assertEqual(t["params"]["lq_path"], self.LQ_POSIX)      # what was asked for
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
        self.assertEqual(t["run_dataroot"], self.LQ_POSIX)

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
        self.assertEqual(t["params"]["mask_path"], self.mask_file.as_posix())

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

    def test_suffix_defaults_to_the_bundle_config(self):
        # 默认后缀来自 SR 团队自己那份配置里的 <Suffix>，不是代码里写死的字面量。
        # 空后缀会把输出名变成输入名（SR 随即改名输入），所以空值一律走这条路。
        write_bundle_suffix(os.environ["SR_BUNDLE_DIR"], "260318")
        c = self.client()
        for body_suffix in (None, ""):                       # null 与 "" 都要落默认
            with self.subTest(suffix=body_suffix):
                r = self._submit(c, suffix=body_suffix)
                self.assertEqual(r.status_code, 201)
                t = self._task(c, r.json()["task_id"])
                self.assertEqual(t["params"]["suffix"], "260318")

    def test_suffix_falls_back_to_the_builtin_when_the_file_is_absent(self):
        # 基类把 SR_BUNDLE_DIR 指到空目录（= 开发机 / 尚未放该文件的机器）：
        # 读不到就回落内置值，提交照常成功。
        c = self.client()
        r = self._submit(c, suffix="")
        self.assertEqual(r.status_code, 201)
        t = self._task(c, r.json()["task_id"])
        self.assertEqual(t["params"]["suffix"], "sr")

    def test_unusable_bundle_suffix_falls_back_instead_of_failing(self):
        # 文件里的值不合法（运维手改出格）→ 回落内置值，不把提交打成 400：
        # 那个值是拼进输出文件名的，宁可换个安全名字也不能让它落地。
        for bad in ("a/b", "x" * 17, "掩码"):
            with self.subTest(value=bad):
                write_bundle_suffix(os.environ["SR_BUNDLE_DIR"], bad)
                c = self.client()
                r = self._submit(c, suffix="")
                self.assertEqual(r.status_code, 201)
                t = self._task(c, r.json()["task_id"])
                self.assertEqual(t["params"]["suffix"], "sr")

    def test_sr_suffix_default_env_var_is_retired(self):
        # 2026-09-16：环境变量已删，文件是唯一权威 —— 设了它不应有任何作用。
        os.environ["SR_SUFFIX_DEFAULT"] = "zzz"
        self.addCleanup(os.environ.pop, "SR_SUFFIX_DEFAULT", None)
        write_bundle_suffix(os.environ["SR_BUNDLE_DIR"], "260318")
        c = self.client()
        r = self._submit(c, suffix="")
        t = self._task(c, r.json()["task_id"])
        self.assertEqual(t["params"]["suffix"], "260318")

    def test_suffix_whitelist(self):
        c = self.client()
        for bad in ("a/b", "../x", "a b", "x" * 17, "suffix;rm", "掩码"):
            with self.subTest(suffix=bad):
                r = self._submit(c, suffix=bad)
                self.assertEqual(r.status_code, 400)
                self.assertIn("suffix 非法", r.json()["detail"])
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

    def test_job_update_frame_carries_updated_at(self):
        """状态变化帧必须带上这次写库的时间戳（updated_at + 运行窗两端）。

        界面上的耗时 = finished_at − started_at，而客户端本地那份是上一次
        GET /api/queue 的快照（通常就在提交刚落库之后，两列都还是 NULL）。
        只广播 state 的话，任务一完成耗时列就从运行中的正常值掉成「0 秒」
        （2026-09-17 实测），或者干脆停在上一次运行/上一个快照的数字上。
        """
        app, c = self.app_client()
        tid = self._submit(c).json()["task_id"]
        # 先让校准器看到 RUNNING，把本次运行的起点钉下来
        with mock.patch.object(svc, "query_job_status",
                               return_value={"active": True,
                                             "state": "RUNNING"}):
            self._task(c, tid)                      # GET 即校准一次
        # 冻结成终态：绕过假调度器的时序，直接让校准器看到 COMPLETED
        with mock.patch.object(svc, "query_job_status",
                               return_value={"active": False,
                                             "state": "COMPLETED"}):

            async def scenario():
                q = asyncio.Queue()
                app.state.subscribers.add(
                    _Subscriber(asyncio.get_running_loop(), q))
                st, changed, fresh = _task_state(
                    app.state, app.state.store.get_sr_task_by_id(tid))
                self.assertEqual((st, changed), ("COMPLETED", True))
                self.assertIsNotNone(fresh)         # 写回后的整行，给调用方用
                return sse_events(await asyncio.wait_for(q.get(), 1))

            frames = asyncio.run(scenario())

        self.assertEqual(len(frames), 1)
        ev = frames[0]
        self.assertEqual(ev["type"], "job_update")
        self.assertEqual(ev["state"], "COMPLETED")
        # 帧里的值 = 库里刚写进去的那些（客户端据此算耗时，不能是另一个数）
        row = app.state.store.get_sr_task_by_id(tid)
        for col in ("updated_at", "started_at", "finished_at"):
            self.assertEqual(ev[col], row[col], col)
        self.assertIsNotNone(row["started_at"])     # RUNNING 那一步钉过
        self.assertGreaterEqual(row["finished_at"], row["started_at"])

    def test_updated_at_is_not_broadcast_when_the_db_write_fails(self):
        app, c = self.app_client()
        tid = self._submit(c).json()["task_id"]
        with mock.patch.object(svc, "query_job_status",
                               return_value={"active": False,
                                             "state": "COMPLETED"}), \
             mock.patch.object(app.state.store, "set_sr_task_state",
                               side_effect=RuntimeError("db down")):

            async def scenario():
                q = asyncio.Queue()
                app.state.subscribers.add(
                    _Subscriber(asyncio.get_running_loop(), q))
                _task_state(app.state, app.state.store.get_sr_task_by_id(tid))
                return sse_events(await asyncio.wait_for(q.get(), 1))

            frames = asyncio.run(scenario())

        self.assertEqual(frames[0]["state"], "COMPLETED")   # 状态照推
        self.assertNotIn("updated_at", frames[0])

    def test_elapsed_counts_this_run_not_the_row_age(self):
        """重交复用的行：耗时必须是**这一次**跑的时长，不是这一行的年龄。

        现场（2026-09-18 真机）：一行 = 一个指纹。第一次交上去挂了/被取消，第二天
        修好再交 —— 幂等层复用同一行，而 created_at 是**第一次**提交的时刻。耗时列
        原来量 updated_at − created_at，于是新跑的这一遍显示成「30 时 00 分」，实际
        只跑了 200 多秒。这里把复现钉死：行确实旧 30 小时，耗时仍然只是这一次的。
        """
        app, c = self.app_client()
        os.environ["SR_SLURM_FAKE_T_MS"] = "600000"     # 停在 PENDING，等被取消
        tid = self._submit(c).json()["task_id"]
        c.post(f"/api/queue/{tid}/cancel")

        # 把这一行整体放旧 30 小时：等效于「昨天交的那一次」
        row0 = app.state.store.get_sr_task_by_id(tid)
        old = row0["created_at"] - 30 * 3600
        db = app.state.store._db()
        db.execute("UPDATE sr_tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                   (old, old, tid))
        db.commit()

        os.environ["SR_SLURM_FAKE_T_MS"] = "500"
        r2 = self._submit(c)                            # 同参数 → 复用同一行、真重跑
        self.assertEqual(r2.json()["task_id"], tid, "幂等层复用的是同一行")
        self.assertEqual(self._poll_state(c, tid)[-1], "COMPLETED")

        row = self._task(c, tid)
        self.assertGreater(row["updated_at"] - row["created_at"], 29 * 3600,
                           "这一行确实是大几十小时前建的")
        self.assertLess(row["finished_at"] - row["started_at"], 10,
                        "耗时量的是这一次跑的时长")
        self.assertIsNotNone(row["started_at"], "本次运行的起点在 RUNNING 那一步钉下了")

    def test_elapsed_absent_when_the_run_was_never_observed(self):
        """没观测到「开始跑」就不编一个耗时：两列都是 NULL，界面显示「—」。

        现场是整段运行期间 sr-api 不在（停机/重启跨过去了）。退回 created_at 顶替
        是不行的 —— 那是行的生日，复用行会退化成行龄，正是这一轮要修的错。
        """
        c = self.client()
        tid = self._submit(c).json()["task_id"]
        with mock.patch.object(svc, "query_job_status",
                               return_value={"active": False, "state": "COMPLETED"}):
            row = self._task(c, tid)                    # 第一次校准就直接看见终态

        self.assertEqual(row["state"], "COMPLETED")
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(row["started_at"])

    def test_restart_does_not_age_a_finished_row(self):
        """sr-api 重启不重算耗时：已终态的行不该被写回、updated_at 不该被抬到「现在」。

        第二个触发点（2026-09-18 实测）：`state.task_cache` 是内存态，重启后是空的。
        校准器原来只拿它当比较基准，空缓存 → 每一行都被判成「状态变了」→ 写回 + 刷新
        updated_at，于是一行几天前就跑完的任务，重启后显示成行龄（实测第 2 次 GET
        起「30 时 00 分」）。现在基准缺失时回落到**库里存的状态**。
        """
        app, c = self.app_client()
        tid = self._submit(c).json()["task_id"]
        self._poll_state(c, tid)                        # 跑到 COMPLETED
        row = self._task(c, tid)
        elapsed = row["finished_at"] - row["started_at"]

        old = row["created_at"] - 30 * 3600             # 整行放旧 30 小时
        db = app.state.store._db()
        db.execute("UPDATE sr_tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                   (old, old, tid))
        db.commit()
        before = self._task(c, tid)

        app2 = self._make_app()                         # 「重启」：新 app、同一个库、空缓存
        c2 = TestClient(app2)
        for _ in range(3):
            got = next(t for t in c2.get("/api/queue").json()["tasks"]
                       if t["task_id"] == tid)

        self.assertEqual(got["updated_at"], before["updated_at"],
                         "重启不该重写已终态的行")
        self.assertEqual(got["finished_at"] - got["started_at"], elapsed,
                         "重启后的耗时仍是这一次运行的时长")


class TestProductPreviewBake(PlatformBase):
    """产物预览急烤（app.py::_eager_bake_tick）。

    **直接调 tick，不等后台循环**：循环的间隔就是 `SR_QUEUE_POLL_SEC`，本仓库的
    惯例是把它设成 60 秒来关掉轮询干扰，等它等于让每个用例睡一分钟。tick 是纯同步
    函数、`state` 上的东西 `create_app` 里已经备齐（`scenes_root` 不在 lifespan
    里），正适合直接驱动。

    夹具造的是一个**真场景目录**（`<目录名>_meta.xml` + `<目录名>.tif`）加一份真
    strip tif 产物 —— `input_scene_path` / `build_preview_pixels` 的实际判据都在
    这条链上，用空文件顶替会让「产物缺失」「读不出尺寸」这类断言变成假绿。
    """

    SCENE = "JL1KF02B03_xxx_L1_PAN"
    SUFFIX = "260318"

    def _set_env(self):
        super()._set_env()
        os.environ["SR_QUEUE_POLL_SEC"] = "60"
        self.scene_dir = Path(self._tmp.name) / self.SCENE
        self.scene_dir.mkdir(parents=True, exist_ok=True)
        (self.scene_dir / (self.SCENE + "_meta.xml")).write_text(
            "<x/>", encoding="utf-8")
        self.inp, _ = make_strip_tif(self.scene_dir, self.SCENE + ".tif", 64, 32)
        self.LQ = self.scene_dir.as_posix()

    # ---- 夹具 -------------------------------------------------------------

    def _row(self, app, *, suffix=None, lq_path=None, status="COMPLETED"):
        """在库里造一行（默认已跑到 COMPLETED 且钉了 finished_at）。"""
        params = {"lq_path": lq_path if lq_path is not None else self.LQ,
                  "mask_path": None, "sr_scale": 2,
                  "suffix": self.SUFFIX if suffix is None else suffix,
                  "gpu": 0, "cloud_limit": 80, "delete_ori": False,
                  "grid_align": True, "options_yml": None}
        fp = task_fingerprint(params)
        app.state.store.put_sr_task(fp, params, status="new", job_id=None)
        tid = app.state.store.get_sr_task(fp)["task_id"]
        if status == "COMPLETED":
            app.state.store.set_sr_task_state(tid, "COMPLETED", mark_finished=True)
        elif status == "FAILED":
            app.state.store.set_sr_task_state(tid, "FAILED", mark_finished=True)
        return tid

    def _product(self, name=None, w=64, h=32):
        return make_strip_tif(self.scene_dir, name or f"{self.SCENE}_{self.SUFFIX}.tif",
                              w, h)[0]

    def _jpg(self, product):
        return product.with_suffix(".preview.jpg")

    def _row_state(self, app, tid):
        row = app.state.store.get_sr_task_by_id(tid)
        return row["preview_state"], row["preview_note"]

    def _rearm(self, app, tid):
        """把 preview_state 打回 NULL（等价于 put_sr_task 的 UPDATE 分支），
        但**保留 finished_at** —— 直接走提交会连 finished_at 一起清掉，那一行就
        不再落在急烤的年龄窗口里了。"""
        db = app.state.store._db()
        db.execute("UPDATE sr_tasks SET preview_state = NULL, preview_note = NULL "
                   "WHERE id = ?", (tid,))
        db.commit()

    def _dims(self, path):
        with Image.open(path) as im:
            return im.size

    # ---- 正常路径 ---------------------------------------------------------

    def test_bakes_the_product_of_a_completed_task(self):
        app, _ = self.app_client()
        tid = self._row(app)
        product = self._product()

        _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "done")
        self.assertIn("baked", note)
        jpg = self._jpg(product)
        self.assertTrue(jpg.is_file(), "产物旁边出现了 <产物 stem>.preview.jpg")
        self.assertEqual(self._dims(jpg), (16, 8), "÷4：64×32 → 16×8")
        # 落点与**输入影像**那份天然不同名 —— 三类图互不覆盖的前提
        self.assertNotEqual(jpg, self.inp.with_suffix(".preview.jpg"))

    def test_input_image_is_not_baked(self):
        """急烤只烤产物。输入影像那份是惰性的：用户看不看它，打开之前无从知道。"""
        app, _ = self.app_client()
        self._row(app)
        self._product()
        _eager_bake_tick(app.state)
        self.assertFalse(self.inp.with_suffix(".preview.jpg").exists())

    def test_div_comes_from_the_env_default_four(self):
        app, _ = self.app_client()
        self._row(app)
        product = self._product(w=80, h=40)
        _eager_bake_tick(app.state)
        self.assertEqual(self._dims(self._jpg(product)), (20, 10))

    def test_div_is_reread_from_the_env_each_tick(self):
        """档位不在 create_app 里快照：改 env 就该下一轮生效。"""
        app, _ = self.app_client()
        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "2"
        tid = self._row(app)
        product = self._product(w=64, h=32)
        _eager_bake_tick(app.state)
        self.assertEqual(self._dims(self._jpg(product)), (32, 16))
        self.assertIn("÷2", self._row_state(app, tid)[1])

        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "8"
        self._rearm(app, tid)
        _eager_bake_tick(app.state)

        self.assertEqual(self._dims(self._jpg(product)), (8, 4),
                         "换档位后重烤，不是留着上一档那份")
        self.assertIn("÷8", self._row_state(app, tid)[1])

    def test_already_baked_at_this_div_is_a_cache_hit(self):
        """用户先打开过、盘上那份已是当前档位 → 不重烤（省掉读遍大图那几十秒）。
        判据与惰性路径是**同一个** cache_hit，所以这里命中的正是用户刚烤的那份。"""
        app, _ = self.app_client()
        tid = self._row(app)
        product = self._product()
        _eager_bake_tick(app.state)
        self._rearm(app, tid)

        _eager_bake_tick(app.state)

        self.assertIn("cached", self._row_state(app, tid)[1])
        self.assertEqual(self._dims(self._jpg(product)), (16, 8))

    def test_one_tick_bakes_at_most_one_item(self):
        """单消费者、并发 1：÷4 烤一份 40000² 产物峰值内存约 400MB，并发会把内存
        乘上去。代价是「同时完成 20 个作业时最后一件要等」——那是可解释的。"""
        app, _ = self.app_client()
        a = self._row(app, suffix="s1", lq_path=self.LQ)
        b = self._row(app, suffix="s2", lq_path=self.LQ)
        self._product(f"{self.SCENE}_s1.tif")
        self._product(f"{self.SCENE}_s2.tif")

        _eager_bake_tick(app.state)

        states = [self._row_state(app, t)[0] for t in (a, b)]
        # 不写死是哪一条：两行的 finished_at 是同一个瞬间，排序不该被断言
        self.assertEqual(states.count("done"), 1, "本轮只烤一条")
        self.assertEqual(states.count(None), 1, "另一件留到下一轮")

    def test_request_path_observing_completed_first_does_not_lose_the_bake(self):
        """`GET /api/queue` 会走 `_task_state`（与后台轮询是**同一**个写终态的函数）。

        把急烤挂在「状态转换」上的话，谁先观测到 COMPLETED 谁把这次转换拿走，另一个
        就看到「没变化」—— 请求路径抢先看一眼队列，这份预览就永远不烤了。改成从库
        派生之后这个竞态在结构上不存在：这里先打几次队列再跑 tick，照样烤。
        """
        app, c = self.app_client()
        tid = self._row(app)
        product = self._product()
        for _ in range(3):
            rows = c.get("/api/queue").json()["tasks"]
            self.assertEqual(next(t for t in rows if t["task_id"] == tid)["state"],
                             "COMPLETED")

        _eager_bake_tick(app.state)

        self.assertEqual(self._row_state(app, tid)[0], "done")
        self.assertTrue(self._jpg(product).is_file())

    # ---- env 开关 ---------------------------------------------------------

    def test_div_zero_disables_eager_baking(self):
        app, _ = self.app_client()
        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "0"
        tid = self._row(app)
        product = self._product()

        _eager_bake_tick(app.state)

        self.assertIsNone(self._row_state(app, tid)[0], "关掉时连认领都不做")
        self.assertFalse(self._jpg(product).exists())

    def test_invalid_div_is_treated_as_off_not_as_a_crash(self):
        """配置写错不该让服务起不来（也不会让轮询炸掉），代价只是「这轮不烤」。"""
        app, _ = self.app_client()
        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "3"       # 不在 PREVIEW_DIVISORS 里
        tid = self._row(app)
        self._product()
        _eager_bake_tick(app.state)
        self.assertIsNone(self._row_state(app, tid)[0])

        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "abc"
        _eager_bake_tick(app.state)
        self.assertIsNone(self._row_state(app, tid)[0])

    def test_max_age_window_keeps_history_out(self):
        """升级当天不把历史 COMPLETED 行全烤一遍 —— 补列之后老行的 preview_state
        全是 NULL，而 NULL 的含义正是「没烤过」。"""
        app, _ = self.app_client()
        tid = self._row(app)
        product = self._product()
        db = app.state.store._db()
        db.execute("UPDATE sr_tasks SET finished_at = ? WHERE id = ?",
                   (time.time() - 90 * 86400, tid))
        db.commit()

        _eager_bake_tick(app.state)

        self.assertIsNone(self._row_state(app, tid)[0])
        self.assertFalse(self._jpg(product).exists())

    # ---- 各条「不烤」的分支 -----------------------------------------------

    def test_failed_row_is_never_touched(self):
        app, _ = self.app_client()
        tid = self._row(app, status="FAILED")
        self._product()                       # 半截产物也在盘上（writeTiff 先改名再写）
        _eager_bake_tick(app.state)
        self.assertIsNone(self._row_state(app, tid)[0],
                          "FAILED 行连 preview_state 都不该被写")

    def test_missing_product_reports_both_candidate_names(self):
        """COMPLETED 但没有产物是**正常结局**（云限额的 `Run skipped:` 就判成合法
        COMPLETED），所以 note 必须列出试过的名字，好让运维一眼分辨「名字猜错了」
        还是「作业本身没产出」。"""
        app, _ = self.app_client()
        tid = self._row(app)
        _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("product_missing", note)
        self.assertIn(f"{self.SCENE}_{self.SUFFIX}.tif", note)
        self.assertIn(f"{self.SCENE}_{self.SUFFIX}.tiff", note)

    def test_row_without_a_suffix_pins_nothing(self):
        app, _ = self.app_client()
        tid = self._row(app, suffix="")
        _eager_bake_tick(app.state)
        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("no_suffix", note)

    def test_sandboxed_run_is_skipped(self):
        """跑在沙箱私有副本上时产物落在副本里，盘阵那份是**上一次**的 —— 烤了用户
        也看不到，还往临时盘撒文件。"""
        app, _ = self.app_client()
        os.environ["SR_EXECUTOR"] = "slurm"
        os.environ["SR_SANDBOX_ROOT"] = "/DiskArray/tmp/sbx"
        tid = self._row(app)
        self._product()

        _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("sandbox", note)

    def test_sandbox_root_with_local_executor_still_bakes(self):
        """**真机当前就是这条路线**：配了 `SR_SANDBOX_ROOT`，但 `SR_EXECUTOR=local`。

        `SR_EXECUTOR=local` 的意义就是「就地跑、产物落在盘阵里」（run_sr
        .sandbox_scene_paths 对 local 恒返回 None），所以这里必须照烤。判据若写成
        「直接看 SR_SANDBOX_ROOT 在不在」，真机上会**永远不烤**，而且是静默的。
        """
        app, _ = self.app_client()
        os.environ["SR_EXECUTOR"] = "local"
        os.environ["SR_SANDBOX_ROOT"] = "/DiskArray/tmp/sbx"
        tid = self._row(app)
        product = self._product()

        _eager_bake_tick(app.state)

        self.assertEqual(self._row_state(app, tid)[0], "done")
        self.assertTrue(self._jpg(product).is_file())

    def test_malformed_sandbox_root_is_skipped_not_guessed(self):
        """`_run_dataroot` 回 None = 「产物落在哪不可知」。宁可如实跳过也不猜。"""
        app, _ = self.app_client()
        os.environ["SR_EXECUTOR"] = "slurm"
        os.environ["SR_SANDBOX_ROOT"] = "relative/not/absolute"
        tid = self._row(app)
        self._product()

        _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("sandbox", note)

    def test_unwritable_directory_is_skipped_not_fallen_back(self):
        """目录不可写就如实跳过，**不退回 SR_TEMP_PREVIEWS_ROOT**：那份按天清，而急烤
        的意义是长期命中；更要命的是急烤没有 HTTP 响应头能告诉用户「这次退化了」，
        静默退化等于骗人。"""
        app, _ = self.app_client()
        tid = self._row(app)
        product = self._product()

        with mock.patch.object(app_mod.os, "access", return_value=False):
            _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("unwritable", note)
        self.assertFalse(self._jpg(product).exists())

    def test_source_rewritten_mid_bake_writes_nothing(self):
        """同一 suffix 重跑会覆盖同一个产物路径。不复核的话，一次「读的时候是旧产物、
        写的时候新产物已经在写」就会把一张半截图永久留在盘上 —— 而缓存判据是「不比
        源旧」，新产物的 mtime 可能仍晚于刚写的 jpg，它不会自愈。"""
        app, _ = self.app_client()
        tid = self._row(app)
        product = self._product()
        real = app_mod.build_preview_pixels

        def _rewrite_then_build(src, max_edge):
            # 读像素的中途，同一 suffix 的重跑把产物覆盖了（尺寸也变了）
            make_strip_tif(self.scene_dir, product.name, 48, 24)
            return real(src, max_edge)

        with mock.patch.object(app_mod, "build_preview_pixels",
                               side_effect=_rewrite_then_build):
            _eager_bake_tick(app.state)

        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "skipped")
        self.assertIn("source_changed", note)
        self.assertFalse(self._jpg(product).exists(), "一个字节都没写")

    def test_build_failure_lands_as_failed_with_the_reason(self):
        app, _ = self.app_client()
        tid = self._row(app)
        self._product()
        with mock.patch.object(app_mod, "build_preview_pixels",
                               side_effect=RuntimeError("boom")):
            _eager_bake_tick(app.state)
        state_name, note = self._row_state(app, tid)
        self.assertEqual(state_name, "failed")
        self.assertIn("boom", note)

    # ---- 队列可见性 -------------------------------------------------------

    def test_queue_row_exposes_the_preview_fields(self):
        app, c = self.app_client()
        tid = self._row(app)
        self._product()

        before = next(t for t in c.get("/api/queue").json()["tasks"]
                      if t["task_id"] == tid)
        self.assertIn("preview_state", before, "字段恒在，没烤过时是 null")
        self.assertIsNone(before["preview_state"])
        self.assertIsNone(before["preview_note"])

        _eager_bake_tick(app.state)

        after = next(t for t in c.get("/api/queue").json()["tasks"]
                     if t["task_id"] == tid)
        self.assertEqual(after["preview_state"], "done")
        self.assertIn("baked", after["preview_note"])
        self.assertEqual(after["state"], "COMPLETED",
                         "急烤写回不该动作业状态")

    # ---- 顺带那一份：未超分（`PAN_NOSR.tif`，2026-09-21 用户口径）----------

    def _nosr(self, w=64, h=32, name=None):
        """未超分那一份栅格：场景目录里的 `PAN_NOSR.tif`（用户给的那个名字）。"""
        return make_strip_tif(self.scene_dir, name or "PAN_NOSR.tif", w, h)[0]

    def _nosr_jpg(self):
        """它的预览落点：拖入链那条规则（`<源 stem>_preview.jpg`，下划线）。"""
        return self.scene_dir / "PAN_NOSR_preview.jpg"

    def _nosr_task(self, tid):
        """`_bake_nosr_preview` 只看 params 里的 lq_path，不必真去库里取整行。"""
        return {"task_id": tid,
                "params": {"lq_path": self.LQ, "suffix": self.SUFFIX}}

    def test_nosr_preview_is_baked_by_the_same_tick(self):
        """超分跑完那一轮里顺带把它烤了：源是 `PAN_NOSR.tif`，档位取全局。"""
        app, _ = self.app_client()
        self._row(app)
        product = self._product()
        self._nosr()

        _eager_bake_tick(app.state)

        self.assertTrue(self._jpg(product).is_file())
        nosr_jpg = self._nosr_jpg()
        self.assertTrue(nosr_jpg.is_file(), "场景目录里出现了 PAN_NOSR_preview.jpg")
        self.assertEqual(self._dims(nosr_jpg), (16, 8), "÷4：64×32 → 16×8")
        self.assertNotEqual(nosr_jpg, self._jpg(product), "两份各一落点，互不覆盖")

    def test_no_nosr_raster_no_nosr_preview(self):
        """没有那份栅格就什么都不写，但「没这份」要报得出来 ——
        这个名字对不对只有真机能证，静默跳过等于没人看得见。"""
        app, _ = self.app_client()
        tid = self._row(app)
        self._product()

        _eager_bake_tick(app.state)

        self.assertFalse(self._nosr_jpg().exists())
        status = _bake_nosr_preview(self._nosr_task(tid), 4)
        self.assertTrue(status.startswith("skipped:"), status)
        self.assertIn("PAN_NOSR.tif", status)

    def test_nosr_preview_is_baked_even_when_the_product_bake_skips(self):
        """两份互不牵连：产物没产出（云限额跳过是合法 COMPLETED）时，
        未超分那份照样烤 —— 它跟这次跑得成不成功本来就无关。"""
        app, _ = self.app_client()
        tid = self._row(app)
        self._nosr()

        _eager_bake_tick(app.state)

        self.assertEqual(self._row_state(app, tid)[0], "skipped")
        self.assertTrue(self._nosr_jpg().is_file())

    def test_nosr_preview_at_the_same_div_is_a_cache_hit(self):
        """同 suffix 反复迭代不该每次重读一遍 GB 级文件：盘上那份是当前档位就不重烤，
        判据与产物那份**同一个** cache_hit。"""
        app, _ = self.app_client()
        tid = self._row(app)
        self._nosr()
        task = self._nosr_task(tid)

        self.assertIn("baked", _bake_nosr_preview(task, 4))
        self.assertEqual(self._dims(self._nosr_jpg()), (16, 8))
        self.assertIn("cached", _bake_nosr_preview(task, 4))

        self.assertIn("baked", _bake_nosr_preview(task, 8), "换档位就该重烤")
        self.assertEqual(self._dims(self._nosr_jpg()), (8, 4))


class TestMasks(PlatformBase):
    def _set_env(self):
        super()._set_env()
        os.environ["SR_SCENES_ROOT"] = os.path.join(self._tmp.name, "scenes")
        os.mkdir(os.environ["SR_SCENES_ROOT"])

    def _scene_row(self, c):
        """真机形态的场景目录：<root>/<编号>/<编号>.tif + <编号>_meta.xml。

        scene_search 的场景判据是目录里有 <目录名>_meta.xml，平铺的裸 tif 不会被列出。
        """
        stem = "GF07A03_PMS01_20260722125045"
        d = os.path.join(os.environ["SR_SCENES_ROOT"], stem)
        os.mkdir(d)
        scene = touch_tif(os.path.join(d, stem + ".tif"))
        Path(d, stem + "_meta.xml").write_text(
            '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
            encoding="utf-8")
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
        self.assertEqual(body["lq_path"], scene.parent.as_posix())
        draft = body["task_draft"]
        self.assertEqual(draft["lq_path"], body["lq_path"])
        self.assertEqual(draft["mask_path"], body["mask_path"])
        self.assertEqual(draft["sr_scale"], 2)
        self.assertFalse(draft["delete_ori"])
        self.assertTrue(draft["grid_align"])
        self.assertEqual(draft["suffix"], "sr")   # 无 SR 配置 → 内置兜底值
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

    def test_bake_mask_draft_carries_the_bundle_suffix(self):
        # 预填给队列表单的后缀与提交时将会用到的默认值同源（同一份 SR 配置），
        # 否则操作员看到的和实际产物名会对不上。
        write_bundle_suffix(os.environ["SR_BUNDLE_DIR"], "260318")
        c = self.client()
        row, _ = self._scene_row(c)
        r = c.post("/api/masks", json={
            "scene_id": row["id"],
            "polygons": [{"label": "roi_1",
                          "points": [[5, 5], [40, 5], [40, 30], [5, 30]]}],
            "W": 60, "H": 40})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["task_draft"]["suffix"], "260318")

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

    # -- 手工场景（盘阵上任意合法目录，走 lq_path） --------------------------
    def _manual_scene(self, name="260318", *, pan=False, meta=True, tif=True):
        """SR_SCENES_ROOT **之外**的一个合法场景目录（真机形态的编号目录）。"""
        # 与真机同构：盘阵根用 `W:\` 表达。开发机（Windows）上临时目录带盘符，
        # 再给那个盘符加一条恒等映射，好让本机绝对路径也能表达同一个目录。
        maps = f"W:={Path(self._tmp.name).as_posix()}"
        drv = Path(self._tmp.name).drive
        if drv:
            maps += f";{drv}={drv}"
        os.environ["SR_DRIVE_MAP"] = maps
        os.environ["SR_ALLOWED_ROOTS"] = "W:\\"
        d = Path(self._tmp.name, "GSHC2IMPS", "PRODUCT", "2026", "09", "17", name)
        d.mkdir(parents=True, exist_ok=True)
        if tif:
            touch_tif(d / f"{name}.tif")
        if pan:
            touch_tif(d / "PAN.tif")
        if meta:
            (d / f"{name}_meta.xml").write_text(
                '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
                encoding="utf-8")
        # 回 POSIX 形态：盘阵路径在平台内部一律 POSIX（提交侧存的、
        # /api/masks 回的、resolve 报的掩码路径都是同一个字符串）
        return d.as_posix()

    def _post_mask(self, c, body):
        return c.post("/api/masks", json={
            **body, "W": 60, "H": 40,
            "polygons": [{"points": [[5, 5], [40, 5], [40, 30], [5, 30]]}]})

    def test_bake_mask_by_lq_path_writes_into_that_dir(self):
        # 场景不在 SR_SCENES_ROOT 之下也要能画能写 —— 这正是"打开盘阵任意目录"
        d = self._manual_scene()
        self.assertNotIn(os.path.realpath(d), os.path.realpath(
            os.environ["SR_SCENES_ROOT"]))
        r = self._post_mask(self.client(), {"lq_path": d})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        # 掩码路径一律 POSIX 形态入库（见 scene_search.derived_mask_path）：
        # 它会被存进 params、参与 task_fingerprint，不能随宿主平台变
        self.assertEqual(body["mask_path"], f"{d}/260318_mask.tif")
        self.assertTrue(Path(body["mask_path"]).is_file())
        self.assertEqual(body["lq_path"], d)
        self.assertEqual(body["task_draft"]["lq_path"], d)
        self.assertEqual(body["task_draft"]["mask_path"], body["mask_path"])

    def test_bake_mask_windows_form_lq_path(self):
        d = self._manual_scene()
        rel = Path(d).relative_to(self._tmp.name).as_posix()
        r = self._post_mask(self.client(), {"lq_path": "W:\\" + rel.replace("/", "\\")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["lq_path"], d)

    def test_pan_scene_writes_the_mask_the_submit_side_will_look_for(self):
        """RC 场景（输入 PAN.tif）：写出去的名字 == 提交时去找的名字。

        以前 bake_mask 取输入文件名、derived_mask_path 取目录名，这里会一个
        写 PAN_mask.tif、一个找 260318_mask.tif —— 提交必 400。这条用例钉住
        两者同源。
        """
        from backend.api.platform import derived_mask_path
        d = self._manual_scene(pan=True, tif=False)
        r = self._post_mask(self.client(), {"lq_path": d})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["mask_path"], f"{d}/PAN_mask.tif")
        self.assertEqual(derived_mask_path(d), r.json()["mask_path"])

    def test_sc_scene_mask_name_is_input_stem(self):
        from backend.api.platform import derived_mask_path
        d = self._manual_scene()
        r = self._post_mask(self.client(), {"lq_path": d})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(derived_mask_path(d), r.json()["mask_path"])

    def test_library_rc_row_bake_matches_derived_mask(self):
        # 库行也不能例外：PAN 行经 scene_id 进来时掩码名同样跟输入影像走
        from backend.api.platform import derived_mask_path
        stem = "KF02B04_PMS05_20260722125045"
        d = Path(os.environ["SR_SCENES_ROOT"], stem)
        d.mkdir(parents=True)
        touch_tif(d / "PAN.tif")
        (d / f"{stem}_meta.xml").write_text(
            '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
            encoding="utf-8")
        c = self.client()
        rows = [r for r in c.get("/api/scenes").json()["results"]
                if r["name"] == "PAN"]
        self.assertTrue(rows, "PAN.tif 应被列为一条场景")
        r = self._post_mask(c, {"scene_id": rows[0]["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(derived_mask_path(str(d)), r.json()["mask_path"])
        self.assertEqual(Path(r.json()["mask_path"]).name, "PAN_mask.tif")

    def test_lq_path_outside_whitelist_403(self):
        d = self._manual_scene()
        outside = Path(self._tmp.name).parent / "sr-not-allowed" / "260318"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "260318_meta.xml").write_text("<x/>", encoding="utf-8")
        touch_tif(outside / "260318.tif")
        try:
            r = self._post_mask(self.client(), {"lq_path": str(outside)})
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("SR_ALLOWED_ROOTS", r.json()["detail"])
        finally:
            shutil.rmtree(outside.parent, ignore_errors=True)

    def test_lq_path_not_a_scene_dir_404_lists_candidates(self):
        d = self._manual_scene(meta=False)
        r = self._post_mask(self.client(), {"lq_path": d})
        self.assertEqual(r.status_code, 404, r.text)
        detail = r.json()["detail"]
        self.assertIn("_meta.xml", detail)
        self.assertIn("260318.tif", detail)

    def test_lq_path_missing_dir_403(self):
        self._manual_scene()                 # 只为把白名单/盘符配好
        ghost = Path(self._tmp.name, "GSHC2IMPS", "PRODUCT", "2026", "09", "17", "nope")
        r = self._post_mask(self.client(), {"lq_path": str(ghost)})
        self.assertEqual(r.status_code, 403, r.text)     # kind="dir" 要求存在

    def test_neither_scene_id_nor_lq_path_400(self):
        self._manual_scene()
        r = self._post_mask(self.client(), {})
        self.assertEqual(r.status_code, 400, r.text)

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


class TestQcListWrite(PlatformBase):
    """`POST /api/qclist/write` —— 《待修复清单》原地写回（api-contract.md §3.7）。

    真机 http 下浏览器写不了盘阵文件（File System Access API 在规范里是
    `[SecureContext]` 标的，Chrome 只在 https/localhost 页面暴露它），所以改走后端。
    这里钉的是：写进去的字节对不对、编码（GBK）对不对、护栏拦不拦得住「导入之后
    别人又改了一版」、以及各条拒绝路径下**原文件一个字节都不动**。
    """

    def _set_env(self):
        super()._set_env()
        # 白名单 = <临时根>/array，W: 也映射到它 —— 用例里就能写用户真会粘的
        # `W:\待修复清单.txt`。白名单刻意收在子目录上：好造「真实存在、但在白名单外」
        # 的越界用例，跨平台都成立（不靠盘符）。
        self.array = Path(self._tmp.name).resolve() / "array"
        self.array.mkdir(parents=True, exist_ok=True)
        env = allowed_roots_env(self.array)
        os.environ["SR_ALLOWED_ROOTS"] = env["SR_ALLOWED_ROOTS"]
        os.environ["SR_DRIVE_MAP"] = (
            f"W:={self.array.as_posix()};{env.get('SR_DRIVE_MAP', '')}").rstrip(";")

    # -- 夹具 ---------------------------------------------------------------
    def _make(self, text="上半部分\n", enc="utf-8", name="待修复清单.txt") -> Path:
        p = self.array / name
        p.write_bytes(text.encode(enc))
        return p

    def _win(self, p: Path) -> str:
        """用户会粘的那种 Windows 形态。"""
        rel = p.resolve().relative_to(self.array).as_posix()
        return "W:\\" + rel.replace("/", "\\")

    def _body(self, p: Path, text: str, **over) -> dict:
        return {"path": self._win(p), "text": text, **over}

    # -- 正常路径 -----------------------------------------------------------
    def test_writes_text_back_in_place(self):
        p = self._make("旧内容\n")
        doc = "上半部分逐字保留\n\nN1\t修复通过\n"
        r = self.client().post("/api/qclist/write", json=self._body(p, doc))
        self.assertEqual(r.status_code, 200, r.text)
        # 回的是盘阵 POSIX 形态（与 /api/masks 同口径），不是 Windows 形态
        self.assertEqual(r.json()["path"], p.resolve().as_posix())
        self.assertEqual(r.json()["bytes"], len(doc.encode("utf-8")))
        self.assertEqual(p.read_text(encoding="utf-8"), doc)

    def test_gbk_written_as_gbk(self):
        p = self._make("旧内容\n", enc="gbk")
        doc = "产品存在伪影 (问题类型:产品存在伪影)\nN1\t修复通过\n"
        r = self.client().post("/api/qclist/write",
                               json=self._body(p, doc, encoding="gbk"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["encoding"], "gbk")
        self.assertEqual(p.read_bytes().decode("gbk"), doc)
        # 真写成 GBK 了 —— 浏览器写盘那条路只能降级成 UTF-8+BOM，这条是修好的部分
        self.assertNotEqual(p.read_bytes(), doc.encode("utf-8"))

    @unittest.skipIf(os.name == "nt", "Windows 的 chmod 只切只读位，测不出权限位")
    def test_preserves_mode_bits(self):
        # mkstemp 出来的是 0600，直接 os.replace 会把原文件的权限一起换掉
        p = self._make("旧内容\n")
        os.chmod(p, 0o640)
        r = self.client().post("/api/qclist/write", json=self._body(p, "新\n"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o640)

    # -- mtime 护栏 ---------------------------------------------------------
    def test_mtime_guard(self):
        p = self._make("旧内容\n")
        os.utime(p, (1_700_000_000, 1_700_000_000))
        c = self.client()
        # 导入时看到的就是这个时间 → 放行
        r = c.post("/api/qclist/write",
                   json=self._body(p, "新\n", mtime=1_700_000_000))
        self.assertEqual(r.status_code, 200, r.text)

        os.utime(p, (1_700_000_600, 1_700_000_600))    # 盘阵上被改过
        r = c.post("/api/qclist/write", json=self._body(p, "又新\n"))
        self.assertEqual(r.status_code, 200, r.text)   # 不带 mtime：护栏不参与
        r = c.post("/api/qclist/write",
                   json=self._body(p, "又新\n", mtime=1_700_000_000))
        self.assertEqual(r.status_code, 400)
        self.assertIn("重新导入", r.json()["detail"])
        self.assertEqual(p.read_text(encoding="utf-8"), "又新\n")

    # -- 拒绝路径（每一条都要确认原文件没被动过）----------------------------
    def test_rejects_dir_and_missing_file(self):
        c = self.client()
        d = self.array / "某个目录.txt"
        d.mkdir()
        r = c.post("/api/qclist/write", json=self._body(d, "x"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("文件不存在", r.json()["detail"])

        r = c.post("/api/qclist/write", json={
            "path": self._win(self.array / "没有这个.txt"), "text": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("文件不存在", r.json()["detail"])

    def test_rejects_non_txt(self):
        p = self._make("旧内容\n", name="清单.md")
        r = self.client().post("/api/qclist/write", json=self._body(p, "x"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("只能写回", r.json()["detail"])
        self.assertEqual(p.read_text(encoding="utf-8"), "旧内容\n")

    def test_rejects_outside_whitelist(self):
        outside = Path(self._tmp.name).resolve() / "外面.txt"   # 真实存在，但在白名单外
        outside.write_text("旧内容\n", encoding="utf-8")
        r = self.client().post("/api/qclist/write", json={
            "path": outside.as_posix(), "text": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("写回目标不可用", r.json()["detail"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "旧内容\n")

    def test_rejects_text_and_encoding_gbk_cannot_encode(self):
        p = self._make("旧内容\n", enc="gbk")
        r = self.client().post("/api/qclist/write",
                               json=self._body(p, "🛰\n", encoding="gbk"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("gbk", r.json()["detail"])
        self.assertEqual(p.read_bytes().decode("gbk"), "旧内容\n")

    def test_rejects_bad_body(self):
        c = self.client()
        p = self._make("旧内容\n")
        r = c.post("/api/qclist/write", json={"text": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("path", r.json()["detail"])
        r = c.post("/api/qclist/write", json={"path": self._win(p), "text": 5})
        self.assertEqual(r.status_code, 400)
        self.assertIn("text", r.json()["detail"])
        r = c.post("/api/qclist/write", json=self._body(p, "x", encoding="utf-16"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("encoding", r.json()["detail"])
        r = c.post("/api/qclist/write", json=self._body(p, "x", mtime="昨天"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("mtime", r.json()["detail"])
        # 穿越段在 to_posix_array_path 就被挡下（同一个守卫，不另写一套）
        r = c.post("/api/qclist/write", json={"path": "W:\\..\\x.txt", "text": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("穿越", r.json()["detail"])

    def test_never_lists_directories(self):
        """「绝不列举目录」是硬约束：写回只认用户给的那一个路径（不扫盘、不找同名文件）。"""
        p = self._make("旧内容\n")
        c = self.client()
        with mock.patch("os.listdir", side_effect=AssertionError("不该列举目录")), \
                mock.patch("os.scandir", side_effect=AssertionError("不该列举目录")), \
                mock.patch("os.walk", side_effect=AssertionError("不该列举目录")), \
                mock.patch("pathlib.Path.iterdir",
                           side_effect=AssertionError("不该列举目录")):
            r = c.post("/api/qclist/write", json=self._body(p, "新\n"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(p.read_text(encoding="utf-8"), "新\n")


if __name__ == "__main__":
    unittest.main()
