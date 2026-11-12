"""Smart Compare source orchestration, using existing bounded TIFF readers."""
from pathlib import Path
import time
import base64
import io

import numpy as np
from PIL import Image

from backend.services import image_diff, batch_difference, preview_jpg, scene_search
from backend.services.spatial_identity import resource_identity
from backend.services.tour_identity import image_identity
from backend.pathguard import PathDeniedError


def prepare_pair(body, resolve):
    specs = [body.get("image_a"), body.get("image_b")]
    if any(not isinstance(s, dict) or not s.get("sceneId") for s in specs):
        raise image_diff.DiffError("智能对比原始 DN 分析需要两张可访问盘阵影像")
    paths = [Path(resolve(s["sceneId"])).resolve() for s in specs]
    if paths[0].parent != paths[1].parent or paths[0] == paths[1]:
        raise image_diff.DiffError("PAN/NOSR 必须是同一场景的不同影像")
    directory = paths[0].parent
    pan = scene_search.body_path(directory)
    if pan is None:
        # Existing RC production convention also works without a metadata XML.
        pan = directory / "PAN.tif"
    pan = pan.resolve()
    nosr_names = {p.resolve() for p in scene_search.nosr_candidates(directory, pan)}
    nosr_names.add((directory / (directory.name + "_NOSR.tif")).resolve())
    if pan not in paths or not any(p in nosr_names for p in paths if p != pan):
        raise image_diff.DiffError("智能对比仅接受经场景命名契约确认的 PAN 本体与 NOSR")
    reference_side = "B" if paths[0] == pan else "A"
    refs = []
    for path, spec in zip(paths, specs):
        info = preview_jpg.probe_tiff(path)
        revision = preview_jpg.source_revision(path)
        if not spec.get("sourceRevision") or spec["sourceRevision"] != revision:
            raise image_diff.DiffError("影像修订缺失或已改变，请重新加载影像")
        role = "pan" if path == pan else "nosr"
        refs.append({"path": str(path), "width": info["W"], "height": info["H"],
                     "revision": revision, "image_key": resource_identity(path)["image_key"],
                     "tourIdentity": image_identity(path, directory.as_posix(), role)})
    try:
        batch_difference.validate_pair(refs[0 if reference_side == "B" else 1], refs[1 if reference_side == "B" else 0])
    except ValueError as e:
        raise image_diff.DiffError(str(e)) from e
    return specs, paths, refs, reference_side


def assert_revisions(paths, refs):
    for path, ref in zip(paths, refs):
        if preview_jpg.source_revision(path) != ref["revision"] or resource_identity(path)["image_key"] != ref["image_key"]:
            raise image_diff.DiffError("分析期间源影像发生变化，请重新分析")


def load_scene_masks(body, resolve, refs, shape, window=None):
    supplied = body.get("sceneMasks") or {}
    if not isinstance(supplied, dict) or set(supplied)-{"cloud","water","shadow"}:
        raise image_diff.DiffError("sceneMasks 仅支持 cloud/water/shadow 来源引用")
    masks = {}
    for name, spec in supplied.items():
        masks[name] = {"registered":False,"status":"unverified-source"}
        if not isinstance(spec, dict) or not spec.get("sceneId") or not spec.get("sourceRevision"):
            continue
        try:
            path = Path(resolve(spec["sceneId"]))
            identity = image_identity(path,path.parent.as_posix(),name)
            expected = refs[0]["tourIdentity"].get("registration")
            actual = identity.get("registration")
            if identity["sourceRevision"] != spec["sourceRevision"] or not expected or not actual:
                continue
            if actual["crs"] != expected["crs"] or not np.allclose(actual["extent"],expected["extent"],rtol=0,atol=1e-8) or actual["orientation"] != expected["orientation"]:
                continue
            width,height = identity["width"],identity["height"]
            if window:
                aw,ah=refs[0]["width"],refs[0]["height"]
                x0,y0,x1,y1=window
                a,v,_,_=image_diff.read_source_window(path,int(x0*width/aw),int(y0*height/ah),
                    int(np.ceil(x1*width/aw)),int(np.ceil(y1*height/ah)),max_edge=4096)
                a,v=image_diff.resize_to(shape,a,v)
            else:
                a,v=batch_difference.sample_grid(path,width,height,shape,{"cancel_check":body.get("_cancel_check",lambda:None)})
            if not v.all() or preview_jpg.source_revision(path) != spec["sourceRevision"]:
                masks[name]["status"]="unverified-invalid-pixels-or-revision"
                continue
            masks[name]={"registered":True,"sourceIdentity":resource_identity(path)["image_key"],"mask":a>0}
        except (OSError,ValueError,KeyError,PathDeniedError):
            masks[name]["status"]="unverified-read-failed"
    return masks


def bind_metadata(result, specs, refs, reference_side, *, windows=None):
    g = result["analysisGrid"]
    from backend.services.auto_mask_geometry import bind_auto_mask
    bind_auto_mask(result, specs, source_windows=windows)
    result["sourceIdentity"] = {"referenceSide": reference_side,
        "images": [{"id": s.get("id"), "sceneId": s.get("sceneId"), "revision": r["revision"],
                    "resource": r["image_key"], "role": r["tourIdentity"]["role"]} for s, r in zip(specs, refs)],
        "registration": "geotiff" if refs[0]["tourIdentity"].get("registration") else "production-proportional-grid"}
    if result["sourceIdentity"]["registration"] == "production-proportional-grid":
        result["warnings"].append("无可靠 GeoTIFF 全幅配准证据；使用已核对同景 PAN/NOSR 的生产比例网格约定，仍需人工核对空间对齐。")
    for region in result["regions"]:
        x, y, w, h = region["bbox"]
        if windows:
            ox, oy, right, bottom = windows[0]
            sx, sy = (right-ox)/g["width"], (bottom-oy)/g["height"]
        else:
            ox, oy = 0, 0
            sx, sy = refs[0]["width"]/g["width"], refs[0]["height"]/g["height"]
        region["sourceBboxA"] = [ox+x*sx, oy+y*sy, w*sx, h*sy]
        region["sourceIdentity"] = result["sourceIdentity"]
        region["maskEligible"] = bool(region.get("maskEligible", True) and windows and sx == 1 and sy == 1)
    result["regionSummary"]={"count":len(result["regions"]),
        "areaPixels":sum(r["areaPx"] for r in result["regions"]),
        "maxConnectedAreaPixels":max((r["areaPx"] for r in result["regions"]),default=0),
        "coverageDensity":sum(r["areaPx"] for r in result["regions"])/max(1,result["coverage"]["validPixels"])}
    if result.get("method") == "smart":
        for p in result.get("autoMask", {}).get("polygons", []):
            seeds = p.get("analysis_seeds", [p.get("analysis_seed")])
            p["candidate_ids"] = [r["id"] for r in result["regions"] if r.get("componentSeed") in seeds]
            p["needs_review"] = True
            p["diagnostics"].append("experimental-smart-candidate-human-review-required")
    return result


def _mask(result):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(result["binaryMask"].split(",", 1)[1])))) > 0


def compare_results(results):
    masks = {name: _mask(value) for name, value in results.items()}
    overlaps = []
    for a, b in (("absdiff", "ssim"), ("absdiff", "smart"), ("ssim", "smart")):
        union = int((masks[a] | masks[b]).sum())
        overlaps.append({"methods": [a, b], "iou": float((masks[a] & masks[b]).sum()/union) if union else 1.,
                         "onlyA": int((masks[a] & ~masks[b]).sum()), "onlyB": int((masks[b] & ~masks[a]).sum())})
    return {"execution": "sequential-shared-input-bounded", "summary": [
        {"method": k, "regions": len(v["regions"]), "areaPixels": int(masks[k].sum()),
         "coverage": float(masks[k].sum()/max(v["coverage"]["validPixels"], 1)), "elapsedMs": v["elapsedMs"],
         "status": "completed"} for k, v in results.items()], "overlaps": overlaps,
         "smartUniquePixels": int((masks["smart"] & ~masks["absdiff"] & ~masks["ssim"]).sum()), "results": results}


def analyze_grid(body, resolve):
    started = time.perf_counter()
    specs, paths, refs, reference_side = prepare_pair(body, resolve)
    edge = min(2048, image_diff.resolve_max_edge(body.get("maxEdge")))
    shape = image_diff.common_grid_shape((refs[0]["height"], refs[0]["width"]),
                                         (refs[1]["height"], refs[1]["width"]), edge)
    check = body.get("_cancel_check", lambda: None)
    arrays = [batch_difference.sample_grid(p, r["width"], r["height"], shape, {"cancel_check":check}) for p, r in zip(paths, refs)]
    scene_masks=load_scene_masks(body,resolve,refs,shape)
    methods = ("absdiff", "ssim", "smart") if body.get("compare") is True else (body.get("method", "smart"),)
    results = {}
    for method in methods:
        check()
        result = image_diff.analyze_arrays(arrays[0][0], arrays[1][0], valid_a=arrays[0][1], valid_b=arrays[1][1],
            method=method, sensitivity=body.get("sensitivity", "standard"), threshold=body.get("threshold"),
            min_area_px=body.get("minAreaPx", 64), open_kernel=body.get("openKernel", 3), close_kernel=body.get("closeKernel", 3),
            blur=body.get("blur", True), ssim_window=body.get("ssimWindow", 7), ssim_gaussian=body.get("ssimGaussian", False),
            source="source-grid", auto_mask=body.get("autoMask") is True,
            smart_options=body.get("smartOptions"), reference_side=reference_side,
            scene_masks=scene_masks,
            scale_a=(refs[0]["width"]/shape[1], refs[0]["height"]/shape[0]),
            scale_b=(refs[1]["width"]/shape[1], refs[1]["height"]/shape[0]), roi=body.get("roi"))
        for side, spec, ref in zip(("A", "B"), specs, refs):
            result["image"+side] = {**spec, "sourceWidth": ref["width"], "sourceHeight": ref["height"],
                                   "gridWidth": shape[1], "gridHeight": shape[0]}
            result["image"+side].pop("grayPng", None)
        # Existing affine convention for a whole source grid; source remains real DN.
        result["analysisGrid"]["mode"] = "preview"
        result["operation"] = "difference-localization"
        if body.get("roi"):
            roi = body["roi"]
            result["analysisGrid"].update(fullWidth=shape[1], fullHeight=shape[0], scopeX=roi["x"], scopeY=roi["y"])
        bind_metadata(result, specs, refs, reference_side)
        results[method] = result
    assert_revisions(paths, refs)
    selected = dict(results[body.get("method", "smart")])
    if len(results) > 1:
        selected["comparison"] = compare_results(results)
    selected["totalElapsedMs"] = round((time.perf_counter()-started)*1000, 2)
    return selected


def analyze_roi(body, resolve):
    """Native DN refinement with context halo; never silently downsample a ROI."""
    from backend.services.blur_detection import config_from_dict
    specs, paths, refs, reference_side = prepare_pair(body, resolve)
    if reference_side != "B":
        raise image_diff.DiffError("原图精检请将 PAN 本体选为 A、NOSR 选为 B，确保按目标 PAN 原始尺度分析")
    body.get("_cancel_check", lambda: None)()
    cfg = config_from_dict(body.get("smartOptions"))
    roi = body.get("roi")
    if not isinstance(roi, dict):
        raise image_diff.DiffError("原图精检需要 ROI")
    try:
        x, y, w, h = [int(roi[k]) for k in ("x", "y", "w", "h")]
    except (ValueError, TypeError, KeyError) as e:
        raise image_diff.DiffError("ROI 必须是整数 xywh") from e
    if min(w, h) < 3 or max(w, h) > 2048:
        raise image_diff.DiffError("原图精检 ROI 边长需为 3–2048；请缩小框选范围后重试")
    aw, ah = refs[0]["width"], refs[0]["height"]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(aw, x+w), min(ah, y+h)
    if min(x1-x0, y1-y0) < 3:
        raise image_diff.DiffError("ROI 在影像范围外或过小")
    opening = image_diff._odd_kernel(body.get("openKernel", 3), 3, "开运算核")
    closing = image_diff._odd_kernel(body.get("closeKernel", 3), 3, "闭运算核")
    halo = cfg.window + 2 + 2 * (opening + closing)
    context = (max(0, x0-halo), max(0, y0-halo), min(aw, x1+halo), min(ah, y1+halo))
    a, va, _, rect_a = image_diff.read_source_window(paths[0], *context, max_edge=4096)
    bw, bh = refs[1]["width"], refs[1]["height"]
    rect_b_request = (int(np.floor(context[0]*bw/aw)), int(np.floor(context[1]*bh/ah)),
                      int(np.ceil(context[2]*bw/aw)), int(np.ceil(context[3]*bh/ah)))
    b, vb, _, rect_b = image_diff.read_source_window(paths[1], *rect_b_request, max_edge=4096)
    # Pixel-centre mapping uses actual source window offsets, not blind resize.
    from scipy.ndimage import map_coordinates
    yy = (np.arange(a.shape[0])+rect_a[1]+.5)*bh/ah - .5 - rect_b[1]
    xx = (np.arange(a.shape[1])+rect_a[0]+.5)*bw/aw - .5 - rect_b[0]
    gy, gx = np.meshgrid(yy, xx, indexing="ij")
    weights = map_coordinates(vb.astype(float), [gy, gx], order=1, mode="nearest")
    b = map_coordinates(np.where(vb, b, 0), [gy, gx], order=1, mode="nearest") / np.maximum(weights, 1e-12)
    vb = weights >= 1-1e-6
    crop = {"x": x0-rect_a[0], "y": y0-rect_a[1], "w": x1-x0, "h": y1-y0}
    result = image_diff.analyze_arrays(a, b, valid_a=va, valid_b=vb, method="smart", roi=crop,
        source="source-roi", auto_mask=body.get("autoMask") is True,
        smart_options=body.get("smartOptions"), reference_side=reference_side,
        scene_masks=load_scene_masks(body,resolve,refs,a.shape,window=rect_a),
        sensitivity=body.get("sensitivity", "standard"), threshold=body.get("threshold"),
        min_area_px=body.get("minAreaPx", 64), open_kernel=body.get("openKernel", 3), close_kernel=body.get("closeKernel", 3),
        scale_a=(1., 1.), scale_b=(bw/aw, bh/ah))
    for side, spec, ref in zip(("A", "B"), specs, refs):
        result["image"+side] = {**spec, "sourceWidth": ref["width"], "sourceHeight": ref["height"],
                               "gridWidth": x1-x0, "gridHeight": y1-y0}
        result["image"+side].pop("grayPng", None)
    result["operation"] = "difference-localization"
    result["analysisGrid"].update(mode="source", origin=[x0, y0], sourceRoi=[x0, y0, x1-x0, y1-y0],
                                   imageASource=[aw, ah], imageBSource=[bw, bh])
    windows = ((x0, y0, x1, y1), (x0*bw/aw, y0*bh/ah, x1*bw/aw, y1*bh/ah))
    bind_metadata(result, specs, refs, reference_side, windows=windows)
    assert_revisions(paths, refs)
    return result


def analyze_native_scan(body, resolve):
    """Bounded native-scale supplement, max-pooling evidence onto shared grid.

    Every tile is read with context halo through analyze_roi. At most 64 tiles
    per request; a larger scene needs an explicit smaller source ROI. The
    returned heat grid is a conservative overview and cannot be a precise mask.
    """
    started = time.perf_counter()
    specs, paths, refs, side = prepare_pair(body, resolve)
    if side != "B":
        raise image_diff.DiffError("原始尺度补扫请将 PAN 本体选为 A、NOSR 选为 B")
    aw, ah = refs[0]["width"], refs[0]["height"]
    roi = body.get("roi") or {"x":0,"y":0,"w":aw,"h":ah}
    try:
        x, y, w, h = [int(roi[k]) for k in ("x","y","w","h")]
    except (TypeError, ValueError, KeyError) as e:
        raise image_diff.DiffError("补扫 ROI 必须为整数 xywh") from e
    x, y = max(0,x), max(0,y)
    w, h = min(w,aw-x), min(h,ah-y)
    tile = 512
    count = int(np.ceil(w/tile)*np.ceil(h/tile))
    if min(w,h) < 3 or count > 64:
        raise image_diff.DiffError("原始尺度补扫最多 64 个 512² 块；请缩小当前视野 ROI")
    edge = min(2048,image_diff.resolve_max_edge(body.get("maxEdge")))
    scale = min(1.,edge/max(w,h))
    gh,gw = max(3,round(h*scale)),max(3,round(w*scale))
    score = np.zeros((gh,gw),np.float32)
    options = {**(body.get("smartOptions") or {})}
    options.setdefault("calibrationScale",1.)
    check = body.get("_cancel_check",lambda:None)
    for ty in range(y,y+h,tile):
        for tx in range(x,x+w,tile):
            check()
            # A final <3px sliver shares the previous tile's context/core.
            left,top = min(tx,x+w-3),min(ty,y+h-3)
            tw,th = min(tile,x+w-left),min(tile,y+h-top)
            part = analyze_roi({**body,"roi":{"x":left,"y":top,"w":tw,"h":th},
                                "autoMask":False,"smartOptions":options},resolve)
            pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(part["differenceMap"].split(",",1)[1]))),dtype=np.float32)/255
            yy = np.minimum(gh-1,((np.arange(th)+top-y+.5)*gh/h).astype(int))
            xx = np.minimum(gw-1,((np.arange(tw)+left-x+.5)*gw/w).astype(int))
            np.maximum.at(score,(yy[:,None],xx[None,:]),pixels)
    arrays=[]
    for p,r in zip(paths,refs):
        rx,ry = r["width"]/aw,r["height"]/ah
        a,v,_,_=image_diff.read_source_window(p,int(x*rx),int(y*ry),int(np.ceil((x+w)*rx)),int(np.ceil((y+h)*ry)),max_edge=edge)
        arrays.append(image_diff.resize_to((gh,gw),a,v))
    result=image_diff.analyze_arrays(arrays[0][0],arrays[1][0],valid_a=arrays[0][1],valid_b=arrays[1][1],
        method="smart",reference_side=side,source="source-tiled",smart_options=options,score_override=score,
        auto_mask=body.get("autoMask") is True,min_area_px=body.get("minAreaPx",64),
        threshold=body.get("threshold"),sensitivity=body.get("sensitivity","standard"),
        open_kernel=body.get("openKernel",3),close_kernel=body.get("closeKernel",3),scale_a=(w/gw,h/gh))
    for key,spec,r in zip(("imageA","imageB"),specs,refs):
        result[key]={**spec,"sourceWidth":r["width"],"sourceHeight":r["height"],"gridWidth":gw,"gridHeight":gh}
    result["operation"]="difference-localization"
    result["analysisGrid"].update(mode="source",origin=[x,y],sourceRoi=[x,y,w,h],imageASource=[aw,ah],
                                   imageBSource=[refs[1]["width"],refs[1]["height"]])
    bind_metadata(result,specs,refs,side,windows=((x,y,x+w,y+h),
        (x*refs[1]["width"]/aw,y*refs[1]["height"]/ah,(x+w)*refs[1]["width"]/aw,(y+h)*refs[1]["height"]/ah)))
    for r in result["regions"]: r["maskEligible"]=False
    result["analysisPrecision"].update(nativeRefined=False,nativeScanned=True,tiles=count,
        overviewAggregation="max",evidenceStatistics="overview-grid; native-score-only",sourcePixelsScanned=w*h)
    result["maskAvailability"] = part["maskAvailability"]
    result["analysisPrecision"]["sceneMaskStatus"] = "applied-per-tile; overview-overlap-unavailable"
    result["warnings"].append("原始尺度补扫采用带 Halo 的重叠上下文和最大值概览投影；概览候选仍需原图 ROI 精检，非精确掩码。")
    result["elapsedMs"]=round((time.perf_counter()-started)*1000)
    assert_revisions(paths,refs)
    return result
