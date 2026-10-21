#!/usr/bin/env node
/**
 * check-terms.mjs — 术语检查（规则见 docs/conventions/terminology-conventions.md）
 *
 * 默认只扫 frontend/src。加 `--all` 连同 backend/、.e2e/、docs/ 一起扫。
 * 只收「机器能判准」的词：§5 里「落点 / 钉 / 赢」一类要结合语境，仍由人工判。
 *
 * 退出码：0 = 通过；1 = 有命中或规范文件缺失（逐条列出 file:line）。
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ALL = process.argv.includes('--all');

const ROOTS = ['frontend/src', ...(ALL ? ['backend', '.e2e', 'docs'] : [])];

const EXTS = new Set([
  '.ts', '.tsx', '.js', '.mjs', '.cjs', '.vue', '.css', '.html', '.py', '.md',
]);

/** 第三方 / 构建产物 / 规范明令禁改的目录 */
const SKIP_DIRS = new Set([
  'node_modules', 'dist', '__pycache__', '.git',
  'agent-book', 'deepseek-harness-master',
]);

/** 这些文件本身就要引用禁用词（规则定义、导航摘要），不参与扫描 */
const SKIP_FILES = new Set([
  'docs/conventions/terminology-conventions.md',
  'docs/README.md',
  'CLAUDE.md',
]);

const RULES = [
  {
    label: '禁用词「烘焙」→ 写「生成预览 / 预览 JPG / 预览生成规则 / 缩放档位」',
    re: /烘焙/g,
  },
  {
    label: '禁用词「烤」→ 写「生成」',
    re: /烤/g,
  },
  {
    label: '「规则戳」→ 写「规则签名」',
    re: /规则戳/g,
  },
  {
    label: '「合流」→ 写「汇合」',
    re: /合流/g,
  },
  {
    label: '「裸 .tif」→ 写「无伴随件的 .tif」',
    re: /裸\s*`?\.tiff?`?/g,
  },
  {
    label: '「掩膜」→ 写「掩码」（盘上真实文件名「掩膜中心点坐标.txt」除外）',
    re: /掩膜/g,
    allowed: (line) => /掩膜中心点坐标|掩膜中心点|掩膜编号/.test(line),
  },
];

function walk(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) {
      if (!SKIP_DIRS.has(e.name)) walk(full, out);
      continue;
    }
    if (!e.isFile()) continue;
    if (!EXTS.has(e.name.slice(e.name.lastIndexOf('.')))) continue;
    out.push(full);
  }
}

function rel(p) {
  return relative(ROOT, p).split(sep).join('/');
}

const errors = [];

/* 规范文件本身也要防丢：合并误删时这里先红。 */
const DOC_REL = 'docs/conventions/terminology-conventions.md';
const DOC = join(ROOT, DOC_REL);
if (existsSync(join(ROOT, 'docs'))) {
  if (!existsSync(DOC)) {
    errors.push(`规范文件缺失：${DOC_REL}`);
  } else {
    const doc = readFileSync(DOC, 'utf8');
    for (const key of ['生成预览', '禁用词']) {
      if (!doc.includes(key)) errors.push(`规范文件内容异常：${DOC_REL} 里找不到「${key}」`);
    }
  }
}

const files = [];
for (const r of ROOTS) {
  const abs = join(ROOT, r);
  if (existsSync(abs)) walk(abs, files);
}

let scanned = 0;
for (const file of files) {
  const r = rel(file);
  if (SKIP_FILES.has(r)) continue;
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch {
    continue;
  }
  scanned += 1;
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    for (const rule of RULES) {
      rule.re.lastIndex = 0;
      const m = rule.re.exec(line);
      if (!m) continue;
      if (rule.allowed && rule.allowed(line)) continue;
      const col = m.index + 1;
      const snippet = line.trim().length > 100 ? line.trim().slice(0, 100) + '…' : line.trim();
      errors.push(`${r}:${i + 1}:${col}  ${rule.label}\n      ${snippet}`);
      break; // 一行同一类只报一次
    }
  }
}

if (errors.length) {
  console.error(`术语检查未通过：${errors.length} 处\n`);
  for (const e of errors) console.error('  ' + e);
  console.error('\n规则：' + DOC_REL);
  console.error('改完再跑：npm run check:terms');
  process.exit(1);
}

console.log(`术语检查通过（扫描 ${scanned} 个文件，范围：${ROOTS.join('、')}）`);
