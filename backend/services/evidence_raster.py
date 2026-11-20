"""Bounded uncompressed strip reader. Reads columns, never whole source rows."""
import os
import time
from pathlib import Path
import numpy as np
from backend.services import preview_jpg as pf
from backend.services.image_diff import _tiff_nodata


class EvidenceError(ValueError):
    pass


def signature(path):
    st = Path(path).stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def read_strip_span(stream, offsets, lengths, strip, start, size, file_size):
    if not 0 <= strip < min(len(offsets), len(lengths)) or start + size > int(lengths[strip]):
        raise EvidenceError('truncated_strip')
    offset = int(offsets[strip]) + start
    if offset < 0 or offset + size > file_size:
        raise EvidenceError('truncated_file')
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise EvidenceError('truncated_file')
    return data


def read_window(path, window, *, max_bytes=33554432, cancelled=lambda:False):
    started = time.monotonic()
    before = signature(path)
    info = pf.probe_tiff(Path(path))
    if int(info['sample_format']) not in (1,2,3) or int(info['planar']) not in (1,2) or info['compression'] != 1 or info['tiled'] or int(info['spp']) != 1 or info['bits'] not in (8,16,32):
        raise EvidenceError('unsupported_format')
    W, H = int(info['W']), int(info['H'])
    x0,y0,x1,y1 = window
    x0,y0,x1,y1 = max(0,int(x0)),max(0,int(y0)),min(W,int(x1)),min(H,int(y1))
    if x1<=x0 or y1<=y0 or max(x1-x0,y1-y0)>2048:
        raise EvidenceError('window_budget')
    big,endian,ifd = pf._parse_tiff_header(path)
    scalars,arrays = pf._read_ifd(path,big,endian,ifd)
    dtype = pf._np_dtype(info['bits'],int(info['sample_format']),endian)
    bpp = info['bits']//8
    needed = (x1-x0)*(y1-y0)*bpp
    if needed > max_bytes:
        raise EvidenceError('read_budget')
    def tag(key):
        return pf._read_array(path,arrays[key],endian) if key in arrays else [scalars[key]]
    offsets, lengths = tag(pf._TAG_OFFS),tag(pf._TAG_BYTES)
    rps = int(scalars.get(pf._TAG_RPS) or H)
    out = np.empty((y1-y0,x1-x0),dtype=np.float32)
    read_bytes=0
    with open(path,'rb') as f:
        for i,y in enumerate(range(y0,y1)):
            if cancelled():
                raise EvidenceError('cancelled')
            strip=y//rps
            local=((y%rps)*W+x0)*bpp
            count=(x1-x0)*bpp
            data = read_strip_span(f, offsets, lengths, strip, local, count, before[2])
            read_bytes += len(data)
            out[i]=np.frombuffer(data,dtype=dtype)
    if before!=signature(path):
        raise EvidenceError('source_changed')
    valid=np.isfinite(out)
    nodata=_tiff_nodata(path,scalars,arrays)
    if nodata is not None and np.isfinite(nodata):
        valid &= out!=np.float32(nodata)
    return out, valid, {'actual_window':[x0,y0,x1,y1], 'source_size':[W,H],
        'read_bytes':read_bytes,'metadata_bytes_excluded':True,'photometric':info.get('photometric'),
        'read_ms':(time.monotonic()-started)*1000,'peak_dn_bytes':out.nbytes}


def display_images(roles, *, max_edge=512):
    """One common DN mapping; invalid pixels excluded from percentiles."""
    from PIL import Image
    samples=[a[v] for a,v,_ in roles.values() if v.any()]
    if not samples:
        raise EvidenceError('no_valid_pixels')
    low,high = np.percentile(np.concatenate(samples),[1,99])
    images={}
    for role,(a,v,meta) in roles.items():
        scaled=np.full(a.shape,127,dtype=np.uint8)
        if high>low:
            scaled[v]=np.clip((a[v]-low)*255/(high-low),0,255).astype(np.uint8)
        if meta['photometric']==0:
            scaled[v]=255-scaled[v]
        rgba=np.stack([scaled,scaled,scaled,v.astype(np.uint8)*255],axis=-1)
        im=Image.fromarray(rgba)
        im.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        images[role]=im
    return images,{'low':float(low),'high':float(high),'method':'shared_percentile_1_99'}
