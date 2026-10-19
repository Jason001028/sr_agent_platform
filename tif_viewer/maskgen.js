/* maskgen.js — 掩码生成纯函数（tif_viewer 专用）
   ---------------------------------------------------------------
   - 栅格化：把 ROI 多边形（原图像素坐标）逐行扫描线填充成 0/255 二值图，
     填充规则与 PIL ImageDraw.polygon 逐像素一致（经验验证）：
     像素 (c,r) 填充 ⟺ 整数点 (c,r) 在多边形内或边界上（even-odd、闭区间）。
   - 掩码文档：输出「掩膜中心点坐标」格式 txt（对齐 JL1KF02B03_..._mask.txt 参考）。
   - TIFF：写 classic little-endian、8bit 灰度、Adobe Deflate 压缩、单条带 TIFF
     （GDAL/PIL/cv2 均可读；SR 的 util.read_img 用 GDAL）。
   - 魔法棒：容差 + 边缘屏障的连通区域生长（PS 风格），配套轮廓提取/洞填充/简化。

   浏览器：<script src="maskgen.js"> → window.MaskGen（依赖全局 pako）
   Node  ：require('./maskgen.js')（可自取 vendor/pako 或全局注入）
   --------------------------------------------------------------- */
(function (root, factory) {
    if (typeof module === 'object' && typeof module.exports === 'object') {
        module.exports = factory();
    } else {
        root.MaskGen = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    /* 取 pako：浏览器全局 → 本地 vendor → npm 包 */
    function pakoLib() {
        if (typeof pako !== 'undefined') return pako;
        if (typeof require === 'function') {
            try { return require('./vendor/pako.min.js'); } catch (e) {}
            try { return require('pako'); } catch (e) {}
        }
        return null;
    }

    /* ---------------- 多边形质心（鞋带公式，退化→包围盒中心） ----------------
       返回值 [cx, cy] 为浮点，坐标系与原多边形一致（x=列、y=行）。 */
    function polygonCentroid(pts) {
        var n = pts.length;
        if (n < 3) return bboxCenter(pts);
        var a2 = 0, cx = 0, cy = 0;
        for (var i = 0; i < n; i++) {
            var j = (i + 1) % n;
            var x0 = pts[i][0], y0 = pts[i][1], x1 = pts[j][0], y1 = pts[j][1];
            var cross = x0 * y1 - x1 * y0;
            a2 += cross;
            cx += (x0 + x1) * cross;
            cy += (y0 + y1) * cross;
        }
        if (a2 === 0) return bboxCenter(pts);
        return [cx / (3 * a2), cy / (3 * a2)];
    }
    function bboxCenter(pts) {
        var minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
        for (var k = 0; k < pts.length; k++) {
            if (pts[k][0] < minx) minx = pts[k][0];
            if (pts[k][0] > maxx) maxx = pts[k][0];
            if (pts[k][1] < miny) miny = pts[k][1];
            if (pts[k][1] > maxy) maxy = pts[k][1];
        }
        return [(minx + maxx) / 2, (miny + maxy) / 2];
    }

    /* ---------------- 掩膜中心点 txt（对齐参考格式） ----------------
       UTF-8、无 BOM、\r\n；头两行全角字符照抄参考文件；
       数据行：掩膜编号,X坐标,Y坐标（质心，保留 2 位小数）。
       polygons 各顶点为原图像素坐标（x=列、y=行）。 */
    var MASK_TXT_HEADER = '＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n';
    function buildMaskTxt(width, height, polygons) {
        var out = [MASK_TXT_HEADER];
        for (var i = 0; i < polygons.length; i++) {
            var c = polygonCentroid(polygons[i]);
            out.push((i + 1) + ',' + c[0].toFixed(2) + ',' + c[1].toFixed(2) + '\r\n');
        }
        return out.join('');
    }

    /* ---------------- 扫描线栅格化（逐行 → emitRow(row, r)） ----------------
       行 r 取整数扫描线 y=r：每条非水平边若闭区间含 y=r 求交；排序成对得闭区间
       [a,b]；填充整数列 c ∈ [ceil(a), floor(b)]。与 Pillow ImageDraw.polygon 一致。
       可选 opts.feed(chunk, rowCount)：以批次喂给下游（如 deflate），返回 Promise 时每批让出主线程。
       opts.batch 默认 128 行/批；opts.onProgress(f) 每批回调。 */
    var EPS = 1e-9;
    /* 行 r 的多边形与扫描线 y=r 的闭区间并集（几何正确的 even-odd + 边界含入）。
       每个多边形**单独**算区间（even-odd 只在单多边形内有效：重叠多边形若合算
       even-odd，重叠区被两条边界穿过判为偶→漏），再对所有区间做**并集**
       （与后端逐多边形填充再 OR 的 union 语义一致）。
       单多边形内：收集边界与 y=r 的所有交点事件 + 水平边跨距，排序去重后逐间隙
       用半数射线交叉计数判内外，再并入事件点（边界点）与水平跨距。
       该规则在「扫描线恰过顶点」时仍保持正确（菱形跨距不坍缩成点）。 */
    function rowIntervals(polygons, r) {
        var all = [];
        for (var p = 0; p < polygons.length; p++) {
            var poly = polygons[p];
            var n = poly.length;
            if (n < 3) continue;                       // 退化多边形不填充（与后端一致）
            var events = [], horiz = [], cross = [];
            for (var i = 0; i < n; i++) {
                var j = (i + 1) % n;
                var x0 = poly[i][0], y0 = poly[i][1], x1 = poly[j][0], y1 = poly[j][1];
                if (y0 === y1) {                       // 水平边恰在扫描线上 → 整段边界含入
                    if (y0 === r) horiz.push([x0 < x1 ? x0 : x1, x0 < x1 ? x1 : x0]);
                    continue;
                }
                var lo = y0 < y1 ? y0 : y1, hi = y0 < y1 ? y1 : y0;
                if (r >= lo && r <= hi) {
                    events.push(x0 + (r - y0) * (x1 - x0) / (y1 - y0));
                }
                if ((y0 <= r && r < y1) || (y1 <= r && r < y0)) {   // 半数规则：下半端点计入、上半端点不计
                    cross.push(x0 + (r - y0) * (x1 - x0) / (y1 - y0));
                }
            }
            if (!events.length && !horiz.length) continue;
            events.sort(function (a, b) { return a - b; });
            var ev = [];
            for (var e = 0; e < events.length; e++) {
                if (!ev.length || events[e] - ev[ev.length - 1] > 1e-6) ev.push(events[e]);
            }
            for (var g = 0; g + 1 < ev.length; g++) {
                var a = ev[g], b = ev[g + 1];
                if (b - a <= 1e-6) continue;
                var mid = (a + b) / 2;
                var cnt = 0;
                for (var c = 0; c < cross.length; c++) if (cross[c] < mid - 1e-9) cnt++;
                if (cnt % 2 === 1) all.push([a, b]);
            }
            for (var q = 0; q < ev.length; q++) all.push([ev[q], ev[q]]);   // 事件点（边界点）含入
            for (var h = 0; h < horiz.length; h++) all.push(horiz[h]);       // 水平跨距含入
        }
        if (!all.length) return [];
        all.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
        var merged = [all[0].slice()];
        for (var m = 1; m < all.length; m++) {
            var last = merged[merged.length - 1];
            if (all[m][0] <= last[1] + 1e-6) {          // 相接或重叠 → 合并
                if (all[m][1] > last[1]) last[1] = all[m][1];
            } else merged.push(all[m].slice());
        }
        return merged;
    }
    /* 逐行扫描线栅格化：行 r 填充整数列 c ∈ [ceil(a), floor(b)]（a,b 来自 rowIntervals）。
       可选 opts.feed(chunk, rowCount)：以批次喂给下游；返回 Promise 时每批让出主线程。
       opts.batch 默认 128 行/批；opts.onProgress(f) 每批回调。 */
    function rasterRows(W, H, polygons, opts) {
        opts = opts || {};
        var batch = opts.batch || 128;
        var feed = opts.feed || null;
        var row = new Uint8Array(W);
        var acc = new Uint8Array(batch * W);
        var inB = 0, bDone = 0, r = 0;
        var totalB = Math.max(1, Math.ceil(H / batch));
        function step() {
            for (; r < H; r++) {
                var gaps = rowIntervals(polygons, r);
                row.fill(0);
                for (var g = 0; g < gaps.length; g++) {
                    var c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
                    var c1 = Math.min(W - 1, Math.floor(gaps[g][1] + EPS));
                    if (c0 <= c1) row.fill(255, c0, c1 + 1);
                }
                acc.set(row, inB * W);
                inB++;
                if (inB === batch) {
                    inB = 0;
                    var chunk = acc;
                    acc = new Uint8Array(batch * W);
                    bDone++;
                    if (opts.onProgress) opts.onProgress(bDone / totalB);
                    if (feed) {
                        var pr = feed(chunk, batch);
                        if (pr && typeof pr.then === 'function') return pr.then(step);
                    }
                }
            }
            if (inB > 0) {
                var tail = acc.subarray(0, inB * W);
                inB = 0;
                bDone++;
                if (opts.onProgress) opts.onProgress(bDone / totalB);
                if (feed) {
                    var pr2 = feed(tail, batch);
                    if (pr2 && typeof pr2.then === 'function') return pr2;
                }
            }
            return Promise.resolve();
        }
        return step();
    }

    /* ---------------- classic TIFF（小端、8bit 灰度、Adobe Deflate、单条带） ----------------
       polygons 顶点为原图像素坐标；返回 Promise<Uint8Array>。
       opts.level 压缩等级（默认 6）；opts.onProgress(f) 按行批回调。 */
    function buildTiff(W, H, polygons, opts) {
        opts = opts || {};
        var pz = opts.pako || pakoLib();
        if (!pz || typeof pz.Deflate !== 'function') {
            return Promise.reject(new Error('pako 不可用，无法压缩掩码 TIFF'));
        }
        var defl = new pz.Deflate({ level: opts.level || 6 });
        var feed = function (chunk) {
            defl.push(chunk, false);
            if (opts.onProgress) return new Promise(function (res) { setTimeout(res, 0); }); // 让 UI 有机会重绘
        };
        return rasterRows(W, H, polygons, {
            batch: opts.batch || 128,
            feed: feed,
            onProgress: opts.onProgress
        }).then(function () {
            defl.push(new Uint8Array(0), true);   // Z_FINISH：补尾块 + adler32
            return assembleTiff(W, H, defl.result);
        });
    }
    function assembleTiff(W, H, comp) {
        var N = 11;
        var dataOff = 8 + 2 + N * 12 + 4;
        var buf = new ArrayBuffer(dataOff + comp.length);
        var v = new DataView(buf);
        var off = 0;
        v.setUint16(off, 0x4949, true); off += 2;   // "II" 小端
        v.setUint16(off, 42, true); off += 2;        // classic TIFF
        v.setUint32(off, 8, true); off += 4;         // IFD 偏移
        v.setUint16(off, N, true); off += 2;
        function entry(tag, type, count, value) {
            v.setUint16(off, tag, true); off += 2;
            v.setUint16(off, type, true); off += 2;
            v.setUint32(off, count, true); off += 4;
            if (type === 3) v.setUint16(off, value, true);
            else v.setUint32(off, value, true);
            off += 4;
        }
        entry(256, 4, 1, W);                 // ImageWidth
        entry(257, 4, 1, H);                 // ImageLength
        entry(258, 3, 1, 8);                 // BitsPerSample
        entry(259, 3, 1, 8);                 // Compression = 8 (Adobe Deflate)
        entry(262, 3, 1, 1);                 // Photometric = BlackIsZero
        entry(273, 4, 1, dataOff);           // StripOffsets
        entry(277, 3, 1, 1);                 // SamplesPerPixel
        entry(278, 4, 1, H);                 // RowsPerStrip
        entry(279, 4, 1, comp.length);       // StripByteCounts
        entry(284, 3, 1, 1);                 // PlanarConfiguration = chunky
        entry(339, 3, 1, 1);                 // SampleFormat = unsigned int
        v.setUint32(off, 0, true); off += 4; // 下一 IFD = 无
        new Uint8Array(buf, dataOff).set(comp);
        return new Uint8Array(buf);
    }

    /* ---------------- 魔法棒：容差 + 边缘屏障 连通区域生长（自适应） ----------------
       rgba 为显示用 RGBA（Uint8ClampedArray，w*h*4）；(sx,sy) 种子像素；
       tol 用户容差（窗口下限）；edgeThresh 边缘梯度阈值（超过视为屏障）。
       选区判定为「自适应区域生长」：7×7 种子窗初始化运行均值/方差，窗口 =
       max(tol, K*spread)，spread 为已选区像素相对运行均值的逐像素 RGB 距离
       RMS（除以 √cnt，与距离同单位，消除 per-channel σ 与 RGB 欧氏距离的 √3
       维度错配）。窗口随选区增量重算、单调不减 —— 有纹理的区域能一次点选填
       满，而非旧逻辑「固定 tol 对比种子像素」只选到种子邻域小椭圆。
       返回 Uint8Array(w*h) 二值选区（1=选中）。行扫描泛洪，带 8e6 像素安全上限。 */
    var WAND_MAX_PX = 8000000;
    var WAND_K = 2.5;
    function floodSelect(w, h, rgba, sx, sy, tol, edgeThresh) {
        var n = w * h;
        var sel = new Uint8Array(n);
        if (sx < 0 || sx >= w || sy < 0 || sy >= h) return sel;
        var eth2 = edgeThresh * edgeThresh;
        // 边缘屏障 =「穿越式」：相邻两像素灰差 > edgeThresh 才阻断跨越，不拦同侧像素。
        // edgeR[i]=1 表示 (i, i+1) 之间有强边缘；edgeD[i]=1 表示 (i, i+w) 之间有强边缘。
        var edgeR = new Uint8Array(n), edgeD = new Uint8Array(n);
        for (var y = 0; y < h; y++) {
            var base = y * w;
            for (var x = 0; x < w; x++) {
                var i = base + x;
                var g = rgba[i * 4] * 0.299 + rgba[i * 4 + 1] * 0.587 + rgba[i * 4 + 2] * 0.114;
                if (x + 1 < w) {
                    var gr = rgba[(i + 1) * 4] * 0.299 + rgba[(i + 1) * 4 + 1] * 0.587 + rgba[(i + 1) * 4 + 2] * 0.114;
                    var d = g - gr;
                    if (d * d > eth2) edgeR[i] = 1;
                }
                if (y + 1 < h) {
                    var gd = rgba[(i + w) * 4] * 0.299 + rgba[(i + w) * 4 + 1] * 0.587 + rgba[(i + w) * 4 + 2] * 0.114;
                    var d2 = g - gd;
                    if (d2 * d2 > eth2) edgeD[i] = 1;
                }
            }
        }
        // 7×7 种子窗初始化运行统计（避免以单像素为均值时的过早偏置）
        var sumR = 0, sumG = 0, sumB = 0, sqR = 0, sqG = 0, sqB = 0, cnt = 0;
        for (var dy = -3; dy <= 3; dy++) {
            var py = sy + dy;
            if (py < 0 || py >= h) continue;
            for (var dx = -3; dx <= 3; dx++) {
                var px = sx + dx;
                if (px < 0 || px >= w) continue;
                var o = (py * w + px) * 4;
                var r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
                sumR += r; sumG += g; sumB += b;
                sqR += r * r; sqG += g * g; sqB += b * b; cnt++;
            }
        }
        var muR = sumR / cnt, muG = sumG / cnt, muB = sumB / cnt;
        function spread() {
            var vr = Math.max(0, sqR - cnt * muR * muR);
            var vg = Math.max(0, sqG - cnt * muG * muG);
            var vb = Math.max(0, sqB - cnt * muB * muB);
            return Math.sqrt(vr + vg + vb) / Math.sqrt(cnt);
        }
        var window = Math.max(tol, WAND_K * spread());
        var win2 = window * window, since = 0;
        function addStat(i) {
            var o = i * 4;
            var r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
            sumR += r; sumG += g; sumB += b;
            sqR += r * r; sqG += g * g; sqB += b * b; cnt++;
            muR = sumR / cnt; muG = sumG / cnt; muB = sumB / cnt;
            if ((++since & 255) === 0) {   // 每 256 像素重算一次窗口，单调不减
                var nw = Math.max(tol, WAND_K * spread());
                if (nw > window) { window = nw; win2 = window * window; }
            }
        }
        function colorOk(i) {
            var o = i * 4;
            var dr = rgba[o] - muR, dg = rgba[o + 1] - muG, db = rgba[o + 2] - muB;
            return dr * dr + dg * dg + db * db <= win2;
        }
        sel[sy * w + sx] = 1;
        var stack = [sx, sy];
        var count = 0;
        while (stack.length) {
            var yy = stack.pop(), xx = stack.pop();
            var xl = xx;
            while (xl > 0) {
                var i2 = yy * w + (xl - 1);
                if (!sel[i2] && !edgeR[i2] && colorOk(i2)) { sel[i2] = 1; addStat(i2); xl--; }
                else break;
            }
            var xr = xx;
            while (xr < w - 1) {
                var i3 = yy * w + (xr + 1);
                if (!sel[i3] && !edgeR[i3 - 1] && colorOk(i3)) { sel[i3] = 1; addStat(i3); xr++; }
                else break;
            }
            for (var c = xl; c <= xr; c++) {
                count++;
                if (count > WAND_MAX_PX) return sel;   // 安全上限
                var idx = yy * w + c;
                if (yy > 0) {
                    var up = (yy - 1) * w + c;
                    if (!sel[up] && !edgeD[idx - w] && colorOk(up)) { sel[up] = 1; addStat(up); stack.push(c, yy - 1); }
                }
                if (yy < h - 1) {
                    var dn = (yy + 1) * w + c;
                    if (!sel[dn] && !edgeD[idx] && colorOk(dn)) { sel[dn] = 1; addStat(dn); stack.push(c, yy + 1); }
                }
            }
        }
        return sel;
    }

    /* ---------------- 洞填充：把无法从边界背景到达的 0 像素填成 1 ----------------
       使选区单连通（掩码=多边形并集模型表达不了洞）。原地返回新数组。 */
    function fillRegionHoles(mask, w, h) {
        var n = w * h;
        var out = new Uint8Array(n);
        var stack = [];
        for (var x = 0; x < w; x++) {
            if (mask[x] === 0 && !out[x]) { out[x] = 1; stack.push(x); }
            var b = (h - 1) * w + x;
            if (mask[b] === 0 && !out[b]) { out[b] = 1; stack.push(b); }
        }
        for (var y = 0; y < h; y++) {
            var l = y * w, r = y * w + w - 1;
            if (mask[l] === 0 && !out[l]) { out[l] = 1; stack.push(l); }
            if (mask[r] === 0 && !out[r]) { out[r] = 1; stack.push(r); }
        }
        while (stack.length) {
            var i = stack.pop();
            var cx = i % w, cy = (i / w) | 0;
            if (cx > 0) { var a = i - 1; if (mask[a] === 0 && !out[a]) { out[a] = 1; stack.push(a); } }
            if (cx < w - 1) { var d = i + 1; if (mask[d] === 0 && !out[d]) { out[d] = 1; stack.push(d); } }
            if (cy > 0) { var u = i - w; if (mask[u] === 0 && !out[u]) { out[u] = 1; stack.push(u); } }
            if (cy < h - 1) { var dn = i + w; if (mask[dn] === 0 && !out[dn]) { out[dn] = 1; stack.push(dn); } }
        }
        var res = mask.slice();
        for (var p = 0; p < n; p++) if (mask[p] === 0 && !out[p]) res[p] = 1;
        return res;
    }

    /* ---------------- 外轮廓提取（Moore 邻居追踪） ----------------
       返回边界像素中心点序（处理坐标）。要求区域四周有 ≥1px 背景以保证闭环。 */
    function traceContour(mask, w, h) {
        var sx = -1, sy = -1;
        for (var y = 0; y < h && sx < 0; y++) {
            for (var x = 0; x < w; x++) {
                if (mask[y * w + x]) { sx = x; sy = y; break; }
            }
        }
        if (sx < 0) return [];
        var dx = [1, 1, 0, -1, -1, -1, 0, 1], dy = [0, 1, 1, 1, 0, -1, -1, -1];
        var pts = [];
        var bx = sx, by = sy, backDir = 4;   // 起点为最左上像素，西侧必为背景 → 回溯方向 W
        var guard = 0, MAXG = w * h * 4;
        while (true) {
            pts.push([bx, by]);
            var found = false;
            for (var s = 1; s <= 8; s++) {
                var d = (backDir + s) % 8;
                var nx = bx + dx[d], ny = by + dy[d];
                if (nx >= 0 && nx < w && ny >= 0 && ny < h && mask[ny * w + nx]) {
                    bx = nx; by = ny;
                    backDir = (d + 4) % 8;
                    found = true;
                    break;
                }
            }
            if (!found) break;
            if (++guard > MAXG) break;
            if (bx === sx && by === sy) break;
        }
        return pts;
    }

    /* ---------------- 折线简化：RDP + 去共线/去重 ----------------
       tol 距离阈值（与折线同单位）。返回顶点数组（不含闭合重复点）。 */
    function simplifyPoly(pts, tol) {
        var n = pts.length;
        if (n <= 2) return pts.slice();
        var clean = [pts[0]];
        for (var i = 1; i < n; i++) {
            var p = pts[i], q = clean[clean.length - 1];
            if (p[0] !== q[0] || p[1] !== q[1]) clean.push(p);
        }
        if (clean.length > 1) {
            var f = clean[0], l = clean[clean.length - 1];
            if (f[0] === l[0] && f[1] === l[1]) clean.pop();
        }
        if (clean.length <= 2) return clean;
        var idx = rdp(clean, tol);
        var out = [];
        for (var k = 0; k < idx.length; k++) out.push(clean[idx[k]]);
        // 去共线点
        var res = [];
        for (var m = 0; m < out.length; m++) {
            var a = out[(m + out.length - 1) % out.length], b = out[m], c = out[(m + 1) % out.length];
            var cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
            if (Math.abs(cross) > 1e-9) res.push(b);
        }
        return res.length >= 3 ? res : out;
    }
    function rdp(pts, tol) {
        var n = pts.length;
        var keep = new Uint8Array(n);
        keep[0] = keep[n - 1] = 1;
        var stack = [[0, n - 1]];
        var tol2 = tol * tol;
        while (stack.length) {
            var seg = stack.pop();
            var a = seg[0], b = seg[1];
            var maxD = -1, idx = -1;
            for (var i = a + 1; i < b; i++) {
                var d = segDistSq(pts[a], pts[b], pts[i]);
                if (d > maxD) { maxD = d; idx = i; }
            }
            if (maxD > tol2 && idx > 0) {
                keep[idx] = 1;
                stack.push([a, idx], [idx, b]);
            }
        }
        var out = [];
        for (var k = 0; k < n; k++) if (keep[k]) out.push(k);
        return out;
    }
    function segDistSq(a, b, p) {
        var dx = b[0] - a[0], dy = b[1] - a[1];
        var len2 = dx * dx + dy * dy;
        if (len2 === 0) {
            var d0 = p[0] - a[0], d1 = p[1] - a[1];
            return d0 * d0 + d1 * d1;
        }
        var t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
        t = Math.max(0, Math.min(1, t));
        var qx = a[0] + t * dx, qy = a[1] + t * dy;
        var rx = p[0] - qx, ry = p[1] - qy;
        return rx * rx + ry * ry;
    }

    /* ---------------- 光栅并集 + 连通分量合并（合并重叠/贴边区域） ----------------
       掩码 .tif 的栅格化语义本就是「重叠/贴边即并集」（见 rowIntervals）。这里把
       多个 ROI 光栅化成并集 mask，按连通分量重追踪轮廓：重叠/贴边的区域并成一个
       连通多边形、真正分开的区域各自保留 —— 供「合并重叠」按钮调用。
       polys 顶点坐标为给定像素空间（调用方传缩略图坐标）。 */
    function rasterMask(polys, w, h) {
        var m = new Uint8Array(w * h);
        for (var r = 0; r < h; r++) {
            var gaps = rowIntervals(polys, r);
            for (var g = 0; g < gaps.length; g++) {
                var c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
                var c1 = Math.min(w - 1, Math.floor(gaps[g][1] + EPS));
                if (c0 <= c1) m.fill(1, r * w + c0, r * w + c1 + 1);
            }
        }
        return m;
    }
    /* 连通分量标记：返回每个分量的像素索引列表（就地清零 work，避免另开标签数组）。 */
    function connectedComponents(mask, w, h) {
        var work = mask.slice();
        var comps = [];
        for (var y = 0; y < h; y++) {
            for (var x = 0; x < w; x++) {
                var i = y * w + x;
                if (!work[i]) continue;
                var list = [];
                var q = [i];
                work[i] = 0;
                while (q.length) {
                    var cur = q.pop();
                    list.push(cur);
                    var cx = cur % w, cy = (cur / w) | 0;
                    if (cx > 0) { var a = cur - 1; if (work[a]) { work[a] = 0; q.push(a); } }
                    if (cx < w - 1) { var b = cur + 1; if (work[b]) { work[b] = 0; q.push(b); } }
                    if (cy > 0) { var c = cur - w; if (work[c]) { work[c] = 0; q.push(c); } }
                    if (cy < h - 1) { var d = cur + w; if (work[d]) { work[d] = 0; q.push(d); } }
                }
                comps.push(list);
            }
        }
        return comps;
    }
    /* 合并重叠/贴边区域（协程版）：
       并集 mask（四周垫 1px 背景保证轮廓闭环）→ 洞填充 → 各连通分量在 **bbox 子图**
       重追踪轮廓 + RDP 简化 → 返回合并后的多边形（原坐标空间）。
       重循环按批让出主线程（gen 每批 yield {phase, progress}）：浏览器用
       mergeConnectedAsync 驱动可实时刷进度、UI 不冻结；Node/测试用 mergeConnected
       同步驱动到完成。每连通域只在自身 bbox 子图（+1px 背景）追踪，不再整图重扫/整图分配。 */
    function* mergeConnectedGen(polys, w, h) {
        var pw = w + 2, ph = h + 2, n = pw * ph;
        var ROWS = 128;      // 栅格化每批行数
        var OPS = 1 << 17;   // BFS/扫描/填充每批操作数（~13 万，单批 <16ms 预算）
        var m = new Uint8Array(n);

        /* 阶段 1：并集栅格化（逐行，每 ROWS 行让出） */
        var r = 0;
        while (r < h) {
            var rEnd = Math.min(r + ROWS, h);
            for (; r < rEnd; r++) {
                var gaps = rowIntervals(polys, r);
                for (var g = 0; g < gaps.length; g++) {
                    var c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
                    var c1 = Math.min(w - 1, Math.floor(gaps[g][1] + EPS));
                    if (c0 <= c1) m.fill(1, (r + 1) * pw + c0 + 1, (r + 1) * pw + c1 + 2);
                }
            }
            yield { phase: '栅格化', progress: 0.25 * (r / h) };
        }

        /* 阶段 2：洞填充——从边框背景可达的 0 像素 BFS 标 out，不可达 0 就地补 1（每 OPS 让出） */
        var out = new Uint8Array(n);
        var stack = [];
        for (var x = 0; x < pw; x++) {
            if (!m[x]) { out[x] = 1; stack.push(x); }
            var bI = (ph - 1) * pw + x;
            if (!m[bI]) { out[bI] = 1; stack.push(bI); }
        }
        for (var yy = 0; yy < ph; yy++) {
            var lI = yy * pw, rI = yy * pw + pw - 1;
            if (!m[lI]) { out[lI] = 1; stack.push(lI); }
            if (!m[rI]) { out[rI] = 1; stack.push(rI); }
        }
        var visited = 0;
        while (stack.length) {
            var ops = 0;
            while (stack.length && ops++ < OPS) {
                var i = stack.pop();
                visited++;
                var cx = i % pw, cy = (i / pw) | 0;
                if (cx > 0) { var a = i - 1; if (!m[a] && !out[a]) { out[a] = 1; stack.push(a); } }
                if (cx < pw - 1) { var d = i + 1; if (!m[d] && !out[d]) { out[d] = 1; stack.push(d); } }
                if (cy > 0) { var u = i - pw; if (!m[u] && !out[u]) { out[u] = 1; stack.push(u); } }
                if (cy < ph - 1) { var dn = i + pw; if (!m[dn] && !out[dn]) { out[dn] = 1; stack.push(dn); } }
            }
            yield { phase: '洞填充', progress: 0.25 + 0.25 * Math.min(1, visited / n) };
        }
        for (var p = 0; p < n; p++) if (!m[p] && !out[p]) m[p] = 1;

        /* 阶段 3：连通域（行扫描 + BFS，每 OPS 让出）——记像素表 + bbox，m 就地清零当 work */
        var comps = [];
        var y = 0, col = 0, flood = 0, scanned = 0;
        var list = null, bfs = null, box = null;
        var scanDone = false;
        while (!scanDone || bfs) {
            var ops2 = 0;
            while (ops2++ < OPS) {
                if (bfs) {
                    if (bfs.length) {
                        var ii = bfs.pop();
                        flood++;
                        var fx = ii % pw, fy = (ii / pw) | 0;
                        if (fx < box[0]) box[0] = fx; else if (fx > box[2]) box[2] = fx;
                        if (fy < box[1]) box[1] = fy; else if (fy > box[3]) box[3] = fy;
                        list.push(ii);
                        if (fx > 0) { var na = ii - 1; if (m[na]) { m[na] = 0; bfs.push(na); } }
                        if (fx < pw - 1) { var nb = ii + 1; if (m[nb]) { m[nb] = 0; bfs.push(nb); } }
                        if (fy > 0) { var nc = ii - pw; if (m[nc]) { m[nc] = 0; bfs.push(nc); } }
                        if (fy < ph - 1) { var nd = ii + pw; if (m[nd]) { m[nd] = 0; bfs.push(nd); } }
                    } else {
                        comps.push({ list: list, box: box });
                        list = null; bfs = null; box = null;
                    }
                } else {
                    while (y < ph && col >= pw) { y++; col = 0; }
                    if (y >= ph) { scanDone = true; break; }
                    var i3 = y * pw + col;
                    scanned++;
                    if (m[i3]) { m[i3] = 0; list = []; box = [col, y, col, y]; bfs = [i3]; }
                    else col++;
                }
            }
            yield { phase: '连通域', progress: 0.5 + 0.25 * Math.min(1, (scanned + flood) / (2 * n)) };
        }

        /* 阶段 4：逐连通域在 bbox 子图（+1px 背景）重追踪轮廓 + 简化；子图填充每 OPS 让出 */
        var outPts = [];
        for (var c = 0; c < comps.length; c++) {
            var cm = comps[c];
            var bw = cm.box[2] - cm.box[0] + 1, bh = cm.box[3] - cm.box[1] + 1;
            var sw2 = bw + 2, sh2 = bh + 2;
            var sub = new Uint8Array(sw2 * sh2);
            var l2 = cm.list, li = 0;
            while (li < l2.length) {
                var liEnd = Math.min(li + OPS, l2.length);
                for (; li < liEnd; li++) {
                    var gi = l2[li];
                    var gx = gi % pw, gy = (gi / pw) | 0;
                    sub[(gy - cm.box[1] + 1) * sw2 + (gx - cm.box[0] + 1)] = 1;
                }
                yield { phase: '轮廓提取', progress: 0.75 + 0.25 * ((c + li / l2.length) / comps.length) };
            }
            var poly = traceContour(sub, sw2, sh2);
            if (poly.length) {
                var offX = cm.box[0] - 2, offY = cm.box[1] - 2;
                var pts = new Array(poly.length);
                for (var k = 0; k < poly.length; k++) pts[k] = [poly[k][0] + offX, poly[k][1] + offY];
                poly = simplifyPoly(pts, 0.5);
                if (poly.length >= 3) outPts.push(poly);
            }
        }
        return outPts;
    }
    /* 同步版（Node 测试 / 兼容）：直接驱动 gen 到完成，忽略 yield。 */
    function mergeConnected(polys, w, h) {
        var g = mergeConnectedGen(polys, w, h);
        var s = g.next();
        while (!s.done) s = g.next();
        return s.value;
    }
    /* 协程版（浏览器主线程）：每批 setTimeout 让出可实时刷进度；onPhase 阶段名、onProgress 0..1。 */
    function mergeConnectedAsync(polys, w, h, opts) {
        opts = opts || {};
        var g = mergeConnectedGen(polys, w, h);
        var onPhase = opts.onPhase || null, onProgress = opts.onProgress || null;
        var lastPct = -1;
        function emit(s) {
            if (!s) return;
            if (onPhase && s.phase) onPhase(s.phase);
            if (onProgress && typeof s.progress === 'number') {
                var pct = Math.round(s.progress * 100);
                if (pct !== lastPct) { lastPct = pct; onProgress(s.progress); }
            }
        }
        return new Promise(function (resolve, reject) {
            (function step() {
                var s;
                try { s = g.next(); }
                catch (e) { reject(e); return; }
                if (s.done) { resolve(s.value); return; }
                emit(s.value);
                setTimeout(step, 0);
            })();
        });
    }

    return {
        polygonCentroid: polygonCentroid,
        buildMaskTxt: buildMaskTxt,
        rasterRows: rasterRows,
        buildTiff: buildTiff,
        floodSelect: floodSelect,
        fillRegionHoles: fillRegionHoles,
        traceContour: traceContour,
        simplifyPoly: simplifyPoly,
        rasterMask: rasterMask,
        mergeConnected: mergeConnected,
        mergeConnectedAsync: mergeConnectedAsync
    };
});
