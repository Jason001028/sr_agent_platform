// Unit-mass underdamped spring: stiffness 260, damping 12.
export const PRESS_SCALE = 0.94;
export const PRESS_MS = 70;
export const RELEASE_MS = 1100;
export function springScale(t: number, start = PRESS_SCALE): number {
  const decay = 6, frequency = Math.sqrt(260 - decay * decay);
  return 1 + (start - 1) * Math.exp(-decay * t)
    * (Math.cos(frequency * t) + decay / frequency * Math.sin(frequency * t));
}

const targets = '.toolbar button.btn, .toolbar button.cmp-btn, .toolbar button.set-btn, .toolbar button.sr-submit, .toolbar button.tour-button, .toolbar button.diff-button, .file-tools button.ft-btn';

/** Viewer-only, capture-phase delegation also covers dynamically created cards.
 * Never intercept clicks, capture pointers, or delay the business action. */
export function installButtonPress(root: HTMLElement, selector = targets,
  profile?: (button: HTMLElement) => { scale: number; pressMs: number; releaseMs: number; overshoot?: number }): () => void {
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Map<HTMLElement, Animation>();
  let held: { button: HTMLElement; shadow: string; pointer?: number; key?: string; motion?: ReturnType<NonNullable<typeof profile>> } | null = null;
  function clear() {
    held = null;
    animations.forEach(a => a.cancel()); animations.clear();
  }
  function press(event: Event, pointer?: number, key?: string) {
    const button = (event.target as Element)?.closest<HTMLElement>(selector);
    if (!button || !root.contains(button) || button.matches(':disabled,[aria-disabled="true"]')
      || media.matches || !button.animate || !CSS.supports('scale', '1')) return;
    clear();
    const shadow = getComputedStyle(button).boxShadow;
    const motion = profile?.(button);
    held = { button, shadow, pointer, key, motion };
    animations.set(button, button.animate([{scale:'1',boxShadow:shadow}, {scale:String(motion?.scale ?? PRESS_SCALE),boxShadow:'none'}],
      {duration:motion?.pressMs ?? PRESS_MS,easing:'ease-out',fill:'forwards'}));
  }
  function release(pointer?: number, key?: string) {
    if (!held || (pointer !== undefined && held.pointer !== pointer) || (key !== undefined && held.key !== key)) return;
    const { button, shadow, motion } = held;
    const start = Number.parseFloat(getComputedStyle(button).scale) || motion?.scale || PRESS_SCALE;
    clear();
    if (!button.isConnected || media.matches) return;
    const frames: Keyframe[] = motion ? [
      { scale: String(start), boxShadow: 'none', offset: 0 },
      ...(motion.overshoot ? [{ scale: String(motion.overshoot), boxShadow: shadow, offset: .78 }] : []),
      { scale: '1', boxShadow: shadow, offset: 1 },
    ] : Array.from({length:67}, (_, i) => ({
      offset:i/66, scale:String(i === 66 ? 1 : springScale(i/66 * RELEASE_MS/1000,start)),
      ...(i === 0 ? {boxShadow:'none'} : i === 66 ? {boxShadow:shadow} : {}),
    }));
    const animation = button.animate(frames,{duration:motion?.releaseMs ?? RELEASE_MS,easing:motion ? 'ease-out' : 'linear'});
    animations.set(button,animation);
    animation.onfinish = () => { if (animations.get(button) === animation) animations.delete(button); };
  }
  const down = (e: PointerEvent) => { if (e.isPrimary && e.button === 0) press(e,e.pointerId); };
  const up = (e: PointerEvent) => release(e.pointerId);
  const keydown = (e: KeyboardEvent) => { if (!e.repeat && (e.key === ' ' || e.key === 'Enter')) press(e,undefined,e.key); };
  const keyup = (e: KeyboardEvent) => release(undefined,e.key);
  root.addEventListener('pointerdown',down,true);
  root.addEventListener('keydown',keydown,true);
  window.addEventListener('pointerup',up,true);
  window.addEventListener('pointercancel',up,true);
  window.addEventListener('keyup',keyup,true);
  window.addEventListener('blur',clear);
  media.addEventListener('change',clear);
  return () => {
    clear(); root.removeEventListener('pointerdown',down,true); root.removeEventListener('keydown',keydown,true);
    window.removeEventListener('pointerup',up,true); window.removeEventListener('pointercancel',up,true);
    window.removeEventListener('keyup',keyup,true); window.removeEventListener('blur',clear);
    media.removeEventListener('change',clear);
  };
}
