/* tif-viewer.html 掩码集成冒烟（jsdom，无需浏览器）
   - 页面加载（DOMContentLoaded）无异常，__viewer 暴露 genMask/wandSelect/maskGen
   - genMask：注入合成 activeRec → 全分辨率 buildTiff + buildMaskTxt → 两次 downloadBlob
   - wandSelect：注入带像素的画布 → floodSelect→轮廓→ROI 追加
   运行：node .e2e/test-html-mask-smoke.js
   注：本机无头 Edge 无法启动（环境问题），故用 jsdom 做接线级验证；
       真机仍需浏览器实测。 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const HTML = path.resolve(__dirname, '..', 'tif_viewer', 'tif-viewer.html');
let passed = 0;
function ok(name, fn) {
    try { fn(); passed++; console.log('  ok -', name); }
    catch (e) { console.error('  FAIL -', name, '\n    ', e && e.stack || e); process.exitCode = 1; }
}
async function okA(name, fn) {
    try { await fn(); passed++; console.log('  ok -', name); }
    catch (e) { console.error('  FAIL -', name, '\n    ', e && e.stack || e); process.exitCode = 1; }
}

function buildDom() {
    let html = fs.readFileSync(HTML, 'utf8');
    // 去掉外部脚本（vendor + maskgen.js），改为 beforeParse 注入等价全局
    html = html.replace(/<script src="[^"]*"><\/script>/g, '');
    const ctx2d = {
        getImageData: function (x, y, w, h) { return { data: new Uint8ClampedArray(w * h * 4), width: w, height: h }; },
        putImageData: function () {}, clearRect: function () {}, drawImage: function () {},
        fillRect: function () {}, strokeRect: function () {}, fillText: function () {},
        beginPath: function () {}, moveTo: function () {}, lineTo: function () {},
        stroke: function () {}, fill: function () {}, closePath: function () {},
        setLineDash: function () {}, arc: function () {},
    };
    const dom = new JSDOM(html, {
        runScripts: 'dangerously',
        url: 'file:///' + path.dirname(HTML).replace(/\\/g, '/') + '/index.html',
        beforeParse(window) {
            // 画布 2d 上下文桩
            window.HTMLCanvasElement.prototype.getContext = function () { return ctx2d; };
            // 等价于 <script src="vendor/pako.min.js"> 与 <script src="maskgen.js">
            window.pako = require('../tif_viewer/vendor/pako.min.js');
            window.MaskGen = require('../tif_viewer/maskgen.js');
            window.UTIF = {}; window.GeoTIFF = {};
            window.URL.createObjectURL = function () { return 'blob:fake'; };
            window.URL.revokeObjectURL = function () {};
        },
    });
    return dom;
}

(async function main() {
    console.log('== tif-viewer.html 掩码集成冒烟（jsdom） ==');
    const dom = buildDom();
    const w = dom.window;
    const load = new Promise((res, rej) => {
        w.addEventListener('error', function (e) {
            rej(new Error('页面运行时错误: ' + (e && e.message || e)));
        });
        w.addEventListener('DOMContentLoaded', res);
        setTimeout(function () { rej(new Error('DOMContentLoaded 超时')); }, 10000);
    });
    await load;
    w.__domError = null;

    ok('页面加载无异常，__viewer 暴露掩码钩子', () => {
        const v = w.__viewer;
        assert.ok(v, '__viewer 不存在');
        assert.strictEqual(typeof v.genMask, 'function');
        assert.strictEqual(typeof v.wandSelect, 'function');
        assert.ok(v.maskGen, 'maskGen（window.MaskGen）未注入');
        assert.strictEqual(typeof v.maskGen.buildTiff, 'function');
        assert.strictEqual(typeof v.maskGen.buildMaskTxt, 'function');
        // 绘制面板与生成按钮都在 DOM 里
        assert.ok(w.document.getElementById('toolWand'), 'toolWand 缺失');
        assert.ok(w.document.getElementById('wandTol'), 'wandTol 缺失');
        assert.ok(w.document.getElementById('genMaskBtn'), 'genMaskBtn 缺失');
    });

    await okA('genMask 全分辨率直出 → 两次下载（掩码.tif + 掩膜中心点坐标.txt）', async () => {
        // 注入合成 activeRec（thumb 为画布桩，W/H 对应原图尺寸）
        const thumb = w.document.createElement('canvas');
        thumb.width = 30; thumb.height = 25;
        w.activeRec = {
            file: { name: 'smoke_test.tif' },
            W: 20, H: 15,
            thumb: thumb,
            maskRois: [[[5, 3], [15, 3], [15, 12], [5, 12]]]
        };
        const downloads = [];
        w.downloadBlob = function (blob, name) { downloads.push({ blob: blob, name: name }); };
        const done = new Promise((res) => { w.__smokeDone = res; });
        // 在 genMask 的 .then 后即两次下载——downloadBlob 覆盖后难以察觉完成时机，改为轮询
        w.genMask();
        const t0 = Date.now();
        while (downloads.length < 2 && Date.now() - t0 < 30000) {
            await new Promise((r) => setTimeout(r, 50));
        }
        assert.strictEqual(downloads.length, 2, '应下载 2 个文件，实际 ' + downloads.length);
        const tif = downloads.find((d) => d.name.endsWith('.掩码.tif'));
        const txt = downloads.find((d) => d.name.endsWith('掩膜中心点坐标.txt'));
        assert.ok(tif, '缺少 掩码.tif 下载');
        assert.ok(txt, '缺少 掩膜中心点坐标.txt 下载');
        assert.strictEqual(tif.name, 'smoke_test.掩码.tif');
        // TIFF 可被解析（II + magic + 尺寸）
        const buf = Buffer.from(await tif.blob.arrayBuffer());
        assert.strictEqual(buf.readUInt16LE(0), 0x4949, '非小端 TIFF');
        assert.strictEqual(buf.readUInt16LE(2), 42, '非 classic TIFF');
        // txt 内容对齐参考格式
        const txtText = await txt.blob.text();
        assert.ok(txtText.startsWith('＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n'), txtText.slice(0, 60));
        assert.ok(/^\d+,\d+\.\d{2},\d+\.\d{2}$/.test(txtText.split('\r\n')[2]), '数据行格式错误: ' + txtText.split('\r\n')[2]);
        assert.ok(txtText.endsWith('\r\n'), 'txt 应 CRLF 结尾');
    });

    await okA('合并重叠按钮接线：重叠矩形并成一个连通区', async () => {
        assert.ok(w.document.getElementById('toolDel'), 'toolDel 缺失');
        assert.ok(w.document.getElementById('mergeRoi'), 'mergeRoi 缺失');
        const thumb = w.document.createElement('canvas');
        thumb.width = 40; thumb.height = 30;
        w.activeRec = { file: { name: 'merge.tif' }, W: 40, H: 30, thumb: thumb, maskRois: [] };
        w.activeRec.maskRois.push([[10, 8], [20, 8], [20, 18], [10, 18]]);
        w.activeRec.maskRois.push([[15, 13], [25, 13], [25, 23], [15, 23]]);
        await w.mergeRois();
        assert.strictEqual(w.activeRec.maskRois.length, 1, '合并后应 1 个区域，实际 ' + w.activeRec.maskRois.length);
    });

    await okA('删除工具：命中检测 + 点选闪烁后移除', async () => {
        const thumb = w.document.createElement('canvas');
        thumb.width = 40; thumb.height = 30;
        w.activeRec = { file: { name: 'del.tif' }, W: 40, H: 30, thumb: thumb, maskRois: [] };
        w.activeRec.maskRois.push([[10, 8], [20, 8], [20, 18], [10, 18]]);
        w.drawMode = true; w.drawTool = 'del';
        assert.strictEqual(w.hitRoi({ x: 15, y: 15 }), 0, '应命中 ROI 0');
        assert.strictEqual(w.hitRoi({ x: 5, y: 5 }), -1, '区域外不应命中');
        w.delClick({ x: 15, y: 15 });
        await new Promise((res) => setTimeout(res, 300));   // 闪烁 200ms 后异步移除
        assert.strictEqual(w.activeRec.maskRois.length, 0, '删除后应 0 个区域');
        w.drawMode = false;
    });

    await okA('wandSelect 选区转 ROI（合成画布 + 全零像素）', async () => {
        const thumb = w.document.createElement('canvas');
        thumb.width = 40; thumb.height = 30;
        // 让 getImageData 返回恒定色（全 0）→ floodSelect 选中整个窗口 → 轮廓为矩形
        const old = w.HTMLCanvasElement.prototype.getContext;
        w.HTMLCanvasElement.prototype.getContext = function () {
            return {
                getImageData: function (x, y, ww, hh) { return { data: new Uint8ClampedArray(ww * hh * 4), width: ww, height: hh }; },
                putImageData: function () {}, clearRect: function () {}, drawImage: function () {},
                fillRect: function () {}, strokeRect: function () {}, beginPath: function () {},
                moveTo: function () {}, lineTo: function () {}, stroke: function () {}, fill: function () {},
                closePath: function () {}, setLineDash: function () {},
            };
        };
        w.activeRec = { file: { name: 'w.tif' }, W: 40, H: 30, thumb: thumb, maskRois: [] };
        w.document.getElementById('wandTol').value = '20';
        w.wandSelect(20, 15);
        assert.strictEqual(w.activeRec.maskRois.length, 1, '应添加 1 个 ROI');
        const roi = w.activeRec.maskRois[0];
        assert.ok(roi.length >= 3, 'ROI 应 ≥3 顶点，实际 ' + roi.length);
        // 顶点都在缩略图窗口范围内
        for (const p of roi) { assert.ok(p[0] >= 0 && p[0] < 40 && p[1] >= 0 && p[1] < 30, JSON.stringify(p)); }
        w.HTMLCanvasElement.prototype.getContext = old;
    });

    console.log('全部通过：', passed, '项');
})().catch((e) => { console.error('测试崩溃', e); process.exitCode = 1; });
