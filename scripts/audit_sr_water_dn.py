"""Locate DN bias in the latest verified GF07 PAN repair on this machine."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen


QUEUE_URL = 'http://127.0.0.1:8000/api/queue'
INPUTS = ('before', 'nosr', 'model', 'published', 'mask')


def report(message):
    print(message, file=sys.stderr, flush=True)


def signature(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def published_job(config):
    data = config.read_bytes()
    root = ET.fromstring(data)
    scene = Path(root.findtext('DatarootLQ') or '')
    if (root.findtext('RepairMode') != 'pan-nosr-v1' or not scene.is_absolute()
            or not re.match(r'(?:JX)?GF07', scene.name, re.I)):
        raise ValueError('not a GF07 PAN repair')
    receipt_path = scene / 'Debug' / ('_SRREPAIR_' + hashlib.sha256(data).hexdigest() + '.json')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('mode') != 'pan-nosr-v1' or Path(receipt['output']).resolve() != (scene / 'PAN.tif').resolve():
        raise ValueError('receipt does not identify this scene\'s PAN.tif')
    for name, key in (('output', 'output_signature'), ('backup', 'backup_signature'), ('nosr', 'nosr_signature')):
        if signature(Path(receipt[name])) != receipt[key]:
            raise ValueError('raster changed after publication: ' + receipt[name])
    return root, scene, receipt_path, receipt


def find_job(queue_url, job_id):
    with urlopen(queue_url, timeout=10) as response:
        tasks = json.load(response)['tasks']
    verified = []
    rejected = []
    for task in tasks:
        if task.get('state') != 'COMPLETED' or not task.get('config_xml') or (
                job_id is not None and str(task.get('job_id')) != job_id):
            continue
        config = Path(task['config_xml'])
        try:
            _, _, receipt, _ = published_job(config)
            verified.append((receipt.stat().st_mtime_ns, config, task))
        except (OSError, ValueError, ET.ParseError, KeyError) as exc:
            if str(exc) != 'not a GF07 PAN repair':
                rejected.append('job {}: {}'.format(task.get('job_id'), exc))
    if not verified:
        raise ValueError('No completed GF07 job with a matching publication receipt' +
                         ('\n' + '\n'.join(rejected[:5]) if rejected else ''))
    _, config, task = max(verified, key=lambda item: item[0])
    return config, task


def job_python(config, task):
    batch = Path(task.get('batch_script') or config.with_suffix('.sh'))
    job_id = task.get('job_id')
    if job_id is not None:
        log_dir = Path(task.get('log_dir') or batch.parent)
        for log in (log_dir / '{}.{}.out'.format(batch.stem, job_id), log_dir / '{}.out'.format(job_id)):
            if log.is_file():
                with log.open(encoding='utf-8', errors='replace') as stream:
                    match = re.search(r'^SR_PYTHON=(.+)$', stream.read(65536), re.M)
                if match:
                    return match.group(1).strip()
    if batch.is_file():
        match = re.search(r'^echo "SR_PYTHON=([^"\r\n]+)"$', batch.read_text(encoding='utf-8'), re.M)
        if match:
            return match.group(1)
    return None


def prepare_python(config, task, roi):
    for python in dict.fromkeys((job_python(config, task), os.environ.get('SR_PYTHON'),
                                sys.executable, '/opt/sr-venv/bin/python')):
        if not python:
            continue
        try:
            check = subprocess.run([python, '-c', 'import cv2, numpy; from osgeo import gdal, gdal_array'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            report('Audit Python: ' + python)
            if python != sys.executable:
                command = [python, '-u', str(Path(__file__).resolve()), '--config', str(config), '--_python-ready']
                if roi:
                    command.extend(['--roi'] + [str(n) for n in roi])
                return subprocess.call(command)
            return None
        report('Dependency probe failed for {}: {}'.format(python, check.stderr.decode(errors='replace').strip()))
    raise ValueError('No Python with cv2, NumPy and working GDAL array bindings')


def stage_files(config):
    root, scene, receipt_path, receipt = published_job(config)
    published_at = receipt_path.stat().st_mtime_ns
    submitted_at = config.stat().st_mtime_ns
    suffix = (root.findtext('Suffix') or '260318').strip()
    ignored = {'DatarootLQ', 'MaskPath', 'Suffix', 'DeleteOriTifNeeded', 'RepairMode', 'RepairBody',
               'RepairNOSR', 'RepairRevision', 'RepairNOSRStage', 'LandConstraint'}

    def shared_settings(xml):
        return [(node.tag, (node.text or '').strip()) for node in xml if node.tag not in ignored]

    expected = shared_settings(root)
    candidates = []
    for xml in receipt_path.parent.glob('.repair-*/inference.xml'):
        try:
            stage_time = xml.stat().st_mtime_ns
            if not submitted_at <= stage_time <= published_at:
                continue
            stage = ET.parse(str(xml)).getroot()
            private = xml.parent / scene.name
            if (Path(stage.findtext('DatarootLQ') or '') != private
                    or (stage.findtext('Suffix') or '').strip() != suffix or shared_settings(stage) != expected):
                continue
            meta = ET.parse(str(private / (scene.name + '_meta.xml'))).getroot()
            solar = meta.find('.//SolarAzimuth')
            if solar is None:
                continue
            stem = scene.name if solar.text else 'PAN'
            ext = '.tif'
            opt = stage.findtext('OPT')
            if opt and Path(opt).is_file():
                text = Path(opt).read_text(encoding='utf-8', errors='replace')
                match = re.search(r'^(?:tif_type|tiftype)\s*:\s*([^#\r\n]+)', text, re.M)
                if match:
                    ext = match.group(1).strip().strip('\'"')
            model = private / (stem + '_' + suffix + ext)
            if stage_time <= model.stat().st_mtime_ns <= published_at:
                candidates.append(model)
        except (OSError, ET.ParseError):
            continue
    if len(candidates) != 1:
        raise ValueError('Expected one verified model-stage candidate in {}, found {}{}'.format(
            receipt_path.parent, len(candidates), ': ' + ', '.join(map(str, candidates)) if candidates else ''))
    model = candidates[0]
    work = model.parent.parent
    mask = next((p for p in (work / 'frozen_pan_mask.tif', work / 'mask_body.tif') if p.is_file()), None)
    if mask is None:
        mask = Path(root.findtext('MaskPath') or '')
        revision = json.loads(root.findtext('RepairRevision') or '{}').get('mask')
        if revision and signature(mask)[:2] != revision[:2]:
            raise ValueError('Job mask changed and no frozen copy is available: ' + str(mask))
        if not revision:
            report('The job config has no mask signature. Using its recorded mask path.')
    if (work / 'inferred_body.tif').is_file():
        report('The compositor aligned the model output to the PAN grid. Auditing that aligned raster: ' + str(model))
        model = work / 'inferred_body.tif'
    report('Scene: ' + str(scene))
    report('Publication receipt: ' + str(receipt_path))
    return (Path(receipt['backup']), Path(receipt['nosr']), model, Path(receipt['output']), mask)


def open_raster(path):
    raster = gdal.Open(path, gdal.GA_ReadOnly)
    if raster is None or raster.RasterCount != 1:
        raise ValueError('Expected a readable single-band raster: ' + path)
    return raster


def size(raster):
    return raster.RasterXSize, raster.RasterYSize


def read(raster, x, y, w, h):
    result = raster.GetRasterBand(1).ReadAsArray(x, y, w, h)
    if result is None or result.shape != (h, w):
        raise ValueError('Could not read the selected window')
    return result


def valid(raster, pixels):
    keep = np.isfinite(pixels)
    nodata = raster.GetRasterBand(1).GetNoDataValue()
    return keep if nodata is None else keep & (pixels != nodata)


def audit(before, nosr, model, published, mask, roi):
    x, y, w, h = roi
    pw, ph = size(before)
    nw, nh = size(nosr)
    if (2 * nw, 2 * nh) != (pw, ph) or any(size(ds) != (pw, ph) for ds in (model, published, mask)):
        raise ValueError('Expected exact 2x NOSR and one shared PAN grid for before, model, published and mask')
    if not (0 <= x < x + w <= pw and 0 <= y < y + h <= ph and max(w, h) <= 2048):
        raise ValueError('ROI must be inside PAN and at most 2048 pixels on each axis')

    # Cubic uses a four-sample kernel. Keep two LR pixels on each side and
    # crop on the global 2x grid, including when the PAN ROI starts on an odd pixel.
    lx, ly = max(0, x // 2 - 2), max(0, y // 2 - 2)
    rx, by = min(nw, (x + w + 1) // 2 + 2), min(nh, (y + h + 1) // 2 + 2)
    raw = read(nosr, lx, ly, rx - lx, by - ly)
    if not valid(nosr, raw).all():
        raise ValueError('Choose a water ROI whose NOSR window and cubic halo contain no NoData')
    control = cv2.resize(raw, (2 * (rx - lx), 2 * (by - ly)), interpolation=cv2.INTER_CUBIC)
    dx, dy = x - 2 * lx, y - 2 * ly
    control = control[dy:dy + h, dx:dx + w].astype(np.float64)
    original = read(before, x, y, w, h).astype(np.float64)
    inferred = read(model, x, y, w, h).astype(np.float64)
    final = read(published, x, y, w, h).astype(np.float64)
    labels = read(mask, x, y, w, h)
    keep = valid(before, original) & valid(model, inferred) & valid(published, final) & np.isfinite(labels)
    band = before.GetRasterBand(1)
    if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        keep &= band.GetMaskBand().ReadAsArray(x, y, w, h) != 0
    inside, outside = keep & (labels > 0), keep & (labels == 0)
    if not inside.any() or not outside.any():
        raise ValueError('Pick a water-only ROI crossing the mask boundary, with valid pixels on both sides')

    differences = {
        'bicubic_NOSR_minus_before': control - original,
        'model_file_minus_bicubic_NOSR': inferred - control,
        'published_minus_model_file': final - inferred,
        'published_minus_before': final - original,
    }
    return {name: {side: {'mean_dn': float(values[region].mean()),
                          'median_dn': float(np.median(values[region])),
                          'changed_pixels': int(np.count_nonzero(values[region])),
                          'valid_pixels': int(region.sum())}
                   for side, region in (('inside', inside), ('outside', outside))}
            for name, values in differences.items()}


def candidate_rois(before, mask):
    width, height = size(before)
    if size(mask) != (width, height):
        raise ValueError('The publication mask must be on the PAN pixel grid')
    w, h = min(192, width), min(192, height)
    scale = max(1, max(width, height) / 768)
    mw, mh = max(1, round(width / scale)), max(1, round(height / scale))
    sample = mask.GetRasterBand(1).ReadAsArray(buf_xsize=mw, buf_ysize=mh)
    if sample is None:
        raise ValueError('Could not read a mask overview')
    coarse = (sample != 0).astype(np.uint8)
    kernel = np.ones((3, 3), dtype=np.uint8)
    ys, xs = np.nonzero(cv2.dilate(coarse, kernel) != cv2.erode(coarse, kernel))
    if not len(xs):
        raise ValueError('No mask edge was found in the overview. Supply --roi X Y W H for a narrow edge.')
    choices = {}
    # Spread the PAN reads across the boundary without making the darkest or
    # largest post-SR jump influence which region is audited.
    for i in np.linspace(0, len(xs) - 1, min(96, len(xs)), dtype=int):
        x = min(width - w, max(0, int((xs[i] + 0.5) * width / mw) - w // 2))
        y = min(height - h, max(0, int((ys[i] + 0.5) * height / mh) - h // 2))
        choices.setdefault((x // w, y // h), (x, y, w, h))
    qualified = []
    for roi in choices.values():
        labels = read(mask, *roi)
        pixels = read(before, *roi).astype(np.float64)
        if not valid(before, pixels).all() or min(np.count_nonzero(labels), np.count_nonzero(labels == 0)) < labels.size / 10:
            continue
        dx = np.abs(np.diff(pixels, axis=1)).ravel()
        dy = np.abs(np.diff(pixels, axis=0)).ravel()
        gradients = np.concatenate((dx, dy))
        if gradients.size:
            qualified.append((float(np.percentile(gradients, 90)), roi))
    chosen = []
    for texture, roi in sorted(qualified):
        if any(abs(roi[0] - prior[0]) < w and abs(roi[1] - prior[1]) < h for _, prior in chosen):
            continue
        chosen.append((texture, roi))
        if len(chosen) == 3:
            break
    if not chosen:
        raise ValueError('No valid ROI straddled the publication mask. Supply --roi X Y W H to target a specific edge.')
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in INPUTS:
        parser.add_argument('--' + name)
    parser.add_argument('--roi', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'))
    parser.add_argument('--job-id', help='audit this completed SR job instead of the latest verified GF07 repair')
    parser.add_argument('--config', type=Path, help='read one job XML directly if the local queue API is unavailable')
    parser.add_argument('--queue-url', default=QUEUE_URL)
    parser.add_argument('--_python-ready', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    explicit = [getattr(args, name) for name in INPUTS]
    config = args.config
    if any(path is not None for path in explicit):
        if not all(explicit) or not args.roi or config or args.job_id:
            parser.error('Specify all five TIFF paths and --roi, or use automatic discovery')
        paths = [Path(path).resolve() for path in explicit]
    else:
        task = {}
        if config is None:
            report('Checking the local SR queue: ' + args.queue_url)
            config, task = find_job(args.queue_url, args.job_id)
            report('Matched completed job: {}'.format(task.get('job_id')))
        config = config.resolve()
        report('Job config: ' + str(config))
        if not args._python_ready:
            child_status = prepare_python(config, task, args.roi)
            if child_status is not None:
                return child_status
        paths = stage_files(config)
    for name, path in zip(INPUTS, paths):
        if not path.is_file():
            parser.error('--{} file not found: {}'.format(name, path))

    global cv2, np, gdal
    import cv2
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.SetCacheMax(64 * 1024 * 1024)
    rasters = []
    for name, path in zip(INPUTS, paths):
        report('Opening {}: {}'.format(name, path))
        rasters.append(open_raster(str(path)))
    if args.roi:
        report('Reading ROI (x, y, width, height): {}'.format(tuple(args.roi)))
        result = audit(*rasters, args.roi)
    else:
        report('Searching for low-texture ROIs that straddle the publication mask')
        results = []
        for texture, roi in candidate_rois(rasters[0], rasters[-1]):
            report('Reading ROI (x, y, width, height): {}'.format(roi))
            try:
                differences = audit(*rasters, roi)
            except ValueError as exc:
                report('Skipped ROI {}: {}'.format(roi, exc))
                continue
            results.append({'roi': roi, 'pre_repair_gradient_p90_dn': texture, 'differences': differences})
        if not results:
            raise ValueError('The detected ROIs did not pass pixel validity checks')
        result = {'config': str(config), 'files': dict(zip(INPUTS, map(str, paths))),
                  'roi_selection': 'Low-texture mask edges, water not confirmed', 'samples': results}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, ImportError, KeyError, ET.ParseError) as error:
        report('Audit failed: ' + str(error))
        sys.exit(1)
