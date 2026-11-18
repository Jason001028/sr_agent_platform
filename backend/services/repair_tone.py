"""Display/ranking prior matching diffTone memberships; never changes masks."""
import numpy as np
from scipy.ndimage import uniform_filter

VERSION='nonlinear-tone.v1'


def smoothstep(lo,hi,value):
    t=np.clip((value-lo)/(hi-lo),0,1)
    return t*t*(3-2*t)


def weight_map(a,b,valid):
    from backend.services.image_diff import _shared_normalize
    a,b=_shared_normalize(a,b,valid)
    radius=max(2,min(8,int(min(a.shape)/300+.5)));size=2*radius+1
    v=valid.astype(np.float32)
    denominator=np.maximum(uniform_filter(v,size=size,mode='constant'),1e-9)
    mean=lambda x:uniform_filter(np.where(valid,x,0).astype(np.float32),size=size,mode='constant')/denominator
    ma,mb=mean(a),mean(b)
    sigma=np.sqrt(np.maximum(0,mean(a*a)-ma*ma))*255
    luma=ma*255;delta=(mb-ma)*255
    mid=smoothstep(90,120,luma)*(1-smoothstep(180,210,luma))
    dark=1-smoothstep(70,110,luma);flat=1-smoothstep(2,8,sigma);bright=smoothstep(2,15,delta)
    weights=np.clip((1+.8*mid*(.5+.5*flat))*(1+bright*(.16*mid+.4*dark)),1,2.5)
    return np.where(valid,weights,1).astype(np.float32)
