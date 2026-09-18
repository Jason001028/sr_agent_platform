/**
 * qclist.test.ts — 待修复清单 .txt 的解析/写回回归
 * ------------------------------------------------------------------
 * 夹具就是质检部门给的真实样例（4 条伪影 + 4 条「修复通过」）。
 * 最关键的一条是「原样 round-trip」：拿原文解析、再把解析出来的状态原样写回，
 * 必须得到一个**与原文逐字节相同**的文件。这是「上半部分逐字保留」的硬保证 ——
 * 只要哪天有人把 topRaw 改成「照着解析结果重新拼」，这条就会红。
 */
import { describe, it, expect } from 'vitest';
import {
  parseQcList, buildQcDoc, decodeQcBytes, encodeQcText,
  QC_STAGES, QC_FINALS, QC_DOC_WORD, QC_STATUS_LABEL, isTerminal,
} from '../qclist.js';
import type { QcStatus } from '../qclist.js';

const N6 = 'JL1KF02B02_PMS07_20260917122028_200538707_101_0006_001_L1';
const N20 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0020_001_L1';
const N21 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0021_001_L1';
const N22 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0022_001_L1';

const issue = (name: string, loc: string, imgType: string, owner: string) =>
  `${name},\t产品存在伪影 (问题类型:产品存在伪影 行列号:${loc} 影像类型:${imgType} )\t${owner}`;

/** 真实样例：上半 4 条伪影、空行、下半 4 条修复通过，文件末尾有换行。 */
const SAMPLE = [
  issue(N6, '30766.11,21862.51', 'PAN', '李鹏飞'),
  issue(N20, '7300.26,1737.98', 'pan', '李佳峻'),
  issue(N21, '30768.07,10886.95', 'PAN', '李佳峻'),
  issue(N22, '20931.93,4841.22', 'pan', '李佳峻'),
  '',
  `${N6}\t修复通过`,
  `${N20}\t修复通过`,
  `${N21}\t修复通过`,
  `${N22}\t修复通过`,
  '',
].join('\n');

describe('parseQcList（真实样例）', () => {
  const list = parseQcList(SAMPLE);

  it('上半部分 4 条全部识别成 issue', () => {
    expect(list.issues.map((i) => i.name)).toEqual([N6, N20, N21, N22]);
  });

  it('名字去掉原文里尾随的逗号', () => {
    for (const i of list.issues) expect(i.name).not.toMatch(/[,，]$/);
  });

  it('行列号按 (行, 列) 拆 —— 与 locatePixel 的 (x=列, y=行) 相反', () => {
    // 0006 是「行列号:30766.11,21862.51」→ 行 30766.11、列 21862.51
    expect(list.issues[0].row).toBe(30766.11);
    expect(list.issues[0].col).toBe(21862.51);
    expect(list.issues[3].row).toBe(20931.93);
    expect(list.issues[3].col).toBe(4841.22);
  });

  it('问题类型 / 影像类型 / 责任人 逐列抽出（大小写原样保留）', () => {
    expect(list.issues[0].kind).toBe('产品存在伪影');
    expect(list.issues[0].imgType).toBe('PAN');
    expect(list.issues[1].imgType).toBe('pan');
    expect(list.issues[0].owner).toBe('李鹏飞');
    expect(list.issues[1].owner).toBe('李佳峻');
  });

  it('下半部分的 4 条状态读回为 fixed', () => {
    expect(list.statuses).toEqual({
      [N6]: 'fixed', [N20]: 'fixed', [N21]: 'fixed', [N22]: 'fixed',
    });
  });

  it('topRaw 不含任何状态行', () => {
    expect(list.topRaw).not.toContain('修复通过');
    expect(list.topRaw.split('\n')[0]).toBe(issue(N6, '30766.11,21862.51', 'PAN', '李鹏飞'));
  });

  it('原样 round-trip：解析后再原样写回 == 原文', () => {
    expect(buildQcDoc(list, list.statuses)).toBe(SAMPLE);
  });
});

describe('buildQcDoc（写回）', () => {
  const list = parseQcList(SAMPLE);

  it('只写终态：中间态与未处理的行都不出现', () => {
    const st: Record<string, QcStatus> = {
      [N6]: 'submitted',   // 中间态 → 不写
      [N20]: 'drawn',      // 中间态 → 不写
      [N21]: 'rejected',   // 终态 → 写
      // N22 未处理 → 不写
    };
    const out = buildQcDoc(list, st);
    const bottom = out.slice(out.indexOf('\n\n') + 2).split('\n').filter(Boolean);
    expect(bottom).toEqual([`${N21}\t驳回`]);
  });

  it('三个终态各写自己的词', () => {
    const out = buildQcDoc(list, { [N6]: 'fixed', [N20]: 'rejected', [N21]: 'no_blur' });
    expect(out).toContain(`${N6}\t修复通过`);
    expect(out).toContain(`${N20}\t驳回`);
    expect(out).toContain(`${N21}\t非模糊通过`);
  });

  it('下半部分顺序跟着上半部分走，与标记先后无关', () => {
    const out = buildQcDoc(list, { [N22]: 'fixed', [N6]: 'rejected' });
    const bottom = out.slice(out.indexOf('\n\n') + 2).split('\n').filter(Boolean);
    expect(bottom).toEqual([`${N6}\t驳回`, `${N22}\t修复通过`]);
  });

  it('上半部分逐字不动 —— 即便同时改了状态', () => {
    const out = buildQcDoc(list, { [N6]: 'rejected' });
    expect(out.startsWith(list.topRaw.replace(/\n+$/, '') + '\n\n')).toBe(true);
  });

  it('一个终态都没有 → 只剩上半部分，且不多一个空行', () => {
    const out = buildQcDoc(list, {});
    expect(out).toBe(list.topRaw.replace(/\n+$/, '') + '\n');
  });

  it('空清单 → 空串', () => {
    expect(buildQcDoc(parseQcList(''), {})).toBe('');
  });
});

describe('parseQcList（边界）', () => {
  it('只有上半部分、没有下半部分 → 无状态', () => {
    const src = issue(N6, '1,2', 'PAN', '甲') + '\n';
    const list = parseQcList(src);
    expect(list.issues).toHaveLength(1);
    expect(list.statuses).toEqual({});
    expect(buildQcDoc(list, {})).toBe(src);
  });

  it('同名多条伪影只留一条（下半部分一名只能有一行状态）', () => {
    const src = [
      issue(N6, '1,2', 'PAN', '甲'),
      issue(N6, '3,4', 'PAN', '乙'),
      '',
      `${N6}\t修复通过`,
      '',
    ].join('\n');
    const list = parseQcList(src);
    expect(list.issues).toHaveLength(1);
    expect(list.issues[0].row).toBe(1);          // 保留首次出现那条
    expect(list.issues[0].owner).toBe('甲');
  });

  it('同名状态行只认第一次', () => {
    const src = issue(N6, '1,2', 'PAN', '甲') + '\n\n'
      + `${N6}\t修复通过\n${N6}\t驳回\n`;
    expect(parseQcList(src).statuses[N6]).toBe('fixed');
  });

  it('认不出的行原样留在上半部分，一行不丢', () => {
    const stray = '这是一行谁也认不出来的东西';
    const src = [issue(N6, '1,2', 'PAN', '甲'), stray, '', `${N6}\t修复通过`, ''].join('\n');
    const list = parseQcList(src);
    expect(list.topRaw).toContain(stray);
    // 认不出的行落在状态行之后 → 归属上半部分；写回后上半部分原样保留
    expect(buildQcDoc(list, list.statuses)).toBe(src);
  });

  it('第二列不是认得的终态词 → 不当状态行，留在上半部分', () => {
    const src = `随便什么名字\t随便什么词\n`;
    const list = parseQcList(src);
    expect(list.statuses).toEqual({});
    // topRaw 保留原文的行结构（含末尾那个空行）；写回时会把它收成一个分隔符
    expect(list.topRaw).toBe('随便什么名字\t随便什么词\n');
    expect(buildQcDoc(list, {})).toBe(src);
  });

  it('两列的问题行（缺责任人列）不当状态行，也不算 issue', () => {
    const src = `XXX_NAME,\t产品存在伪影 (问题类型:产品存在伪影 行列号:1,2 影像类型:PAN )\n`;
    const list = parseQcList(src);
    expect(list.issues).toHaveLength(0);
    expect(list.statuses).toEqual({});
    expect(list.topRaw).toBe(src);
    expect(buildQcDoc(list, {})).toBe(src);
  });

  it('缺行列号 → row/col 为 null，其余字段照常', () => {
    const list = parseQcList(`${N6},\t产品存在伪影 (问题类型:产品存在伪影 影像类型:PAN )\t李鹏飞\n`);
    expect(list.issues[0].row).toBeNull();
    expect(list.issues[0].col).toBeNull();
    expect(list.issues[0].imgType).toBe('PAN');
  });

  it('CRLF 文件：换行符照原样写回，不被改成 LF', () => {
    const src = SAMPLE.replace(/\n/g, '\r\n');
    const list = parseQcList(src);
    expect(list.eol).toBe('\r\n');
    expect(buildQcDoc(list, list.statuses)).toBe(src);
  });

  it('剥掉文件开头的 UTF-8 BOM', () => {
    const list = parseQcList('﻿' + SAMPLE);
    expect(list.issues).toHaveLength(4);
    expect(list.topRaw.startsWith(N6)).toBe(true);
  });

  it('空文件不炸', () => {
    const list = parseQcList('');
    expect(list.issues).toEqual([]);
    expect(list.statuses).toEqual({});
    expect(list.topRaw).toBe('');
  });
});

describe('状态词表', () => {
  it('三段式 = 已绘制掩码 / 已提交任务 / 已修复', () => {
    expect(QC_STAGES.map((s) => QC_STATUS_LABEL[s]))
      .toEqual(['已绘制掩码', '已提交任务', '已修复']);
  });

  it('人工终态 = 驳回 / 非模糊通过', () => {
    expect(QC_FINALS.map((s) => QC_STATUS_LABEL[s])).toEqual(['驳回', '非模糊通过']);
  });

  it('只有终态有文档词，中间态没有', () => {
    expect(QC_DOC_WORD.fixed).toBe('修复通过');
    expect(QC_DOC_WORD.rejected).toBe('驳回');
    expect(QC_DOC_WORD.no_blur).toBe('非模糊通过');
    expect(QC_DOC_WORD.drawn).toBeUndefined();
    expect(QC_DOC_WORD.submitted).toBeUndefined();
  });

  it('isTerminal 认三个终态；三段式里只有第三段（已修复）同时是终态', () => {
    expect(QC_STAGES.filter(isTerminal)).toEqual(['fixed']);
    expect(QC_STAGES.filter((s) => !isTerminal(s))).toEqual(['drawn', 'submitted']);
    expect(QC_FINALS.every(isTerminal)).toBe(true);
    expect(isTerminal(undefined)).toBe(false);
  });
});

describe('编码', () => {
  const bytesOf = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer;

  it('UTF-8 无 BOM → 按 UTF-8 解', () => {
    const r = decodeQcBytes(bytesOf('修复通过'));
    expect(r).toEqual({ text: '修复通过', encoding: 'utf-8' });
  });

  it('UTF-8 带 BOM → 剥掉 BOM', () => {
    const r = decodeQcBytes(bytesOf('﻿修复通过'));
    expect(r.encoding).toBe('utf-8');
    expect(r.text).toBe('修复通过');
  });

  it('GBK 字节（老版记事本存的 ANSI）→ 退到 GBK 解出正常中文', () => {
    // 「修复通过」的 GBK 码：修 D0DE / 复 B8B4 / 通 CDA8 / 过 B9FD
    const b = new Uint8Array([0xD0, 0xDE, 0xB8, 0xB4, 0xCD, 0xA8, 0xB9, 0xFD]);
    const r = decodeQcBytes(b.buffer as ArrayBuffer);
    expect(r.encoding).toBe('gbk');
    expect(r.text).toBe('修复通过');
  });

  // 断言一律看**原始字节**，不用 blob.text()：Blob.text() 走的是 UTF-8 decode，
  // 按规范会把开头的 BOM 吃掉，用它根本验不出「到底写没写 BOM」。
  const bytesOfBlob = async (b: Blob) => new Uint8Array(await b.arrayBuffer());
  const UTF8_BOM = [0xEF, 0xBB, 0xBF];

  it('写回：UTF-8 原样，不加 BOM', async () => {
    const { blob, fellBack } = encodeQcText('修复通过', 'utf-8');
    expect(fellBack).toBe(false);
    const buf = await bytesOfBlob(blob);
    expect([...buf.slice(0, 3)]).not.toEqual(UTF8_BOM);
    expect(new TextDecoder('utf-8').decode(buf)).toBe('修复通过');
  });

  it('写回：原编码是 GBK → 编不回去，退 UTF-8+BOM 并置 fellBack', async () => {
    const { blob, fellBack } = encodeQcText('修复通过', 'gbk');
    expect(fellBack).toBe(true);
    const buf = await bytesOfBlob(blob);
    expect([...buf.slice(0, 3)]).toEqual(UTF8_BOM);
    expect(new TextDecoder('utf-8').decode(buf.subarray(3))).toBe('修复通过');
  });

  it('GBK 清单走完一整圈：解码 → 解析 → 写回 → 再解码，中文不出乱码', () => {
    // 「修复通过」的 GBK 字节，前面拼一段纯 ASCII 的问题行
    const gbkLine = new Uint8Array([0xD0, 0xDE, 0xB8, 0xB4, 0xCD, 0xA8, 0xB9, 0xFD]);
    const ascii = new TextEncoder().encode(`${N6},\tdesc\towner\n\n${N6}\t`);
    const bytes = new Uint8Array(ascii.length + gbkLine.length);
    bytes.set(ascii, 0);
    bytes.set(gbkLine, ascii.length);

    const dec = decodeQcBytes(bytes.buffer as ArrayBuffer);
    expect(dec.encoding).toBe('gbk');
    const list = parseQcList(dec.text);
    expect(list.statuses[N6]).toBe('fixed');
    expect(buildQcDoc(list, list.statuses)).toContain('\t修复通过');
  });
});
