"""Session-scoped references and bounded reads; no server-file access or tool replay."""
from __future__ import annotations

import hashlib
from itertools import islice
import json
import re
import uuid

from backend.services.tool_result_formatter import META, ResultBudget, encode, format_result, size
from backend.services.trajectory import _redact
from backend.tools.contract import Tool, ToolRegistry, err, ok


REPAIR_HISTORY_TOOLS = frozenset({
    "inspect_repair_workspace", "query_batch_repair", "get_repair_workspace",
    "list_repair_items", "get_repair_item",
})


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
        # Models routinely ask for a larger page than the contract.  The schema
        # accepts the request but the server keeps its bounded page size, so a
        # harmless over-request never turns into a validation failure.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 20))
        if self.store is not None and self.session_id is not None:
            value = self.store.read_tool_result(self.session_id, result_id)
        else:
            raw = self.memory.get(result_id)
            value = json.loads(raw) if raw is not None else None
        if value is None:
            return err("Result reference unavailable, expired, or outside this session. No tool was re-executed.")
        # Apply the existing trajectory redaction policy before selecting a field.
        value = _redact(value)
        resolved = ''
        try:
            if path:
                if not path.startswith('/'):
                    raise ValueError()
                for part in path.split('/')[1:]:
                    if re.search(r'~(?![01])', part):
                        raise ValueError()
                    part = part.replace('~1','/').replace('~0','~')
                    if isinstance(value, list):
                        if not re.fullmatch(r'0|[1-9][0-9]*', part):
                            raise ValueError()
                        child = value[int(part)]
                    elif isinstance(value, dict):
                        child = value[part]
                    else:
                        raise TypeError()
                    resolved += '/' + part.replace('~','~0').replace('/','~1')
                    value = child
        except (KeyError, IndexError, ValueError, TypeError):
            return self._path_error(result_id, path, resolved, value)
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

    def _path_error(self, result_id, path, resolved, value):
        """Describe actual children of the last resolved node, never invent aliases."""
        children = []
        if isinstance(value, dict):
            entries = value.items()
        elif isinstance(value, list):
            entries = enumerate(value)
        else:
            entries = ()
        for key, child in islice(entries, 8):
            pointer = resolved + '/' + str(key).replace('~','~0').replace('/','~1')
            if len(pointer) <= 512:
                children.append({'path': pointer, 'type':
                    'object' if isinstance(child, dict) else 'array' if isinstance(child, list)
                    else 'string' if isinstance(child, str) else 'scalar'})
        result = err('工具结果路径不存在或格式不正确；请按 available_paths 读取实际字段。'
                     '这只是历史结果回读失败，不代表修复任务启动失败；不要重新提交修复。')
        result['data'] = {'result_id': result_id, 'requested_path': path,
                          'resolved_path': resolved, 'available_paths': children,
                          'recovery': '可先使用 path="" 查看根对象，或按截断视图的 read_paths 选择字段；'
                                      '修复计划目标使用 /data/targets/items，逐项读取 item_id，不存在 targets.ids；'
                                      '质检分页使用 /data/items。任务进度使用 list_repair_items 查询。'}
        # Diagnostics are bounded too; never archive errors into another read loop.
        while size(result) > self.budget.limit and children:
            children.pop()
        for field in ('requested_path', 'resolved_path'):
            while size(result) > self.budget.limit and result['data'][field]:
                result['data'][field] = result['data'][field][:len(result['data'][field]) // 2]
        return result

    def registry(self, registry):
        active = ToolRegistry(registry.list())
        active.register(Tool("read_tool_result", "只读读取当前会话已保存工具结果的有界页面，不重新执行原工具，也不读取任意服务端文件。"
                             "使用截断结果中 full_result.available=true 的引用 id；无持久化会话时引用仅本次运行有效。"
                             "路径基于完整的 ok/data/error 结果，读取前按既有策略脱敏。数组按元素、对象按键、字符串按字符分页；"
                             "修复计划目标列表路径为 /data/targets/items，每项有 item_id；不存在 /data/targets/ids。"
                             "截断视图的 read_paths 列出本结果实际可读字段；路径失败时查看 available_paths，按实际字段改读；"
                             "不确定先用空 path 查看根对象，不重复猜同一路径，也不要把示例当成本结果字段。"
                             "返回 value、shown、total、has_more、next_offset，标量仅返回 value 和引用信息。"
                             "has_more=true 时沿 next_offset 继续；预算可缩小页面（page_reduced），单项超限时 shown=0、游标不前进，须改读更深路径。"
                             "过期、跨会话、不可用引用或不存在的路径返回错误；不要因此盲目重调有副作用的原工具。",
            {"type":"object", "additionalProperties":False, "required":["result_id"], "properties":{
                "result_id":{"type":"string","pattern":"^[a-f0-9]{32}$",
                    "description":"当前会话工具结果中 full_result.available=true 时提供的 id，32 位小写十六进制；不是 job_id 或文件路径。"},
                "path":{"type":"string","maxLength":512,
                    "description":"完整结果中的 JSON Pointer，最多 512 字符；省略或空串选根对象。必须取本结果实际存在的字段，优先按截断视图的 read_paths 选择；例如 /data/items 或 /data/targets/items。~1 表示 /，~0 表示 ~，数组用从 0 起的索引。"},
                "offset":{"type":"integer","minimum":0,
                    "description":"从 0 起的非负偏移，省略为 0；单位是数组元素、对象键或字符串字符。下一页使用返回的 next_offset；标量不使用此参数。"},
                "limit":{"type":"integer","minimum":1,"maximum":100,
                    "description":"页大小，省略为 10；服务端最多取 20（大于 20 按 20 处理）；数组/对象最多取此数量的元素/键，字符串最多 min(limit×100,1000) 字符；还受字节预算限制，标量不使用此参数。"}}}, self.read))
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
