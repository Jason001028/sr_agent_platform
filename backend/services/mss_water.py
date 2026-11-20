"""P0 advisory water products; no repair or review side effects."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from functools import partial
from itertools import chain
from pathlib import Path

import numpy as np
from PIL import Image

from backend.pathguard import ensure_allowed, scene_name_layers
from backend.services import scene_resolve
from backend.services.evidence_raster import read_strip_span
from backend.services.preview_jpg import source_revision
from backend.services.spatial_identity import resource_identity
from backend.services.store import default_db_path


VERSION = 'mss-ndwi.v3'
ASSETS = {'ndwi.tif', 'water.tif', 'valid.tif', 'metadata.json', 'overlay.png', 'mss-preview.png'}
SPATIAL_TAGS = (274, 33550, 33922, 34264, 34735, 34736, 34737, 50844)
SCENE = re.compile(
    r'(?P<sat>[A-Z0-9]+)[_ ](?P<sensor>PMS[0-9]*)[_ ][0-9]{14}[_ ][0-9]{9}'
    r'[_ ][0-9]{3}[_ ][0-9]{4}[_ ][0-9]{3}[_ ]L[0-9]+[_ ]PAN', re.ASCII | re.IGNORECASE)


class WaterUnavailable(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class WaterProfile:
    name: str = 'mss-ndwi-reference'
    threshold: float = 0.3

    def __post_init__(self):
        if not math.isfinite(self.threshold) or not -1 <= self.threshold <= 1:
            raise WaterUnavailable('invalid_profile', 'NDWI 阈值必须在 [-1, 1] 内')


def _exact_files(directory, names):
    files = {}
    for name in names:
        candidate = directory / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        checked = ensure_allowed(candidate, kind='file')
        if candidate.is_symlink() or checked.parent != directory:
            raise WaterUnavailable('unsafe_source', 'MSS 文件必须在对应场景目录内，且不是链接')
        files[checked] = None
    return list(files)


def resolve_mss(pan):
    pan = ensure_allowed(pan, kind='file')
    directory = pan.parent
    match = SCENE.fullmatch(directory.name)
    bases = {directory.name.casefold(), 'pan'}
    if (not match or pan.suffix.lower() not in ('.tif', '.tiff')
            or pan.stem.casefold() not in bases | {base + '_nosr' for base in bases}):
        raise WaterUnavailable('not_pan', '需要生产场景内的 PAN 或原始 NOSR TIFF')
    mss_name = directory.name[:-3] + 'MSS'
    names = scene_resolve.name_candidates(mss_name)
    candidates = [directory.with_name(mss_name)]
    pan_layers = scene_name_layers(directory.name)
    mss_layers = scene_name_layers(mss_name)
    if pan_layers and mss_layers and directory.parent.name == pan_layers[1]:
        candidates.append(directory.parent.with_name(mss_layers[1]) / mss_name)
    candidates.extend(Path(candidate) for candidate in names['base_candidates'])
    hits = {}
    for candidate in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        scene = ensure_allowed(candidate, kind='dir')
        if candidate.is_symlink() or scene.name.casefold() != mss_name.casefold():
            raise WaterUnavailable('unsafe_source', 'MSS 场景目录身份不符')
        tiffs = _exact_files(scene, [base + suffix for base in (mss_name, 'MSS')
                                    for suffix in ('.tif', '.tiff', '.TIF', '.TIFF')])
        xmls = _exact_files(scene, [mss_name + '_meta' + suffix for suffix in ('.xml', '.XML')])
        if tiffs or xmls:
            hits[scene.resolve()] = (tiffs, xmls)
    if len(hits) > 1 or any(len(tiffs) > 1 or len(xmls) > 1 for tiffs, xmls in hits.values()):
        raise WaterUnavailable('ambiguous_mss', '命中多份精确同景的 MSS TIFF 或元数据')
    if not hits or not all(next(iter(hits.values()))):
        raise WaterUnavailable('missing_mss', '找不到成对的精确同景 MSS TIFF / XML')
    tiffs, xmls = next(iter(hits.values()))
    return pan, tiffs[0], xmls[0], match['sat'].upper(), match['sensor'].upper()


class _CalibrationXML(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('DTD is not allowed')


def read_calibration(path, satellite, sensor, scene_name):
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('XML too large')
        root = ET.fromstring(raw, parser=ET.XMLParser(target=_CalibrationXML()))
    except (ET.ParseError, ValueError, LookupError) as exc:
        raise WaterUnavailable('invalid_xml', 'MSS XML 无法安全解析') from exc
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]

    def field(paths, code, label, *, optional=False):
        nodes = [node for query in paths for node in root.findall(query)]
        if not nodes and optional:
            return None
        if len(nodes) != 1 or len(nodes[0]) or not (nodes[0].text or '').strip():
            raise WaterUnavailable(code, f'MSS XML 缺少唯一的 {label}')
        return nodes[0].text.strip()

    def paths(*labels):
        return [prefix + label for prefix in ('', 'ProductInfo/', 'ProcessInfo/') for label in labels]

    xml_sensor = field(paths('SensorID', 'SensorId'), 'sensor_mismatch', 'SensorID')
    xml_sat = field(paths('SatelliteID', 'SatelliteId'), 'sensor_mismatch', 'SatelliteID', optional=True)
    if xml_sensor.upper() != sensor or (xml_sat and xml_sat.upper() != satellite):
        raise WaterUnavailable('sensor_mismatch', 'MSS XML 的传感器或卫星与 PAN 场景不符')
    scene_id = field(paths('SceneID', 'SceneId'), 'scene_mismatch', 'SceneID', optional=True)
    product = field(paths('ProductType'), 'scene_mismatch', 'ProductType', optional=True)
    if ((scene_id and scene_id.casefold().replace(' ', '_') != scene_name.casefold().replace(' ', '_'))
            or (product and product.upper() != 'MSS')):
        raise WaterUnavailable('scene_mismatch', 'MSS XML 声明的场景或产品与精确匹配的 MSS 目录不符')
    order = field(paths('BandOrder', 'BandNames'), 'band_mismatch', 'BandOrder/BandNames')
    bands = tuple(part.strip().upper() for part in order.split(','))
    if (len(bands) != 4 or len(set(bands)) != 4 or not all(bands)
            or bands[1] != 'GREEN' or bands[3] not in ('NIR', 'NEAR INFRARED', 'NEAR-INFRARED')):
        raise WaterUnavailable('band_mismatch', 'MSS 必须明确声明 Band 2=Green、Band 4=NIR')
    if len(root.findall('ProcessInfo')) != 1:
        raise WaterUnavailable('invalid_calibration', 'MSS XML 缺少唯一的 ProcessInfo 定标块')

    def coefficients(tag, *, positive=False):
        text = field(['ProcessInfo/' + tag], 'invalid_calibration', tag)
        try:
            values = tuple(float(part) for part in text.split(','))
        except ValueError as exc:
            raise WaterUnavailable('invalid_calibration', f'{tag} 不是有效的数字数组') from exc
        if (len(values) != len(bands) or not all(math.isfinite(number) for number in values)
                or (positive and min(values) <= 0)):
            raise WaterUnavailable('invalid_calibration', f'{tag} 数量或取值无效')
        return values

    return (bands, coefficients('AbsCalibrationGain', positive=True),
            coefficients('AbsCalibrationBias'), {'scene_id': scene_id, 'product_type': product})


def ndwi_window(green, nir, nodata, gain, bias, profile):
    valid = np.isfinite(green) & np.isfinite(nir)
    if nodata is not None and math.isfinite(nodata):
        valid &= (green != nodata) & (nir != nodata)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore', under='ignore'):
        green = green.astype(np.float64) * gain[1] + bias[1]
        nir = nir.astype(np.float64) * gain[3] + bias[3]
        denom = green + nir
        valid &= np.isfinite(green) & np.isfinite(nir) & np.isfinite(denom)
        valid &= (green >= 0) & (nir >= 0)
        valid &= np.abs(denom) > np.finfo(np.float64).eps * np.maximum(np.abs(green), np.abs(nir))
        values = np.full(green.shape, np.nan)
        np.divide(green - nir, denom, out=values, where=valid)
        valid &= np.isfinite(values) & (np.abs(values) <= np.finfo(np.float32).max)
        values[~valid] = np.nan
    values = values.astype('<f4')
    water = np.full(values.shape, 255, dtype=np.uint8)
    water[valid] = values[valid].astype(np.float64) >= profile.threshold
    return values, water, valid.astype(np.uint8)


def _sidecars(path):
    scene = path.parent.name
    bases = {path.stem, scene, re.sub(r'[_ ](?:MSS|PAN)$', '', scene, flags=re.IGNORECASE)}
    rpc = [path.with_name(base + separator + band + suffix)
           for base in bases for separator in ('_', ' ') for band in ('B1', 'B2', 'B3', 'B4')
           for suffix in ('_rpc.txt', '_RPC.txt', '_RPC.TXT')]
    return [candidate for candidate in (
        path.with_suffix('.tfw'), path.with_suffix('.TFW'), path.with_suffix('.tifw'),
        path.with_suffix('.tiffw'), path.with_suffix('.wld'), path.with_suffix('.prj'),
        path.with_suffix('.rpb'), path.with_suffix('.RPB'), path.with_suffix('.rpc'),
        path.with_suffix('.rpc.txt'), path.with_name(path.stem + '_RPC.txt'),
        path.with_name(path.name + '.aux.xml'), path.with_suffix('.aux.xml'),
        path.with_name(path.name + '.msk'),
        *rpc,
    ) if candidate.exists() or candidate.is_symlink()]


def _tags(page):
    return tuple((code, int(page.tags[code].dtype), page.tags[code].count,
                  page.tags[code].value, False) for code in SPATIAL_TAGS if code in page.tags)


def _grid(page):
    tags = {code: value for code, _, _, value, _ in _tags(page)}
    if (any(code in tags for code in (34264, 50844)) or tags.get(274, 1) != 1
            or page.imagewidth <= 0 or page.imagelength <= 0):
        return None
    scale, tie, keys = (tags.get(code, ()) for code in (33550, 33922, 34735))
    if (len(scale) != 3 or len(tie) != 6 or len(keys) < 4 or keys[:3] not in ((1, 1, 0), (1, 1, 1))
            or len(keys) != 4 + keys[3] * 4 or not all(map(math.isfinite, (*scale, *tie)))
            or min(scale[:2]) <= 0 or scale[2] != 0 or tie[:3] != (0, 0, 0)):
        return None
    entries = [keys[index:index + 4] for index in range(4, len(keys), 4)]
    if len({entry[0] for entry in entries}) != len(entries):
        return None
    direct = {key: value for key, location, count, value in entries if location == 0 and count == 1}
    model = direct.get(1024)
    epsg = direct.get({1: 3072, 2: 2048}.get(model), 0)
    if direct.get(1025) != 1 or not 1024 <= epsg < 32767:
        return None
    return (keys, tags.get(34736), tags.get(34737)), tie[3:], (
        scale[0] * page.imagewidth, scale[1] * page.imagelength)


def _alignment(pan, mss_page, mss_sidecars):
    import tifffile

    unavailable = {'status': 'unaligned', 'detail': '未完成空间对齐：没有可验证的同 CRS、同范围轴对齐像元网格'}
    if mss_sidecars:
        return {'status': 'unaligned', 'detail': '未完成空间对齐：MSS 含外部 RPC，仅显示 MSS 原始像素网格'}
    try:
        if _sidecars(pan):
            return unavailable
        with tifffile.TiffFile(pan) as tif:
            pan_page = tif.pages[0]
            pan_grid, mss_grid = _grid(pan_page), _grid(mss_page)
            if (pan_page.samplesperpixel != 1 or not pan_grid or not mss_grid
                    or pan_grid != mss_grid or not all(map(math.isfinite, pan_grid[2]))):
                return unavailable
            return {'status': 'aligned', 'method': 'exact_embedded_geotiff_footprint',
                    'width': pan_page.imagewidth, 'height': pan_page.imagelength}
    except (ValueError, TypeError, OSError, IndexError, OverflowError):
        return unavailable


def _mss_rows(page, stream):
    width, height = page.imagewidth, page.imagelength
    dtype = page.dtype
    if (page.samplesperpixel != 4 or any(int(sample) in (1, 2) for sample in page.extrasamples)
            or int(page.photometric) not in (1, 2)):
        raise WaterUnavailable('band_mismatch', 'MSS TIFF 必须有四个非 Alpha 光谱波段')
    if (not 0 < width <= 131072 or not 0 < height <= 131072 or page.is_tiled or page.compression != 1
            or page.planarconfig not in (1, 2) or dtype is None or page.predictor != 1
            or page.fillorder != 1 or page.bitspersample != dtype.itemsize * 8
            or (dtype.kind, dtype.itemsize) not in {('u', 1), ('u', 2), ('i', 1), ('i', 2), ('f', 4)}):
        raise WaterUnavailable('unsupported_raster', 'MSS 需要受支持的无压缩条带 TIFF 与 8/16 位整数或 32 位浮点采样')
    for tag, expected, default in ((258, dtype.itemsize * 8, 1), (339, {'u': 1, 'i': 2, 'f': 3}[dtype.kind], 1)):
        values = page.tags[tag].value if tag in page.tags else default
        if values != expected and values != (expected,) * 4:
            raise WaterUnavailable('unsupported_raster', 'MSS TIFF 波段采样类型不一致')
    dtype = dtype.newbyteorder(page.parent.byteorder)
    rows_per_strip = page.rowsperstrip
    if rows_per_strip <= 0:
        raise WaterUnavailable('invalid_raster', 'MSS 条带高度无效')
    strips = (height + rows_per_strip - 1) // rows_per_strip
    size = os.fstat(stream.fileno()).st_size
    expected = strips * (4 if page.planarconfig == 2 else 1)
    if (len(page.dataoffsets) != expected or len(page.databytecounts) != expected
            or min(page.dataoffsets, default=0) <= 0):
        raise WaterUnavailable('invalid_raster', 'MSS 存在缺失条带')
    stride = 4 if page.planarconfig == 1 else 1
    row_bytes = width * dtype.itemsize * stride
    read = partial(read_strip_span, stream, page.dataoffsets, page.databytecounts, file_size=size)
    for row in range(height):
        strip, local = divmod(row, rows_per_strip)
        if stride == 4:
            pixels = np.frombuffer(read(strip, local * row_bytes, row_bytes), dtype).reshape(width, 4)
            yield pixels[:, 1], pixels[:, 3]
        else:
            yield tuple(np.frombuffer(read(strip + band * strips, local * row_bytes, row_bytes), dtype)
                        for band in (1, 3))


def _create_tiff(stack, path, shape, dtype, spatial, nodata):
    import tifffile

    tags = spatial + (((42113, 's', 0, nodata, False),) if nodata is not None else ())
    with tifffile.TiffWriter(path, byteorder='<', bigtiff=math.prod(shape) * np.dtype(dtype).itemsize >= 2**31) as writer:
        offset, _ = writer.write(None, dtype=dtype, shape=shape, metadata=None,
                                 photometric='minisblack', extratags=tags, returnoffset=True,
                                 rowsperstrip=max(1, (256 * 1024) // (shape[1] * np.dtype(dtype).itemsize)))
    stream = stack.enter_context(path.open('r+b'))
    stream.seek(offset)
    return stream


def _read_nodata(page):
    metadata = page.tags.get(42112)
    if metadata is not None:
        if metadata.count > 1024 * 1024:
            raise WaterUnavailable('unsupported_raster_metadata', 'MSS 内嵌 GDAL 元数据超过读取预算')
        try:
            root = ET.fromstring(metadata.value, parser=ET.XMLParser(target=_CalibrationXML()))
        except (ET.ParseError, ValueError, TypeError, LookupError) as exc:
            raise WaterUnavailable('invalid_raster', 'MSS 内嵌 GDAL 元数据无法解析') from exc
        if any(node.get('name', '').strip().upper() in {'NODATA_VALUES', 'NODATA_VALUE', '_FILLVALUE'}
               for node in root.iter()):
            raise WaterUnavailable('unsupported_nodata', 'MSS 声明了不支持的联合或逐波段 NoData；不生成水体产物')
    raw = page.tags.get(42113)
    if raw is not None and raw.count > 128:
        raise WaterUnavailable('invalid_raster', 'MSS NoData 标签超过读取预算')
    try:
        return float(raw.value) if raw is not None else None
    except (ValueError, TypeError) as exc:
        raise WaterUnavailable('invalid_raster', 'MSS NoData 标签无效') from exc


def _render(page, stream, target, profile, calibration, alignment):
    bands, gain, bias, _ = calibration
    nodata = _read_nodata(page)
    height, width = page.imagelength, page.imagewidth
    rows = _mss_rows(page, stream)
    first = next(rows)
    dtype = first[0].dtype
    limit = float(np.finfo(dtype).max) if dtype.kind == 'f' else max(abs(int(np.iinfo(dtype).min)), int(np.iinfo(dtype).max))
    if any(not math.isfinite((limit * gain[index] + abs(bias[index])) * 2) for index in (1, 3)):
        raise WaterUnavailable('invalid_calibration', 'MSS 定标系数超出可计算范围')
    spatial = _tags(page)
    if shutil.disk_usage(target).free < width * height * 6 + 16 * 1024 * 1024:
        raise WaterUnavailable('storage_budget', '水体产物目录剩余空间不足')
    preview_scale = min(1, 1024 / max(width, height))
    pw, ph = max(1, round(width * preview_scale)), max(1, round(height * preview_scale))
    aligned = alignment['status'] == 'aligned'
    preview = np.zeros((ph, pw, 4), np.uint8)
    palette = np.array([(46, 56, 65, 255), (0, 189, 255, 255), (118, 103, 84, 255)], np.uint8)
    sample_rows = {(2 * index + 1) * height // (2 * ph): index for index in range(ph)}
    sample_cols = (2 * np.arange(pw) + 1) * width // (2 * pw)
    valid_count = water_count = 0
    with ExitStack() as stack:
        outputs = [_create_tiff(stack, target / name, (height, width), kind, spatial, sentinel)
                   for name, kind, sentinel in [('ndwi.tif', '<f4', 'nan'), ('water.tif', 'u1', '255'),
                                                ('valid.tif', 'u1', None)]]
        for index, (green, nir) in enumerate(chain((first,), rows)):
            result = ndwi_window(green, nir, nodata, gain, bias, profile)
            _, water, valid = result
            valid_count += int(np.count_nonzero(valid))
            water_count += int(np.count_nonzero(water == 1))
            for output, values in zip(outputs, result):
                output.write(values.tobytes())
            if index in sample_rows:
                sampled = water[sample_cols]
                if aligned:
                    preview[sample_rows[index], sampled == 1] = (0, 189, 255, 128)
                else:
                    preview[sample_rows[index]] = palette[np.where(sampled == 255, 2, sampled)]
    if not valid_count:
        raise WaterUnavailable('no_valid_pixels', 'MSS 没有可计算 NDWI 的有效像素')
    Image.fromarray(preview).save(target / ('overlay.png' if aligned else 'mss-preview.png'))
    return {'width': width, 'height': height, 'dtype': dtype.name,
            'spatial_tags': [code for code, *_ in spatial], 'source_nodata': str(nodata) if nodata is not None else None,
            'bands': list(bands), 'gain': list(gain), 'bias': list(bias),
            'valid_pixels': valid_count, 'water_pixels': water_count}


def _config_profile():
    try:
        return WaterProfile(threshold=float(os.environ.get('SR_MSS_WATER_THRESHOLD', '0.3')))
    except ValueError as exc:
        raise WaterUnavailable('invalid_profile', 'SR_MSS_WATER_THRESHOLD 必须是 [-1, 1] 内的有限数字') from exc


def plan_water(pan, profile=None):
    profile = profile or _config_profile()
    pan, mss, xml, satellite, sensor = resolve_mss(pan)
    sidecars = sorted(set(_sidecars(mss)))
    unsupported = [path.name for path in sidecars
                   if not path.name.lower().endswith(('.rpb', '.rpc', '.rpc.txt', '_rpc.txt'))]
    if unsupported:
        raise WaterUnavailable('unsupported_spatial_metadata',
                               '无法安全读取 MSS 旁车空间参考或有效性信息：' + '、'.join(unsupported))
    sources = {role: {'scene': path.parent.name, 'name': path.name, **resource_identity(path)}
               for role, path in (('pan', pan), ('mss', mss), ('xml', xml))}
    sources['pan']['preview_revision'] = source_revision(pan)
    sources['mss']['external_rpc'] = [path.name for path in sidecars]
    key = hashlib.sha256(json.dumps([VERSION, asdict(profile), sources, bool(_sidecars(pan))],
                                    sort_keys=True).encode()).hexdigest()
    root = Path(os.environ.get('SR_MSS_WATER_DIR') or Path(default_db_path()).resolve().parent / 'mss_water')
    return key, root / key, (pan, mss, xml, satellite, sensor), sources, profile


def generate_water(plan):
    key, output, (pan, mss, xml, satellite, sensor), sources, profile = plan
    if plan_water(pan, profile)[0] != key:
        raise WaterUnavailable('source_changed', 'MSS 或 PAN 在排队期间变更，请重试')
    if (output / 'metadata.json').is_file():
        return json.loads((output / 'metadata.json').read_text(encoding='utf-8'))
    try:
        import tifffile
    except ImportError as exc:
        raise WaterUnavailable('raster_dependency', 'API 环境未安装 MSS GeoTIFF 输出依赖 tifffile') from exc
    calibration = read_calibration(xml, satellite, sensor, mss.parent.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mss-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'products'
        staged.mkdir()
        try:
            with tifffile.TiffFile(mss) as tif, mss.open('rb') as stream:
                page = tif.pages[0]
                if len(tif.pages) != 1 or page.subifds:
                    raise WaterUnavailable('unsupported_raster', 'MSS 必须是单 IFD 四波段 TIFF')
                alignment = _alignment(pan, page, sources['mss']['external_rpc'])
                raster = _render(page, stream, staged, profile, calibration, alignment)
        except WaterUnavailable:
            raise
        except (ValueError, TypeError, IndexError, StopIteration, OverflowError) as exc:
            raise WaterUnavailable('invalid_raster', '无法按原始 MSS 网格读取或编码 TIFF') from exc
        if plan_water(pan, profile)[0] != key:
            raise WaterUnavailable('source_changed', 'MSS 或 PAN 在处理期间变更，请重试')
        result = {'status': 'ready', 'version': VERSION, 'id': key, 'profile': asdict(profile),
                  'sensor': sensor, 'satellite': satellite, 'sources': sources, 'mss_grid': raster,
                  'xml_identity': calibration[3], 'usage': 'viewer_preview_only',
                  'classification': 'float32 NDWI promoted to float64 >= profile.threshold',
                  'alignment': alignment, 'assets': sorted(path.name for path in staged.iterdir()) + ['metadata.json'],
                  'encoding': {'ndwi': 'float32; NaN=invalid', 'water': 'uint8; 0=land, 1=water, 255=invalid',
                               'valid': 'uint8; 0=invalid, 1=valid'}}
        (staged / 'metadata.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2),
                                             encoding='utf-8')
        try:
            staged.rename(output)
        except OSError:
            if not (output / 'metadata.json').is_file():
                raise
            return json.loads((output / 'metadata.json').read_text(encoding='utf-8'))
    return result
