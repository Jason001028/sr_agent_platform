import { LOGO_CELLS, LOGO_FPS, LOGO_CYCLE_MS } from './asciiLogoFrames';
const GLYPHS = '@#%*+=:/';
const TAU = Math.PI * 2;
/** Pure periodic field: exactly the same state at either side of a loop seam. */
export function cellStyle(x: number, y: number, coverage: number, elapsed: number) {
  const phase = (elapsed % LOGO_CYCLE_MS) / LOGO_CYCLE_MS * TAU;
  const wave = Math.sin(phase + x * .026 - y * .018);
  const flow = Math.sin(phase * 2 - y * .035 + x * .012);
  const seed = (x * 7 + y * 13) % GLYPHS.length;
  return {
    glyph: GLYPHS[(seed + Math.floor((flow + 1) * 3.49)) % GLYPHS.length],
    alpha: (.72 + .08 * wave) * (.82 + .18 * coverage),
  };
}
export function drawLogo(ctx: CanvasRenderingContext2D, mask: CanvasImageSource, elapsed: number) {
  ctx.clearRect(0, 0, 400, 400);
  ctx.save();
  ctx.translate(-40, -40);
  ctx.scale(1.2, 1.2);
  ctx.font = '600 8px "Courier New", monospace';
  ctx.textBaseline = 'top';
  ctx.fillStyle = '#447f8e';
  for (const [x, y, coverage] of LOGO_CELLS) {
    const { glyph, alpha } = cellStyle(x,y,coverage,elapsed);
    ctx.globalAlpha = alpha;
    ctx.fillText(glyph,x,y);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = 'destination-in';
  ctx.drawImage(mask,0,0,400,400);
  ctx.globalCompositeOperation = 'source-over';
  ctx.restore();
}
/** Timestamp-driven fixed-step scheduler, with no catch-up burst after suspension. */
export function createLogoPlayer(paint: (elapsed: number) => void,
  request = requestAnimationFrame, cancel = cancelAnimationFrame) {
  let id = 0, active = false, previous: number | undefined, elapsed = 0, rendered = -1;
  const step = 1000 / LOGO_FPS;
  function tick(time: number) {
    if (!active) return;
    if (previous !== undefined) elapsed += Math.max(0,time-previous);
    previous = time;
    const frame = Math.floor(elapsed/step);
    if (frame !== rendered) { paint(frame*step); rendered = frame; }
    id = request(tick);
  }
  return {
    start() { if (active) return; active=true; previous=undefined; id=request(tick); },
    stop() { active=false; cancel(id); id=0; previous=undefined; },
  };
}
