"""CLI for the M1 agent loop.

    python -m backend.agent [--max-turns N] [--json] [--tools] "prompt"
    echo "prompt" | python -m backend.agent --max-turns 6

The LLM endpoint comes from env (SR_LLM_*), see backend/config.py. api_key 最后
配置: cloud endpoints need SR_LLM_API_KEY; a local Ollama endpoint accepts empty.

Every run persists to the SQLite store (SR_AGENT_DB, default ./sr_agent.db) and
prints the session_id — P1⑦ will add `--resume <session_id>`.
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.config import load_config
from backend.services import store as store_svc
from backend.tools.contract import manifest

from .loop import run_loop


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the M1 agent loop.")
    ap.add_argument("prompt", nargs="?", help="user prompt (else read stdin)")
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--json", action="store_true",
                    help="emit the full transcript as JSON")
    ap.add_argument("--tools", action="store_true",
                    help="print the tool manifest and exit")
    ap.add_argument("--db", help="SQLite store path (default SR_AGENT_DB or ./sr_agent.db)")
    args = ap.parse_args()

    if args.tools:
        print(json.dumps(manifest(), indent=2, ensure_ascii=False))
        return 0

    prompt = args.prompt or sys.stdin.read().strip()
    if not prompt:
        ap.error("prompt required (positional arg or stdin)")

    cfg = load_config()
    store = store_svc.Store(args.db) if args.db else store_svc.default_store()
    result = run_loop(cfg, prompt, max_turns=args.max_turns,
                      verbose=not args.json, store=store)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if result.get("session_id"):
        print(f"\n[store] session_id={result['session_id']} db={store.path}")

    for m in result["messages"]:
        role = m.get("role")
        if role == "assistant":
            if m.get("content"):
                print(f"\n[assistant] {m['content']}")
            for tc in m.get("tool_calls", []):
                print(f"  [tool call] {tc['name']}({tc.get('arguments', '')[:160]})")
        elif role == "tool":
            snippet = json.dumps(m["content"], ensure_ascii=False)
            print(f"  [tool {m['name']}] {snippet[:240]}")
        else:
            print(f"[{role}] {m.get('error') or m.get('content')}")

    if result["ok"]:
        print("\nOK — answer:", result["answer"])
        return 0
    print("\nFAIL —", result["error"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
