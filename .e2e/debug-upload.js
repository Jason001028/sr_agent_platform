// 调试：headless 里 uploadFile 后 File 是否可读（size / arrayBuffer）
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const PATHS = [
    'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\rgb8.tif',
    'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\f32_gray.tif',
];
(async () => {
    for (const p of PATHS) {
        const st = fs.statSync(p);
        console.log('node sees:', p, 'size=', st.size);
    }
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    for (const p of PATHS) {
        await input.uploadFile(p);
        const info = await page.evaluate(() => {
            const f = document.getElementById('fileInput').files;
            if (!f.length) return { n: 0 };
            const file = f[f.length - 1];
            try {
                const size = file.size;
                // 尝试读开头 64 字节，若抛错说明 blob 不可读
                let readErr = null, byteLen = 0;
                file.slice(0, 64).arrayBuffer().then(b => { byteLen = b.byteLength; }).catch(e => { readErr = e.message; });
                return new Promise(res => setTimeout(() => res({ n: f.length, name: file.name, size, readErr, byteLen }), 500));
            } catch (e) {
                return { n: f.length, name: file.name, size: '?', err: e.message };
            }
        });
        console.log('uploaded:', p.split('\\').pop(), JSON.stringify(info));
    }
    await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
