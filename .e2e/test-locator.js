// 像素定位：输入坐标 → 视图居中跳转 + 中心出现红叉 + 约 7 秒后消失；越界提示
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const RGB = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\rgb8.tif';

async function centerPixel(page) {
    return page.evaluate(() => {
        const cw = document.getElementById('viewCanvas').width, ch = document.getElementById('viewCanvas').height;
        const d = document.getElementById('viewCanvas').getContext('2d').getImageData(Math.floor(cw / 2), Math.floor(ch / 2), 1, 1).data;
        return { r: d[0], g: d[1], b: d[2], cw, ch };
    });
}
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    await input.uploadFile(RGB);
    for (let i = 0; i < 80; i++) {
        const st = await page.evaluate(() => window.activeRec ? window.activeRec.status : '');
        if (st.indexOf('完成') === 0) break;
        await new Promise(r => setTimeout(r, 100));
    }
    // 定位到 (512,512)
    await page.type('#locX', '512');
    await page.type('#locY', '512');
    await page.click('#locBtn');
    await new Promise(r => setTimeout(r, 200));
    const after = await page.evaluate(() => {
        const t = window.activeRec.thumb;
        return { ox: window.view.ox, oy: window.view.oy, scale: window.view.scale,
                 tx: window.marker && window.marker.tx, ty: window.marker && window.marker.ty,
                 thumbW: t.width, W: window.activeRec.W };
    });
    const cp = await centerPixel(page);
    console.log('after locate:', JSON.stringify(after), '| center px:', JSON.stringify(cp));
    const expTx = 512 * (after.thumbW / after.W);   // 1024*?  -> rgb8 ps=1 → 512
    const okJump = Math.abs(after.scale - 1) < 1e-6 && Math.abs(after.tx - expTx) < 1e-3
        && Math.abs(cp.cw / 2 - (after.ox + after.tx * after.scale)) < 1e-3;
    const okCross = cp.r > 200 && cp.g < 100;
    console.log('okJump', okJump, 'okCross', okCross);

    // 7.5 秒后红叉消失
    await new Promise(r => setTimeout(r, 7600));
    const expired = await page.evaluate(() => ({ marker: window.marker }));
    const cp2 = await centerPixel(page);
    console.log('after 7.6s marker:', JSON.stringify(expired), '| center px:', JSON.stringify(cp2));
    const okExpire = expired.marker === null && !(cp2.r > 200 && cp2.g < 100);

    // 越界提示
    await page.evaluate(() => { document.getElementById('locX').value = '99999'; document.getElementById('locY').value = '1'; });
    await page.click('#locBtn');
    await new Promise(r => setTimeout(r, 120));
    const errVisible = await page.evaluate(() => document.getElementById('errBox').style.display !== 'none');
    const errText = await page.evaluate(() => document.getElementById('errBox').textContent);
    console.log('out-of-range err visible:', errVisible, '| text:', errText.slice(0, 30));
    const okErr = errVisible && /超出范围/.test(errText);

    await browser.close();
    const ok = okJump && okCross && okExpire && okErr && errors.length === 0;
    console.log(ok ? '\nALL PASS' : '\nFAIL');
    process.exit(ok ? 0 : 1);
})().catch(e => { console.error('TEST ERROR:', e); process.exit(1); });
