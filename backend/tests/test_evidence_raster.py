import numpy as np
import pytest
import tifffile
from backend.services.evidence_raster import read_window, display_images


@pytest.mark.parametrize('dtype',['uint8','uint16','uint32','int16','float32'])
@pytest.mark.parametrize('big,endian,rps',[(False,'<',1),(True,'>',3),(False,'>',128)])
def test_exact_pixels_and_column_budget(tmp_path,dtype,big,endian,rps):
    a=np.arange(128*2048).reshape(128,2048).astype(dtype)
    p=tmp_path/'image.tif'
    tifffile.imwrite(p,a,bigtiff=big,byteorder=endian,rowsperstrip=rps)
    dn,valid,meta=read_window(p,[13,7,26,19])
    np.testing.assert_array_equal(dn,a[7:19,13:26].astype('float32'))
    assert valid.all()
    assert meta['read_bytes']==13*12*a.dtype.itemsize
    assert meta['read_bytes']<2048*12*a.dtype.itemsize


def test_budget_truncation_and_validity(tmp_path):
    p=tmp_path/'image.tif';a=np.ones((32,32),dtype='float32');a[0,0]=np.nan
    tifffile.imwrite(p,a)
    dn,v,m=read_window(p,[0,0,32,32]);assert not v[0,0]
    images,stretch=display_images({'output':(dn,v,m)})
    assert images['output'].getpixel((0,0))[3]==0
    with pytest.raises(ValueError,match='read_budget'):read_window(p,[0,0,32,32],max_bytes=1)
    with p.open('r+b') as f:f.truncate(p.stat().st_size-100)
    with pytest.raises(ValueError,match='truncated'):read_window(p,[0,0,32,32])
