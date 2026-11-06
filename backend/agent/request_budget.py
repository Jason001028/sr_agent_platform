"""Request-local budgets. HTTP timeout is an inactivity bound, not hard realtime."""
import time


class DeadlineExceeded(TimeoutError):
    pass


def timeout_for(cfg, deadline=None, now=None):
    now = time.monotonic() if now is None else now
    remaining = None if deadline is None else deadline - now
    cap = cfg.llm_response_timeout if remaining is None and cfg.llm_response_timeout is not None else cfg.llm_timeout
    available = cap if remaining is None else remaining - cfg.llm_deadline_margin
    if available <= 0:
        raise DeadlineExceeded("round response/cleanup reserve reached")
    return min(cap, available)


def response_limits(cfg, *, deadline=None, has_images=False, streaming=True):
    """Ordinary chat can progress beyond its idle limit; Goal slices never extend."""
    adaptive = deadline is None and cfg.llm_response_timeout is not None
    if not adaptive:
        total = max(cfg.llm_timeout, cfg.llm_vision_timeout) if has_images and deadline is None else cfg.llm_timeout
        return {"total": total, "first_output": total, "idle": total, "adaptive": False}
    total = cfg.llm_response_timeout
    first = cfg.llm_first_output_timeout or cfg.llm_timeout
    if has_images:
        first = max(first, cfg.llm_vision_timeout)
    # Without a stream there is no observable first-token signal: allow the
    # bounded whole-response budget rather than pretending to detect progress.
    return {"total": total, "first_output": min(first, total) if streaming else total,
            "idle": cfg.llm_timeout, "adaptive": True}


def timeout_message(diagnostics, cfg):
    phase = diagnostics.get('timeout_phase')
    seconds = diagnostics.get('timeout_limit_seconds', diagnostics.get('model_timeout_seconds', cfg.llm_timeout))
    if phase == 'first_output':
        return f'等待模型首段有效输出超过 {seconds:g} 秒（包含服务排队与预填充），本次请求已取消'
    if phase == 'idle':
        return f'模型连续 {seconds:g} 秒没有新的正文、推理或工具参数输出，本次请求已取消'
    if phase == 'total':
        return f'模型单次响应达到 {seconds:g} 秒总时限，本次请求已取消'
    if phase == 'connection':
        return f'连接模型服务超过 {seconds:g} 秒，请检查模型服务与网络'
    return f'模型请求超时（配置上限 {seconds:g} 秒），本次请求已取消'


def request_policy(cfg, deadline=None, now=None):
    now = time.monotonic() if now is None else now
    profile = cfg.llm_thinking_profile
    params = {}
    # Operator must verify this protocol on the deployed endpoint first.
    if cfg.llm_thinking_adapter == "reasoning_effort":
        params["reasoning_effort"] = {"fast": "low", "low": "low", "balanced": "medium", "deep": "high"}[profile]
    elif cfg.llm_thinking_adapter == "chat_template":
        template = {"enable_thinking": profile != "fast"}
        if profile != "fast":
            template["reasoning_effort"] = {"low": "low", "balanced": "medium", "deep": "xhigh"}[profile]
        params["extra_body"] = {"chat_template_kwargs": template}
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
