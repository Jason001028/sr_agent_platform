/** 动效令牌的 JS 侧：CSS 变量进不了 rAF 循环，查看器归位时长在此单列。
 *  角色与选型见 docs/conventions/motion-conventions.md。 */
export const VIEW_FIT_MS = 160;
export const VIEW_RESET_MS = 140;

/** 批量入列的整段错峰窗口；单卡淡入 `CARD_ENTER_MS` 已含在内。
 *  `CARD_ENTER_MS` 与 style.css 的 `--motion-fade` 同值 —— 两边只许一起改，
 *  motion.test.ts 会读 style.css 把这条等式钉住。 */
export const CARD_CASCADE_MS = 1800;
/** 逐条固定间隔：12 条以内每张比上一张晚 150ms 入场（延迟按条数线性累计）。 */
export const CARD_STEP_MS = 150;
export const CARD_ENTER_MS = 120;

/** 第 `i` 张卡（共 `n` 张）的入场延迟：12 条以内按 `CARD_STEP_MS` 固定累计；
 *  超过 12 条时等距压进同一个 `CARD_CASCADE_MS` 窗口（步长按份数摊薄），
 *  末张连同淡入仍收在窗口末尾，不会随份数线性拖长。
 *  12 条 × 150ms = 1650ms + 120ms 淡入仍在 1800ms 内，所以边界条数也是严格 150ms。 */
export function cascadeDelay(i: number, n: number): number {
  if (n <= 1) return 0;
  const span = CARD_CASCADE_MS - CARD_ENTER_MS;
  const step = Math.min(CARD_STEP_MS, span / (n - 1));
  return Math.round(step * i);
}
