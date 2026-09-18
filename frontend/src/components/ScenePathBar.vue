<script setup lang="ts">
/**
 * ScenePathBar.vue — 手工打开盘阵场景目录 / 单个 TIF（场景库页 / 查看器工具栏共用）
 * ------------------------------------------------------------------
 * 输入框预填**当天日期前缀**（`W:\GSHC2IMPS\PRODUCT\<年>\<月>\<日>`）—— 注意它
 * 只是前缀，还得往下补**三层**到景级目录（真机六层树：
 * `<年>\<月>\<日>\<卫星型号>\<段级目录>\<景级目录>`）。日期目录本身不是场景
 * 目录，直接点「打开」只会 404。支持两种形态：Windows 的 `W:\...`（服务端按
 * SR_DRIVE_MAP 映射到 /DiskArray）与 `/DiskArray/...`；也接受**单个 `.tif`
 * 文件路径**。
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
  placeholder: '粘贴景级场景目录（W:\\GSHC2IMPS\\PRODUCT\\<年>\\<月>\\<日>\\<卫星型号>\\<段级>\\<景级=完整生产名>）或单个 .tif 文件路径',
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
      预填的只是<strong>当天日期前缀</strong>，还要往下补三层
      （&lt;卫星型号&gt;\&lt;段级目录&gt;\&lt;景级目录&gt;）才是场景目录。
      路径认两种写法：<strong>W:\…（盘阵）</strong>与 /DiskArray/…；
      只打开你填的这一个路径，不扫盘。
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
