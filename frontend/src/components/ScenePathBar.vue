<script setup lang="ts">
/**
 * ScenePathBar.vue — 手工打开盘阵场景目录 / 单个 TIF（场景库页 / 查看器工具栏共用）
 * ------------------------------------------------------------------
 * 输入框预填**当天**的场景前缀（`W:\GSHC2IMPS\PRODUCT\<年>\<月>\<日>`），用户
 * 补上生产编号即可。支持两种形态：Windows 的 `W:\...`（服务端按 SR_DRIVE_MAP
 * 映射到 /DiskArray）与 `/DiskArray/...`；也接受**单个 `.tif` 文件路径**。
 *
 * 两条纪律：
 *  1. **不扫盘**。组件只把用户填的这一个路径往外抛，由调用方交给后端
 *     POST /api/scenes/resolve stat 一次；铺「当天目录列表」是明确不做的。
 *  2. 错误由**调用方**决定展示位置（ScenesPage 只渲染 scenes.error，查看器的
 *     错误条挂在 /viewer），所以这里只 emit('open', path)，不自己弹提示。
 */
import { ref } from 'vue';
import { todayScenePrefix } from '../lib/scene.js';

const props = withDefaults(defineProps<{
  /** 打开中（禁用按钮，避免连点） */
  busy?: boolean;
  /** 输入框占位（两处入口语境不同） */
  placeholder?: string;
}>(), {
  busy: false,
  placeholder: '粘贴场景目录（W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\17\\<生产编号>）或单个 .tif 文件路径',
});

const emit = defineEmits<{ (e: 'open', path: string): void }>();

const path = ref(todayScenePrefix());

function submit(): void {
  const p = path.value.trim();
  if (!p) return;
  emit('open', p);
}

/** 重新填回当天前缀（改过路径之后想回到「今天」）。 */
function resetToday(): void {
  path.value = todayScenePrefix();
}
</script>

<template>
  <div class="spb">
    <span class="spb-label">盘阵场景</span>
    <input v-model="path" class="spb-in" type="text" spellcheck="false"
           :placeholder="props.placeholder" :disabled="props.busy"
           @keyup.enter="submit" />
    <button type="button" class="btn mini" :disabled="props.busy" @click="submit">
      {{ props.busy ? '打开中…' : '打开' }}
    </button>
    <button type="button" class="btn mini ghost" :disabled="props.busy"
            title="填回当天前缀" @click="resetToday">今天</button>
    <span class="spb-hint">
      支持 W:\ 形态（服务端自动映射到 /DiskArray）；只打开你填的这一个路径，
      不扫盘。目录须含 &lt;目录名&gt;_meta.xml，以及 &lt;目录名&gt;.tif
      或 PAN.tif；<strong>也可以直接粘单个 .tif 文件路径</strong> —— 那张图照样
      能看，但父目录不是场景目录时不能提交 SR。首次打开要在服务器烘焙 1/2 预览图
      （要读一遍大图），可能较慢；此后打开读缓存，很快。
    </span>
  </div>
</template>

<style scoped>
.spb {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  padding: 10px 14px;
}
.spb-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
}
.spb-in {
  flex: 1 1 420px;
  min-width: 260px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  font-size: 13px;
  color: var(--ink);
  background: var(--surface-2);
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}
.spb-in::placeholder { color: var(--ink-faint); }
.spb-in:hover { border-color: #d8dfdc; }
.spb-in:focus { border-color: var(--accent-3); background: var(--surface); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.14); }
.spb-hint {
  flex: 1 1 100%;
  color: var(--ink-sub);
  font-size: 12px;
  line-height: 1.6;
}
.btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid transparent;
  border-radius: var(--r-ctrl);
  background: var(--accent-grad);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  transition: filter 0.15s ease, box-shadow 0.15s ease;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.22);
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .5; cursor: not-allowed; box-shadow: none; }
.btn.ghost {
  background: var(--surface);
  color: var(--ink-body);
  border-color: var(--line);
  box-shadow: none;
}
.btn.ghost:hover { color: var(--accent-deep); border-color: var(--accent-2); }
.btn.mini { height: 34px; padding: 0 14px; font-size: 12px; font-weight: 500; }
</style>
