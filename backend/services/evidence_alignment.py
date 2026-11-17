"""Bounded same-CRS affine pairing. CRS reprojection is explicitly unsupported."""
import numpy as np
from scipy.ndimage import map_coordinates
from backend.services import preview_jpg as pf
from backend.services.evidence_raster import EvidenceError, read_window


def spatial_metadata(path):
    big,endian,offset=pf._parse_tiff_header(path)
    scalars,arrays=pf._read_ifd(path,big,endian,offset)
    def values(tag, dtype):
        ref=arrays.get(tag)
        if ref is None:return None
        count,size,location=ref
        if count*size>65536:raise EvidenceError('alignment_metadata_budget')
        if isinstance(location,(bytes,bytearray)):data=bytes(location)
        else:
            with open(path,'rb') as f:f.seek(location);data=f.read(count*size)
        if len(data)<count*size:raise EvidenceError('truncated_geotags')
        return np.frombuffer(data,dtype=('<' if endian=='little' else '>')+dtype,count=count)
    transform=values(34264,'f8');scale=values(33550,'f8');tie=values(33922,'f8');keys=values(34735,'u2')
    if transform is None and scale is None and tie is None and keys is None:return None
    if keys is None or len(keys)<4:raise EvidenceError('alignment_unverified')
    if len(keys)!=4+4*int(keys[3]):raise EvidenceError('alignment_unverified')
    codes={int(keys[i]):int(keys[i+3]) for i in range(4,len(keys),4) if keys[i+1]==0 and keys[i+2]==1}
    crs=codes.get(3072) or codes.get(2048)
    if not crs or crs==32767:raise EvidenceError('alignment_unverified')
    if transform is not None:
        if len(transform)!=16:raise EvidenceError('alignment_unverified')
        matrix=transform.reshape(4,4)
        if not np.allclose(matrix[3],[0,0,0,1]) or not np.allclose(matrix[[0,1],2],0):raise EvidenceError('alignment_unverified')
        affine=matrix[np.ix_([0,1,3],[0,1,3])]
    elif scale is not None and tie is not None and len(scale)==3 and len(tie)==6:
        sx,sy,_=scale;i,j,_,x,y,_=tie
        affine=np.array([[sx,0,x-i*sx],[0,-sy,y+j*sy],[0,0,1]],dtype=float)
    else:raise EvidenceError('alignment_unverified')
    if codes.get(1025,1)==2:affine=affine@np.array([[1,0,-.5],[0,1,-.5],[0,0,1]])
    elif codes.get(1025,1)!=1:raise EvidenceError('alignment_unverified')
    if not np.isfinite(affine).all() or abs(np.linalg.det(affine))<1e-20:raise EvidenceError('alignment_unverified')
    return {'crs':crs,'affine':affine}


def pairing(pan_path,nosr_path,pan_size,nosr_size):
    pan=spatial_metadata(pan_path);nosr=spatial_metadata(nosr_path)
    if pan is not None or nosr is not None:
        if pan is None or nosr is None or pan['crs']!=nosr['crs']:raise EvidenceError('alignment_unverified')
        return np.linalg.inv(nosr['affine'])@pan['affine'],'geotiff_affine'
    pw,ph=pan_size;nw,nh=nosr_size
    if min(pw,ph,nw,nh)<1 or pw%nw or ph%nh or pw//nw!=ph//nh:raise EvidenceError('alignment_unverified')
    return np.diag([nw/pw,nh/ph,1.]),'production_ratio'


def aligned_window(path,pan_window,transform,source_size,*,max_bytes=33554432):
    x0,y0,x1,y1=pan_window
    # Only four corners are needed to bound any affine-transformed rectangle.
    corners=np.array([[x0+.5,x1-.5,x0+.5,x1-.5],[y0+.5,y0+.5,y1-.5,y1-.5],[1,1,1,1]])
    mapped=transform@corners
    sw,sh=source_size
    lo=np.floor(mapped[:2].min(axis=1)-.5).astype(int)-1
    hi=np.ceil(mapped[:2].max(axis=1)-.5).astype(int)+2
    requested=[max(0,int(lo[0])),max(0,int(lo[1])),min(sw,int(hi[0])),min(sh,int(hi[1]))]
    source,valid,meta=read_window(path,requested,max_bytes=max_bytes)
    h,w=y1-y0,x1-x0
    if max(h,w)>2048:raise EvidenceError('window_budget')
    yy,xx=np.mgrid[y0:y1,x0:x1]
    sx=transform[0,0]*(xx+.5)+transform[0,1]*(yy+.5)+transform[0,2]-.5
    sy=transform[1,0]*(xx+.5)+transform[1,1]*(yy+.5)+transform[1,2]-.5
    ax,ay,_,_=meta['actual_window']
    coords=[sy-ay,sx-ax]
    values=map_coordinates(np.where(valid,source,0),coords,order=1,mode='nearest')
    weights=map_coordinates(valid.astype(np.float32),coords,order=1,mode='nearest')
    supported=(sx>=-.5)&(sx<sw-.5)&(sy>=-.5)&(sy<sh-.5)&(weights>1-1e-5)
    result=np.where(supported,values/np.maximum(weights,1e-6),0).astype(np.float32)
    meta.update(pan_window=pan_window,resampling='pixel_center_bilinear_valid_weight',pan_to_source=transform.tolist(),resample_peak_bytes=int(xx.nbytes+yy.nbytes+sx.nbytes+sy.nbytes+result.nbytes))
    return result,supported,meta
