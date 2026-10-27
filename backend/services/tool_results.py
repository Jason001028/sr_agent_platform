"""Session-scoped references and bounded reads; no server-file access or tool replay."""
from __future__ import annotations

import hashlib
import json
import uuid

from backend.services.tool_result_formatter import META, ResultBudget, encode, format_result, size
from backend.services.trajectory import _redact
from backend.tools.contract import Tool, ToolRegistry, err, ok


class ToolResults:
    def __init__(self, store=None, session_id=None, budget=None):
        self.store, self.session_id = store, session_id
        self.budget = budget or ResultBudget.from_env()
        self.memory = {}
        self.scope = session_id or uuid.uuid4().hex

    def model_view(self, name, result, execution_key, *, arguments=None, tool=None):
        preview = format_result(name, result, budget=self.budget, arguments=arguments, tool=tool)
        if META not in preview:
            return preview
        # A saved model view is already the authoritative replay representation.
        if isinstance(result, dict) and META in result and size(result) <= self.budget.limit:
            return result
        if self.store is not None and self.session_id is not None:
            try:
                reference = self.store.archive_tool_result(self.session_id, execution_key, name, result, self.budget)
            except Exception as exc:
                from backend.services.goal_lease import LeaseLost
                if isinstance(exc, LeaseLost):
                    raise
                reference = {"available": False, "reason": "archive_unavailable"}
        else:
            raw = encode(result).encode("utf-8")
            ref_id = hashlib.sha256((self.scope + execution_key).encode() + raw).hexdigest()[:32]
            if len(raw) > self.budget.archive_bytes or sum(len(v) for v in self.memory.values()) + len(raw) > self.budget.archive_total_bytes:
                reference = {"available": False, "reason": "archive_size_limit"}
            else:
                self.memory[ref_id] = raw
                reference = {"available": True, "id": ref_id, "lifetime": "current_run_only"}
        return format_result(name, result, budget=self.budget, reference=reference, arguments=arguments, tool=tool)

    def read(self, result_id, path="", offset=0, limit=10):
        if self.store is not None and self.session_id is not None:
            value = self.store.read_tool_result(self.session_id, result_id)
        else:
            raw = self.memory.get(result_id)
            value = json.loads(raw) if raw is not None else None
        if value is None:
            return err("Result reference unavailable, expired, or outside this session. No tool was re-executed.")
        # Apply the existing trajectory redaction policy before selecting a field.
        value = _redact(value)
        try:
            if path:
                if not path.startswith('/'):
                    raise ValueError()
                for part in path.split('/')[1:]:
                    part = part.replace('~1','/').replace('~0','~')
                    value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return err("Result path does not exist; use a JSON pointer within the saved result.")
        reference = {"available": True, "id": result_id}
        if not isinstance(value, (list, str, dict)):
            result = ok({"value": value, "path": path, "result_id": result_id})
            return format_result("read_tool_result", result, budget=self.budget, reference=reference)

        requested = min(limit*100, 1000) if isinstance(value, str) else limit
        available = max(0, min(requested, len(value)-offset))
        keys = list(value)[offset:offset+available] if isinstance(value, dict) else None

        def page_result(shown):
            page = ({key: value[key] for key in keys[:shown]} if keys is not None
                    else value[offset:offset+shown])
            end = offset + shown
            data = {"value": page, "offset": offset, "next_offset": end,
                    "total": len(value), "has_more": end < len(value), "path": path,
                    "result_id": result_id, "shown": shown,
                    "page_reduced": shown < available}
            if keys is not None:
                data["keys"] = keys[:shown]
            return ok(data)

        # Size complete records/keys/characters together with their final cursor.
        # Do not send the page through generic structural clipping afterwards:
        # that could shorten its value while retaining a cursor past unseen data.
        for shown in range(available, -1, -1):
            result = page_result(shown)
            if shown == 0 and available:
                result[META] = {
                    "version": 1, "truncated": True,
                    "budget_error": "page_item_exceeds_budget",
                    "full_result": reference, "read_tool": "read_tool_result",
                    "message": "No item consumed. Read a deeper JSON pointer at this offset; the cursor has not advanced."}
            if size(result) <= self.budget.limit:
                return result
        # Even the page envelope (for example a long path) cannot fit. It must
        # not offer a progressing cursor or claim that any record was delivered.
        return format_result("read_tool_result", page_result(0),
                             budget=self.budget, reference=reference)

    def registry(self, registry):
        active = ToolRegistry(registry.list())
        active.register(Tool("read_tool_result", "只读读取当前会话已保存工具结果的有界页面，不重新执行原工具，也不读取任意服务端文件。"
                             "使用截断结果中 full_result.available=true 的引用 id；无持久化会话时引用仅本次运行有效。"
                             "路径基于完整的 ok/data/error 结果，读取前按既有策略脱敏。数组按元素、对象按键、字符串按字符分页；"
                             "返回 value、shown、total、has_more、next_offset，标量仅返回 value 和引用信息。"
                             "has_more=true 时沿 next_offset 继续；预算可缩小页面（page_reduced），单项超限时 shown=0、游标不前进，须改读更深路径。"
                             "过期、跨会话、不可用引用或不存在的路径返回错误；不要因此盲目重调有副作用的原工具。",
            {"type":"object", "additionalProperties":False, "required":["result_id"], "properties":{
                "result_id":{"type":"string","pattern":"^[a-f0-9]{32}$",
                    "description":"当前会话工具结果中 full_result.available=true 时提供的 id，32 位小写十六进制；不是 job_id 或文件路径。"},
                "path":{"type":"string","maxLength":512,
                    "description":"完整结果中的 JSON Pointer，最多 512 字符；省略或空串选根对象，例如 /data/results；~1 表示 /，~0 表示 ~，数组用从 0 起的索引。"},
                "offset":{"type":"integer","minimum":0,
                    "description":"从 0 起的非负偏移，省略为 0；单位是数组元素、对象键或字符串字符。下一页使用返回的 next_offset；标量不使用此参数。"},
                "limit":{"type":"integer","minimum":1,"maximum":20,
                    "description":"页大小，1..20，省略为 10；数组/对象最多取此数量的元素/键，字符串最多 min(limit×100,1000) 字符；还受字节预算限制，标量不使用此参数。"}}}, self.read))
        return active

    def project_history(self, messages, registry):
        """Project legacy/recovered tool results without altering assistant declarations."""
        count, calls = 0, {}
        projected = []
        for message in messages:
            message = dict(message)
            if message.get("role") == "assistant":
                count += 1
                for tc in message.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    calls[tc['id']] = (count, fn.get('name','unknown'), fn.get('arguments','{}'))
            if message.get('role') == 'tool':
                call_id = message.get('tool_call_id')
                ordinal, name, args = calls.get(call_id, (count, 'unknown', '{}'))
                try:
                    value = json.loads(message['content'])
                except (TypeError, ValueError):
                    value = ok(message['content'])
                message['content'] = encode(self.model_view(name, value,
                    f'{self.session_id}:{ordinal}:{call_id}', arguments=args, tool=registry.get(name)))
            projected.append(message)
        return projected
