"""The installed batch-repair skill: discoverable, budget-bounded, permission-neutral."""
from pathlib import Path

from backend.services.skills import SkillCatalog

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / '.agents' / 'skills'
RESULT_BUDGET_BYTES = 4000


def catalog():
    return SkillCatalog([SKILLS])


def test_batch_repair_skill_is_discoverable_and_budget_bounded():
    found = catalog()
    assert 'batch-repair' in found.skills
    skill = found.get('batch-repair')
    assert skill.description and len(skill.description) <= 1024
    body = found.read('batch-repair')['content']
    # Every file must fit one bounded tool result; otherwise the model only
    # sees a truncated slice and has to page the instructions.
    assert len(body.encode('utf-8')) <= RESULT_BUDGET_BYTES
    assert 'inspect_repair_workspace' in body and '仅质检' in body
    # The confirmation gate and the layer distinction must stay explicit.
    assert '界面' in body and '确认' in body
    assert '人工验收' in body and '验收' in body
    assert 'plan_id' in body
    # The plan card is the default start path; start_repair is supplementary.
    assert '卡片' in body and '补充' in body


def test_batch_repair_references_fit_budget_and_are_readable():
    found = catalog()
    for resource in ('references/workflow.md', 'references/state-model.md',
                     'references/plan-and-tools.md', 'references/failure-playbook.md'):
        content = found.read('batch-repair', resource)['content']
        assert len(content.encode('utf-8')) <= RESULT_BUDGET_BYTES, resource
    workflow = found.read('batch-repair', 'references/workflow.md')['content']
    for required in ('get_repair_workspace', 'list_repair_items', 'get_repair_item',
                     'query_batch_repair', 'prepare_repair', 'start_repair'):
        assert required in workflow
    assert 'cursor' in workflow and 'plan_version' in workflow
    state = found.read('batch-repair', 'references/state-model.md')['content']
    assert 'AWAITING_REVIEW' in state and 'verification_layers' in state
    plan = found.read('batch-repair', 'references/plan-and-tools.md')['content']
    assert 'mask_policy' in plan and 'plan_version' in plan
    playbook = found.read('batch-repair', 'references/failure-playbook.md')['content']
    assert 'UNKNOWN' in playbook and '仅质检' in playbook


def test_batch_repair_skill_does_not_add_permissions():
    found = catalog()
    body = found.read('batch-repair')['content']
    assert 'disable-model-invocation: false' in body
    assert 'approved=true' not in body
    assert 'allowed-tools' not in body
