"""Fixed Chinese paired evaluation with an isolated Fake Executor.

Default: scripted contract replay (NOT model selection accuracy).
--live: configured real LLM, still no production tools or filesystem access.
Baseline loads HEAD sources in memory; no working tree file is replaced.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import types
from unittest.mock import patch

from backend.agent import loop
from backend.config import load_config
from backend.tools.contract import ok, err

CASES = [
    ('local', '我刚拖入了本地 TIFF，当前打开哪张图？', 'get_current_image_info', {}),
    ('draft', '当前修复清单勾选12景排除2景，帮我核对数量。', 'get_repair_workspace', {}),
    ('batch50', '这50景批次现在进行到哪一步？', 'get_repair_workspace', {}),
    ('failed', '这批哪些修复项失败了？', 'list_repair_items', {'goal_id':'eval-goal','status':'FAILED'}),
    ('review', '哪些景还在等人工审核？', 'list_repair_items', {'goal_id':'eval-goal','status':'AWAITING_REVIEW'}),
    ('third', '第三景为什么没完成？请查询检测、Mask、SR和人工复核状态。', 'list_repair_items', {'goal_id':'eval-goal','cursor':2,'limit':1}),
    ('empty', '当前工作区没有图，现在修复清单有几项？', 'get_repair_workspace', {}),
    ('job', '已知SR Job 42现在是什么状态？', 'sr_job_status', {'job_id':42}),
    ('search', '请明确搜索场景资源库中尚未打开的 GF07 其他场景。', 'search_scenes', {'scope':'library','query':'GF07'}),
]


def baseline(ref):
    modules = []
    for name, path in [('viewer', 'backend/services/viewer_context.py'), ('loop', 'backend/agent/loop.py')]:
        module = types.ModuleType('workspace_eval_' + name)
        source = subprocess.check_output(['git','show',f'{ref}:{path}']).decode('utf-8-sig')
        if name == 'loop':
            source = source.replace('from backend.services.viewer_context import bind_registry, project_messages',
                                    'pass  # evaluation binds the baseline observation module')
        exec(compile(source,path,'exec'),module.__dict__)
        modules.append(module)
    modules[1].bind_registry = modules[0].bind_registry
    modules[1].project_messages = modules[0].project_messages
    return modules[1]


def evaluate(live=False, ref='HEAD', core_only=False):
    cfg = load_config()
    if live and (cfg.llm_mock or not cfg.llm_api_key and 'api.openai.com' in cfg.llm_base_url):
        raise RuntimeError('真实模型评测未执行：未配置可用模型凭证/端点；不能以回放代替选择准确率。')
    rows = []
    for enhanced, module in [(False,baseline(ref)), (True,loop)]:
        for case, prompt, expected, arguments in CASES:
            if core_only and case not in {'local','draft','third','job','search'}:
                continue
            expected_chain=[expected,'get_repair_item'] if case=='third' else [expected]
            events, executed = [], []
            exposed = set()
            started = time.perf_counter()
            effective_at = None
            def fake_executor(tc, invoke):
                nonlocal effective_at
                # Deliberately do not call invoke: no SR, manifest, array or DB.
                if tc['name'] not in exposed:
                    return err('tool unavailable in this revision')
                executed.append(tc['name'])
                if tc['name'] == expected and effective_at is None:
                    effective_at = time.perf_counter() - started
                data = {'source':'isolated_fake_executor','fixture':case,'state_note':'SR提交/执行/契约/人工审核分别记录'}
                if tc['name'] == 'get_current_image_info':
                    data['image'] = {'name':'local.tif'}
                elif tc['name'] == 'get_repair_workspace':
                    data.update(total=0 if case=='empty' else 14,selected=0 if case=='empty' else 12,excluded=0 if case=='empty' else 2)
                elif tc['name'] == 'sr_job_status':
                    data.update(job_id=42,state='RUNNING')
                elif tc['name'] == 'list_repair_items':
                    data.update(items=[{'item_id':'item-3','ordinal':3,'name':'scene-3','status':'FAILED'}],
                                ordinal_base=1,order='frozen_manifest_including_excluded',next_cursor=None)
                elif tc['name'] == 'get_repair_item':
                    data.update(item_id='item-3',ordinal=3,status='FAILED',job_status='FAILED',
                                candidate_count=2,review_status='pending')
                elif tc['name'] == 'search_scenes':
                    data.update(results=[],count=0)
                return ok(data) if tc['name'] in expected_chain else err('fixture: unrelated tool; no data')

            def scripted_chat(messages, tools):
                exposed.update(t['function']['name'] for t in tools)
                responses=[m for m in messages if m.get('role')=='tool']
                if case=='third' and len(responses)==1 and expected in executed:
                    return {'finish_reason':'tool_calls','content':None,'tool_calls':[
                        {'id':'eval-detail','name':'get_repair_item','arguments':json.dumps({'goal_id':'eval-goal','item_id':'item-3'})}]}
                if responses:
                    return {'finish_reason':'stop','content':'固定工具调用回放结束。','tool_calls':[]}
                return {'finish_reason':'tool_calls','content':None,'tool_calls':[
                    {'id':'eval-call','name':expected,'arguments':json.dumps(arguments)}]}

            snapshot = {'version':1,'current':None if case=='empty' else {'name':'local.tif','image_key':'local-session:fixture','source_revision':'r1'},
                'batch_goal':None if case=='empty' else {'goal_id':'eval-goal'},
                'repair_selection':{'total':0 if case=='empty' else 14,'selected':0 if case=='empty' else 12,'excluded':0 if case=='empty' else 2,'version':1}}
            # All tool effects pass through Fake Executor. Disable external skills
            # and plugin discovery to keep this evaluation reproducible.
            with patch.dict('os.environ', {'SR_SKILL_ROOTS':str(Path(__file__).parent/'tests'/'no-skills')}):
                if live:
                    # A wrapper observes the actual manifest, then delegates to
                    # the real provider; all execution remains fake.
                    real_chat = module._default_chat(cfg)
                    def observed_chat(messages, tools, **kwargs):
                        exposed.update(t['function']['name'] for t in tools)
                        return real_chat(messages,tools,**kwargs)
                result = module.run_loop(cfg,prompt,viewer_snapshot=snapshot,on_step=events.append,
                    chat=observed_chat if live else scripted_chat,tool_executor=fake_executor,max_tool_calls=5,stream=False)
            calls = [e['name'] for e in events if e.get('type')=='tool_call']
            rows.append({'enhanced':enhanced,'case':case,'expected':expected,'calls':calls,
                'expected_chain':expected_chain,'chain_correct':calls==expected_chain if live else None,
                'extra_tool_calls':max(0,len(calls)-len(expected_chain)),
                'ordinal_lookup_correct':next((e.get('args',{}).get('cursor')==2 and not e.get('args',{}).get('status')
                    for e in events if e.get('type')=='tool_call' and e.get('name')=='list_repair_items'),False) if case=='third' else None,
                'first_tool_correct':bool(calls and calls[0]==expected) if live else None,
                'expected_tool_executed':expected in executed,
                'irrelevant_searches':calls.count('search_scenes') if case!='search' else 0,
                'tool_calls':len(calls),'first_effective_tool_seconds':effective_at,
                'elapsed_seconds':time.perf_counter()-started,'ok':result.get('ok'),
                'answer':result.get('answer') if live else None,
                'unsupported_state_reports':None,'answer_review':'真实模型答案需人工复核；回放不产生自然语言状态结论。'})
    old_viewer = types.ModuleType('old_viewer_projection')
    source=subprocess.check_output(['git','show',f'{ref}:backend/services/viewer_context.py']).decode('utf-8-sig')
    exec(compile(source,'baseline-viewer','exec'),old_viewer.__dict__)
    from backend.services.viewer_context import project_messages
    sample=[{'role':'user','content':'这批图', 'viewer_snapshot':{'version':1,
        'batch_draft':{'total':50,'count':50,'version':1,'candidates':[
            {'name':f'scene-{n}','path':f'/isolated/scene-{n}/PAN.tif','evidence':'待检测，需人工审核'} for n in range(50)]}}}]
    projection_bytes={'baseline':len(json.dumps(old_viewer.project_messages(sample),ensure_ascii=False).encode('utf-8')),
                      'enhanced':len(json.dumps(project_messages(sample),ensure_ascii=False).encode('utf-8'))}
    return {'mode':'live_model_fake_executor' if live else 'scripted_contract_replay',
        'draft50_projection_utf8_bytes':projection_bytes,
        'baseline_ref':ref,'note':'回放仅测工具可达性与链路，不代表模型选工具准确率；耗时不代表真实首次有效回答。', 'rows':rows}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--baseline-ref',default='HEAD')
    parser.add_argument('--core-only',action='store_true',help='仅运行当前影像、修复清单、第三景、已知Job、搜索其他场景五类')
    parser.add_argument('--output',default='.e2e/workspace-eval.json')
    args=parser.parse_args()
    try:
        result=evaluate(args.live,args.baseline_ref,args.core_only)
    except RuntimeError as exc:
        result={'mode':'live_model_fake_executor','status':'blocked','reason':str(exc),
                'tool_selection_accuracy':None,'irrelevant_searches':None,'extra_tool_calls':None,'rows':[]}
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(result['reason'])
        raise SystemExit(2)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({str(enhanced):{
        'cases':len(rows:=[r for r in result['rows'] if r['enhanced']==enhanced]),
        'expected_tool_executed':sum(r['expected_tool_executed'] for r in rows),
        'irrelevant_searches':sum(r['irrelevant_searches'] for r in rows),
        'tool_calls':sum(r['tool_calls'] for r in rows)} for enhanced in (False,True)}))


if __name__=='__main__':
    main()
