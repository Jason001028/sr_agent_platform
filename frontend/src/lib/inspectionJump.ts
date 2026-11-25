/**
 * inspectionJump.ts — 「质检结果」行 → 对应景的一键定位
 * ------------------------------------------------------------------
 * 质检总表每行只给景名与候选数；人看完还要自己找景、自己挑原图与 NOSR、
 * 自己开差异定位。这里把那串动作收成一个入口：
 *
 *   1. 跳到对应景（已在查看器里就只切过去，不重复取图）；
 *   2. 取同景 siblings，锁定「原图（input）」与「NOSR」，按需取像素；
 *   3. 按本次质检设置运行差异定位，显示独立热力图；
 *   4. 打开「图像对比 → 点选对比」，展开对比条，并调出差异定位设置浮层。
 *
 * 失败不抛：返回错误串（'' = 成功），由调用方决定怎么显示 —— Agent 卡片在
 * 查看器侧舱与独立聊天页都会出现，错误必须落到用户看得见的那条错误栏。
 */
import { nextTick } from 'vue';
import router from '../router';
import { useViewerStore } from '../stores/viewer';
import { useScenesStore } from '../stores/scenes';
import { useDiffStore } from '../stores/diff';
import { loadSrConfig } from './scene';
import { apiSceneSiblings, nosrItemOf } from './api';
import type { SceneSiblings } from './api';

function dirBase(p: string | null | undefined): string {
  return (p ?? '').replace(/[/\\]+$/, '').split(/[/\\]/).pop() ?? '';
}

/** 质检结果的 PAN/NOSR 缩略图 URL：`/api/scenes/<id>/preview?div=16`。
 *  后端一律用 `scene_id_abs(path)` 拼 id（`~` + base64url 绝对路径），所以从
 *  这里能拿回这一行对应的**精确栅格路径**。手工打开的 RC 场景 rec.name 只有
 *  `PAN`，按名字反查会撞车；有 URL 时优先用它。 */
export interface InspectionSceneRef { id: string; path: string }
const inspectionMethodLabels = { absdiff: '绝对差异', ssim: '结构差异 SSIM', smart: 'LTD 纹理退化' };

export function sceneRefFromPreviewUrl(url?: string | null): InspectionSceneRef | null {
  if (!url) return null;
  try {
    const pathname = new URL(url, 'http://local').pathname;
    const match = pathname.match(/\/api\/scenes\/([^/]+)\/preview/);
    if (!match) return null;
    const id = decodeURIComponent(match[1] ?? '');
    if (!id.startsWith('~')) return null;
    const body = id.slice(1).replace(/-/g, '+').replace(/_/g, '/');
    const padded = body + '='.repeat((4 - (body.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
    const path = new TextDecoder().decode(bytes);
    return path ? { id, path } : null;
  } catch {
    return null;
  }
}

/**
 * 把某个质检景带到眼前：打开 → 原图/NOSR → 差异定位 → 点选对比。
 *
 * `name` 来自质检结果的 `item.name`；`imageUrl` 是同一行 PAN（或退而求其次
 * NOSR）的预览 URL，用来拿精确场景身份。本函数不改任何修复状态，只读盘阵、
 * 只驱动查看器与差异定位。
 */
export async function jumpToInspectionScene(name: string, imageUrl?: string | null, settings?: {
  method?: unknown; toneWeight?: unknown;
}): Promise<string> {
  const trimmed = String(name ?? '').trim();
  if (!trimmed) return '景名为空，无法跳转';

  const viewer = useViewerStore();
  const scenes = useScenesStore();
  const diff = useDiffStore();

  // 质检卡片也可能出现在独立聊天页；那里没有画布，先回查看器再开图。
  if (router.currentRoute.value.name !== 'viewer') {
    await router.push('/viewer');
    await nextTick();
  }

  // 当前活动图就是这一景（本体或同目录产物）时不再重复 open：open 会重新取
  // 一遍 JPG、重走激活与适配。这里只确保它在眼前。
  const sceneRef = sceneRefFromPreviewUrl(imageUrl);
  const current = viewer.activeRec;
  const alreadyHere = !!current?.sceneId && (sceneRef
    ? current.sceneId === sceneRef.id
    : current.name === trimmed || dirBase(current.sceneDir) === trimmed);
  if (!alreadyHere) {
    // 有预览 URL 就按精确路径开（手工行 openQuiet 不写 scenes.error，原因原样带回）；
    // 失败行没有图片时才退回按名字解析。
    const openErr = sceneRef
      ? await scenes.openQuiet(sceneRef.path)
      : await scenes.openByName(trimmed);
    if (openErr) {
      viewer.showErr(openErr);
      return openErr;
    }
  }

  const rec = viewer.activeRec;
  if (!rec?.sceneId) {
    const msg = '「' + trimmed + '」未绑定可读取的盘阵场景，无法定位原图与 NOSR';
    viewer.showErr(msg);
    return msg;
  }

  let siblings: SceneSiblings;
  try {
    siblings = await apiSceneSiblings(loadSrConfig(), rec.sceneId);
  } catch (e) {
    const msg = '读取「' + trimmed + '」同景影像失败：'
      + (e instanceof Error ? e.message : String(e));
    viewer.showErr(msg);
    return msg;
  }

  // 原图 = 刚打开/已在眼前的这一张（resolve 与质检行都指向本体输入）。
  // **不再按 siblings 的 input 项另建卡**：两类入口的 scene id 形态不同
  // （`~` 绝对路径 id 与场景根下的 rel id），按 id 比会认成两张同源卡。
  const aId = rec.id;

  const nosr = nosrItemOf(siblings);
  if (!nosr) {
    const tried = siblings.nosrCandidates.length
      ? '（试过 ' + siblings.nosrCandidates.slice(0, 4).join(' / ') + '）'
      : '';
    const msg = '盘阵上没有「' + trimmed + '」的 NOSR' + tried + '，无法做原图/NOSR 差异定位';
    viewer.showErr(msg);
    return msg;
  }
  const bId = viewer.ensureSceneImage(nosr, siblings);
  if (bId == null) {
    const msg = '「' + trimmed + '」的 NOSR 尺寸未知，无法建立共同分析网格';
    viewer.showErr(msg);
    return msg;
  }

  // 差异定位只吃已取到像素的图；NOSR 往往是这一次才第一次打开。
  const [aReady, bReady] = await Promise.all([
    viewer.ensureCardPixels(aId),
    viewer.ensureCardPixels(bId),
  ]);
  if (!aReady || !bReady) {
    const msg = '原图或 NOSR 预览未就绪，无法做差异定位';
    viewer.showErr(msg);
    return msg;
  }

  // 让原图回到当前格：分析结果与原图标记都落在它上面。
  if (viewer.activeId !== aId) await viewer.activate(aId);

  if (diff.busy) diff.cancelAnalysis();
  const method = settings?.method;
  diff.method = method === 'absdiff' || method === 'smart' ? method : 'ssim';
  diff.toneWeight = typeof settings?.toneWeight === 'boolean' ? settings.toneWeight : true;
  const methodLabel = inspectionMethodLabels[diff.method];
  // 质检使用整景共同覆盖范围。
  diff.scope = 'common';
  diff.parallelCompare = false;
  // 默认落在「独立热力图」：进来先看差异本身，而不是原图上的标记框。
  // 叠加总开关若被关过，热力图在画布上根本不可见 —— 本入口的意图是「看得见差异」，
  // 这里显式打开；它只影响展示，不覆盖任何模块的编辑状态。
  diff.display = 'heat';
  viewer.overlayMasterVisible = true;
  diff.setPair(aId, bId);
  viewer.showToast(`正在加载「${trimmed}」原图与 NOSR · ${methodLabel}…`);
  const analyzed = await diff.analyze();
  if (!analyzed) {
    const msg = diff.error || '差异定位未完成';
    viewer.showErr(msg);
    return msg;
  }

  viewer.setCompareMode('click');
  // 「点选对比」要让人看得见：把工具栏下方那条对比条展开，同时调出差异定位的
  // 设置浮层（显示方式那一栏正停在「独立热力图」）。
  viewer.setCmpStripOpen(true);
  diff.openPanel();
  viewer.showToast(`已跳到「${trimmed}」 · ${methodLabel}热力图 · 点选对比`);
  return '';
}
