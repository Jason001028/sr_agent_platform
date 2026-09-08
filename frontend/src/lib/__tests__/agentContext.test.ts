/**
 * agentContext.test.ts — Agent 会话上下文附文纯函数（阶段6，Node 环境）
 * ------------------------------------------------------------------
 * 验证：CTX_DIVIDER 拼接形状（question / divider / 附文）、确定性数字逐项进文、
 * ROI 有/无选中两种注记、buildContextNote 携带 scene_id/W/H/显示尺寸/拉伸。
 * 纪律前提：附文数字全部来自确定性来源（buildStats / 几何），不允许 LLM 编造 ——
 * 这里断言的就是「发送内容里确实带上了这些数字」。
 */
import { describe, it, expect } from 'vitest';
import {
  CTX_DIVIDER, agentPayload, buildContextNote, roiStatLine,
} from '../agentContext.js';
import type { RoiCtxNote, ViewerContextSnap } from '../agentContext.js';
import type { RoiStats, RoiGeom } from '../roiStats.js';

function stats(over: Partial<RoiStats> = {}): RoiStats {
  return {
    n: 4, min: 100, max: 255, mean: 176.25, std: 57.6,
    hiPct: 50, clipPct: 25, sampled: false, stride: 1, rowSpan: 2, rowsSampled: 2,
    ...over,
  };
}
const geom: RoiGeom = { w: 40, h: 30, area: 900 };

function snap(over: Partial<ViewerContextSnap> = {}): ViewerContextSnap {
  return {
    name: 'GF07_A', sceneId: 's-123', W: 1024, H: 768,
    dispW: 512, dispH: 384, stretch: '2% 线性', roi: null,
    ...over,
  };
}

describe('agentPayload / CTX_DIVIDER 拼接', () => {
  it('= question + 分隔线 + 上下文注记', () => {
    const p = agentPayload('这片云多吗？', '附文内容');
    expect(p).toBe('这片云多吗？\n' + CTX_DIVIDER + '\n附文内容');
  });
  it('分隔线文案提示只读（供气泡视觉分离）', () => {
    expect(CTX_DIVIDER).toContain('只读');
  });
});

describe('buildContextNote 数字附文', () => {
  it('无选中 ROI → 注记含图像确定性信息 + 未选中提示，无 ROI 行', () => {
    const n = buildContextNote(snap());
    expect(n).toContain('"GF07_A"');
    expect(n).toContain('scene=s-123');
    expect(n).toContain('原图=1024×768');
    expect(n).toContain('显示=512×384');
    expect(n).toContain('拉伸=2% 线性');
    expect(n).toContain('未选中 ROI');
    expect(n).not.toContain('ROI#');
  });

  it('无 sceneId（本地）→ 注记如实标本地', () => {
    const n = buildContextNote(snap({ sceneId: null }));
    expect(n).toContain('本地(无 sceneId)');
  });

  it('有选中 ROI → 附确定性统计行（n/min/max/mean/std/亮像元/过曝）', () => {
    const roi: RoiCtxNote = { index: 2, geom, stats: stats() };
    const n = buildContextNote(snap({ roi }));
    expect(n).toContain('ROI#2');
    expect(n).toContain('bbox=40×30px');
    expect(n).toContain('面积≈900 px²');
    expect(n).toContain('n=4 min=100 max=255 mean=176.3 std=57.6');   // 1 位小数 176.3
    expect(n).toContain('亮像元(≥200)=50%');
    expect(n).toContain('过曝(≥250)=25%');
  });

  it('抽行统计 → 行末带 [抽行 stride=N] 标注', () => {
    const roi: RoiCtxNote = { index: 1, geom, stats: stats({ sampled: true, stride: 3 }) };
    expect(roiStatLine(roi)).toContain('[抽行 stride=3]');
  });

  it('空 ROI（n=0）→ 统计附文明确无有效像元而非编数字', () => {
    const roi: RoiCtxNote = {
      index: 1, geom,
      stats: stats({ n: 0, min: null, max: null, mean: null, std: null, hiPct: 0, clipPct: 0 }),
    };
    expect(roiStatLine(roi)).toContain('无有效显示像元');
    expect(roiStatLine(roi)).not.toContain('min=null');
  });
});
