<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { LOGO_MASK_SRC } from '../lib/asciiLogoFrames';
import { createLogoPlayer, drawLogo } from '../lib/asciiLogoFlow';
const canvas = ref<HTMLCanvasElement | null>(null);
const playing = ref(false);
const failed = ref(false);
let mask: HTMLImageElement;
let motion: MediaQueryList;
let player: ReturnType<typeof createLogoPlayer> | undefined;
let ready = false;
function sync() {
  if (!ready) return;
  const run = !document.hidden && !motion.matches;
  playing.value = run;
  if (run) player?.start(); else player?.stop();
}
onMounted(() => {
  const ctx = canvas.value?.getContext('2d');
  if (!ctx || !canvas.value) { failed.value = true; return; }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.value.width = canvas.value.height = Math.round(400*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  mask = new Image();
  mask.onload = () => {
    ready = true;
    drawLogo(ctx,mask,0);
    player = createLogoPlayer(time => drawLogo(ctx,mask,time));
    sync();
  };
  mask.onerror = () => { failed.value = true; };
  mask.src = `${import.meta.env.BASE_URL}${LOGO_MASK_SRC}`;
  motion.addEventListener('change',sync);
  document.addEventListener('visibilitychange',sync);
});
onUnmounted(() => {
  player?.stop();
  motion?.removeEventListener('change',sync);
  document.removeEventListener('visibilitychange',sync);
  if (mask) { mask.onload = null; mask.onerror = null; }
});
</script>

<template>
  <div class="logo-intro" :data-playing="playing">
    <canvas v-show="!failed" ref="canvas" class="ascii-logo-canvas" aria-hidden="true"></canvas>
    <span v-if="failed" class="brand-fallback">&#38271;&#20809;&#21355;&#26143;</span>
  </div>
</template>

<style scoped>
.logo-intro { position: relative; width: 100%; max-width: 400px; aspect-ratio: 1; margin-inline: auto; display: grid; place-items: center; pointer-events: none; }
.ascii-logo-canvas { position: absolute; inset: 0; width: 100%; height: 100%; background: transparent; }
.brand-fallback { font-size: 22px; font-weight: 600; letter-spacing: .12em; }
</style>
