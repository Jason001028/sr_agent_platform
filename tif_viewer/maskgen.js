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

    /* ---------------- 魔法棒：容差 + 边缘屏障 连通区域生长 ----------------
       rgba 为显示用 RGBA（Uint8ClampedArray，w*h*4）；(sx,sy) 种子像素；
       tol 颜色容差（与种子色欧氏距离 ≤ tol）；edgeThresh 边缘梯度阈值（超过视为屏障）。
       返回 Uint8Array(w*h) 二值选区（1=选中）。行扫描泛洪，带 8e6 像素安全上限。 */
    var WAND_MAX_PX = 8000000;
    function floodSelect(w, h, rgba, sx, sy, tol, edgeThresh) {
        var n = w * h;
        var sel = new Uint8Array(n);
        if (sx < 0 || sx >= w || sy < 0 || sy >= h) return sel;
        var seedIdx = sy * w + sx;
        var sr = rgba[seedIdx * 4], sg = rgba[seedIdx * 4 + 1], sb = rgba[seedIdx * 4 + 2];
        var tol2 = tol * tol, eth2 = edgeThresh * edgeThresh;
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
        sel[seedIdx] = 1;
        var stack = [sx, sy];
        var count = 0;
        while (stack.length) {
            var yy = stack.pop(), xx = stack.pop();
            var xl = xx;
            while (xl > 0) {
                var i2 = yy * w + (xl - 1);
                if (!sel[i2] && !edgeR[i2] && colorClose(rgba, i2, sr, sg, sb, tol2)) { sel[i2] = 1; xl--; }
                else break;
            }
            var xr = xx;
            while (xr < w - 1) {
                var i3 = yy * w + (xr + 1);
                if (!sel[i3] && !edgeR[i3 - 1] && colorClose(rgba, i3, sr, sg, sb, tol2)) { sel[i3] = 1; xr++; }
                else break;
            }
            for (var c = xl; c <= xr; c++) {
                count++;
                if (count > WAND_MAX_PX) return sel;   // 安全上限
                var idx = yy * w + c;
                if (yy > 0) {
                    var up = (yy - 1) * w + c;
                    if (!sel[up] && !edgeD[idx - w] && colorClose(rgba, up, sr, sg, sb, tol2)) { sel[up] = 1; stack.push(c, yy - 1); }
                }
                if (yy < h - 1) {
                    var dn = (yy + 1) * w + c;
                    if (!sel[dn] && !edgeD[idx] && colorClose(rgba, dn, sr, sg, sb, tol2)) { sel[dn] = 1; stack.push(c, yy + 1); }
                }
            }
        }
        return sel;
    }
    function colorClose(rgba, i, sr, sg, sb, tol2) {
        var o = i * 4;
        var dr = rgba[o] - sr, dg = rgba[o + 1] - sg, db = rgba[o + 2] - sb;
        return dr * dr + dg * dg + db * db <= tol2;
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

    return {
        polygonCentroid: polygonCentroid,
        buildMaskTxt: buildMaskTxt,
        rasterRows: rasterRows,
        buildTiff: buildTiff,
        floodSelect: floodSelect,
        fillRegionHoles: fillRegionHoles,
        traceContour: traceContour,
        simplifyPoly: simplifyPoly
    };
});
