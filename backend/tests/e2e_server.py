"""开发机上的离机验收后端：`python -m backend.tests.e2e_server`。

与 `python -m backend.api` 的唯一差别是执行器入口换成内存替身
（`backend/tests/exec_double.py`），于是 `.e2e/` 里的浏览器用例不必真的起
bash 子进程、也不需要有真机上的 SR bundle 与 conda 解释器，就能把「提交 →
PENDING → RUNNING → COMPLETED → SSE 帧 → 完成提醒」这条链跑通。

替身只住在测试树里，生产模块里没有假成功路径；真执行器的行为由
`test_local_exec.py`（单槽、取消、重启记录）与 `test_local_chain.py`（真
bash + 真校验器 + 真判定文件）覆盖。真机上的 SR 验收见
`docs/status/real-machine-acceptance.md` §D。

Env：SR_API_HOST（默认 127.0.0.1）、SR_API_PORT（默认 8000）、
SR_TEST_EXEC_STAGE_MS（替身一格的毫秒数，缺省 500）。
"""

from __future__ import annotations

import os

import uvicorn

from backend.tests.exec_double import fake_exec


def main() -> None:
    host = os.environ.get("SR_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SR_API_PORT", "8000"))
    installed = fake_exec()
    installed.start()           # 进程级：起在 uvicorn 之前，之后不再摘掉
    uvicorn.run("backend.api.app:create_app", factory=True,
                host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
