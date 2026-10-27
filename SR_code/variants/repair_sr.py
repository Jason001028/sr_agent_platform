#!/usr/bin/env python
"""PAN repair adapter. Python 3.6 compatible; GDAL stays in the SR environment.

Inference runs in a private scene with NOSR linked as its input. Original PAN,
NOSR and metadata are untouched until a validated candidate is published.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

MODE = 'pan-nosr-v1'


def signature(path):
    st = os.stat(str(path))
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def receipt_path(config):
    config = Path(config)
    scene = Path(ET.parse(str(config)).getroot().findtext('DatarootLQ'))
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    return scene / 'Debug' / ('_SRREPAIR_' + digest + '.json')


def _open(gdal, path, update=False):
    ds = gdal.Open(str(path), gdal.GA_Update if update else gdal.GA_ReadOnly)
    if ds is None:
        raise ValueError('Cannot open raster: ' + str(path))
    if ds.RasterCount != 1:
        raise ValueError('PAN repair requires single-band rasters: ' + str(path))
    return ds


def _size(ds):
    return ds.RasterXSize, ds.RasterYSize


def reduce_mask(gdal, source, destination, width, height):
    """Conservative area mapping: a selected high-resolution pixel is not lost."""
    import numpy as np
    src = _open(gdal, source)
    sw, sh = _size(src)
    if width > sw or height > sh:
        raise ValueError('Mask must be in PAN or NOSR coordinates')
    out = gdal.GetDriverByName('GTiff').Create(str(destination), width, height, 1, gdal.GDT_Byte)
    if out is None:
        raise ValueError('Cannot create mapped mask')
    selected = 0
    # One LR row at a time: memory scales with width, not full PAN area.
    left = np.floor(np.arange(width) * sw / width).astype(int)
    right = np.ceil((np.arange(width) + 1) * sw / width).astype(int)
    for row in range(height):
        top = int(math.floor(row * sh / height))
        bottom = int(math.ceil((row + 1) * sh / height))
        data = src.GetRasterBand(1).ReadAsArray(0, top, sw, bottom - top)
        columns = np.any(data != 0, axis=0)
        cumulative = np.concatenate(([0], np.cumsum(columns)))
        mask = ((cumulative[right] - cumulative[left]) > 0).astype('uint8') * 255
        selected += int(np.count_nonzero(mask))
        out.GetRasterBand(1).WriteArray(mask.reshape(1, width), 0, row)
    out.FlushCache()
    out = src = None
    if not selected:
        raise ValueError('Repair mask is empty')


def compose(gdal, pan, nosr, mask, model, destination, work):
    """Keep PAN's TIFF layout and byte size, replace pixels in bounded blocks."""
    import numpy as np
    current = _open(gdal, pan)
    original = _open(gdal, nosr)
    inferred = _open(gdal, model)
    if _size(inferred) != _size(current):
        raise ValueError('Model output dimensions do not match current PAN')
    dtype = current.GetRasterBand(1).DataType
    if inferred.GetRasterBand(1).DataType != dtype or original.GetRasterBand(1).DataType != dtype:
        raise ValueError('PAN, NOSR and model output sample types differ')
    compression = (current.GetMetadata('IMAGE_STRUCTURE') or {}).get('COMPRESSION', 'NONE')
    if compression.upper() != 'NONE':
        raise ValueError('Exact byte-size repair requires an uncompressed PAN TIFF')
    width, height = _size(current)
    bg_path = Path(work) / 'bicubic.tif'
    bg = gdal.Translate(str(bg_path), original, format='GTiff', width=width, height=height,
                        resampleAlg='cubic', outputType=dtype,
                        creationOptions=['BIGTIFF=IF_SAFER', 'COMPRESS=NONE'])
    if bg is None:
        raise ValueError('NOSR interpolation failed')
    selection = _open(gdal, mask)
    if _size(selection) != (width, height):
        raise ValueError('Publication mask must match current PAN dimensions')
    shutil.copyfile(str(pan), str(destination))
    target = _open(gdal, destination, update=True)
    for y in range(0, height, 256):
        h = min(256, height - y)
        for x in range(0, width, 1024):
            w = min(1024, width - x)
            roi = selection.GetRasterBand(1).ReadAsArray(x, y, w, h) != 0
            baseline = bg.GetRasterBand(1).ReadAsArray(x, y, w, h)
            generated = inferred.GetRasterBand(1).ReadAsArray(x, y, w, h)
            target.GetRasterBand(1).WriteArray(np.where(roi, generated, baseline), x, y)
    target.FlushCache()
    target = selection = inferred = original = bg = current = None
    if os.path.getsize(str(destination)) != os.path.getsize(str(pan)):
        raise ValueError('Candidate byte size differs from current PAN; nothing published')


def run(config, sr_script, gdal=None, execute=None):
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
    pan = scene / 'PAN.tif'
    backup = scene / 'PAN_old_2.tif'
    raw_nosr = root.findtext('RepairNOSR')
    if not raw_nosr:
        raise ValueError('RepairNOSR is required; never infer from current PAN')
    nosr = Path(raw_nosr).resolve()
    if nosr.parent != scene or not nosr.stem.endswith('_NOSR') or nosr == pan:
        raise ValueError('RepairNOSR must identify a same-scene _NOSR raster')
    mask = Path(root.findtext('MaskPath') or '').resolve()
    for path in (pan, nosr, mask, scene / (scene.name + '_meta.xml')):
        if not path.is_file():
            raise ValueError('Missing repair input: ' + str(path))
    if os.path.samefile(str(pan), str(nosr)):
        raise ValueError('NOSR must not alias current PAN')
    if backup.exists():
        raise ValueError('Backup exists; refusing to overwrite ' + str(backup))
    expected = json.loads(root.findtext('RepairRevision') or '{}')
    for key, path in (('mask', mask), ('nosr', nosr)):
        if key in expected and signature(path)[:2] != expected[key][:2]:
            raise ValueError('Queued repair input changed: ' + str(path))
    debug = scene / 'Debug'
    debug.mkdir(exist_ok=True)
    lock = debug / '_SRREPAIR.lock'
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    work = Path(tempfile.mkdtemp(prefix='.repair-', dir=str(debug)))
    try:
        pan_sig, nosr_sig, mask_sig = signature(pan), signature(nosr), signature(mask)
        p, n, m = _open(gdal, pan), _open(gdal, nosr), _open(gdal, mask)
        pw, ph = _size(p)
        nw, nh = _size(n)
        if (p.GetMetadata('IMAGE_STRUCTURE') or {}).get('COMPRESSION', 'NONE').upper() != 'NONE':
            raise ValueError('Exact byte-size repair requires an uncompressed PAN TIFF')
        if p.GetRasterBand(1).DataType != n.GetRasterBand(1).DataType:
            raise ValueError('PAN and NOSR sample types differ')
        if not (pw >= nw and ph >= nh and (pw, ph) != (nw, nh)):
            raise ValueError('NOSR must have lower resolution than current PAN')
        if _size(m) not in ((pw, ph), (nw, nh)):
            raise ValueError('Mask dimensions match neither PAN nor NOSR')
        mask_is_pan = _size(m) == (pw, ph)
        p = n = m = None
        private = work / scene.name
        private.mkdir()
        # Preserve SC naming and RC PAN naming without renaming any original.
        for filename in ('PAN.tif', scene.name + '.tif'):
            os.link(str(nosr), str(private / filename))
        meta = scene.name + '_meta.xml'
        shutil.copyfile(str(scene / meta), str(private / meta))
        lr_mask = work / 'mask_lr.tif'
        reduce_mask(gdal, mask, lr_mask, nw, nh)
        full_mask = mask
        if not mask_is_pan:
            full_mask = work / 'mask_pan.tif'
            ds = gdal.Translate(str(full_mask), str(mask), format='GTiff', width=pw, height=ph,
                                resampleAlg='nearest')
            if ds is None:
                raise ValueError('Cannot map NOSR mask to PAN')
            ds = None
        for tag, text in (('DatarootLQ', str(private)), ('MaskPath', str(lr_mask)),
                          ('Suffix', root.findtext('Suffix') or '260318'), ('DeleteOriTifNeeded', 'False')):
            node = root.find(tag)
            if node is None:
                node = ET.SubElement(root, tag)
            node.text = text
        # Repair tags belong to the adapter, not the legacy inference program.
        for tag in ('RepairMode', 'RepairNOSR', 'RepairRevision'):
            node = root.find(tag)
            if node is not None:
                root.remove(node)
        stage_config = work / 'inference.xml'
        tree.write(str(stage_config), encoding='utf-8', xml_declaration=True)
        execute([sys.executable, str(Path(__file__).resolve()), '--infer',
                 '--sr-script', str(Path(sr_script).resolve()), '-f', str(stage_config)])
        # Reuse the ordinary verifier before trusting a silent exit(0).
        from verify_sr_run import check_contract
        verdict = check_contract(str(stage_config), 0)
        if not verdict.ok or verdict.skip:
            raise ValueError('Inference produced no verified repair: ' + verdict.reason_text)
        candidate = work / 'PAN_repaired.tif'
        compose(gdal, pan, nosr, full_mask, verdict.output, candidate, work)
        if [signature(pan), signature(nosr), signature(mask)] != [pan_sig, nosr_sig, mask_sig]:
            raise ValueError('Repair input changed during inference; nothing published')
        if backup.exists():
            raise ValueError('Backup appeared during inference; nothing published')
        # Reserve backup without overwriting, then atomically replace current PAN.
        os.link(str(pan), str(backup))
        try:
            os.replace(str(candidate), str(pan))
        except BaseException:
            backup.unlink()
            raise
        receipt = {'mode': MODE, 'output': str(pan), 'backup': str(backup), 'nosr': str(nosr),
                   'output_signature': signature(pan), 'backup_signature': pan_sig,
                   'nosr_signature': nosr_sig, 'width': pw, 'height': ph,
                   'size_bytes': pan_sig[0]}
        target = receipt_path(config)
        temporary = work / 'receipt.json'
        temporary.write_text(json.dumps(receipt), encoding='utf-8')
        os.replace(str(temporary), str(target))
        # The success log is written only after publication and receipt persistence.
        log = debug / 'PAN_SRLOG.txt'
        log.write_text('PAN repair from NOSR; mask SR, outside mask cubic.\nRun finished.\n', encoding='utf-8')
        return receipt
    finally:
        # Retain staging files for diagnosis, including uncommitted candidates.
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-f', '--config', required=True)
    parser.add_argument('--sr-script', required=True)
    parser.add_argument('--infer', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.infer:
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
            sys.argv = [str(script), '-f', args.config]
            runpy.run_path(str(script), run_name='__main__')
            return 0
        run(args.config, args.sr_script)
        return 0
    except Exception as exc:
        print('PAN repair failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
