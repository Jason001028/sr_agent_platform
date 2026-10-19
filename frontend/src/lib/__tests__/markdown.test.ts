// @vitest-environment jsdom
/**
 * lib/markdown.ts — Markdown 渲染与清洗
 * ------------------------------------------------------------------
 * 覆盖聊天里最常见的几类语法，以及 v-html 前的安全边界。
 * 环境单文件切 jsdom：DOMPurify 需要真实 DOM，其它 lib 测试仍是 node。
 */
import { describe, expect, it } from 'vitest';
import { renderMarkdown } from '../markdown.js';

describe('renderMarkdown', () => {
  it('渲染加粗、斜体和行内代码', () => {
    const html = renderMarkdown('这是 **加粗**、*斜体* 和 `code`。');
    expect(html).toContain('<strong>加粗</strong>');
    expect(html).toContain('<em>斜体</em>');
    expect(html).toContain('<code>code</code>');
  });

  it('单个换行保留为 <br>（聊天场景）', () => {
    expect(renderMarkdown('第一行\n第二行')).toContain('<br>');
  });

  it('渲染列表、表格与代码块', () => {
    const html = renderMarkdown([
      '- 甲',
      '- 乙',
      '',
      '| 项 | 值 |',
      '| --- | --- |',
      '| a | 1 |',
      '',
      '```python',
      'print(1)',
      '```',
    ].join('\n'));
    expect(html).toContain('<ul>');
    expect(html).toContain('<table>');
    expect(html).toContain('<pre>');
    expect(html).toContain('print(1)');
  });

  it('剥掉脚本与事件属性', () => {
    const html = renderMarkdown('<script>alert(1)</script><p onclick="x()">正文</p>');
    expect(html).not.toContain('<script');
    expect(html).not.toContain('onclick');
    expect(html).toContain('正文');
  });

  it('拒绝 javascript: 链接并给外链补 target/rel', () => {
    const bad = renderMarkdown('[点我](javascript:alert(1))');
    expect(bad).not.toContain('javascript:');

    const good = renderMarkdown('[示例](https://example.com)');
    expect(good).toContain('href="https://example.com"');
    expect(good).toContain('target="_blank"');
    expect(good).toContain('rel="noopener noreferrer"');
  });
});
