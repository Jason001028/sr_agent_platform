"""Agent orchestration layer (M1: a self-written loop over the tool registry).

See docs/knowledge/agent-orchestration-research.md §5 — M1 keeps the loop
dependency-free; LangGraph is a candidate replacement thin layer only if
cross-hour checkpoint / human-review is confirmed later (M3).
"""
