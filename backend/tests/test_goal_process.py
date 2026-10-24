"""P0 process/HTTP acceptance. Fake model and SR; no production/GPU claim."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from PIL import Image

from backend.services.store import Store
from backend.tests import allowed_roots_env

REPO = Path(__file__).resolve().parents[2]


class ExternalServices:
    def __init__(self, root, scenes, options, report):
        self.root, self.scenes, self.options, self.report = (
            root,
            scenes,
            options,
            report,
        )
        self.jobs = {}
        self.model_calls = 0
        self.unexpected_poll_calls = 0
        self.policy = "two"
        self.block_first_model = False
        self.model_started = threading.Event()
        self.release_model = threading.Event()
        self.lock = threading.Lock()
        self.ledger = root / "physical_submissions.jsonl"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, BrokenPipeError):
                    # Intentional backend termination also drops keepalive
                    # connections to these independent fake services.
                    pass

            def log_message(self, *args):
                pass

            def send(self, value):
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                job_id = int(self.path.rsplit("/", 1)[1])
                with owner.lock:
                    job = owner.jobs[job_id]
                    complete = job["complete"]
                self.send(
                    {
                        "job_id": job_id,
                        "active": not complete,
                        "state": "COMPLETED" if complete else "RUNNING",
                        "exit_code": "0:0" if complete else None,
                    }
                )

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/submit":
                    with owner.lock:
                        job_id = 101 + len(owner.jobs)
                        owner.jobs[job_id] = {**body, "complete": False}
                        with owner.ledger.open("a", encoding="utf-8") as output:
                            output.write(json.dumps({"job_id": job_id, **body}) + "\n")
                            output.flush()
                            os.fsync(output.fileno())
                    self.send({"job_id": job_id})
                    return
                self.send(owner.model(body))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release_model.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def model(self, request):
        with self.lock:
            self.model_calls += 1
            ordinal = self.model_calls
        self.model_started.set()
        if self.block_first_model and ordinal == 1:
            if not self.release_model.wait(15):
                raise TimeoutError("test did not release blocked model")
        results = [
            json.loads(m["content"]) for m in request["messages"] if m["role"] == "tool"
        ]
        completed = {
            r["data"]["job_id"]
            for r in results
            if isinstance(r.get("data"), dict) and r["data"].get("state") == "COMPLETED"
        }
        submitted = {
            r["data"]["job_id"]
            for r in results
            if isinstance(r.get("data"), dict) and r["data"].get("job_id") is not None
        }
        if submitted - completed:
            with self.lock:
                self.unexpected_poll_calls += 1
        if self.policy == "two" and not any(
            isinstance(r.get("data"), dict)
            and r["data"].get("source") == "process-test"
            for r in results
        ):
            name, args = "search_scenes", {}
        elif len(completed) < (2 if self.policy == "two" else 1):
            index = len(completed) if self.policy == "two" else 0
            name, args = "run_sr", {
                "lq_path": str(self.scenes[index]),
                "suffix": "p0",
                "options_yml": str(self.options),
            }
        elif not any(
            isinstance(r.get("data"), dict) and r["data"].get("report") for r in results
        ):
            name, args = "finish_report", {}
        else:
            name = None
        message = {
            "role": "assistant",
            "content": None if name else "all requested steps complete",
        }
        if name:
            message["tool_calls"] = [
                {
                    "id": f"call_{ordinal}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ]
        return {
            "id": f"chatcmpl-{ordinal}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "fake-process-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if name else "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def finish(self, job_id):
        config = ET.parse(self.jobs[job_id]["config_xml"]).getroot()
        scene = Path(config.findtext("DatarootLQ"))
        suffix = config.findtext("Suffix")
        debug = scene / "Debug"
        debug.mkdir(exist_ok=True)
        (debug / f"{scene.name}_SRLOG.txt").write_text(
            "Run finished.", encoding="utf-8"
        )
        Image.new("I;16", (16, 16), 7).save(scene / f"{scene.name}_{suffix}.tif")
        with self.lock:
            self.jobs[job_id]["complete"] = True

    def physical_count(self):
        if not self.ledger.exists():
            return 0
        return len(self.ledger.read_text(encoding="utf-8").splitlines())


class GoalProcessAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scenes = [self.root / "scene_one", self.root / "scene_two"]
        for scene in self.scenes:
            scene.mkdir()
            (scene / f"{scene.name}_meta.xml").write_text(
                "<meta><SolarAzimuth>1</SolarAzimuth></meta>"
            )
            Image.new("I;16", (8, 8), 7).save(scene / f"{scene.name}.tif")
        self.options = self.root / "options.yml"
        self.options.write_text("tiftype: .tif\n", encoding="utf-8")
        self.report = self.root / "report.json"
        self.external = ExternalServices(
            self.root, self.scenes, self.options, self.report
        )
        self.settings = self.root / "settings.json"
        self.write_settings()
        self.store = Store(self.root / "goals.db")
        self.processes = []
        self.logs = []
        self.evidence = []
        self.env = {
            **os.environ,
            **allowed_roots_env(self.root),
            "PYTHONUTF8": "1",
            "SR_AGENT_DB": self.store.path,
            "SR_LLM_MOCK": "0",
            "SR_LLM_BASE_URL": self.external.url + "/v1",
            "SR_LLM_API_KEY": "test-only",
            "SR_LLM_MODEL": "fake-process-model",
            "SR_LLM_TIMEOUT": "10",
            "SR_LLM_STREAM": "0",
            "SR_EXECUTOR": "slurm",
            "SR_SLURM_FAKE": "0",
            "SR_SLURM_WORK_DIR": str(self.root / "work"),
            "SR_BUNDLE_DIR": str(self.root / "no-production-bundle"),
            "SR_SANDBOX_ROOT": "",
            "SR_LOCKED_DIR": "",
            "SR_VERIFY_SCRIPT": str(REPO / "SR_code/variants/verify_sr_run.py"),
            "SR_SCENES_ROOT": "",
            "SR_PRODUCT_PREVIEW_DIV": "0",
            "SR_GOAL_POLL_SEC": "0.05",
            "SR_GOAL_ROUND_TURNS": "1",
            "SR_GOAL_LEASE_SEC": "1.2",
            "SR_GOAL_ROUND_SECONDS": "10",
        }

    def write_settings(self, **extra):
        self.settings.write_text(
            json.dumps(
                {
                    "external_url": self.external.url,
                    "scenes": [str(p) for p in self.scenes],
                    "report": str(self.report),
                    **extra,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.external.release_model.set()
        for process in self.processes:
            self.stop(process)
        self.external.close()
        self.store.close()
        for log in self.logs:
            log.close()
        self.tmp.cleanup()

    def spawn(self, mode="tick", port=None):
        log = (self.root / f"child_{len(self.processes)}.log").open(
            "w", encoding="utf-8"
        )
        self.logs.append(log)
        command = [
            sys.executable,
            "-m",
            "backend.tests.goal_process_fixture",
            "--settings",
            str(self.settings),
            "--mode",
            mode,
        ]
        if port is not None:
            command.extend(["--port", str(port)])
        process = subprocess.Popen(
            command,
            cwd=REPO,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        self.processes.append(process)
        return process

    def stop(self, process):
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

    def run_tick(self, expected=0):
        process = self.spawn()
        code = process.wait(timeout=20)
        if code != expected:
            self.fail(
                f"child exit {code}, expected {expected}: "
                + (self.root / f"child_{len(self.processes)-1}.log").read_text(
                    encoding="utf-8"
                )
            )
        return process

    def goal_body(self, minimum=1):
        return {
            "title": "Process acceptance: complete specified SR jobs and report",
            "checkpoint": {
                "acceptance": {
                    "min_sr_jobs": minimum,
                    "sr_scenes": [str(p) for p in self.scenes[:minimum]],
                    "required_tools": ["run_sr"]
                    + (["search_scenes", "finish_report"] if minimum == 2 else []),
                    "artifacts": [str(self.report)] if minimum == 2 else [],
                }
            },
        }

    def goal(self, minimum=1):
        return self.store.create_goal(**self.goal_body(minimum))

    def capture(self, phase):
        goals = self.store.list_goals()
        self.evidence.append(
            {
                "phase": phase,
                "physical_submissions": self.external.physical_count(),
                "model_calls": self.external.model_calls,
                "unexpected_model_calls_while_waiting": self.external.unexpected_poll_calls,
                "goals": [
                    {
                        "goal": g,
                        "steps": self.store.list_goal_steps(g["goal_id"]),
                        "intents": self.store.list_tool_intents(g["goal_id"]),
                        "events": self.store.list_goal_events(g["goal_id"]),
                        "messages": (
                            self.store.get_messages(g["session_id"])
                            if g["session_id"]
                            else []
                        ),
                    }
                    for g in goals
                ],
            }
        )

    def write_evidence(self):
        destination = os.environ.get("P0_TEST_EVIDENCE_DIR")
        if destination:
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (self._testMethodName + ".json")).write_text(
                json.dumps(
                    {
                        "test": self.id(),
                        "model": "fake HTTP OpenAI-compatible service",
                        "executor": "fake HTTP executor with fsynced physical ledger",
                        "phases": self.evidence,
                        "child_exit_codes": [p.poll() for p in self.processes],
                        "physical_ledger": (
                            [
                                json.loads(line)
                                for line in self.external.ledger.read_text(
                                    encoding="utf-8"
                                ).splitlines()
                            ]
                            if self.external.ledger.exists()
                            else []
                        ),
                        "final_report": (
                            json.loads(self.report.read_text(encoding="utf-8"))
                            if self.report.exists()
                            else None
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.025)
        self.fail("timed out waiting for process acceptance condition")

    def request(self, base, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            base + path, data=data, headers={"Content-Type": "application/json"}
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def serve(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = self.spawn("serve", port)
        base = f"http://127.0.0.1:{port}"

        def ready():
            if process.poll() is not None:
                self.fail("API subprocess exited before readiness")
            try:
                return self.request(base, "/api/health")
            except OSError:
                return None

        self.wait_for(ready)
        return process, base

    def test_hard_exit_after_external_accept_before_receipt_never_resubmits(self):
        self.external.policy = "single"
        self.write_settings(crash_after_accept=True)
        gid = self.goal()["goal_id"]
        self.run_tick(expected=86)
        crashed = self.store.get_goal(gid)
        self.assertEqual(crashed["status"], "RUNNING")
        self.assertIsNotNone(crashed["lease_token"])
        self.assertEqual(self.external.physical_count(), 1)
        self.assertIsNone(self.store.list_sr_tasks()[0]["job_id"])
        self.assertEqual(self.store.list_tool_intents(gid)[0]["status"], "EXECUTING")
        self.assertEqual(crashed["rounds_used"], 1)
        self.assertEqual(crashed["model_calls_used"], 1)
        self.assertEqual(
            self.store.get_messages(crashed["session_id"])[-1]["role"], "assistant"
        )
        events = self.store.list_goal_events(gid)
        self.assertFalse(any(e["event_type"] == "round_finished" for e in events))
        self.capture("external_accepted_worker_hard_exited_before_receipt")
        time.sleep(max(0, crashed["lease_until"] - time.time()) + 0.05)
        self.write_settings()
        self.run_tick()
        recovered = self.store.get_goal(gid)
        self.assertEqual(recovered["status"], "BLOCKED")
        self.assertEqual(self.external.physical_count(), 1)
        self.assertEqual(self.external.model_calls, 1)
        self.assertTrue(
            any(
                e["event_type"] == "tool_recovery_unknown"
                for e in self.store.list_goal_events(gid)
            )
        )
        self.capture("new_process_blocked_without_resubmission")
        self.run_tick()
        self.assertEqual(self.external.physical_count(), 1)
        self.assertEqual(
            recovered["checkpoint"], self.store.get_goal(gid)["checkpoint"]
        )
        self.write_evidence()

    def test_old_process_cannot_submit_after_new_worker_takes_over(self):
        self.external.policy = "single"
        self.external.block_first_model = True
        gid = self.goal()["goal_id"]
        old_process = self.spawn()
        self.assertTrue(self.external.model_started.wait(5))
        old_token = self.store.get_goal(gid)["lease_token"]
        self.store._db().execute(
            "UPDATE agent_goals SET lease_until=0 WHERE goal_id=?", (gid,)
        )
        self.store._db().commit()
        self.run_tick()
        successor = self.store.get_goal(gid)
        self.assertEqual(successor["status"], "WAITING_EXTERNAL")
        self.assertNotEqual(old_token, successor["lease_token"])
        self.assertEqual(self.external.physical_count(), 1)
        events = self.store.list_goal_events(gid)
        self.capture("new_worker_submitted_old_worker_still_waiting_for_model")
        self.external.release_model.set()
        self.assertEqual(old_process.wait(timeout=10), 0)
        self.assertEqual(self.external.physical_count(), 1)
        self.assertEqual(events, self.store.list_goal_events(gid))
        self.assertEqual(len(self.store.list_tool_intents(gid)), 1)
        self.assertEqual(len(self.store.list_goal_steps(gid)), 1)
        self.assertEqual(self.store.get_goal(gid)["model_calls_used"], 2)
        self.assertEqual(self.store.list_sr_tasks()[0]["job_id"], 101)
        self.assertEqual(self.store.get_goal(gid)["checkpoint"]["external_job_id"], 101)
        self.assertEqual(sum(e["event_type"] == "goal_claimed" for e in events), 2)
        self.capture("old_model_response_discarded_without_side_effect_or_event")
        self.write_evidence()

    def test_two_jobs_http_disconnect_and_hard_backend_restart(self):
        backend, base = self.serve()
        accepted = self.request(base, "/api/goals", self.goal_body(minimum=2))
        gid = accepted["goal_id"]
        first = self.wait_for(
            lambda: (
                g
                if (g := self.request(base, f"/api/goals/{gid}"))["status"]
                == "WAITING_EXTERNAL"
                else None
            )
        )
        first_job = first["checkpoint"]["external_job_id"]
        self.assertEqual(self.external.physical_count(), 1)
        # A real HTTP SSE connection is closed by the observing client.
        with urlopen(base + f"/api/goals/{gid}/events/stream", timeout=3) as stream:
            self.assertTrue(stream.readline().startswith(b"data: "))
        calls = self.external.model_calls
        time.sleep(0.2)
        self.assertEqual(self.external.model_calls, calls)
        self.assertEqual(self.external.unexpected_poll_calls, 0)
        self.capture("sse_client_disconnected_while_first_sr_waiting")
        self.stop(backend)
        self.external.finish(first_job)
        backend, base = self.serve()
        second = self.wait_for(
            lambda: (
                g
                if (g := self.request(base, f"/api/goals/{gid}"))["status"]
                == "WAITING_EXTERNAL"
                and g["checkpoint"]["external_job_id"] != first_job
                else None
            )
        )
        self.assertEqual(self.external.physical_count(), 2)
        self.assertFalse(
            any(
                e["event_type"] == "goal_verified"
                for e in self.store.list_goal_events(gid)
            )
        )
        self.capture("first_restart_recovered_then_second_sr_waiting")
        self.stop(backend)
        self.external.finish(second["checkpoint"]["external_job_id"])
        backend, base = self.serve()
        done = self.wait_for(
            lambda: (
                g
                if (g := self.request(base, f"/api/goals/{gid}"))["status"]
                == "COMPLETED"
                else None
            )
        )
        self.assertEqual(done["model_calls_used"], 5)
        self.assertEqual(self.external.physical_count(), 2)
        self.assertEqual(self.external.model_calls, 5)
        self.assertEqual(self.external.unexpected_poll_calls, 0)
        self.assertEqual(json.loads(self.report.read_text())["job_ids"], [101, 102])
        self.assertEqual(
            [s["status"] for s in self.store.list_goal_steps(gid)], ["SUCCEEDED"] * 4
        )
        events = self.store.list_goal_events(gid)
        self.assertEqual(
            sum(e["event_type"] == "external_job_terminal" for e in events), 2
        )
        self.assertEqual(sum(e["event_type"] == "goal_verified" for e in events), 1)
        self.assertEqual(
            [
                m["content"]
                for m in self.store.get_messages(done["session_id"])
                if m["role"] == "user"
            ],
            [accepted["title"]],
        )
        self.stop(backend)
        self.capture("second_restart_recovered_and_all_steps_verified")
        self.write_evidence()


if __name__ == "__main__":
    unittest.main()
