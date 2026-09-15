// 列表即探：文件加入后应立刻显示 压缩/布局（无需解码）
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    await input.uploadFile(
        'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\gf_like.tif',
        'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\deflate_tile128.tif'
    );
    await new Promise(r => setTimeout(r, 1500));   // 等头部探测完成
    const metas = await page.evaluate(() => {
        return [...document.querySelectorAll('.file-item')].map(el => ({
            name: el.querySelector('.name').textContent,
            meta: el.querySelector('.meta').textContent
        }));
    });
    console.log(JSON.stringify(metas, null, 2));
    const gf = metas.find(m => m.name.includes('gf_like'));
    const dt = metas.find(m => m.name.includes('deflate_tile128'));
    const ok = gf && /无压缩/.test(gf.meta) && /条带/.test(gf.meta)
        && dt && /Deflate/.test(dt.meta) && /瓦片128×128/.test(dt.meta);
    await browser.close();
    console.log(ok ? '\nALL PASS' : '\nFAIL');
    process.exit(ok ? 0 : 1);
})().catch(e => { console.error('TEST ERROR:', e); process.exit(1); });
