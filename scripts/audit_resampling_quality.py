"""Reproduce the P0/P1 quality audit from the repository root.

Requires NumPy, Pillow, tifffile, scikit-image, psutil, and GDAL Python 3.9+.
Uses an isolated temporary TIFF (about 4.9 GB with --full). Source code is read-only.
"""
import argparse
import io
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter

import numpy as np
import psutil
import tifffile
from PIL import Image, ImageDraw
from skimage.metrics import structural_similarity

from backend.services import image_tiles as tiles
from backend.services import preview_jpg as p0
from backend.services.raster_window import RasterWindowReader, _gdal, raster_revision

ROI = 1024
NEAREST = Image.Resampling.NEAREST


def motif():
    y, x = np.indices((ROI, ROI))
    dn = 6400 + 4*x + 3*y + 3200*((x//13 + y//11) % 2)
    dn += ((x*233 + y*457 + x*y*23) % 683)
    dn[64:288, 64:224] = 7000
    dn[64:288, 130] = 39000  # exactly one source pixel, even phase
    dn[320:480, 80:320] = 7000
    dn[320:480, 195:320] = 38500  # near-vertical edge, asymmetric phase
    dn[512:768, 512:768] = 14000 + 17000*((x[512:768, 512:768]+y[512:768, 512:768]) % 2)
    dn[768:974, 64:438] = 21700 + x[768:974, 64:438] % 7  # small DN contrast
    dn[800, 450:970] = 46500  # narrow bright horizontal strip
    return dn.astype(np.uint16)


def write_strip(path, dn, width, height):
    x = np.arange(width) % ROI
    chunks = (dn[np.ix_(np.arange(y, min(y+32, height)) % ROI, x)]
              for y in range(0, height, 32))
    tifffile.imwrite(path, data=chunks, shape=(height, width), dtype=np.uint16,
                     bigtiff=True, rowsperstrip=32, photometric="minisblack", metadata=None)


def measured(fn, repeats=1):
    process = psutil.Process()
    base = process.memory_info().rss
    peak = [base]
    stop = Event()

    def sample():
        while not stop.wait(.002):
            peak[0] = max(peak[0], process.memory_info().rss)

    timer = Thread(target=sample)
    timer.start()
    durations = []
    try:
        value = None
        for _ in range(repeats):
            start = perf_counter()
            value = fn()
            durations.append((perf_counter()-start)*1000)
        peak[0] = max(peak[0], process.memory_info().rss)
        return value, {"ms_median": round(median(durations), 3), "n": repeats,
                       "base_mib": round(base/2**20, 2), "peak_mib": round(peak[0]/2**20, 2),
                       "delta_mib": round((peak[0]-base)/2**20, 2)}
    finally:
        stop.set()
        timer.join()


def u8(dn):
    # Fixed linear map shared by all space-only controls.
    return np.rint(np.clip((dn.astype(float)-4096)/(49152-4096), 0, 1)*255).astype(np.uint8)


def metric(reference, actual):
    delta = actual.astype(float) - reference
    return {"mae": round(float(np.abs(delta).mean()), 4),
            "ssim": round(float(structural_similarity(reference, actual, data_range=255)), 6)}


def visual_metrics(reference, shown):
    road = shown[80:272, 127:135].max(axis=1)
    edge = shown[340:460, 180:212].astype(float).mean(axis=0)
    stripe = shown[796:805, 480:960].max(axis=0)
    dn = shown[780:960, 80:420]
    return {**metric(reference, shown), "road_pct": round(float(np.mean(road > 80)*100), 2),
            "road_peak_mean": round(float(road.mean()), 2),
            "stripe_pct": round(float(np.mean(stripe > 135)*100), 2),
            "edge_gradient": round(float(np.abs(np.diff(edge)).max()), 2),
            "edge_min_max": [int(edge.min()), int(edge.max())],
            "checker_sigma": round(float(shown[528:752, 528:752].std()), 2),
            "low_contrast_levels": int(np.unique(dn).size)}


def display_native(arr):
    return np.asarray(Image.fromarray(arr).resize((ROI, ROI), NEAREST))


def sheet(path, entries, crop):
    cols, side, header, gap = 3, crop[2]-crop[0], 22, 10
    rows = (len(entries)+cols-1)//cols
    result = Image.new("RGB", (cols*(side+gap)+gap, rows*(side+header+gap)+gap), "#192027")
    draw = ImageDraw.Draw(result)
    for i, (label, arr) in enumerate(entries):
        x, y = gap+(i%cols)*(side+gap), gap+(i//cols)*(side+header+gap)
        panel = Image.fromarray(arr).crop(crop)
        draw.text((x+2, y+3), label, fill="white")
        result.paste(panel, (x, y+header))
    result.save(path)


def run(out, scratch, full):
    gdal = _gdal()
    w, h = (49475, 49454) if full else (4163, 4142)
    x0 = y0 = 24576 if full else 2048
    dn = motif()
    source = scratch / "pan.tif"
    print(f"Writing {w}x{h} deterministic UInt16 TIFF", flush=True)
    write_strip(source, dn, w, h)
    reader = RasterWindowReader()
    service = tiles.ImageTiles(None)
    rev = raster_revision(source)
    info = reader.info(source)
    result = {"size": [w,h], "roi": [x0,y0,ROI,ROI], "gdal": gdal.VersionInfo("RELEASE_NAME"),
              "numpy": np.__version__, "cap32768_plans": [p0.preview_plan(49475,49454,d,32768)
                                                          for d in (1,2,4,16)],
              "timing": {}, "space": {}, "tone": {}, "codec": {}, "overview": {}}
    timing = result["timing"]
    raw, timing["A_raw_512"] = measured(lambda: reader.read_window(source,x0,y0,512,512).dn, 7)
    assert np.array_equal(raw, dn[:512,:512])
    raw_roi = np.block([[reader.read_window(source,x0+i*512,y0+j*512,512,512).dn
                         for i in range(2)] for j in range(2)])
    assert np.array_equal(raw_roi,dn)
    tifffile.imwrite(out/"A-L0-UInt16.tif", raw_roi, photometric="minisblack", metadata=None)
    reference = u8(dn)
    Image.fromarray(reference).save(out/"A-L0-fixed-linear.png")

    edge = p0.preview_max_edge(source, 1, cap=32768)
    print(f"Running P0 at effective long edge {edge}", flush=True)
    p0_pixels, timing["B_P0_read_stretch"] = measured(lambda: p0.build_preview_pixels(source, edge))
    th, tw = p0_pixels.shape
    result["p0_size"] = [tw,th]
    jpg_path = scratch / "p0.jpg"
    _, timing["B_P0_encode_q85"] = measured(lambda: p0.write_preview_jpg(jpg_path, p0_pixels, div=1, max_edge=32768))
    # Endpoint-aligned global grids. Retain only preview samples whose source centres fall in ROI.
    xi = np.round(np.arange(tw)*(w-1)/(tw-1)).astype(int)
    yi = np.round(np.arange(th)*(h-1)/(th-1)).astype(int)
    xa, xb = np.searchsorted(xi, [x0,x0+ROI]); ya, yb = np.searchsorted(yi, [y0,y0+ROI])
    box = (int(xa),int(ya),int(xb),int(yb))
    result["p0_jpeg_crop_xyxy"] = box
    p0_pre = p0_pixels[ya:yb,xa:xb].copy()
    del p0_pixels
    def jpeg_crop():
        with Image.open(jpg_path) as image:
            return np.asarray(image.crop(box)).copy()
    p0_post, timing["B_Pillow_decode_full_jpeg_crop"] = measured(jpeg_crop)
    Image.fromarray(p0_pre).save(out/"B-P0-precodec.png")
    Image.fromarray(p0_post).save(out/"B-P0-Q85-decoded.png")
    result["codec"]["P0_actual_Q85"] = metric(p0_pre,p0_post)
    # Reconstruct exact source extent by the nearest delivered centre.
    bx = np.argmin(np.abs(xi[xa:xb,None] - np.arange(x0,x0+ROI)), axis=0)
    by = np.argmin(np.abs(yi[ya:yb,None] - np.arange(y0,y0+ROI)), axis=0)
    p0_shown = p0_post[np.ix_(by,bx)]

    shared_stats, timing["P1_tone_first_source"] = measured(lambda: service._source_stats(source,rev,1,info))
    actual = [("A | reference, fixed linear", reference), ("B | P0 JPEG Q85", p0_shown)]
    fixed_p0 = u8(dn[np.ix_(yi[ya:yb]-y0, xi[xa:xb]-x0)])
    p0_same_lut = fixed_p0[np.ix_(by,bx)]
    result["space"]["P0_nearest_same_LUT"] = visual_metrics(reference, p0_same_lut)
    result["sampled_dn_range"] = list(shared_stats[:2])
    result["edge"] = {}
    space = []
    for level in (0,1,2):
        step = 1 << level
        tx, ty = x0//(512*step), y0//(512*step)
        window = tiles.tile_window(w,h,level,tx,ty)
        read = lambda: reader.read_window(source,*window, grid_step=step).dn
        observed, timing[f"P1_L{level}_DN"] = measured(read, 7)
        expected = dn[step//2:window[3]:step,step//2:window[2]:step]
        assert np.array_equal(observed[:min(512,1024//step),:min(512,1024//step)],expected[:1024//step,:1024//step])
        span = 512*step
        tail = tiles.tile_window(w,h,level,(w-1)//span,(h-1)//span)
        tx0, ty0, sw, sh, ow, oh = tail
        xs = np.arange(ow)*step
        ys = np.arange(oh)*step
        final_x = (tx0+xs+np.minimum(sw-xs,step)//2) % ROI
        final_y = (ty0+ys+np.minimum(sh-ys,step)//2) % ROI
        actual_edge = reader.read_window(source,*tail,grid_step=step).dn
        assert np.array_equal(actual_edge,dn[np.ix_(final_y,final_x)])
        result["edge"][f"L{level}"] = {"source_window":tail[:4],"output":tail[4:],"different_dn":0}
        cached, timing[f"P1_L{level}_tile_cold"] = measured(lambda: service._tile(source,rev,1,info,level,tx,ty))
        _, timing[f"P1_L{level}_tile_hot"] = measured(lambda: service._tile(source,rev,1,info,level,tx,ty), 7)
        with Image.open(io.BytesIO(cached[0])) as image:
            decoded = np.asarray(image).copy()
        assert np.array_equal(decoded,tiles.display_pixels(observed,info.nodata,shared_stats))
        if level == 0:
            parts = [[np.asarray(Image.open(io.BytesIO(service._tile(source,rev,1,info,0,tx+i,ty+j)[0])))
                      for i in range(2)] for j in range(2)]
            pixels = np.block(parts)
            label = "C"
        else:
            pixels = decoded[:ROI//step,:ROI//step]
            label = "D"
        Image.fromarray(pixels).save(out/f"{label}-P1-L{level}-api-pixels.png")
        actual.append((f"{label} | P1 L{level} | global sampled LUT",display_native(pixels)))
        if not level:
            continue
        n = ROI//step
        for method in ("nearest","average","bilinear"):
            generic, timing[f"P1_generic_L{level}_{method}"] = measured(
                lambda: reader.read_window(source,x0,y0,ROI,ROI,n,n,resampling=method).dn, 7)
            result["space"][f"GDAL_L{level}_{method}"] = visual_metrics(reference, display_native(u8(generic)))
        methods = {"Nearest":dn[step//2::step,step//2::step]}
        # All filters operate on raw DN before the same fixed linear LUT.
        for name in ("Average","Bilinear","Lanczos"):
            kernel = Image.Resampling.BOX if name == "Average" else getattr(Image.Resampling,name.upper())
            methods[name], timing[f"E_L{level}_{name}"] = measured(
                lambda: np.asarray(Image.fromarray(dn.astype(np.float32)).resize((n,n),kernel)), 7)
        for name, values in methods.items():
            fixed = u8(values)
            Image.fromarray(fixed).save(out/f"E-L{level}-{name}.png")
            shown = display_native(fixed)
            result["space"][f"L{level}_{name}"] = visual_metrics(reference,shown)
            space.append((f"E | L{level} {name}",shown))

    tone_arrays = {"fixed_linear":reference,"P0_equal_on_same_L0":p0.stretch_equal(dn),
                   "P1_sampled_equal":tiles.display_pixels(dn,None,shared_stats),
                   "P1_constant_sample":tiles.display_pixels(dn,None,tiles.stretch_stats(np.full(1024,1000.0))),
                   "diagnostic_narrow_linear":np.rint(np.clip((dn.astype(float)-21696)/16,0,1)*255).astype(np.uint8)}
    for name, values in tone_arrays.items():
        result["tone"][name] = {"unique":int(np.unique(values).size),
                                "low_contrast_levels":int(np.unique(values[780:960,80:420]).size),
                                **metric(reference,values)}
        Image.fromarray(values).save(out/f"LUT-{name}.png")
    png = Image.fromarray(reference)
    for format, quality in (("PNG",None),("JPEG",85),("JPEG",95)):
        buf=io.BytesIO()
        png.save(buf, format=format, **({"quality":quality} if quality else {}))
        name = format+(f"-Q{quality}" if quality else "")
        result["codec"][name] = {"bytes":len(buf.getvalue()),**metric(reference,np.asarray(Image.open(buf)))}
        (out/f"codec-{name}.{format.lower()}").write_bytes(buf.getvalue())

    for name, reduced in (("Nearest",dn[1::2,1::2]),
                          ("Average",np.rint(dn.reshape(512,2,512,2).mean(axis=(1,3))).astype(np.uint16))):
        ov = scratch/f"{name}.tif"
        with tifffile.TiffWriter(ov) as file:
            file.write(dn, subifds=1, rowsperstrip=32, photometric="minisblack")
            file.write(reduced, subfiletype=1, rowsperstrip=32, photometric="minisblack")
        # Actual P1 strict grid reads Overview samples directly. P0 selects it then resamples.
        r = reader.read_window(ov,0,0,1024,1024,512,512,grid_step=2).dn
        assert np.array_equal(r,reduced)
        q = p0.build_preview_pixels(ov,350)
        indices = np.round(np.arange(350)*511/349).astype(int)
        assert np.array_equal(q,p0.stretch_equal(reduced[np.ix_(indices,indices)]))
        result["overview"][name] = {"P1_L1":visual_metrics(reference,display_native(u8(r))),
                                     "P0_350_equals_overview_then_nearest":True}
        reader.release(ov)
    compressed = scratch/"compressed-u16.tif"
    tifffile.imwrite(compressed,np.array([[254,255,256,1000,65535]],dtype=np.uint16),
                     compression="deflate", photometric="minisblack")
    result["p0_pillow_fallback"] = {"input":[254,255,256,1000,65535],
                                     "output":p0.build_preview_pixels(compressed,5).ravel().tolist()}
    reader.release(source)
    service.reader.release(source)
    service.pool.shutdown()
    sheet(out/"actual-road-edge.png",actual,(64,64,352,352))
    controls = [("A | fixed linear L0",reference),("B | P0 grid, fixed linear",p0_same_lut),*space]
    sheet(out/"space-road.png",controls,(64,64,352,352))
    sheet(out/"space-texture.png",controls,(512,512,768,768))
    sheet(out/"lut-low-contrast.png",list(tone_arrays.items()),(64,736,352,1024))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--full",action="store_true")
    parser.add_argument("--scratch-dir",type=Path,default=Path.cwd())
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix="sr-resampling-",dir=args.scratch_dir) as temp:
        result=run(args.output,Path(temp),args.full)
    (args.output/"metrics.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result["timing"],indent=2))


if __name__ == "__main__":
    main()
