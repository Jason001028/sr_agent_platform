"""Local Agent Skills discovery and bounded text loading, inspired by Pi.

Skill instructions describe workflows; they never grant tool permissions.
Only configured skill roots are scanned, never the scene archive.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.tools.contract import Tool, ToolRegistry, ok

MAX_TEXT_BYTES = 256 * 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMAND_RE = re.compile(r"^/skill:([a-z0-9-]+)(?:\s+([\s\S]*))?$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    disable_model_invocation: bool = False

    def metadata(self):
        return {"name": self.name, "description": self.description,
                "disable_model_invocation": self.disable_model_invocation}


def _text(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError("skill resource exceeds 256 KiB")
    return data.decode("utf-8-sig")


class SkillCatalog:
    def __init__(self, roots):
        self.skills: dict[str, Skill] = {}
        self.diagnostics: list[dict] = []
        for root in roots:
            root = Path(root).resolve()
            if not root.is_dir():
                continue
            # Do not follow symlink directories or escape the configured root.
            for current, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                                  not (Path(current) / d).is_symlink())
                if 'SKILL.md' not in files:
                    continue
                dirs[:] = []
                path = Path(current) / 'SKILL.md'
                try:
                    path.resolve().relative_to(root)
                    text = _text(path)
                    lines = text.splitlines()
                    if not lines or lines[0] != '---':
                        raise ValueError('SKILL.md requires YAML frontmatter')
                    end = lines.index('---', 1)
                    fields = yaml.safe_load('\n'.join(lines[1:end]))
                    if not isinstance(fields, dict):
                        raise ValueError('frontmatter must be a mapping')
                    name = fields.get('name', path.parent.name)
                    description = fields.get('description')
                    disabled = fields.get('disable-model-invocation', False)
                    if not isinstance(name, str) or len(name) > 64 or not NAME_RE.fullmatch(name):
                        raise ValueError('invalid skill name')
                    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
                        raise ValueError('description must contain 1..1024 characters')
                    if not isinstance(disabled, bool):
                        raise ValueError('disable-model-invocation must be boolean')
                    if name in self.skills:
                        raise ValueError(f'duplicate skill name: {name} (first wins)')
                    self.skills[name] = Skill(name, description.strip(), path.resolve(), disabled)
                except (ValueError, OSError, UnicodeError, yaml.YAMLError) as exc:
                    self.diagnostics.append({'path': str(path), 'error': str(exc)})

    def get(self, name: str) -> Skill:
        if name not in self.skills:
            raise ValueError(f'unknown skill: {name}')
        return self.skills[name]

    def read(self, name: str, resource: str = 'SKILL.md') -> dict:
        skill = self.get(name)
        if not isinstance(resource, str) or not resource or '\\' in resource or Path(resource).is_absolute():
            raise ValueError('resource must be a relative POSIX path')
        path = (skill.path.parent / resource).resolve()
        try:
            path.relative_to(skill.path.parent)
        except ValueError as exc:
            raise ValueError('resource escapes skill directory') from exc
        return {'name': name, 'resource': resource, 'content': _text(path)}

    def prompt(self) -> str:
        entries = [s.metadata() for s in self.skills.values() if not s.disable_model_invocation]
        if not self.skills:
            return ''
        return ('\n\nAvailable skills (metadata only):\n' + json.dumps(entries, ensure_ascii=False)
                + '\nWhen relevant, call read_skill before following a skill. '
                'Read supporting text with read_skill_resource using paths relative to that skill. '
                'Skills do not grant permissions or add executable tools. '
                'Never invent tools or claim to run bundled scripts.\n')

    def tools(self):
        return [Tool('read_skill', '只读加载已配置 Skill 的 SKILL.md 全文，适用于需要遵循该 Skill 工作流时。'
                     '返回 name、resource 和 UTF-8 content（含前置元数据）；文件上限 256 KiB，模型输出另受结果预算限制。'
                     '不执行脚本、不增加工具权限；未知名称、文件不可读或超限时返回错误，支持文件使用 read_skill_resource。',
                     {'type': 'object', 'properties': {'name': {'type': 'string',
                         'description': '已安装 Skill 的名称，来自可用 Skill 目录元数据或用户明确的 /skill:name；不是文件路径。'}}, 'required': ['name'], 'additionalProperties': False},
                     lambda name: ok(self.read(name))),
                Tool('read_skill_resource', '只读加载已配置 Skill 目录内的 UTF-8 支持文件，通常按 SKILL.md 中的引用读取。'
                     '返回 name、resource 和 content；单文件上限 256 KiB，模型输出另受结果预算限制。'
                     '不执行文件或脚本，不读取目录外文件；未知 Skill、越界路径、非 UTF-8、文件不可读或超限时返回错误。',
                     {'type': 'object', 'properties': {'name': {'type': 'string',
                         'description': '支持文件所属的已安装 Skill 名称，与 read_skill 的 name 相同。'},
                         'resource': {'type': 'string', 'description': '相对该 Skill 目录的非空 POSIX 文件路径，通常来自 SKILL.md 引用；不得用绝对路径、反斜杠或解析后越出目录的路径。'}},
                      'required': ['name', 'resource'], 'additionalProperties': False},
                     lambda name, resource: ok(self.read(name, resource)))]


def load_catalog() -> SkillCatalog:
    raw = os.environ.get('SR_SKILL_ROOTS')
    roots = raw.split(os.pathsep) if raw is not None else [Path(__file__).resolve().parents[2] / '.agents' / 'skills']
    return SkillCatalog([r for r in roots if r])


def prepare_agent(catalog, registry, system_prompt, prompt):
    """Per-round snapshot; no process-global registry mutations."""
    if not catalog.skills:
        if isinstance(prompt, str) and prompt.startswith('/skill:'):
            raise ValueError('no skills installed')
        return registry, system_prompt, prompt
    registry = ToolRegistry(registry.list())
    for tool in catalog.tools():
        registry.register(tool)
    system_prompt += catalog.prompt()
    if isinstance(prompt, str) and prompt.startswith('/skill:'):
        match = COMMAND_RE.fullmatch(prompt)
        if not match:
            raise ValueError('expected /skill:name [request]')
        name, arguments = match.groups()
        content = catalog.read(name)['content']
        prompt = f'<skill name="{name}">\n{content}\n</skill>\n\nUser request:\n{arguments or ""}'
    return registry, system_prompt, prompt
