"""Execution constraint for verified, coincident embedded GeoTIFF grids."""
import hashlib
import json
import math
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from PIL import Image

from backend.pathguard import PathDeniedError
from backend.services import batch_mask, mss_water

VERSION = 'mss-land.v1'
METHOD = 'exact_embedded_geotiff_footprint'
LAND, WATER, UNCERTAIN = 0, 1, 2
WaterUnavailable = mss_water.WaterUnavailable


def _profile(satellite, sensor):
    try:
        path = Path(os.environ['SR_MSS_EXEC_PROFILE']).resolve(strict=True)
        with path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('profile too large')
        profile = json.loads(raw)
        for key in ('profile_id', 'verification_ref'):
            if not isinstance(profile[key], str) or not profile[key].strip():
                raise ValueError(key)
        if (profile['satellite'], profile['sensor'], profile['registration_method']) != (
                satellite, sensor, METHOD):
            raise ValueError('profile scope mismatch')
        fields = ('land_threshold', 'water_threshold', 'shoreline_pan_pixels',
                  'registration_error_pan_pixels')
        for key in fields:
            value = profile[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(key)
        if not -1 <= profile['land_threshold'] < profile['water_threshold'] <= 1:
            raise ValueError('threshold ordering')
        if any(not 0 <= profile[key] <= 256 for key in fields[2:]):
            raise ValueError('buffer outside supported range')
        profile = {key: profile[key] for key in (*fields, 'profile_id', 'verification_ref',
                                                  'satellite', 'sensor', 'registration_method')}
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise WaterUnavailable('execution_profile', '缺少适用且经验证的 MSS 执行 Profile') from exc
    return profile, {'path': str(path), 'size': stat.st_size,
                     'mtime_ns': stat.st_mtime_ns, 'sha256': hashlib.sha256(raw).hexdigest()}


def _preflight(pan_path):
    tifffile = mss_water.require_tifffile()

    plan = mss_water.plan_water(pan_path)
    key, _, paths, sources, _ = plan
    pan, mss, xml, satellite, sensor = paths
    profile, profile_source = _profile(satellite, sensor)
    bands, gain, bias, identity = mss_water.read_calibration(xml, satellite, sensor, mss.parent.name)
    if not all(identity.values()):
        raise WaterUnavailable('xml_identity', 'MSS 执行要求 XML 明确声明同景 SceneID 和 ProductType')
    with tifffile.TiffFile(mss) as mss_tif, tifffile.TiffFile(pan) as pan_tif:
        mss_page, pan_page = mss_tif.pages[0], pan_tif.pages[0]
        alignment = mss_water._alignment(pan, mss_page, mss_water.external_spatial_metadata(sources['mss']))
        if alignment.get('method') != METHOD:
            raise WaterUnavailable('registration_required', 'MSS 与 PAN 缺少可信执行级配准')
        if (len(pan_tif.pages) != 1 or pan_page.subifds or pan_page.is_tiled
                or int(pan_page.photometric) != 1 or pan_page.extrasamples
                or pan_page.dtype is None or pan_page.dtype.kind not in 'uif'):
            raise WaterUnavailable('pan_validity', 'PAN 栅格或有效性掩膜不受支持')
        mss_water._read_nodata(pan_page)
        if len(mss_tif.pages) != 1 or mss_page.subifds:
            raise WaterUnavailable('registration_required', 'MSS 含有不支持的附加网格')
        mss_water._read_nodata(mss_page)
        with mss.open('rb') as stream:
            next(mss_water._mss_rows(mss_page, stream))
        ratios = (pan_page.imagewidth / mss_page.imagewidth, pan_page.imagelength / mss_page.imagelength)
        margin = profile['shoreline_pan_pixels'] + profile['registration_error_pan_pixels']
        # Use a rectangular envelope covering the entire MSS pixel plus the
        # declared error and shoreline margin, rounded outwards on each axis.
        radius = [math.ceil(ratio + margin) for ratio in ratios]
        if max(radius) > 512 or (2 * radius[1] + 1) * pan_page.imagewidth > 8_000_000:
            raise WaterUnavailable('registration_required', 'MSS 像元与配准保护距离超出处理预算')
        alignment.update({'mss_width': mss_page.imagewidth, 'mss_height': mss_page.imagelength,
                          'radius_pan_pixels': radius, 'resampling': 'nearest_pixel_center'})
    for role, path in (('pan', pan), ('mss', mss), ('xml', xml)):
        st = path.stat()
        sources[role].update(path=str(path), size=st.st_size, mtime_ns=st.st_mtime_ns,
                             inode=st.st_ino, ctime_ns=st.st_ctime_ns)
    sources['profile'] = profile_source
    if mss_water.plan_water(pan_path)[0] != key:
        raise WaterUnavailable('source_changed', 'MSS 或 PAN 在预检期间变化')
    evidence = {'version': VERSION, 'sources': sources, 'p0_revision': key, 'profile': profile,
                'alignment': alignment, 'xml_identity': identity,
                'band_mapping': {'green': 2, 'nir': 4, 'bands': bands, 'gain': gain, 'bias': bias}}
    evidence = json.loads(json.dumps(evidence, allow_nan=False))
    evidence['revision'] = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return evidence, plan


def preflight(pan_path):
    try:
        return _preflight(pan_path)[0]
    except WaterUnavailable:
        raise
    except (PathDeniedError, OSError, ValueError, TypeError, IndexError, StopIteration) as exc:
        raise WaterUnavailable('execution_input', '无法验证执行级 MSS 输入') from exc


def _artifact(path, *, contents=True):
    stat = path.stat()
    fact = {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'inode': stat.st_ino, 'ctime_ns': stat.st_ctime_ns}
    if contents:
        fact['sha256'] = batch_mask._sha256(path)
    return fact


def verify(pan_path, evidence, *, contents=True):
    current = preflight(pan_path)
    if any(current[key] != evidence.get(key) for key in current):
        raise WaterUnavailable('source_changed', 'MSS、PAN 或执行 Profile 已变化')
    if 'candidate_mask' in evidence or 'final_mask' in evidence:
        try:
            for key in ('candidate_mask', 'final_mask', 'classification', 'preview'):
                asset = evidence[key]
                if key == 'candidate_mask' and asset is None:
                    continue
                current_asset = _artifact(Path(asset['path']), contents=contents)
                if any(value != asset.get(name) for name, value in current_asset.items()):
                    raise ValueError(key)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise WaterUnavailable('mask_changed', 'MSS 执行掩膜或其证据已变化') from exc
    return current


def _open_array(stack, tifffile, path, temporary, name, shape):
    try:
        pixels = tifffile.memmap(path, mode='r')
    except ValueError:
        tif = stack.enter_context(tifffile.TiffFile(path))
        page = tif.pages[0]
        if (page.shape != shape or page.is_tiled
                or page.rowsperstrip * shape[1] * page.dtype.itemsize > 128 * 1024**2):
            raise WaterUnavailable('execution_raster', '栅格布局超出执行掩膜读取预算')
        pixels = page.asarray(out=str(temporary / name), maxworkers=1)
    if pixels.shape != shape:
        pixels._mmap.close()
        raise WaterUnavailable('execution_raster', '执行栅格尺寸不匹配')
    stack.callback(pixels._mmap.close)
    return pixels


def _nearest(source, target):
    return (2 * np.arange(target, dtype=np.int64) + 1) * source // (2 * target)


def _render(staged, candidate, candidates, evidence, pan_path, mss_path, water_profile):
    tifffile = mss_water.require_tifffile()
    from scipy.ndimage import maximum_filter

    grid, profile = evidence['alignment'], evidence['profile']
    width, height = grid['width'], grid['height']
    shape = height, width
    mw, mh = grid['mss_width'], grid['mss_height']
    rx, ry = grid['radius_pan_pixels']
    cols, rows = _nearest(mw, width), _nearest(mh, height)
    edge = min(1, 1024 / max(shape))
    ph, pw = max(1, round(height * edge)), max(1, round(width * edge))
    preview = np.zeros((ph, pw, 4), np.uint8)
    preview_rows, preview_cols = _nearest(height, ph), _nearest(width, pw)
    palette = np.array([(0, 0, 0, 0), (0, 189, 255, 150), (245, 158, 11, 150)], np.uint8)
    candidates, _ = batch_mask._normalized_candidates(candidates, width, height)
    boxes = [(item['bbox'][1], item['bbox'][3]) for item in candidates]
    counts = np.zeros((len(candidates), 2), np.int64)
    candidate_pixels = final_pixels = 0
    with tifffile.TiffFile(pan_path) as tif:
        page = tif.pages[0]
        nodata, spatial = mss_water._read_nodata(page), mss_water._tags(page)
        needed = (page.dtype.itemsize + 3) * width * height + 4 * mw * mh + 32 * 1024**2
    if shutil.disk_usage(staged).free < needed:
        raise WaterUnavailable('storage_budget', '执行掩膜暂存空间不足')
    block = max(1, min(128, 2_000_000 // width))
    with ExitStack() as stack:
        ndwi = np.memmap(staged / 'ndwi.raw', mode='w+', dtype='<f4', shape=(mh, mw))
        stack.callback(ndwi._mmap.close)
        # Recompute from the frozen MSS instead of consuming an advisory cache
        # whose bytes could have changed since publication.
        calibration = evidence['band_mapping']
        with tifffile.TiffFile(mss_path) as tif, mss_path.open('rb') as stream:
            page = tif.pages[0]
            mss_nodata = mss_water._read_nodata(page)
            for index, (green, nir) in enumerate(mss_water._mss_rows(page, stream)):
                ndwi[index] = mss_water.ndwi_window(
                    green, nir, mss_nodata, calibration['gain'], calibration['bias'], water_profile)[0]
        pan = _open_array(stack, tifffile, pan_path, staged, 'pan.raw', shape)
        selected = (_open_array(stack, tifffile, candidate, staged, 'candidate.raw', shape)
                    if candidate is not None else None)
        classification = mss_water._create_tiff(stack, staged / 'classification.tif', shape, 'u1', spatial, None)
        final = mss_water._create_tiff(stack, staged / 'final.tif', shape, 'u1', spatial, None)
        for y in range(0, height, block):
            stop = min(y + block, height)
            upper, lower = max(0, y - ry), min(height, stop + ry)
            halo = slice(y - upper, stop - upper)
            values = ndwi[np.ix_(rows[upper:lower], cols)].astype(np.float64)
            land = values <= profile['land_threshold']
            water = values >= profile['water_threshold']
            window = (2 * ry + 1, 2 * rx + 1)
            safe_land = ~maximum_filter(~land, size=window, mode='constant', cval=1)[halo]
            safe_water = ~maximum_filter(~water, size=window, mode='constant', cval=1)[halo]
            classes = np.full(safe_land.shape, UNCERTAIN, np.uint8)
            classes[safe_land] = LAND
            classes[safe_water] = WATER
            samples = pan[y:stop]
            valid = np.isfinite(samples)
            if nodata is not None:
                valid &= samples != nodata
            classes[~valid] = UNCERTAIN
            proposed = (selected[y:stop] == 255 if selected is not None
                        else np.zeros(safe_land.shape, dtype=bool))
            execute = proposed & safe_land & valid
            classification.write(classes.tobytes())
            final.write((execute.astype(np.uint8) * 255).tobytes())
            candidate_pixels += int(np.count_nonzero(proposed))
            final_pixels += int(np.count_nonzero(execute))
            for index, item in enumerate(candidates):
                if boxes[index][1] < y or boxes[index][0] >= stop:
                    continue
                geometry = np.asarray(batch_mask._strip_image(width, stop - y, [item], y)) != 0
                counts[index] += (np.count_nonzero(geometry & proposed), np.count_nonzero(geometry & execute))
            keep = (preview_rows >= y) & (preview_rows < stop)
            sample = np.ix_(preview_rows[keep] - y, preview_cols)
            pixels = palette[classes[sample]]
            proposed_only = proposed[sample] & ~execute[sample]
            hatch = (np.flatnonzero(keep)[:, None] + np.arange(pw)) % 2 == 0
            pixels[proposed_only & hatch] = (187, 96, 247, 210)
            pixels[execute[sample]] = (42, 209, 110, 200)
            preview[keep] = pixels
    if candidate is not None and not candidate_pixels:
        raise WaterUnavailable('execution_mask', '原始候选掩膜为空')
    Image.fromarray(preview).save(staged / 'preview.png')
    details = [{'candidate_id': item['candidate_id'], 'candidate_pixels': int(before),
                'executable_pixels': int(after), 'state': (
                    'excluded' if not after else 'clipped' if after < before else 'retained')}
               for item, (before, after) in zip(candidates, counts)]
    return {'candidates': len(details), 'excluded': sum(d['state'] == 'excluded' for d in details),
            'clipped': sum(d['state'] == 'clipped' for d in details),
            'executable': sum(d['state'] != 'excluded' for d in details),
            'candidate_pixels': candidate_pixels, 'executable_pixels': final_pixels, 'details': details}


def build(pan_path, candidate_mask_path, output_dir, candidates):
    """Publish a content-bound constraint bundle after rechecking its inputs."""
    try:
        evidence, plan = _preflight(pan_path)
        candidate = Path(candidate_mask_path) if candidate_mask_path is not None else None
        output = Path(output_dir)
        candidate_fact = _artifact(candidate) if candidate is not None else None
        grid = evidence['alignment']
        if candidate is not None:
            batch_mask.verify_mask(candidate, width=grid['width'], height=grid['height'],
                                   sha256=candidate_fact['sha256'])
        elif candidates:
            raise WaterUnavailable('execution_mask', '候选区域缺少原始候选掩膜')
        geometry_sha256 = hashlib.sha256(
            json.dumps(candidates, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()

        def reuse():
            current = json.loads((output / 'evidence.json').read_text(encoding='utf-8'))
            comparable = {**current}
            for key in ('final_mask', 'classification', 'preview'):
                asset = comparable.get(key, {})
                if all(asset.get(field) == evidence[key][field] for field in ('path', 'sha256')):
                    comparable[key] = evidence[key]
            if comparable != evidence:
                raise WaterUnavailable('mask_changed', '已发布 MSS 执行产物与当前证据不一致')
            verify(pan_path, current)
            return current

        mss_water.generate_water(plan)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.mss-land-', dir=output.parent) as temp:
            base = Path(temp)
            staged = base / 'bundle'
            staged.mkdir()
            evidence['stats'] = _render(staged, candidate, candidates, evidence, plan[2][0], plan[2][1], plan[4])
            verify(pan_path, evidence)
            if candidate is not None and _artifact(candidate) != candidate_fact:
                raise WaterUnavailable('mask_changed', '生成期间原始候选掩膜变化')
            evidence['candidate_mask'] = candidate_fact
            evidence['geometry_sha256'] = geometry_sha256
            for key, name in (('final_mask', 'final.tif'), ('classification', 'classification.tif'), ('preview', 'preview.png')):
                evidence[key] = {**_artifact(staged / name), 'path': str((output / name).resolve())}
            evidence['encoding'] = {'LAND': LAND, 'WATER': WATER, 'UNCERTAIN': UNCERTAIN}
            (staged / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
            # Decode scratch arrays may reside beside the artifacts. Never
            # publish them as part of the execution bundle.
            for scratch in staged.glob('*.raw'):
                scratch.unlink()
            try:
                staged.rename(output)
            except OSError:
                if not output.is_dir():
                    raise
                return reuse()
        verify(pan_path, evidence)
        return evidence
    except WaterUnavailable:
        raise
    except (PathDeniedError, OSError, ValueError, TypeError, IndexError, StopIteration) as exc:
        raise WaterUnavailable('execution_raster', '无法生成 MSS 约束的最终执行掩膜') from exc
