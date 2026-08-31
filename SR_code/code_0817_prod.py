# =============================================================================
# SFSR inference - code_0817_prod (production)
# Based on 超分代码0803/code_0817.py (原 0817定版/code_0817_deploy，已去重删除);
# English comments only, logging via _log helper.
# Run: python code_0817_prod.py -f <config.xml>
# External deps: models/, utils/, options/ (intranet mmsr_bundle).
# =============================================================================

import os
import cv2
import os.path as osp
import logging
import argparse
import numpy as np
import options.options as option
import utils.util as util
from models import create_model
import torch
import numpy.ctypeslib as npct
import time
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
import pynvml
import socket

lib = npct.load_library("/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes/tools/ImgHistMatch", ".")


# ---------------------------------------------------------------------------
# MTA-Grid: shift grid origin by delta so grid lines hug the ROI bbox,
# minimizing SR tile count. Pure numpy; does not touch inference/stitching.
# Ref: total_plan_0804.md sec 2.3/2.4.
# ---------------------------------------------------------------------------

def _count_sr_tiles(mask, wrap_top, wrap_btm, wrap_lt, wrap_rt, t_ht, t_wd):
    """Count SR tiles intersecting the mask (same metric as Phase A pre-scan)."""
    m = np.pad(mask, ((wrap_top, wrap_btm), (wrap_lt, wrap_rt)),
               mode='constant', constant_values=0)
    rows, cols = m.shape[0] // t_ht, m.shape[1] // t_wd
    if rows == 0 or cols == 0:
        return 0
    return int(m[:rows * t_ht, :cols * t_wd]
               .reshape(rows, t_ht, cols, t_wd).any(axis=(1, 3)).sum())


def _grid_offset_planner(mask_binary, tl_obj, t_ht, t_wd, pad):
    """Pick grid offset (delta_y, delta_x) that minimizes SR tile count.

    Returns (delta_y, delta_x, stats); stats is None for an empty mask.
    Candidates: bbox edges aligned to grid lines (+ (0,0) fallback),
    constrained by pad/wrap slack. Never worse than no shift.
    """
    ys = np.flatnonzero(mask_binary.any(axis=1))
    if ys.size == 0:
        return 0, 0, None
    xs = np.flatnonzero(mask_binary.any(axis=0))
    y0, y1, x0, x1 = ys[0], ys[-1], xs[0], xs[-1]
    bh, bw = y1 - y0 + 1, x1 - x0 + 1
    roi = int(mask_binary.sum())

    wt, wb, wl, wr = (tl_obj["wrap_top"], tl_obj["wrap_btm"],
                      tl_obj["wrap_lt"], tl_obj["wrap_rt"])

    def count(wt_, wb_, wl_, wr_):
        return _count_sr_tiles(mask_binary, wt_, wb_, wl_, wr_, t_ht, t_wd)

    n_before = count(wt, wb, wl, wr)

    # bbox top-left in wrap coords = (wt + y0, wl + x0); align it to grid lines.
    y_wrap, x_wrap = wt + y0, wl + x0
    dy_set = {y_wrap % t_ht, (y_wrap + bh) % t_ht}
    dy_set |= {v - t_ht for v in tuple(dy_set)}
    dx_set = {x_wrap % t_wd, (x_wrap + bw) % t_wd}
    dx_set |= {v - t_wd for v in tuple(dx_set)}
    cands = [(dy, dx)
             for dy in sorted(dy_set)
             for dx in sorted(dx_set)
             if wt - dy >= pad and wb + dy >= pad
             and wl - dx >= pad and wr + dx >= pad]
    cands.append((0, 0))  # fallback: no shift

    best = min(cands, key=lambda c: (count(wt - c[0], wb + c[0], wl - c[1], wr + c[1]),
                                     abs(c[0]) + abs(c[1])))
    dy, dx = best
    n_after = count(wt - dy, wb + dy, wl - dx, wr + dx)

    def halo(n):
        return (n * t_ht * t_wd - roi) / roi if roi else 0.0

    stats = dict(before_tiles=n_before, after_tiles=n_after,
                 saved_tiles=n_before - n_after,
                 before_halo=halo(n_before), after_halo=halo(n_after),
                 roi_pixels=roi, delta=(dy, dx))
    return dy, dx, stats


def _log(msg, opt, newline=True):
    """Print to terminal and append to SRLOG.txt (newline: leading \\n in file)."""
    print(msg)
    with open(opt['log_abs_path'], 'a') as f:
        f.writelines(("\n" if newline else "") + msg)


def main():
    t_wall_start = time.time()
    _t0 = t_wall_start      # rolling checkpoint
    tm = {}                 # per-stage timing (seconds)

    # ---- config ----
    parser = argparse.ArgumentParser()
    start_time = util.get_timestamp()
    parser.add_argument('-f', type=str, required=True, help='Path to SFSR configure XML file.')
    global scl, t_ht, t_wd, pad, max_DN, tiftype, sr_tile_pad
    sfsr_config_file = parser.parse_args().f

    lq_path = util.get_cfg_value(sfsr_config_file, "DatarootLQ")
    lq_path = lq_path.replace('\\', '/')
    del_ori_tif = util.get_cfg_value(sfsr_config_file, "DeleteOriTifNeeded")
    resize_scale = util.get_cfg_value(sfsr_config_file, "SRScale")
    suffix = util.get_cfg_value(sfsr_config_file, "Suffix")
    ymlpath = util.get_cfg_value(sfsr_config_file, "OPT")
    cloudlimit = int(util.get_cfg_value(sfsr_config_file, "CloudLimit"))

    # MaskPath optional: absent -> full-image SR (get_cfg_value raises on missing tag)
    try:
        mask_path = util.get_cfg_value(sfsr_config_file, "MaskPath")
    except Exception:
        mask_path = None

    # MTA-Grid toggle via <GridAlign>false|0</GridAlign>; default on
    enable_mta_grid = True
    try:
        _ga = util.get_cfg_value(sfsr_config_file, "GridAlign")
        if _ga is not None and _ga.strip().lower() in ("false", "0"):
            enable_mta_grid = False
    except Exception:
        pass

    opt = option.parse(ymlpath, is_train=False)
    opt = option.dict_to_nonedict(opt)
    scl, t_ht, t_wd, pad, tiftype = opt["scale"], opt["t_ht"], opt["t_wd"], opt["pad"], opt["tif_type"]
    tm['config'] = time.time() - _t0; _t0 = time.time()

    # ---- env / GPU ----
    os.environ['CUDA_VISIBLE_DEVICES'] = "0"
    gpuid = '0'

    # ---- input discovery + meta ----
    sr_previous_step = util.check_sr_previous_step(lq_path)
    img_abs_path = util.get_l1_pan_tif_rcsc(lq_path, tiftype, sr_previous_step)
    meta_abs_path = osp.join(lq_path, osp.basename(lq_path) + "_meta.xml")

    assert img_abs_path is not None, "L1 PAN.tif file not found."
    img_name = osp.basename(img_abs_path)
    max_DN = (2 << (int(util.get_cfg_value(meta_abs_path, "DataBits")) - 1)) - 1
    cloudpercent_tmp = util.get_cfg_value(meta_abs_path, "CloudPercent")
    cloudpercent = int(cloudpercent_tmp) if cloudpercent_tmp is not None else 0
    if cloudpercent > cloudlimit:
        exit(0)

    # ---- logger ----
    util.setup_logger('base', level=logging.INFO, screen=True)
    logger = logging.getLogger('base')
    opt['log_abs_path'] = osp.join(lq_path, "Debug", img_name[:-4] + "_SRLOG.txt")
    os.makedirs(osp.dirname(opt['log_abs_path']), exist_ok=True)

    msg = 'network_G:{:s} max_DN:{:s}'.format(str(opt['network_G']), str(max_DN))
    logger.info(msg)
    print(msg)
    with open(opt['log_abs_path'], 'w') as f:
        f.writelines(msg + '\n')

    msg = '{:s} SR {:s} Start [GPU {:s}]......'.format(str(start_time), osp.basename(img_abs_path), gpuid)
    logger.info(msg)
    _log(msg, opt, newline=False)

    util.write_print_gpu_mem_info(int(gpuid), opt['log_abs_path'])
    util.write_print_cpu_mem_info(opt['log_abs_path'])
    util.write_print_nvsmi(gpuid, opt['log_abs_path'])

    # ---- image + mask ----
    ori_img = util.read_img(img_abs_path)
    ori_min, ori_max, ori_mean, ori_shp = ori_img.min(), ori_img.max(), ori_img.mean(), ori_img.shape
    msg = "Input ori img min {:.1f}, max {:.1f}, mean {:.1f}, {:s}, {:s}".format(
        ori_min, ori_max, ori_mean, str(ori_img.dtype), str(ori_shp))
    _log(msg, opt)

    # mask optional: local SR only on ROI
    mask_binary = None
    do_local_sr = (mask_path is not None and mask_path.strip() != "")
    if do_local_sr:
        mask_raw = util.read_img(mask_path)
        if mask_raw.shape[:2] != ori_shp[:2]:
            logger.warning("Mask shape {:s} mismatch with image {:s}, skip local SR."
                           .format(str(mask_raw.shape), str(ori_shp)))
            do_local_sr = False
        else:
            _, mask_binary = cv2.threshold(mask_raw.astype(np.uint8), 0, 1, cv2.THRESH_BINARY)
            if mask_binary.ndim > 2:
                mask_binary = mask_binary[:, :, 0]
            logger.info("Local SR mask loaded, ROI pixels: {:d} / {:d} ({:.2f}%)"
                        .format(int(mask_binary.sum()), mask_binary.size,
                                100.0 * mask_binary.sum() / mask_binary.size))
            del mask_raw

    tm['io'] = time.time() - _t0; _t0 = time.time()

    # ---- grid / wrap / MTA-Grid ----
    tile_grid_dict, tl_info = util.dvd_2_grid([img_abs_path], pad, t_ht, t_wd, tiftype, ori_shp)
    tl_obj = tl_info[img_name]

    # MTA-Grid: rewrite tl_obj wrap keys in place; total wrap size unchanged.
    if enable_mta_grid and do_local_sr and mask_binary is not None:
        _mt_t0 = time.perf_counter()
        delta_y, delta_x, grid_stats = _grid_offset_planner(mask_binary, tl_obj, t_ht, t_wd, pad)
        _mt_ms = (time.perf_counter() - _mt_t0) * 1000.0
        if grid_stats is not None:
            tl_obj["wrap_top"] -= delta_y
            tl_obj["wrap_btm"] += delta_y
            tl_obj["wrap_lt"]  -= delta_x
            tl_obj["wrap_rt"]  += delta_x
            logger.info(
                "MTA-Grid aligned: offset=({:+d},{:+d})px  SR tiles {:d} -> {:d} (saved {:d})  "
                "halo {:.2f} -> {:.2f}  planner {:.1f}ms".format(
                    delta_y, delta_x,
                    grid_stats["before_tiles"], grid_stats["after_tiles"],
                    grid_stats["saved_tiles"],
                    grid_stats["before_halo"], grid_stats["after_halo"],
                    _mt_ms))

    img_ori_wrap = util.pad_reflect(ori_img, tl_obj["wrap_top"], tl_obj["wrap_btm"], tl_obj["wrap_lt"], tl_obj["wrap_rt"])

    # restormer: gamma-corrected input
    gamma = 1.45 if ymlpath.find("restormer", 0, len(ymlpath)) > -1 else 1.0
    if ymlpath.find("restormer", 0, len(ymlpath)) > -1:
        gamma_img = np.uint16(np.around((cv2.pow(ori_img / max_DN, 1 / gamma)) * max_DN))
        gamma_min, gamma_max, gamma_mean, gamma_shp = gamma_img.min(), gamma_img.max(), gamma_img.mean(), gamma_img.shape
        msg = "Input gamma({:.2f}) min {:.1f}, max {:.1f}, mean {:.1f}".format(gamma, gamma_min, gamma_max, gamma_mean)
        _log(msg, opt)
        img_gamma_wrap = util.pad_reflect(gamma_img, tl_obj["wrap_top"], tl_obj["wrap_btm"], tl_obj["wrap_lt"], tl_obj["wrap_rt"])
        if not do_local_sr:
            del ori_img
        del gamma_img
    else:
        if not do_local_sr:
            del ori_img

    # mask zero-pad to wrap coords (no dilation; tile reflect-padding handles boundaries)
    mask_wrapped = None
    if do_local_sr:
        mask_wrapped = np.pad(mask_binary,
                              ((tl_obj["wrap_top"], tl_obj["wrap_btm"]),
                               (tl_obj["wrap_lt"], tl_obj["wrap_rt"])),
                              mode='constant', constant_values=0)
        logger.info("Local SR mask wrapped (no dilation), shape: {:s}".format(str(mask_wrapped.shape)))

    tm['prep'] = time.time() - _t0; _t0 = time.time()

    opt = option.dict_to_nonedict(opt)

    # ---- model load ----
    # NUM_CTX stays 1 under torch<2.0 (stream concurrency unavailable).
    is_trt = ymlpath.find("realesrgan_trt", 0, len(ymlpath)) > -1
    if is_trt:
        NUM_CTX = 1
    else:
        NUM_CTX = 1  # bump to 2 when torch>=2.0
        free_mb, _ = torch.cuda.mem_get_info()
        free_mb = free_mb // (1024 * 1024)
        VRAM_PER_REPLICA_MB = 2048
        VRAM_SAFETY_MARGIN_MB = 2048
        if free_mb < VRAM_PER_REPLICA_MB * NUM_CTX + VRAM_SAFETY_MARGIN_MB:
            NUM_CTX = 1
            logger.info("VRAM low ({}MB free), degraded to single-context".format(free_mb))

    # Mini-batch per forward (PyTorch paths); no speedup under compute-bound, keep 1.
    # TRT engine is fixed-input -> forced batch=1.
    BATCH = 1

    models, streams = [], []
    for _ in range(NUM_CTX):
        if is_trt:
            import torch_tensorrt  # must be imported here
            models.append(torch.jit.load('/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes/tools/trt/t2trt_fp16_realESRGAN_1640.trt'))
        else:
            models.append(create_model(opt))
        streams.append(torch.cuda.Stream())
    tm['model'] = time.time() - _t0; _t0 = time.time()

    # warmup: pre-allocate CUDA memory, avoid lazy alloc cross-stream sync
    dummy = torch.zeros(1, 1, t_ht + 2 * pad, t_wd + 2 * pad).cuda()
    for m in models:
        m.feed_data_grid(dummy, need_GT=False)
        m.test()
    torch.cuda.synchronize()
    del dummy
    logger.info("Multi-context warmup done, {} replica(s) ready".format(NUM_CTX))

    # ---- mini-batch parallel worker ----
    def _run_sr_parallel(tiles, row_step):
        """Feed tiles [(row, col, key), ...] to N GPU workers.

        Grabs BATCH tiles per iteration, one batched forward, then per-tile
        fill_sr_grid / pt_process. Returns (t_sr, t_fill, t_pt) seconds.
        """
        if not tiles:
            return 0.0, 0.0, 0.0

        q = Queue()
        for item in tiles:
            q.put(item)

        lock_w = threading.Lock()    # fill_sr_grid
        lock_t = threading.Lock()    # timing accumulators
        lock_p = threading.Lock()    # pt_process row dedup
        lock_log = threading.Lock()  # SRLOG write
        lock_m = threading.Lock()    # serialize feed+test; GPU runs async after
        rows_seen = set()
        acc = dict(sr=0.0, fill=0.0, pt=0.0)

        # set ImgHistMatch argtypes once per process
        if ymlpath.find("restormer", 0, len(ymlpath)) > -1:
            lib.ImgHistMatch.argtypes = [
                npct.ndpointer(dtype=np.uint16, ndim=1, flags="C_CONTIGUOUS"),
                npct.ndpointer(dtype=np.uint16, ndim=1, flags="C_CONTIGUOUS"),
                npct.ctypes.c_uint16, npct.ctypes.c_uint16,
                npct.ctypes.c_uint16, npct.ctypes.c_uint16,
                npct.ctypes.c_uint32]

        def _worker(wid):
            m = models[wid % len(models)]
            stream = streams[wid % len(streams)]
            bs = 1 if ymlpath.find("realesrgan_trt", 0, len(ymlpath)) > -1 else BATCH

            while True:
                items = []
                for _ in range(bs):
                    try:
                        items.append(q.get_nowait())
                    except:
                        break
                if not items:
                    return

                for row, col, key in items:
                    if col == 0:
                        print("{}... row sr start [w{}]".format(key, wid))
                        with lock_log:
                            with open(opt['log_abs_path'], 'a') as srlog:
                                srlog.writelines("\n {:s}_...row sr start".format(key))

                grids = [tile_grid_dict[key] for _, _, key in items]
                t0 = time.time()

                if ymlpath.find("restormer", 0, len(ymlpath)) > -1:
                    tiles_g = [img_gamma_wrap[g[0]:g[1], g[2]:g[3]] for g in grids]
                    tiles_o = [img_ori_wrap[g[0]:g[1], g[2]:g[3]] for g in grids]
                    with lock_m:
                        with torch.cuda.stream(stream):
                            t_in = torch.cat([util.get_LQ_tile(t, max_DN) for t in tiles_g], dim=0)
                            m.feed_data_grid(t_in, need_GT=False)
                            m.test()
                    stream.synchronize()
                    vis = m.get_current_visuals(need_GT=False)
                    srs = []
                    for i, t_o in enumerate(tiles_o):
                        sr = util.tensor2img_fast(vis['rlt'][i:i + 1], gamma,
                                                  gamma_min, gamma_max, max_DN)
                        sr_flat = sr.flatten()
                        lib.ImgHistMatch(t_o.flatten(), sr_flat,
                                         t_o.shape[0], t_o.shape[1],
                                         sr.shape[0], sr.shape[1], max_DN + 1)
                        srs.append(np.reshape(sr_flat, (sr.shape[0], sr.shape[1])))
                elif ymlpath.find("realesrgan_trt", 0, len(ymlpath)) > -1:
                    grid = grids[0]
                    tile = img_ori_wrap[grid[0]:grid[1], grid[2]:grid[3]]
                    with torch.cuda.stream(stream):
                        t_in = util.get_LQ_tile(tile, max_DN).cuda().half().unsqueeze(0)
                        visuals = util.trt_test(t_in, m)
                    stream.synchronize()
                    srs = [util.tensor2img_fast_trt(visuals, out_type=np.uint16,
                                                    min_dn=ori_min, max_dn=ori_max,
                                                    max_DN=max_DN)]  # config to uint16
                elif (ymlpath.find("realesrgan", 0, len(ymlpath)) > -1
                      or ymlpath.find("espan", 0, len(ymlpath)) > -1):
                    tiles = [img_ori_wrap[g[0]:g[1], g[2]:g[3]] for g in grids]
                    with lock_m:
                        with torch.cuda.stream(stream):
                            t_in = torch.cat([util.get_LQ_tile(t, max_DN) for t in tiles], dim=0)
                            m.feed_data_grid(t_in, need_GT=False)
                            m.test()
                    stream.synchronize()
                    vis = m.get_current_visuals(need_GT=False)
                    srs = [util.tensor2img_fast_trt(vis['rlt'][i:i + 1],
                                                    out_type=np.uint16,
                                                    min_dn=ori_min, max_dn=ori_max,
                                                    max_DN=max_DN)
                           for i in range(len(items))]
                else:  # non-model fallback (CPU bicubic)
                    srs = []
                    for g in grids:
                        tile = img_ori_wrap[g[0]:g[1], g[2]:g[3]]
                        h, w = tile.shape[:2]
                        srs.append(cv2.resize(tile, (w * scl, h * scl),
                                              interpolation=cv2.INTER_CUBIC))

                with lock_t:
                    acc['sr'] += time.time() - t0

                for (row, col, key), sr in zip(items, srs):
                    t_f0 = time.time()
                    with lock_w:
                        util.fill_sr_grid(key, sr, result_sr_wrap, tile_rows,
                                          tile_cols, tl_info, img_name, pad, scl,
                                          t_ht, t_wd)
                    with lock_t:
                        acc['fill'] += time.time() - t_f0

                    t_p0 = time.time()
                    if col == 0:
                        with lock_p:
                            if row not in rows_seen:
                                util.pt_process(key, tl_obj, gpuid, t_ht, t_wd,
                                                opt['log_abs_path'], gamma,
                                                row_step=row_step)
                                rows_seen.add(row)
                    with lock_t:
                        acc['pt'] += time.time() - t_p0

        n = min(NUM_CTX, len(tiles))
        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return acc['sr'], acc['fill'], acc['pt']

    # ---- tile loop ----
    result_sr_wrap = util.init_res_img(tl_obj, scl)
    tile_rows = ((tl_obj["ori_ht"] + tl_obj["wrap_top"] + tl_obj["wrap_btm"]) // t_ht)
    tile_cols = ((tl_obj["ori_wd"] + tl_obj["wrap_lt"] + tl_obj["wrap_rt"]) // t_wd)

    torch.cuda.empty_cache()
    print("torch.cuda.EMPTY_CACHE()")

    t_loop_start = time.time()
    t_sr_total = 0.0
    t_bicubic_total = 0.0
    t_fill = 0.0
    t_pt = 0.0

    if do_local_sr and mask_wrapped is not None:
        # Phase A: vectorized mask pre-scan
        t_pre = time.time()
        mask_h = tile_rows * t_ht
        mask_w = tile_cols * t_wd
        assert mask_wrapped.shape[0] >= mask_h and mask_wrapped.shape[1] >= mask_w, \
            "mask_wrapped shape mismatch with tile grid"
        needs_sr_grid = mask_wrapped[:mask_h, :mask_w].reshape(
            tile_rows, t_ht, tile_cols, t_wd).any(axis=(1, 3))

        sr_tiles = []
        bicubic_tiles = []
        for key in tile_grid_dict.keys():
            row, col = map(int, key.split("_"))
            if needs_sr_grid[row, col]:
                sr_tiles.append((row, col, key))
            else:
                bicubic_tiles.append((row, col, key))
        sr_tiles.sort(key=lambda x: (x[0], x[1]))  # row-major
        tiles_sr_count = len(sr_tiles)
        tiles_skip_count = len(bicubic_tiles)
        t_precomp = time.time() - t_pre

        # Phase B: parallel bicubic (CPU)
        t_bic = time.time()
        if bicubic_tiles:

            def _bicubic_worker(key, grid):
                lq_tile = img_ori_wrap[grid[0]:grid[1], grid[2]:grid[3]]
                h, w = lq_tile.shape[:2]
                sr = cv2.resize(lq_tile, (w * scl, h * scl),
                                interpolation=cv2.INTER_CUBIC)
                return key, np.clip(sr, ori_min, ori_max).astype(np.uint16)

            bicubic_results = []
            max_w = min(4, (os.cpu_count() or 2))
            with ThreadPoolExecutor(max_workers=max_w) as executor:
                futures = [executor.submit(_bicubic_worker, key, tile_grid_dict[key])
                           for _, _, key in bicubic_tiles]
                for f in futures:
                    bicubic_results.append(f.result())
            t_bicubic_total = time.time() - t_bic

            # serial fill to avoid race on result_sr_wrap
            t_fill_start = time.time()
            for key, sr_tile_pad in bicubic_results:
                util.fill_sr_grid(key, sr_tile_pad, result_sr_wrap, tile_rows,
                                  tile_cols, tl_info, img_name, pad, scl, t_ht, t_wd)
            t_fill += time.time() - t_fill_start
        else:
            t_bicubic_total = 0.0

        # Phase C: parallel SR (GPU)
        t_sr_local, t_fill_local, t_pt_local = _run_sr_parallel(sr_tiles, row_step=1)
        t_sr_total += t_sr_local
        t_fill     += t_fill_local
        t_pt       += t_pt_local

        t_loop_total = time.time() - t_loop_start
        t_bookkeeping = t_loop_total - t_sr_total - t_bicubic_total
        print("[TIMING] tile loop total: {:.1f}s | precomp: {:.2f}s | "
              "SR: {:d} tiles {:.1f}s | bicubic: {:d} tiles {:.2f}s | "
              "fill_sr_grid: {:.1f}s | pt_process: {:.2f}s | workers: {:d}".format(
              t_loop_total, t_precomp,
              tiles_sr_count, t_sr_total, tiles_skip_count, t_bicubic_total,
              t_fill, t_pt, NUM_CTX))
        if do_local_sr:
            logger.info("Local SR: {:d} tiles SR, {:d} tiles skipped (bicubic)"
                        .format(tiles_sr_count, tiles_skip_count))

    else:
        # full-image SR: all tiles through GPU worker
        all_tiles = []
        for key in tile_grid_dict.keys():
            row, col = map(int, key.split("_"))
            all_tiles.append((row, col, key))
        tiles_sr_count = len(all_tiles)
        tiles_skip_count = 0

        t_sr_local, t_fill_local, t_pt_local = _run_sr_parallel(all_tiles, row_step=3)
        t_sr_total += t_sr_local
        t_fill     += t_fill_local
        t_pt       += t_pt_local

        t_loop_total = time.time() - t_loop_start
        print("[TIMING] tile loop total: {:.1f}s | SR: {:d} tiles {:.1f}s (avg {:.1f}s) | "
              "bicubic: {:d} tiles {:.2f}s | fill_sr_grid: {:.1f}s | pt_process: {:.2f}s | workers: {:d}".format(
              t_loop_total, tiles_sr_count, t_sr_total,
              t_sr_total / max(tiles_sr_count, 1), tiles_skip_count,
              t_bicubic_total, t_fill, t_pt, NUM_CTX))
        logger.info("Full SR: {:d} tiles processed ({} workers)".format(tiles_sr_count, NUM_CTX))

    tm['loop']   = t_loop_total
    tm['sr']     = t_sr_total
    tm['bicubic'] = t_bicubic_total
    tm['fill']   = t_fill
    tm['pt']     = t_pt
    if do_local_sr and mask_wrapped is not None:
        tm['scan'] = t_precomp
    _t0 = time.time()

    # ---- post-processing: cut padding / resize / clip / write ----
    if ymlpath.find("restormer", 0, len(ymlpath)) > -1:
        del img_gamma_wrap, img_ori_wrap, models
    else:
        del img_ori_wrap, models

    img_no_wrap = util.cut_down_wrap(result_sr_wrap, tl_obj["ori_ht"], tl_obj["ori_wd"], tl_obj["wrap_top"], tl_obj["wrap_lt"], scl)
    del result_sr_wrap

    torch.cuda.empty_cache()
    print("torch.cuda.EMPTY_CACHE()")

    if resize_scale == 2:
        result_sr = img_no_wrap
    else:
        result_sr = util.resize_by_sat_rcsc_2(img_no_wrap, img_name, resize_scale, sr_previous_step)

    # clip value range; redundant for scale=2 (tiles already clipped in tensor2img)
    if resize_scale != 2:
        np.clip(result_sr, ori_min, ori_max, out=result_sr)

    del img_no_wrap

    msg = "Output min {:.1f}, max {:.1f}, mean {:.1f}, dtype {:s}, shape {:s}".format(
        result_sr.min(), result_sr.max(), result_sr.mean(), str(result_sr.dtype), str(result_sr.shape))
    _log(msg, opt)

    if suffix is not None:
        util.writeTiff(result_sr, lq_path + "/" + img_name[0:-4] + "_" + suffix, tiftype=tiftype,
                       del_ori_tif=del_ori_tif)
    else:
        util.writeTiff(result_sr, lq_path + "/" + img_name[0:-4], tiftype=tiftype,
                       del_ori_tif=del_ori_tif)

    del result_sr

    util.update_meta_gsd(osp.join(lq_path, osp.basename(lq_path) + "_meta.xml"))
    util.update_meta_integrationtime(osp.join(lq_path, osp.basename(lq_path) + "_meta.xml"))

    tm['post'] = time.time() - _t0
    t_total = time.time() - t_wall_start

    # ---- pipeline timing summary (terminal + SRLOG) ----
    lines = []
    bar_w = 48
    lines.append("=" * bar_w)
    lines.append("  Pipeline Timing Summary  (tile={:d}  workers={:d})".format(t_ht, NUM_CTX))
    lines.append("-" * bar_w)
    _add = lambda name, key: lines.append(
        "  {:<24s} {:>6.1f}s  ({:>5.1f}%)".format(
            name, tm.get(key, 0), tm.get(key, 0) / t_total * 100))
    _add("config",              'config')
    _add("image io",            'io')
    _add("prep (grid+wrap+mask)", 'prep')
    _add("model load",          'model')
    if 'scan' in tm:
        _add("  Phase A: pre-scan", 'scan')
    _add("SR infer (GPU)",      'sr')
    _add("bicubic (CPU)",       'bicubic')
    _add("tile fill",           'fill')
    _add("pt log",              'pt')
    _add("post (write)",        'post')
    lines.append("-" * bar_w)
    lines.append("  {:<24s} {:>6.1f}s".format("total", t_total))
    lines.append("=" * bar_w)
    for line in lines:
        print(line)
    with open(opt['log_abs_path'], 'a') as srlog:
        srlog.writelines("\n" + "\n".join(lines) + "\n")

    util.write_print_gpu_mem_info(int(gpuid), opt['log_abs_path'])
    util.write_print_cpu_mem_info(opt['log_abs_path'])
    util.write_print_nvsmi(int(gpuid), opt['log_abs_path'])

    logger.info('Run {:s} finished.[GPU {:s}] {:s}'.format(img_name, gpuid, util.get_timestamp()))
    with open(opt['log_abs_path'], 'a') as srlog:
        srlog.writelines("\nRun finished.")
    print('Run {:s} finished.[GPU {:s}]'.format(img_name, gpuid))

    return


if __name__ == "__main__":
    global tiftype, tl_info
    global t_ht, t_wd, pad, scl

    tl_info = {}

    util.setup_logger('gpu', level=logging.INFO, screen=True)
    logger = logging.getLogger('gpu')
    os.environ['CUDA_VISIBLE_DEVICES'] = "1"

    pynvml.nvmlInit()
    gpu_count = pynvml.nvmlDeviceGetCount()
    pynvml.nvmlShutdown()

    gpu_available = torch.cuda.is_available()
    hostname = socket.gethostbyname(socket.gethostname())

    if gpu_available is False or gpu_count != 4:
        logger.info('Host {:s} gpu error, remove from product queue'.format(hostname))
        cmd = "systemctl stop slurmd.service"
        os.system(cmd)
        slurm_stop_log_file = "/DiskArray/ProductionSchedule/config/SlurmStopLog.txt"
        if osp.exists(slurm_stop_log_file) is True:
            with open(slurm_stop_log_file, 'a') as srlog:
                srlog.writelines("{:s} stop {:s}\n".format(util.get_timestamp(), hostname))

        print('GPU Error, gpu_available={:s} GPU COUNT = {:s}\n'.format(str(gpu_available), str(gpu_count)))
        exit(3)

    main()
    torch.cuda.empty_cache()
    exit()
