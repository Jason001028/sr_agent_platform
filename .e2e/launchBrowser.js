// 统一浏览器启动 —— 每次独立临时 profile，根治 Edge "profile 单实例" 导致的 Code:0
// 候选顺序：Chrome 优先。临时 profile 仍挡不住 Edge 与本机正在运行的 Edge 实例握手，
// 表现为 `Failed to launch the browser process: Code: 0`（2026-09-15 实测：Chrome 通过，
// Edge 失败）。要用别的浏览器就设 SR_E2E_BROWSER=/path/to/exe（始终排在最前）。
const puppeteer = require('puppeteer-core');
const os = require('os');
const path = require('path');
const fs = require('fs');

const CANDIDATES = [
  process.env.SR_E2E_BROWSER,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean);

function findBrowser() {
  for (const p of CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error('未找到 Edge/Chrome，请设置 SR_E2E_BROWSER 环境变量');
}

async function launchBrowser(opts = {}) {
  // 关键：每次独立临时 profile，避免与真实浏览器实例握手后 Code:0 退出
  const userDataDir = path.join(os.tmpdir(), 'sr-e2e-' + process.pid + '-' + Date.now());
  const browser = await puppeteer.launch({
    executablePath: opts.executablePath || findBrowser(),
    headless: opts.headless !== false,          // 调试传 {headless:false, slowMo:50}
    slowMo: opts.slowMo || 0,
    timeout: 30000,
    protocolTimeout: 120000,
    args: [
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--no-first-run',
      '--no-default-browser-check',
      `--user-data-dir=${userDataDir}`,
      ...(opts.args || []),
    ],
  });
  browser._userDataDir = userDataDir;
  return browser;
}

async function launchPage(opts = {}) {
  const browser = await launchBrowser(opts);
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('[pageerror] ' + (e.message || e)));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push('[console.error] ' + m.text().slice(0, 300));
  });
  return { browser, page, errors };
}

module.exports = { launchBrowser, launchPage, findBrowser };
