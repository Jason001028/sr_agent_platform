/**
 * lib/stage.ts — 盘阵场景里的「环节」：本体 / 本轮超分产物 / 未超分产物
 * ------------------------------------------------------------------
 * 一个场景目录（= 一景）里会同时躺着好几份影像：本体输入、本次 SR 产物、
 * 上一次的 NOSR 产物。三者**只有本体是可修复对象** —— SR 与掩码坐标都建在本体
 * 影像的网格上，产物是它的 2 倍（或别的倍数）放大结果，拿产物的 W/H 去写掩码
 * 就会把一张产物尺寸的掩码盖到本体的掩码文件上。所以「这是哪个环节」必须一路
 * 带进前端，不能只看「有没有盘阵目录」。
 *
 * 环节名的权威来源是后端（`POST /api/scenes/resolve` 的 `resolved.kind/suffix`，
 * `/api/scenes/siblings` 的 `item.kind/suffix`）；这里的函数只负责把它翻成标签
 * 文案与那句拒绝修复的人话，两条入口（拖进来 / 快捷芯片打开）共用一份，免得
 * 一处写 `SR` 一处写 `sr`。
 */

/** 环节：本体输入 / 本轮超分产物 / 未超分产物（与后端 kind 同字面量）。 */
export type StageKind = 'input' | 'product' | 'nosr';

/** 中间产物 = 任何不是本体的环节。`undefined`（老 rec、本地图片）按本体算 ——
 *  它们本来就没有盘阵目录，另有 `lqPath` 那道门管着。 */
export function isIntermediateStage(kind?: StageKind | null): boolean {
  return kind === 'product' || kind === 'nosr';
}

/** 卡片与提示上的环节标签。
 *
 *  取值为 `PAN` / `本体` / `<SUFFIX>` / `NOSR`：
 *  * 本体：RC 场景的输入影像是 `PAN.tif`，标 `PAN`；SC 场景的输入件名字就是
 *    生产全名（四十来字），塞进标签没意义，标 `本体`。
 *  * 本轮超分产物：用后端给的 suffix 大写（真机上是 `SR`）。
 *  * 未超分产物：固定 `NOSR`（它没有属于自己的 suffix，用的是产物那份）。
 */
export function stageLabel(kind: StageKind | undefined | null,
                           suffix: string | undefined | null,
                           fileName: string): string {
  if (kind === 'nosr') return 'NOSR';
  if (kind === 'product') {
    const s = (suffix || tailOf(stemOf(fileName))).trim();
    return s ? s.toUpperCase() : '产物';
  }
  return /^pan$/i.test(stemOf(fileName)) ? 'PAN' : '本体';
}

/** 中间产物的修复拒绝语（`enterDraw` / `bakeMaskToServer` / `submitSr` 共用一句，
 *  三处措辞一致用户才不会以为是三个毛病）。 */
export function stageRefusal(label: string): string {
  return '本图像是中间产物（' + label + '），仅用于对比，不作修复 —— '
    + 'SR 与掩码都建在本体影像的网格上，请先打开本体再修复与提交';
}

function stemOf(name: string): string {
  return name.replace(/\.(tif|tiff|img|jpg|jpeg)$/i, '');
}

/** 名字末尾那一段（`A_B_sr` → `sr`）。只在后端没给 suffix 时兜底。 */
function tailOf(stem: string): string {
  const i = stem.lastIndexOf('_');
  return i >= 0 ? stem.slice(i + 1) : '';
}
