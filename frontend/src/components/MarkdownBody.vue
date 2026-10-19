<script setup lang="ts">
/**
 * MarkdownBody.vue — assistant 气泡内的 Markdown 渲染容器
 * ------------------------------------------------------------------
 * 渲染与清洗都在 lib/markdown.ts（纯函数，vitest 可测）；这里只负责挂 v-html。
 * 样式必须用非 scoped 的 .md-body 前缀：v-html 插入的节点不会带 scoped 属性，
 * scoped 选择器对它们不生效。
 */
import { computed } from 'vue';
import { renderMarkdown } from '../lib/markdown.js';

const props = defineProps<{ source: string }>();

const html = computed(() => renderMarkdown(props.source));
</script>

<template>
  <div class="md-body" v-html="html" />
</template>

<style>
/* 颜色/字体一律取全局令牌，随主题走；只在这个容器内生效。 */
.md-body {
  font-size: inherit;
  line-height: 1.68;
  color: inherit;
  overflow-wrap: anywhere;
}
.md-body > :first-child { margin-top: 0; }
.md-body > :last-child { margin-bottom: 0; }

.md-body p { margin: 0.5em 0; }

.md-body h1, .md-body h2, .md-body h3,
.md-body h4, .md-body h5, .md-body h6 {
  margin: 0.95em 0 0.4em;
  line-height: 1.35;
  color: var(--ink);
  font-weight: 650;
}
.md-body h1 { font-size: 1.32em; }
.md-body h2 { font-size: 1.2em; }
.md-body h3 { font-size: 1.08em; }
.md-body h4, .md-body h5, .md-body h6 { font-size: 1em; }

.md-body strong { color: var(--ink); font-weight: 650; }
.md-body em { font-style: italic; }
.md-body del { color: var(--ink-faint); }

.md-body a {
  color: var(--accent-deep);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.md-body a:hover { color: var(--accent-3); }

.md-body ul, .md-body ol { margin: 0.5em 0; padding-left: 1.5em; }
.md-body li { margin: 0.22em 0; }
.md-body li > p { margin: 0.2em 0; }
.md-body li::marker { color: var(--ink-sub); }

.md-body code {
  font-family: var(--font-mono);
  font-size: 0.9em;
  background: rgba(18, 14, 38, 0.055);
  border-radius: 4px;
  padding: 1px 5px;
}
.md-body pre {
  margin: 0.7em 0;
  padding: 10px 12px;
  background: var(--surface-3);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow-x: auto;
}
.md-body pre code {
  display: block;
  background: none;
  padding: 0;
  font-size: 0.88em;
  line-height: 1.6;
  white-space: pre;
}

.md-body blockquote {
  margin: 0.65em 0;
  padding: 0.15em 0 0.15em 0.9em;
  border-left: 3px solid var(--accent-1);
  color: var(--ink-sub);
}
.md-body hr { border: none; border-top: 1px solid var(--line); margin: 1em 0; }

/* 宽表横向滚动；display:block 是让 table 自身可滚的常见做法 */
.md-body table {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  margin: 0.7em 0;
  border-collapse: collapse;
  font-size: 0.95em;
}
.md-body th, .md-body td {
  border: 1px solid var(--line);
  padding: 5px 9px;
  text-align: left;
  vertical-align: top;
}
.md-body th { background: var(--surface-2); color: var(--ink); font-weight: 600; }
.md-body tr:nth-child(even) td { background: rgba(246, 245, 250, 0.6); }

.md-body img { max-width: 100%; border-radius: 8px; }
.md-body input[type='checkbox'] { margin-right: 0.35em; vertical-align: middle; }
</style>
