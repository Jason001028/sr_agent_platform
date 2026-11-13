# Errors

## 2026-11-13 — Review-fix verification setup

The new component fixture initially omitted its API audit array; complete the
fixture instead of relaxing the production schema. Whole-scene completion
tests must set SR_BATCH_ARTIFACT_DIR to their temporary workspace. A generated
report from the first run was moved under ignored test-tifs artifacts.
Windows PowerShell stdin can replace non-ASCII Python source: use apply_patch
or escaped Unicode data and assert document replacements actually match.

## 2026-11-13 — Smart Compare verification

New source-scale regression initially assumed the common grid followed NOSR;
the shared planner uses the larger PAN grid (up to max_edge). The test now
explicitly requests 128 and verifies the actual source scale. A new UI label
failed terminology checks; use 掩码. Restrict Smart parameter cancellation to
Smart/comparison requests to preserve legacy decoding/discard notices.
Browser comparison actions must open their details element before clicking.
On PowerShell quote agent-browser refs such as '@e16'; unquoted @ is splatting.
The local agent-browser stdin pipeline returned null; quoted inline eval worked.
One CLI wait timed out; offline doctor passed, and the actual calculated UI was
then checked from DOM state. Automated HTTP/browser regression also passed.

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

## 2026-10-28: PowerShell inline Python text matching
UTF-8 Chinese source sent through a shell pipe may not retain literal matching text. Use ASCII structural anchors or apply_patch; inspect failed edits before rerunning. No user changes were discarded.

## SSE closure validation

The regression command initially referenced nonexistent test_tool_result_budget.py; the module is test_tool_result_formatter.py. Restricted Windows process tests timed out in taskkill cleanup. A subprocess-permitted retry cleaned up successfully but retained startup and completion failures. Use UTF-8-safe patch tools for Chinese source text; the Windows shell Python pipe replaced non-ASCII text.

2026-10-29: npm via nvm failed sandbox realpath C:/Users/lenovo (EPERM). Try direct node entry scripts or approved test execution.


## 2026-10-29 P1 validation
- npm launcher hit EPERM resolving C:/Users/lenovo; direct local Node entry with --preserve-symlinks --preserve-symlinks-main succeeded.
- Goal timeout test (0.25 second slice) failed before model dispatch, including isolated rerun; do not claim full Goal regression passes.

## 2026-11-04 local UI verification
Node npm failed under sandbox with EPERM on user directory; authorized execution succeeded. agent-browser auto-launch failed with CDP channel closed despite doctor passing; use repository isolated-profile Puppeteer launcher. Mock API matching must inspect URL pathname, not source module filename.


## 2026-11-05 PowerShell Python stdin encoding
中文脚本通过 PowerShell 管道送入 Python 前须将 $OutputEncoding 设为 UTF8；默认编码使中文匹配字符串丢失。本次失败发生在写文件之前，重跑已修正。


## 2026-11-05 Batch Mock UI timing
Puppeteer 点击跟随复选框后立即读取 checked 在布局/响应式更新期间出现 false；增加调试往返后可稳定观察到 store.follow=true。验收改为等待真实 checked=true，再验证 userIntent 退出；未修改业务状态或断言目标。


## 2026-11-06 Image Refresh metadata and browser fixtures
- `_manual_row` leaves W/H unset; the read-only result endpoint must explicitly read TIFF header dimensions before reporting a client-usable result. Added a dimension assertion alongside the ready/changed-source regression.
- Browser camera assertions must use public `setPaneView` / scalar `view` fields after initial ResizeObserver layout settles; private `viewA` is not exposed by the setup store and reactive proxies serialize incompletely through CDP.

## [ERR-20261106-BATCH-PS] PowerShell source encoding

**Logged**: 2026-11-06
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Windows PowerShell's default ASCII pipeline corrupted Chinese literals passed to Python stdin during the batch Panel edit.

### Error
Chinese UI text became question marks; Unicode-dependent replacement anchors consequently failed. A separate relative-path command also ran from the frontend directory rather than the repository root.

### Suggested Fix
Use apply_patch for Unicode source edits, or explicitly set `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` before Python pipelines; specify the intended working directory. Inspect rendered screenshots as well as repeated-question-mark searches, since a single corrupted character is easy to miss. Quote agent-browser `@eN` arguments in PowerShell to avoid splatting; direct eval arguments work where the Windows stdin pipeline does not forward the script.

### Metadata
- Reproducible: yes
- Related Files: frontend/src/components/BatchRepairDraft.vue, .e2e/test-batch-panel.js

## 2026-11-07 ? Test file path relative to workdir

- Status: resolved
- A Python test-file creation used frontend/src while the command workdir was already frontend; creation failed with FileNotFoundError.
- Fix: apply patches from workspace root with explicit paths; use src paths for commands running inside frontend.

## 2026-11-09 - Concurrency regression URL corrected
- Status: resolved
- New regression initially requested /api/sessions, which does not exist. Corrected to /api/chat/sessions and reran successfully.
- Check registered routes before choosing an independent endpoint for concurrency tests.

## 2026-11-09 Preview variant path length

Preview variant names that repeated the scene stem exceeded Windows legacy path limits in the existing six-level scene fixtures. Use a short identity digest and a short temporary-file prefix. Re-run path/preview scope tests; preserve guarded `_preview.jpg` cleanup recognition.

## 2026-11-10 resolved: invalid pixel offset in new sparse progression fixture

The first one-pass sampling test used pixel data at file offset 0. The existing sparse reader treats offset 0 as absent, so its required first navigation row never arrived. Corrected the fixture to place pixels after a 16-byte header; no production reader exception was weakened. Read-set and DN parity assertions now pass.

## 2026-11-10 resolved: asynchronous cleanup test patched shared asyncio module

Mocking image_open.asyncio.sleep changed the shared asyncio module during AnyIO's lazy import and leaked the mock into its imported sleep reference. Patch the local module's asyncio binding with a small namespace instead; a fresh-process image-open suite then passed. PowerShell callback test URL concatenations also need parentheses per array element to ensure every invalid URL is tested independently.
