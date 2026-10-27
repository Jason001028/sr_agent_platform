"""Goal-only control tool. No external side effects or global registration."""
from backend.tools.contract import Tool, ok

NAME = "request_clarification"
POLICY = """Durable Goal 任务澄清：先利用消息级 Viewer 快照、Checkpoint 和已有答案。
安全只读工具能够补足的信息先查后问；只有关键缺失信息或实质歧义、且无可靠低风险默认值时才提问。
低风险、可逆的默认值可以采用并说明假设；同一决策尽量组织成一个具体问题及有限选项。
使用 request_clarification 必须单独成批，调用后本轮立即结束，等待真实用户答案；不得自行回答或重复询问已回答的问题。
该工具只补充任务信息，不是高风险操作授权；SR 状态未知仍走原有对账机制。
"""
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["question"],
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"},
        "options": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "label"],
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"\S"},
                "label": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": r"\S"},
            }}},
        "allow_free_text": {"type": "boolean", "default": True},
        "recommended_option": {"type": ["string", "null"], "maxLength": 100},
    },
}


def normalize(question, options=None, allow_free_text=True, recommended_option=None):
    options = options or []
    ids = [o["id"] for o in options]
    if len(ids) != len(set(ids)):
        raise ValueError("option IDs must be unique")
    if recommended_option is not None and recommended_option not in ids:
        raise ValueError("recommended_option must refer to an existing option")
    if not options and not allow_free_text:
        raise ValueError("provide options or allow free text")
    return dict(question=question.strip(), options=options, allow_free_text=allow_free_text,
                recommended_option=recommended_option)


def control_tool():
    return Tool(NAME, "缺少关键任务信息且无法安全查询或推断时，向用户提出一个具体问题。"
                "可提供稳定 id/label 选项及推荐选项。必须单独成批；成功后暂停本轮并持久化等待用户，"
                "不用于审批、不执行外部作业。", SCHEMA,
                lambda **args: ok(normalize(**args)), visibility="goal")
