import { ref, watch } from 'vue';
const key = 'sr.heat-palette.v1';
function read(): 'classic' | 'spectrum' {
  try { return localStorage.getItem(key) === 'spectrum' ? 'spectrum' : 'classic'; }
  catch { return 'classic'; }
}
export const heatPalette = ref<'classic' | 'spectrum'>(read());
watch(heatPalette, value => {
  try { localStorage.setItem(key, value); } catch { /* Session preference still applies. */ }
});
