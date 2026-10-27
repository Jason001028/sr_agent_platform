# Errors

## 2026-10-26 — test work directory isolation

SR idempotency tests wrote to the default /tmp/sr_agent_work under Windows,
which is outside the writable test workspace. Isolate SR_SLURM_WORK_DIR in
the test's TemporaryDirectory. Do not treat this as a pipeline failure.

## 2026-10-27 — Windows source encoding and local entry points

PowerShell's default pipeline encoding replaced Chinese literals sent to
Python stdin with question marks. Use apply_patch for Unicode source edits
and verify the resulting file. Locate module paths with rg before reading.
The sandbox could not resolve npm's global launcher through the user home;
calling the workspace-local Node CLI entry points succeeded. Keep learning
log paths rooted at the repository when commands use a frontend workdir.

## 2026-10-27 - Browser CLI on Windows
For agent-browser eval use --base64 with UTF-8 encoded source: PowerShell launcher stdin and quoting can silently lose script content. Quote browser refs such as '@e3' to avoid PowerShell splatting. Use a dedicated writable temporary profile if automatic Chrome launch fails; run doctor first. apply_patch cannot delete and add the same path in one patch; use Update File or sequential patches.

## 2026-10-27 Viewer context validation
- Node/npm typecheck inside managed sandbox failed with EPERM reading the user installation directory. Retry outside sandbox; do not interpret this as a TypeScript error.
