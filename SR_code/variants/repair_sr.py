#!/usr/bin/env python
"""PAN repair adapter. Python 3.6 compatible; GDAL stays in the SR environment.

Inference runs in a private scene with an independent NOSR copy. Original PAN,
NOSR and metadata are untouched until a validated candidate is published.
"""
import argparse
import hashlib
import json
import math
import os
import errno
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

MODE = 'pan-nosr-v1'
_GF07_SCENE_RE = re.compile(r'(?:JX)?GF07[A-Z0-9]*_', re.IGNORECASE)

#: Inference card, fixed. Slurm injects an integer CUDA_VISIBLE_DEVICES without
#: binding the device, so every job lands on card 0, where the LLM service
#: lives; the SR model then gets the leftovers and a tile's forward OOMs.
#: Card 1 is kept free of the LLM service.
INFERENCE_GPU = 1

#: Model-side markers for a swallowed per-tile failure. The bundle's
#: `test()` catches RuntimeError, leaves `fake_H` from the previous tile and
#: keeps running, so an OOM would otherwise publish a stale block as success.
INFERENCE_ERROR_MARKERS = ('out of memory', 'runtimeerror', 'exception is')

# Linux FICLONE creates a separate inode while sharing the source's data extents.
FICLONE = 0x40049409
CLONE_UNAVAILABLE = {errno.EINVAL, errno.ENOTTY, errno.EOPNOTSUPP, errno.ENOSYS,
                     errno.EXDEV, errno.EPERM}
COMPOSE_WIDTH, COMPOSE_HEIGHT = 1024, 256


def signature(path):
    st = os.stat(str(path))
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def verify_land_constraint(proof, body, nosr, mask):
    if not isinstance(proof, dict) or proof.get('version') != 'land-constraint.v1':
        raise ValueError('Unsupported land constraint proof')
    sources = proof.get('sources')
    if not isinstance(sources, dict) or not {'pan', 'nosr', 'mss', 'xml'} <= set(sources):
        raise ValueError('Incomplete land constraint sources')
    for role, identity in sources.items():
        if (not isinstance(identity, dict) or not isinstance(identity.get('path'), str)
                or type(identity.get('size')) is not int or type(identity.get('mtime_ns')) is not int):
            raise ValueError('Invalid land constraint source: ' + role)
        path = Path(identity['path']).resolve()
        if (role == 'pan' and path != body.resolve()) or (role == 'nosr' and path != nosr):
            raise ValueError('Land constraint identifies a different repair input: ' + role)
        stat = path.stat()
        actual = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                  'inode': stat.st_ino, 'ctime_ns': stat.st_ctime_ns}
        if any(value != identity[key] for key, value in actual.items() if key in identity):
            raise ValueError('Land constraint source changed: ' + role)
        if 'sha256' in identity and file_sha256(path) != identity['sha256']:
            raise ValueError('Land constraint source content changed: ' + role)
    absent = proof.get('sidecars_absent', [])
    if not isinstance(absent, list) or any(not isinstance(path, str) for path in absent):
        raise ValueError('Invalid land constraint sidecars')
    if any(Path(path).exists() or Path(path).is_symlink() for path in absent):
        raise ValueError('A spatial or validity sidecar appeared after land constraint preparation')
    verify_land_mask(proof, mask)


def verify_land_mask(proof, mask):
    if file_sha256(mask) != proof.get('mask_sha256'):
        raise ValueError('Land constraint mask hash changed')


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def receipt_path(config):
    config = Path(config)
    scene = Path(ET.parse(str(config)).getroot().findtext('DatarootLQ'))
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    return scene / 'Debug' / ('_SRREPAIR_' + digest + '.json')


def repair_body(scene, pinned_name=None):
    """Current body raster: RC -> PAN.tif, SC -> <scene>.tif.

    Mirrors util.check_sr_previous_step: an empty <SolarAzimuth> means the
    previous step was RC, a value means SC. The detector must agree with the
    SR script, because the body is the raster this repair replaces. Repairing
    an SC scene against PAN.tif (the old hardcoded name) both compares the mask
    with the wrong grid and would publish into an unrelated file.
    """
    scene = Path(scene)
    meta = scene / (scene.name + '_meta.xml')
    if not meta.is_file():
        raise ValueError('Missing repair input: ' + str(meta))
    try:
        root = ET.parse(str(meta)).getroot()
    except ET.ParseError as exc:
        raise ValueError('Cannot parse ' + str(meta) + ': ' + str(exc))
    # Descendant search, not root.findall: production meta.xml nests the tag
    # (minidom getElementsByTagName walks the whole tree). ElementTree's
    # findall only looks at direct children, which made real scenes look like
    # they had no SolarAzimuth at all.
    node = next(root.iter('SolarAzimuth'), None)
    if node is None:
        raise ValueError('meta.xml has no SolarAzimuth; cannot choose the repair body')
    step = 'RC' if node.text is None else 'SC'
    if pinned_name is not None:
        if pinned_name != 'PAN.tif' or not _GF07_SCENE_RE.match(scene.name):
            raise ValueError('Invalid pinned GF07 repair body')
        return step, scene / pinned_name
    name = 'PAN.tif' if step == 'RC' else scene.name + '.tif'
    return step, scene / name


def _open(gdal, path, update=False):
    ds = gdal.Open(str(path), gdal.GA_Update if update else gdal.GA_ReadOnly)
    if ds is None:
        raise ValueError('Cannot open raster: ' + str(path))
    if ds.RasterCount != 1:
        raise ValueError('PAN repair requires single-band rasters: ' + str(path))
    return ds


def _size(ds):
    return ds.RasterXSize, ds.RasterYSize


def _reduce_byte_mask_2x(src, out, width, height, constrained):
    import numpy as np

    # Read up to 128 source rows at once, bounded by the width of the mask.
    rows_per_batch = max(1, min(64, (8 * 1024 * 1024) // (4 * width)))
    selected = False
    for top in range(0, height, rows_per_batch):
        rows = min(rows_per_batch, height - top)
        data = src.ReadAsArray(0, top * 2, width * 2, rows * 2)
        if constrained and np.any((data != 0) & (data != 255)):
            raise ValueError('Land constraint requires a binary 0/255 publication mask')
        mapped = data[::2, ::2] | data[1::2, ::2] | data[::2, 1::2] | data[1::2, 1::2]
        if not constrained:
            # Legacy byte masks treat every nonzero value as selected.
            mapped = (mapped != 0).astype('uint8') * 255
        selected = selected or bool(np.any(mapped))
        out.WriteArray(mapped, 0, top)
    return selected


def reduce_mask(gdal, source, destination, width, height, *, constrained=False):
    """Conservative area mapping: a selected high-resolution pixel is not lost."""
    import numpy as np
    src = _open(gdal, source)
    sw, sh = _size(src)
    if width > sw or height > sh:
        raise ValueError('Mask must be in PAN or NOSR coordinates')
    out = gdal.GetDriverByName('GTiff').Create(str(destination), width, height, 1, gdal.GDT_Byte)
    if out is None:
        raise ValueError('Cannot create mapped mask')
    if sw == width * 2 and sh == height * 2 and src.GetRasterBand(1).DataType == gdal.GDT_Byte:
        selected = _reduce_byte_mask_2x(src.GetRasterBand(1), out.GetRasterBand(1), width, height, constrained)
    else:
        selected = 0
        # One LR row at a time: memory scales with width, not full PAN area.
        left = np.floor(np.arange(width) * sw / width).astype(int)
        right = np.ceil((np.arange(width) + 1) * sw / width).astype(int)
        for row in range(height):
            top = int(math.floor(row * sh / height))
            bottom = int(math.ceil((row + 1) * sh / height))
            data = src.GetRasterBand(1).ReadAsArray(0, top, sw, bottom - top)
            if constrained and np.any((data != 0) & (data != 255)):
                raise ValueError('Land constraint requires a binary 0/255 publication mask')
            columns = np.any(data != 0, axis=0)
            cumulative = np.concatenate(([0], np.cumsum(columns)))
            mask = ((cumulative[right] - cumulative[left]) > 0).astype('uint8') * 255
            selected += int(np.count_nonzero(mask))
            out.GetRasterBand(1).WriteArray(mask.reshape(1, width), 0, row)
    out.FlushCache()
    out = src = None
    if not selected:
        raise ValueError('Repair mask is empty')


def copy_candidate(source, destination):
    if sys.platform.startswith('linux'):
        import fcntl

        with source.open('rb') as src, destination.open('wb') as dst:
            try:
                fcntl.ioctl(dst.fileno(), FICLONE, src.fileno())
                return 'reflink'
            except OSError as exc:
                if exc.errno not in CLONE_UNAVAILABLE:
                    raise
    shutil.copyfile(str(source), str(destination))
    return 'copy'


def repair_timing(stage, started, detail=''):
    elapsed = time.monotonic() - started
    print('[REPAIR TIMING] {}: {:.3f}s{}'.format(stage, elapsed, detail), flush=True)


def window_manifest(model):
    return Path(str(model) + '.windows.json')


class WindowOutput:
    """Accept the same slice assignments as the wrapped SR array, on disk."""

    def __init__(self, gdal, path, width, height, left, top, clip_bounds):
        self.path = path
        self.width, self.height = width, height
        self.left, self.top = left, top
        self.clip_bounds = clip_bounds
        self.windows = []
        self.dataset = gdal.GetDriverByName('GTiff').Create(
            str(path), width, height, 1, gdal.GDT_UInt16,
            options=['BIGTIFF=IF_SAFER', 'TILED=YES', 'SPARSE_OK=YES'])
        self.band = self.dataset.GetRasterBand(1)

    def __setitem__(self, slices, pixels):
        import numpy as np

        rows, cols = slices
        x, y = cols.start - self.left, rows.start - self.top
        w, h = cols.stop - cols.start, rows.stop - rows.start
        if pixels.shape != (h, w):
            raise ValueError('SR tile does not match its output window')
        left, top = max(0, x), max(0, y)
        right, bottom = min(self.width, x + w), min(self.height, y + h)
        if right <= left or bottom <= top:
            raise ValueError('SR tile is outside the output grid')
        samples = pixels[top - y:bottom - y, left - x:right - x].astype('uint16', copy=False)
        if self.clip_bounds is not None:
            samples = np.clip(samples, *self.clip_bounds)
        if self.band.WriteArray(samples, left, top) not in (None, 0):
            raise ValueError('Failed to write SR output window')
        self.windows.append((left, top, right, bottom))

    def finish(self, expected_windows):
        if not expected_windows or len(self.windows) != expected_windows:
            raise ValueError('Inference did not write all selected SR windows')
        self.dataset.FlushCache()
        self.band = self.dataset = None
        window_manifest(self.path).write_text(json.dumps(self.windows), encoding='utf-8')
        print('[REPAIR WINDOW OUTPUT] {} windows written'.format(expected_windows), flush=True)


def model_window_index(model):
    manifest = window_manifest(model)
    if not manifest.is_file():
        return None
    index = {}
    for left, top, right, bottom in json.loads(manifest.read_text(encoding='utf-8')):
        for y in range(top // COMPOSE_HEIGHT, (bottom - 1) // COMPOSE_HEIGHT + 1):
            for x in range(left // COMPOSE_WIDTH, (right - 1) // COMPOSE_WIDTH + 1):
                index.setdefault((x, y), []).append((left, top, right, bottom))
    return index


def compose(gdal, pan, nosr, mask, model, destination, work, *, constrained=False):
    """Keep PAN's TIFF layout and byte size, replace pixels in bounded blocks."""
    import numpy as np
    current = _open(gdal, pan)
    original = _open(gdal, nosr)
    inferred = _open(gdal, model)
    model_nodata = inferred.GetRasterBand(1).GetNoDataValue()
    dtype = current.GetRasterBand(1).DataType
    if inferred.GetRasterBand(1).DataType != dtype or original.GetRasterBand(1).DataType != dtype:
        raise ValueError('PAN, NOSR and model output sample types differ')
    compression = (current.GetMetadata('IMAGE_STRUCTURE') or {}).get('COMPRESSION', 'NONE')
    if compression.upper() != 'NONE':
        raise ValueError('Exact byte-size repair requires an uncompressed PAN TIFF')
    width, height = _size(current)
    window_index = model_window_index(model)
    if _size(inferred) != (width, height):
        if window_index is not None:
            raise ValueError('Windowed model output must match the PAN grid')
        started = time.monotonic()
        # SC scenes need not sit on an integer multiple of NOSR: the field case
        # is 30831x30948 from 15730x15790 (x1.96), while SRScale=2 produces
        # 31460x31580. Refuse only a gross mismatch (wrong scale), otherwise
        # resample the model output onto the body grid before composing.
        iw, ih = _size(inferred)
        if not 0.9 <= iw / float(width) <= 1.1 or not 0.9 <= ih / float(height) <= 1.1:
            raise ValueError('Model output is off the body grid: %dx%d vs %dx%d'
                             % (iw, ih, width, height))
        resampled = Path(work) / 'inferred_body.tif'
        inferred = None
        ds = gdal.Translate(str(resampled), str(model), format='GTiff',
                            width=width, height=height, resampleAlg='cubic',
                            outputType=dtype,
                            creationOptions=['BIGTIFF=IF_SAFER', 'COMPRESS=NONE'])
        if ds is None:
            raise ValueError('Cannot resample the model output onto the body grid')
        ds = None
        inferred = _open(gdal, resampled)
        repair_timing('resample_model', started)
    selection = _open(gdal, mask)
    if _size(selection) != (width, height):
        raise ValueError('Publication mask must match current PAN dimensions')
    started = time.monotonic()
    copy_method = copy_candidate(Path(pan), Path(destination))
    repair_timing('copy_candidate', started, ' method=' + copy_method)
    current_band = current.GetRasterBand(1)
    model_band = inferred.GetRasterBand(1)
    current_nodata = current_band.GetNoDataValue()
    target = _open(gdal, destination, update=True)
    target_band = target.GetRasterBand(1)
    selection_band = selection.GetRasterBand(1)
    validity_band = (None if current_band.GetMaskFlags() & gdal.GMF_ALL_VALID
                     else current_band.GetMaskBand())

    def selected_pixels(x, y, w, h):
        selected = selection_band.ReadAsArray(x, y, w, h)
        if constrained and np.any((selected != 0) & (selected != 255)):
            raise ValueError('Land constraint requires a binary 0/255 publication mask')
        return selected != 0

    def allowed_pixels(selected, before, x, y, w, h):
        allowed = selected & np.isfinite(before)
        if current_nodata is not None:
            allowed &= before != current_nodata
        if validity_band is not None:
            allowed &= validity_band.ReadAsArray(x, y, w, h) != 0
        return allowed

    started = time.monotonic()
    empty_windows = written_windows = 0
    for y in range(0, height, COMPOSE_HEIGHT):
        h = min(COMPOSE_HEIGHT, height - y)
        for x in range(0, width, COMPOSE_WIDTH):
            w = min(COMPOSE_WIDTH, width - x)
            selected = selected_pixels(x, y, w, h)
            if not selected.any():
                empty_windows += 1
                continue
            baseline = current_band.ReadAsArray(x, y, w, h)
            roi = allowed_pixels(selected, baseline, x, y, w, h)
            if not roi.any():
                continue
            if window_index is not None:
                uncovered = roi.copy()
                key = (x // COMPOSE_WIDTH, y // COMPOSE_HEIGHT)
                for left, top, right, bottom in window_index.get(key, ()):
                    uncovered[max(0, top - y):bottom - y, max(0, left - x):right - x] = False
                if uncovered.any():
                    raise ValueError('SR output windows do not cover the publication mask')
            generated = model_band.ReadAsArray(x, y, w, h)
            replacement = generated[roi]
            if not np.isfinite(replacement).all() or any(
                    value is not None and np.any(replacement == value)
                    for value in (current_nodata, model_nodata)):
                raise ValueError('Model returned invalid samples inside the publication mask')
            baseline[roi] = replacement
            if target_band.WriteArray(baseline, x, y) not in (None, 0):
                raise ValueError('Failed to write candidate PAN block')
            written_windows += 1
    target.FlushCache()
    target_band = target = None
    repair_timing('compose', started, ' empty_windows={} written_windows={}'.format(
        empty_windows, written_windows))

    started = time.monotonic()
    staged = _open(gdal, destination)
    staged_band = staged.GetRasterBand(1)
    block_width, block_height = current_band.GetBlockSize()
    # Read full strips or adjacent tiles in one call, targeting ~1M samples
    # per window while keeping complete native blocks together.
    read_width = min(width, block_width * max(1, 4096 // block_width))
    read_height = block_height * max(1, (1024 * 1024) // (read_width * block_height))
    for y in range(0, height, read_height):
        h = min(read_height, height - y)
        for x in range(0, width, read_width):
            w = min(read_width, width - x)
            before = current_band.ReadAsArray(x, y, w, h)
            after = staged_band.ReadAsArray(x, y, w, h)
            selected = selected_pixels(x, y, w, h)
            if selected.any():
                allowed = allowed_pixels(selected, before, x, y, w, h)
                # Mask the permitted changes in memory before comparing bytes.
                after[allowed] = before[allowed]
            if not np.array_equal(before.view(np.uint8), after.view(np.uint8)):
                raise ValueError('Candidate PAN changed pixels outside the publication mask')
    staged_band = staged = validity_band = selection_band = current_band = model_band = None
    selection = inferred = original = current = None
    if os.path.getsize(str(destination)) != os.path.getsize(str(pan)):
        raise ValueError('Candidate byte size differs from current PAN; nothing published')
    repair_timing('verify_candidate', started)


def inference_errors(private):
    """Lines in the private SRLOG that mark a swallowed per-tile failure."""
    found = []
    for log in sorted(Path(private).rglob('*_SRLOG.txt')):
        text = log.read_text(errors='replace')
        for line in text.splitlines():
            lowered = line.lower()
            if any(marker in lowered for marker in INFERENCE_ERROR_MARKERS):
                found.append(log.name + ': ' + line.strip()[:200])
    return found


def run(config, sr_script, gdal=None, execute=None, gpu_note=None):
    if gdal is None:
        from osgeo import gdal
    execute = execute or subprocess.check_call
    if hasattr(gdal, 'UseExceptions'):
        gdal.UseExceptions()
    config = Path(config).resolve()
    tree = ET.parse(str(config))
    root = tree.getroot()
    if root.findtext('RepairMode') != MODE:
        raise ValueError('Missing supported RepairMode')
    scene = Path(root.findtext('DatarootLQ')).resolve()
    _, body = repair_body(scene, root.findtext('RepairBody'))
    body_stem = body.stem
    # 新旧顺序：<完整生产名>_old（上一轮产物）→ <本体 stem>_old_latest（本次修复前）
    # → 本体（修复后）。旧平台写过的 PAN_old_2 只做只读兼容，不再新写。
    backup = scene / (body_stem + '_old_latest.tif')
    legacy_backups = (scene / (body_stem + '_old_2.tif'),)
    raw_nosr = root.findtext('RepairNOSR')
    if not raw_nosr:
        raise ValueError('RepairNOSR is required; never infer from the current body')
    nosr = Path(raw_nosr).resolve()
    gf07_alias = nosr.name == 'PAN_ori.tif' and _GF07_SCENE_RE.match(scene.name)
    if nosr.parent != scene or nosr == body or not (nosr.stem.endswith('_NOSR') or gf07_alias):
        raise ValueError('RepairNOSR must identify a same-scene _NOSR raster or GF07 PAN_ori.tif')
    mask = Path(root.findtext('MaskPath') or '').resolve()
    for path in (body, nosr, mask, scene / (scene.name + '_meta.xml')):
        if not path.is_file():
            raise ValueError('Missing repair input: ' + str(path))
    if os.path.samefile(str(body), str(nosr)):
        raise ValueError('NOSR must not alias the current body')
    existing_backup = next((p for p in (backup,) + legacy_backups if p.exists()), None)
    if existing_backup is not None:
        raise ValueError('Backup exists; refusing to overwrite ' + str(existing_backup))
    expected = json.loads(root.findtext('RepairRevision') or '{}')
    stage_node = root.find('RepairNOSRStage')
    stage = json.loads(stage_node.text or '{}') if stage_node is not None else None
    if stage_node is not None and not stage:
        raise ValueError('Missing NOSR staging descriptor')
    proof_node = root.find('LandConstraint')
    proof = json.loads(proof_node.text or '{}') if proof_node is not None else None
    if proof_node is not None:
        started = time.monotonic()
        expires_at = proof.get('expires_at') if isinstance(proof, dict) else None
        if expires_at is not None and (type(expires_at) not in (int, float)
                                       or not math.isfinite(expires_at) or time.time() >= expires_at):
            raise ValueError('Land constraint confirmation expired')
        verify_land_constraint(proof, body, nosr, mask)
        repair_timing('verify_land_inputs', started)
    for key, path in (('mask', mask), ('nosr', nosr)):
        if key in expected and signature(path)[:2] != expected[key][:2]:
            raise ValueError('Queued repair input changed: ' + str(path))
    debug = scene / 'Debug'
    debug.mkdir(exist_ok=True)
    lock = debug / '_SRREPAIR.lock'
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    work = Path(tempfile.mkdtemp(prefix='.repair-', dir=str(debug)))
    aliases = []
    local_copy = None
    try:
        body_sig, nosr_sig, mask_sig = signature(body), signature(nosr), signature(mask)
        nosr_ctime = nosr.stat().st_ctime_ns
        p, n, m = _open(gdal, body), _open(gdal, nosr), _open(gdal, mask)
        pw, ph = _size(p)
        nw, nh = _size(n)
        if (p.GetMetadata('IMAGE_STRUCTURE') or {}).get('COMPRESSION', 'NONE').upper() != 'NONE':
            raise ValueError('Exact byte-size repair requires an uncompressed body TIFF')
        if p.GetRasterBand(1).DataType != n.GetRasterBand(1).DataType:
            raise ValueError('Body and NOSR sample types differ')
        if not (pw >= nw and ph >= nh and (pw, ph) != (nw, nh)):
            raise ValueError('NOSR must have lower resolution than the current body')
        if _size(m) not in ((pw, ph), (nw, nh)):
            raise ValueError('Mask dimensions match neither body nor NOSR')
        mask_is_body = _size(m) == (pw, ph)
        if proof is not None and (not mask_is_body or m.GetRasterBand(1).DataType != gdal.GDT_Byte):
            raise ValueError('Land constraint requires a byte mask in the PAN pixel grid')
        p = n = m = None
        frozen_source = nosr
        started = time.monotonic()
        if stage:
            source_copy = Path(stage['path'])
            # A Slurm sandbox may contain a separate copy of the same scene.
            frozen_source = Path(stage.get('source_path') or raw_nosr).resolve(strict=True)
            if (source_copy.resolve(strict=True) != source_copy or not source_copy.is_file()
                    or os.path.samefile(str(source_copy), str(nosr))
                    or os.path.samefile(str(source_copy), str(frozen_source))
                    or os.path.samefile(str(source_copy), str(body))
                    or frozen_source.name != nosr.name
                    or stage['source_signature'] != signature(frozen_source)
                    or stage['source_ctime_ns'] != frozen_source.stat().st_ctime_ns
                    or stage['source_signature'][:2] != nosr_sig[:2]
                    or signature(source_copy) != stage['signature'] or stage['signature'][0] != nosr_sig[0]):
                raise ValueError('Staged NOSR or original revision changed')
        else:
            # Direct repair and already queued legacy configs still isolate the original inode.
            local_copy = source_copy = work / 'nosr-input.tif'
            shutil.copyfile(str(nosr), str(source_copy))
            if signature(nosr) != nosr_sig or nosr.stat().st_ctime_ns != nosr_ctime:
                raise ValueError('Original NOSR changed while copying')
        repair_timing('stage_nosr', started, ' method=' + ('staged' if stage else 'copy'))
        copy_sig = signature(source_copy)
        private = work / scene.name
        private.mkdir()
        # Both production names refer to the copy, including across mounts.
        for filename in dict.fromkeys(('PAN.tif', scene.name + '.tif')):
            alias = private / filename
            try:
                os.link(str(source_copy), str(alias))
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                os.symlink(str(source_copy), str(alias))
            aliases.append(alias)
        copy_ctime = source_copy.stat().st_ctime_ns
        meta = scene.name + '_meta.xml'
        shutil.copyfile(str(scene / meta), str(private / meta))
        lr_mask = work / 'mask_lr.tif'
        full_mask = mask
        if proof is not None:
            started = time.monotonic()
            full_mask = work / 'frozen_pan_mask.tif'
            shutil.copyfile(str(mask), str(full_mask))
            verify_land_mask(proof, full_mask)
            repair_timing('freeze_mask', started)
        started = time.monotonic()
        reduce_mask(gdal, full_mask, lr_mask, nw, nh, constrained=proof is not None)
        repair_timing('reduce_mask', started)
        if not mask_is_body:
            started = time.monotonic()
            full_mask = work / 'mask_body.tif'
            ds = gdal.Translate(str(full_mask), str(mask), format='GTiff', width=pw, height=ph,
                                resampleAlg='nearest')
            if ds is None:
                raise ValueError('Cannot map NOSR mask to the body grid')
            ds = None
            repair_timing('expand_publication_mask', started)
        for tag, text in (('DatarootLQ', str(private)), ('MaskPath', str(lr_mask)),
                          ('Suffix', root.findtext('Suffix') or '260318'), ('DeleteOriTifNeeded', 'False')):
            node = root.find(tag)
            if node is None:
                node = ET.SubElement(root, tag)
            node.text = text
        # Repair tags belong to the adapter, not the legacy inference program.
        for tag in ('RepairMode', 'RepairBody', 'RepairNOSR', 'RepairRevision', 'RepairNOSRStage', 'LandConstraint'):
            node = root.find(tag)
            if node is not None:
                root.remove(node)
        stage_config = work / 'inference.xml'
        tree.write(str(stage_config), encoding='utf-8', xml_declaration=True)
        execute([sys.executable, str(Path(__file__).resolve()), '--infer',
                 '--sr-script', str(Path(sr_script).resolve()), '--pan-size', str(pw), str(ph),
                 '-f', str(stage_config)])
        # The model wrapper swallows per-tile RuntimeError and keeps the previous
        # tile's result, so an out-of-memory run still exits 0 with a full output.
        # Refuse it here: a stale block must never be published as a repair.
        errors = inference_errors(private)
        if errors:
            raise ValueError('Inference reported errors: ' + '; '.join(errors))
        # Reuse the ordinary verifier before trusting a silent exit(0).
        from verify_sr_run import check_contract
        verdict = check_contract(str(stage_config), 0)
        if not verdict.ok or verdict.skip:
            raise ValueError('Inference produced no verified repair: ' + verdict.reason_text)
        candidate = work / (body_stem + '_repaired.tif')
        if signature(source_copy) != copy_sig or source_copy.stat().st_ctime_ns != copy_ctime:
            raise ValueError('Staged NOSR changed during inference')
        # Read the original in the compositor so GDAL sees its existing sidecars.
        compose(gdal, body, nosr, full_mask, verdict.output, candidate, work, constrained=proof is not None)
        if ([signature(body), signature(nosr), signature(mask)] != [body_sig, nosr_sig, mask_sig]
                or nosr.stat().st_ctime_ns != nosr_ctime or signature(source_copy) != copy_sig):
            raise ValueError('Repair input changed during inference; nothing published')
        if stage and (signature(frozen_source) != stage['source_signature']
                      or frozen_source.stat().st_ctime_ns != stage['source_ctime_ns']):
            raise ValueError('Original NOSR changed during inference; nothing published')
        if proof is not None:
            verify_land_constraint(proof, body, nosr, mask)
            verify_land_mask(proof, full_mask)
        existing_backup = next((p for p in (backup,) + legacy_backups if p.exists()), None)
        if existing_backup is not None:
            raise ValueError('Backup appeared during inference; nothing published: '
                             + str(existing_backup))
        # Reserve backup without overwriting, then atomically replace the body.
        os.link(str(body), str(backup))
        try:
            os.replace(str(candidate), str(body))
        except BaseException:
            backup.unlink()
            raise
        receipt = {'mode': MODE, 'output': str(body), 'backup': str(backup), 'nosr': str(nosr),
                   'output_signature': signature(body), 'backup_signature': body_sig,
                   'nosr_signature': nosr_sig, 'width': pw, 'height': ph,
                   'size_bytes': body_sig[0]}
        if proof is not None:
            receipt['land_constraint'] = proof
        target = receipt_path(config)
        temporary = work / 'receipt.json'
        temporary.write_text(json.dumps(receipt), encoding='utf-8')
        os.replace(str(temporary), str(target))
        # The success log is written only after publication and receipt persistence.
        log = debug / (body_stem + '_SRLOG.txt')
        note = ('Repair GPU: ' + gpu_note + '\n') if gpu_note else ''
        log.write_text('Repair from NOSR; mask SR, outside mask unchanged.\n' + note
                       + 'Run finished.\n', encoding='utf-8')
        return receipt
    finally:
        # Retain diagnostics and results while releasing private input aliases.
        try:
            for path in aliases + ([local_copy] if local_copy else []):
                if path.exists() or path.is_symlink():
                    path.unlink()
        finally:
            lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-f', '--config', required=True)
    parser.add_argument('--sr-script', required=True)
    parser.add_argument('--infer', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--pan-size', nargs=2, type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.infer:
            print('[REPAIR INFER START] {:.6f}'.format(time.time()), flush=True)
            # The production file-size heuristic classifies some valid NOSR
            # inputs as already SRed. Selection is explicit inside this private
            # scene, so bypass that heuristic without editing production util.
            script = Path(args.sr_script).resolve()
            sys.path.insert(0, str(script.parent))
            import utils.util as util
            def select_input(directory, file_type, previous_step):
                name = 'PAN.tif' if previous_step == 'RC' else Path(directory).name + file_type
                path = Path(directory) / name
                if not path.is_file():
                    raise ValueError('Missing staged NOSR input: ' + str(path))
                return str(path)
            util.get_l1_pan_tif_rcsc = select_input

            def repair_window_output(path, tl_obj, scale, clip_bounds):
                width = tl_obj['ori_wd'] * scale
                height = tl_obj['ori_ht'] * scale
                if args.pan_size != [width, height]:
                    return None
                from osgeo import gdal

                gdal.UseExceptions()
                return WindowOutput(gdal, path, width, height,
                                    tl_obj['wrap_lt'] * scale, tl_obj['wrap_top'] * scale,
                                    clip_bounds)

            sys.argv = [str(script), '-f', args.config]
            runpy.run_path(str(script), run_name='__main__',
                           init_globals={'repair_window_output': repair_window_output})
            return 0
        # Pin the card in the parent, before any CUDA import; the --infer child
        # inherits CUDA_VISIBLE_DEVICES. This deliberately overrides the integer
        # Slurm injects, which points every job at the LLM service's card.
        os.environ['CUDA_VISIBLE_DEVICES'] = str(INFERENCE_GPU)
        gpu_note = 'card %d (fixed)' % INFERENCE_GPU
        print('Repair inference GPU: ' + gpu_note, flush=True)
        run(args.config, args.sr_script, gpu_note=gpu_note)
        return 0
    except Exception as exc:
        print('PAN repair failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
