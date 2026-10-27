# Errors

## 2026-10-26 — test work directory isolation

SR idempotency tests wrote to the default /tmp/sr_agent_work under Windows,
which is outside the writable test workspace. Isolate SR_SLURM_WORK_DIR in
the test's TemporaryDirectory. Do not treat this as a pipeline failure.
