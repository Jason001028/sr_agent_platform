/**
 * lib/markdown.ts — Agent 回复的 Markdown → 安全 HTML
 * ------------------------------------------------------------------
 * 只渲染 assistant 消息；用户气泡保持纯文本（用户输入里的 `**` / `<` 不应被当语法）。
 *
 * - marked：GFM（表格/删除线/任务列表）+ breaks（单个换行即 <br>）。聊天场景里模型
 *   常用单换行分行，按 CommonMark 的「两空格换行」会更常见地显示成一段，反而难读。
 * - DOMPurify：模型输出是不可信文本，且 v-html 会执行其中 HTML，必须清洗。白名单只
 *   放 Markdown 会产出的标签/属性；链接统一补 target/rel，避免内网机误导航。
 *
 * 后端 assistant 事件一次性下发最终文本（agent/loop.py），不是逐 token 增量，
 * 因此这里不必处理「半截 Markdown 标记」的流式中间态。
 */
import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({
  gfm: true,
  breaks: true,
});

const SANITIZE_OPTIONS = {
  ALLOWED_TAGS: [
    'a', 'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'img', 'input',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'target', 'rel', 'src', 'alt',
    'type', 'checked', 'disabled', 'align', 'colspan', 'rowspan', 'start',
  ],
  ALLOW_DATA_ATTR: false,
  FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
};

/** 站外链接新开页：清洗后统一补，不让 Markdown 里的裸链接把当前会话导航走。 */
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

/** Markdown 源文本 → 可直接 v-html 的安全 HTML。 */
export function renderMarkdown(source: string): string {
  const raw = marked.parse(source, { async: false }) as string;
  return DOMPurify.sanitize(raw, SANITIZE_OPTIONS);
}
