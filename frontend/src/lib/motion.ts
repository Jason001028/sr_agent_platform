/** 动效令牌的 JS 侧：CSS 变量进不了 rAF 循环，查看器归位时长在此单列。
 *  角色与选型见 docs/conventions/motion-conventions.md。 */
export const VIEW_FIT_MS = 160;
export const VIEW_RESET_MS = 140;

/** 批量入列的整段错峰窗口；单卡淡入 `CARD_ENTER_MS` 已含在内。
 *  `CARD_ENTER_MS` 与 style.css 的 `--motion-fade` 同值 —— 两边只许一起改，
 *  motion.test.ts 会读 style.css 把这条等式钉住。 */
export const CARD_CASCADE_MS = 1800;
/** 逐条固定间隔：每张比上一张晚 300ms 入场，**不随份数摊薄**。 */
export const CARD_STEP_MS = 300;
export const CARD_ENTER_MS = 120;

/** 窗口内能排满步长的卡数：6 张（5 × 300ms + 120ms 淡入 = 1620ms ≤ 1800ms）。
 *  第 7 张若照排要到 1920ms 才落定，于是干脆省略它的入场动画。 */
export const CARD_STAGGER_MAX = Math.floor((CARD_CASCADE_MS - CARD_ENTER_MS) / CARD_STEP_MS) + 1;

/** 第 `i` 张卡（共 `n` 张）的入场延迟；`undefined` 表示这张不做入场动画。
 *  前 `CARD_STAGGER_MAX` 张坚持 `CARD_STEP_MS` 固定累计（份数再多也不压缩步长）；
 *  其后的卡片直接显示 —— 批次越大，省掉的是尾巴，不是节奏。 */
export function cascadeDelay(i: number, n: number): number | undefined {
  if (n <= 1) return 0;
  if (i >= CARD_STAGGER_MAX) return undefined;
  return i * CARD_STEP_MS;
}
