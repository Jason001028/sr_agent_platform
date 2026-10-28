"""Request-local budgets. HTTP timeout is an inactivity bound, not hard realtime."""
import time


class DeadlineExceeded(TimeoutError):
    pass


def timeout_for(cfg, deadline=None, now=None):
    now = time.monotonic() if now is None else now
    remaining = None if deadline is None else deadline - now
    available = cfg.llm_timeout if remaining is None else remaining - cfg.llm_deadline_margin
    if available <= 0:
        raise DeadlineExceeded("round response/cleanup reserve reached")
    return min(cfg.llm_timeout, available)


def request_policy(cfg, deadline=None, now=None):
    now = time.monotonic() if now is None else now
    profile = cfg.llm_thinking_profile
    params = {}
    # Operator must verify this protocol on the deployed endpoint first.
    if cfg.llm_thinking_adapter == "reasoning_effort":
        params["reasoning_effort"] = {"fast": "low", "balanced": "medium", "deep": "high"}[profile]
    elif cfg.llm_thinking_adapter == "chat_template":
        params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": profile != "fast"}}
    return {"thinking_profile_requested": profile,
            "thinking_profile": profile if params else "unavailable",
            "thinking_parameters": params, "max_tokens": cfg.llm_max_tokens,
            "timeout": timeout_for(cfg, deadline, now),
            "remaining_round_seconds_at_request": None if deadline is None else max(0, deadline-now)}


def error_class(exc):
    import httpx
    import openai
    if isinstance(exc, DeadlineExceeded):
        return "DEADLINE_EXCEEDED"
    if isinstance(exc, (TimeoutError, openai.APITimeoutError, httpx.TimeoutException)):
        return "MODEL_TIMEOUT"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "AUTHENTICATION_ERROR"
    if status == 429:
        return "RATE_LIMITED"
    if status is not None and status >= 500:
        return "SERVICE_UNAVAILABLE"
    if status is not None and 400 <= status < 500:
        return "INVALID_REQUEST"
    if isinstance(exc, (openai.APIConnectionError, httpx.ConnectError, ConnectionError)):
        return "CONNECTION_ERROR"
    if isinstance(exc, (httpx.ReadError, httpx.RemoteProtocolError)):
        return "RESPONSE_INTERRUPTED"
    return "INVALID_RESPONSE"
