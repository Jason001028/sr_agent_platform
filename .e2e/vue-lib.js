// vue-lib.js — 共享测试基建：无头 Edge + 临时 profile + 本地静态服务（顶替 nginx）
// + SPA fallback。每个测试自建服务器（多端口不冲突）；场景 http 测试另起 uvicorn。
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const puppeteer = require('puppeteer-core');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.tif': 'image/tiff', '.json': 'application/json',
  '.ico': 'image/x-icon',
  '.map': 'application/json',
};

async function launchBrowser() {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'srv-e2e-'));
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu',
      '--allow-file-access-from-files', '--disable-dev-shm-usage'],
    userDataDir,
  });
  return browser;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// 静态服务：dist（SPA fallback → index.html）+ 可选 /disk-array/<rel> → scenesRoot。
// 返回 { url, port, close }。幂等幂等。
function startStaticServer({ dist, scenesRoot, port = 0 }) {
  const distDir = path.resolve(dist);
  const server = http.createServer((req, res) => {
    const u = decodeURIComponent(req.url.split('?')[0]);
    let file;
    if (scenesRoot && u.startsWith('/disk-array/')) {
      const rel = u.slice('/disk-array/'.length);
      file = path.resolve(scenesRoot, rel);
      // 防穿越：必须落在 scenesRoot 内
      if (!file.startsWith(path.resolve(scenesRoot) + path.sep) && file !== path.resolve(scenesRoot)) {
        res.writeHead(403); res.end('forbidden'); return;
      }
    } else {
      file = path.join(distDir, u === '/' ? 'index.html' : u);
    }
    fs.readFile(file, (err, data) => {
      if (err) {
        // SPA fallback：非 /disk-array、且非真实资源 → index.html（history 路由）
        if (!u.startsWith('/disk-array/')) {
          return fs.readFile(path.join(distDir, 'index.html'), (e2, html) => {
            if (e2) { res.writeHead(404); res.end('not found'); return; }
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            res.end(html);
          });
        }
        res.writeHead(404); res.end('not found'); return;
      }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise(resolve => {
    server.listen(port, '127.0.0.1', () => {
      const { port: p } = server.address();
      resolve({ url: `http://127.0.0.1:${p}`, port: p, close: () => server.close() });
    });
  });
}

// 反复 evaluate 直到谓词真或超时
async function waitFor(page, fn, timeoutMs = 30000, step = 120) {
  const dl = Date.now() + timeoutMs;
  for (;;) {
    let v = null;
    try { v = await page.evaluate(fn); } catch (e) { v = null; }
    if (v) return v;
    if (Date.now() > dl) throw new Error('waitFor timeout: ' + fn.toString().slice(0, 140));
    await sleep(step);
  }
}

module.exports = { EDGE, launchBrowser, startStaticServer, sleep, waitFor, MIME };
