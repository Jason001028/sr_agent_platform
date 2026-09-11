"""Tests for backend/config.py — SrRuntime and the SR default constants.

Two things are pinned:

  * the defaults equal the values that used to be hard-coded across
    run_sr.py / store.py / app.py, so centralising them cannot silently move a
    real-machine path (the bundle path in particular must keep agreeing with
    the load_library() call at code_0817_prod.py:27);
  * sr_runtime() reads the environment **at call time**. The systemd unit and
    the tests both configure the SR layer by setting env vars around the call,
    so a cached snapshot would leak one case's settings into the next.
"""

import inspect
import os
import unittest
from unittest import mock

from backend import config
from backend.services import run_sr as svc
from backend.services import store as store_mod

ENV_NAMES = ("SR_BUNDLE_DIR", "SR_PYTHON", "SR_SLURM_WORK_DIR",
             "SR_SLURM_PARTITION", "SR_SLURM_GRES", "SR_SLURM_TIME",
             "SR_SLURM_CPUS", "SR_SLURM_MEM", "SR_SLURM_FAKE",
             "SR_QUEUE_POLL_SEC", "SR_AGENT_DB", "SR_SCENES_ROOT")


class SrRuntimeDefaultsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ENV_NAMES:
            os.environ.pop(name, None)

    def test_defaults_match_the_old_hard_coded_values(self):
        rt = config.sr_runtime()
        self.assertEqual(
            rt.bundle_dir,
            "/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes")
        self.assertEqual(rt.python, "python")
        self.assertEqual(rt.slurm_work_dir, "/tmp/sr_agent_work")
        self.assertEqual(rt.partition, "")            # no --partition line
        self.assertEqual(rt.gres, 1)
        self.assertEqual(rt.time, "02:00:00")
        self.assertEqual(rt.cpus, 4)
        self.assertEqual(rt.mem, "")
        self.assertFalse(rt.fake)
        self.assertEqual(rt.queue_poll_sec, 2.0)
        self.assertEqual(rt.agent_db, store_mod.DEFAULT_DB)
        self.assertIsNone(rt.scenes_root)             # unset → fake fallback

    def test_run_sr_constants_are_the_config_defaults(self):
        self.assertEqual(svc.DEFAULT_BUNDLE_DIR, config.SR_DEFAULT_BUNDLE_DIR)
        self.assertEqual(svc.DEFAULT_WORK_DIR, config.SR_DEFAULT_WORK_DIR)
        self.assertEqual(svc.DEFAULT_OPTIONS_YML, config.SR_DEFAULT_OPTIONS_YML)
        # and the constants equal what the verifier's deploy docs promise
        self.assertEqual(svc.DEFAULT_BUNDLE_DIR,
                         config.sr_runtime().bundle_dir)
        self.assertEqual(svc.DEFAULT_WORK_DIR, config.sr_runtime().slurm_work_dir)

    def test_scenes_root_default_matches_paths_module(self):
        from backend.api import paths
        self.assertIsNone(paths.scenes_root())
        os.environ["SR_SCENES_ROOT"] = "/data/scenes"
        self.assertEqual(config.sr_runtime().scenes_root, "/data/scenes")


class SrRuntimeEnvTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ENV_NAMES:
            os.environ.pop(name, None)

    def test_reads_env_at_call_time_not_at_import(self):
        # Nothing was set when the module was imported; set now and re-read.
        os.environ["SR_BUNDLE_DIR"] = "/mnt/shared/bundle/codes"
        self.assertEqual(config.sr_runtime().bundle_dir, "/mnt/shared/bundle/codes")
        os.environ["SR_BUNDLE_DIR"] = "/mnt/other/codes"
        self.assertEqual(config.sr_runtime().bundle_dir, "/mnt/other/codes")
        del os.environ["SR_BUNDLE_DIR"]
        self.assertEqual(config.sr_runtime().bundle_dir,
                         config.SR_DEFAULT_BUNDLE_DIR)

    def test_every_field_takes_its_env_var(self):
        os.environ.update({
            "SR_PYTHON": "/opt/conda/envs/torch1.9.1py36/bin/python",
            "SR_SLURM_WORK_DIR": "/DiskArray/tmp/sr_agent_work",
            "SR_SLURM_PARTITION": "gpu",
            "SR_SLURM_GRES": "2",
            "SR_SLURM_TIME": "04:30:00",
            "SR_SLURM_CPUS": "8",
            "SR_SLURM_MEM": "64G",
            "SR_SLURM_FAKE": "1",
            "SR_QUEUE_POLL_SEC": "5",
            "SR_AGENT_DB": "/var/lib/sr/agent.db",
            "SR_SCENES_ROOT": "/data/scenes",
        })
        rt = config.sr_runtime()
        self.assertEqual(rt.python, "/opt/conda/envs/torch1.9.1py36/bin/python")
        self.assertEqual(rt.slurm_work_dir, "/DiskArray/tmp/sr_agent_work")
        self.assertEqual(rt.partition, "gpu")
        self.assertEqual(rt.gres, 2)
        self.assertEqual(rt.time, "04:30:00")
        self.assertEqual(rt.cpus, 8)
        self.assertEqual(rt.mem, "64G")
        self.assertTrue(rt.fake)
        self.assertEqual(rt.queue_poll_sec, 5.0)
        self.assertEqual(rt.agent_db, "/var/lib/sr/agent.db")
        self.assertEqual(rt.scenes_root, "/data/scenes")

    def test_empty_string_falls_back_to_the_default(self):
        # `Environment=SR_SLURM_PARTITION=` in a unit file yields "" — treat an
        # empty value as unset rather than as "partition named ''".
        os.environ["SR_BUNDLE_DIR"] = ""
        os.environ["SR_PYTHON"] = ""
        os.environ["SR_SLURM_WORK_DIR"] = ""
        rt = config.sr_runtime()
        self.assertEqual(rt.bundle_dir, config.SR_DEFAULT_BUNDLE_DIR)
        self.assertEqual(rt.python, config.SR_DEFAULT_PYTHON)
        self.assertEqual(rt.slurm_work_dir, config.SR_DEFAULT_WORK_DIR)

    def test_garbage_numbers_fall_back_instead_of_raising(self):
        # A typo in the unit file must not take the API down at first call.
        os.environ["SR_SLURM_GRES"] = "gpu"
        os.environ["SR_QUEUE_POLL_SEC"] = "soon"
        rt = config.sr_runtime()
        self.assertEqual(rt.gres, config.SR_DEFAULT_SLURM_GRES)
        self.assertEqual(rt.queue_poll_sec, config.SR_DEFAULT_QUEUE_POLL_SEC)

    def test_fake_flag_is_exactly_one(self):
        os.environ["SR_SLURM_FAKE"] = "true"
        self.assertFalse(config.sr_runtime().fake)
        os.environ["SR_SLURM_FAKE"] = "1"
        self.assertTrue(config.sr_runtime().fake)


class ConfigSeparationTest(unittest.TestCase):
    def test_config_module_does_not_import_services(self):
        # config.py <-> services is a cycle waiting to happen: services import
        # config for defaults, so config must never import them back.
        text = inspect.getsource(config)
        for banned in ("from .services", "from backend.services",
                       "import services"):
            self.assertNotIn(banned, text)

    def test_llm_config_is_untouched(self):
        os.environ["SR_LLM_MODEL"] = "unit-test-model"
        self.addCleanup(os.environ.pop, "SR_LLM_MODEL", None)
        self.assertEqual(config.load_config().llm_model, "unit-test-model")


if __name__ == "__main__":
    unittest.main()
