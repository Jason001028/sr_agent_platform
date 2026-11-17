import type { PlatformSseEvent } from './api';

/** Ephemeral execution facts only. Never inspect reasoning or infer progress from elapsed time. */
export interface ChatActivity {
  label: string;
  analysis: string;
  pending: { id: string; name: string }[];
  terminal: boolean;
}
const tools: Record<string, [string, string]> = {
  search_scenes: ['正在检索匹配场景', '正在结合检索结果分析'],
  get_current_image_info: ['正在读取当前影像信息', '正在结合影像信息分析'],
  run_sr: ['正在提交超分任务', '正在检查超分提交结果'],
  sr_job_status: ['正在查询超分任务状态', '正在结合任务状态分析'],
  fix_bad_lines: ['正在处理影像坏线', '正在检查坏线处理结果'],
  present_spatial_annotations: ['正在生成空间批注', '正在整理空间批注说明'],
  read_tool_result: ['正在读取工具结果', '正在结合工具结果分析'],
  read_skill: ['正在读取任务操作指引', '正在结合操作指引分析'],
  read_skill_resource: ['正在读取操作指引附件', '正在结合指引附件分析'],
  prepare_repair: ['正在准备修复计划', '正在核对修复计划'],
  start_repair: ['正在启动修复任务', '正在核对启动回执'],
};
const actions: Record<string, string> = {
  search_scenes:'检索场景', get_current_image_info:'读取影像信息', prepare_repair:'准备修复计划',
  read_tool_result:'读取工具结果', start_repair:'启动修复任务', run_sr:'提交超分任务',
  sr_job_status:'查询任务状态', get_repair_workspace:'读取修复工作区', list_repair_items:'读取修复清单',
  get_repair_item:'读取修复项', read_skill:'读取操作指引', read_skill_resource:'读取指引附件',
  present_spatial_annotations:'生成空间批注', request_clarification:'请求用户确认', fix_bad_lines:'处理影像坏线',
};
export function toolActionLabel(name: string): string { return actions[name] ?? '执行工具操作'; }
export function startActivity(hasImages = false, hasRegion = false): ChatActivity {
  const analysis = hasRegion ? '正在分析选中区域' : hasImages ? '正在分析影像内容' : '正在分析任务';
  return { label: analysis, analysis, pending: [], terminal: false };
}
function running(pending: ChatActivity['pending']): string {
  return pending.length > 1 ? `正在执行 ${pending.length} 项工具操作`
    : tools[pending[0].name]?.[0] ?? '正在执行工具操作';
}
export function advanceActivity(state: ChatActivity, event: PlatformSseEvent): ChatActivity {
  if (state.terminal) return state;
  let label = state.label, analysis = state.analysis, pending = state.pending, terminal = false;
  switch (event.type) {
    case 'llm_phase':
      label = event.phase === 'answering' ? '正在整理回答'
        : event.phase === 'tool_call' ? '正在组织工具调用' : analysis;
      if (pending.length) label = running(pending);
      break;
    case 'tool_call': {
      const id = event.id ?? event.name;
      if (pending.some(p => p.id === id)) return state;
      pending = [...pending, { id, name: event.name }];
      label = running(pending);
      break;
    }
    case 'tool_result':
      pending = pending.filter(p => p.id !== (event.id ?? event.name));
      analysis = event.ok ? tools[event.name]?.[1] ?? '正在结合工具结果分析' : '正在分析工具失败原因';
      label = pending.length ? running(pending) : analysis;
      break;
    case 'token':
      if (event.delta) label = pending.length ? running(pending) : '正在整理回答';
      break;
    case 'reasoning':
      if (event.delta) label = pending.length ? running(pending) : analysis;
      break;
    case 'error': label = '本次处理异常中止'; terminal = true; pending = []; break;
    case 'turn_done': label = event.error ? '本次处理异常中止' : ''; terminal = true; pending = []; break;
    default: return state;
  }
  return label === state.label && analysis === state.analysis && pending === state.pending && terminal === state.terminal
    ? state : { label, analysis, pending, terminal };
}
