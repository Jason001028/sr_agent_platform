"""`python -m backend.api` —— 直启 FastAPI（uvicorn 程序化）。

systemd/生产走 `uvicorn backend.api.app:create_app --factory`；这里只给
开发/离机验收一个快捷入口。Env：SR_API_HOST（默认 127.0.0.1）、SR_API_PORT
（默认 8000）。
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("SR_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SR_API_PORT", "8000"))
    uvicorn.run("backend.api.app:create_app", factory=True,
                host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
