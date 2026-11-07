import numpy as np
from pathlib import Path
import pytest
import tifffile
from PIL import Image
from unittest.mock import patch

from backend.services import batch_detector, batch_manifest, batch_mask, batch_worker, image_diff, batch_difference


def refs(tmp_path, size=(160,160)):
    w,h=size; pan=tmp_path/'PAN.tif'; nosr=tmp_path/'scene_NOSR.tif'
    a=np.full((h,w),100,np.uint16)
    for y in range(10,110,20):
        for x in range(10,110,20): a[y:y+10,x:x+10]=200
    tifffile.imwrite(pan,a);tifffile.imwrite(nosr,np.full((h//2,w//2),100,np.uint16))
    return {**batch_manifest._image(pan),'item_id':'i'},batch_manifest._image(nosr),a


def test_http_and_batch_share_core_and_batch_keeps_more_than_16(tmp_path):
    target,reference,original=refs(tmp_path)
    config={'mode':'diff','min_area_px':1,'blur':False,'open_kernel':1,'close_kernel':1}
    with patch.object(image_diff,'analyze_arrays',wraps=image_diff.analyze_arrays) as core:
        detection=batch_detector.detect(target,reference,config)
        http=image_diff.analyze_payload({'image_a':{'id':1,'sourceWidth':160,'sourceHeight':160,'grayPng':image_diff._b64_png(original.astype('uint8'))},
            'image_b':{'id':2,'sourceWidth':80,'sourceHeight':80,'grayPng':image_diff._b64_png(np.full((160,160),100,np.uint8))},
            'autoMask':True,'minAreaPx':1,'blur':False,'openKernel':1,'closeKernel':1})
        assert core.call_count==2
    assert len(http['autoMask']['polygons'])==16
    assert detection['candidate_count']==25
    assert detection['candidate_accounting']=={'total':25,'processed':25,'unprocessed':0,'rejected':[], 'region_budget':512,'budget_exhausted':False}
    assert detection['detector_name']=='shared-difference' and detection['provenance']=='real'
    result=batch_mask.build_mask_result(detection,target,{'path':str(tmp_path/'bundle'/'mask.tif'),'rows_per_strip':7})
    # Decode independently of tifffile's optional, host-specific imagecodecs.
    with Image.open(result['path']) as image:
        mask = np.asarray(image)
    assert mask.shape==original.shape and mask.dtype==np.uint8
    assert np.all(mask[original==200]==255)
    assert set(np.unique(mask))=={0,255}
    with pytest.raises(ValueError,match='另一份源影像'):
        batch_mask.build_mask_result(detection,{**target,'image_key':'different-image'},{'path':str(tmp_path/'bad'/'mask.tif')})


def test_grid_sampler_is_bounded_and_matches_shared_bilinear_resize(tmp_path):
    from backend.services.batch_difference import sample_grid
    target,_,a=refs(tmp_path,(160,160))
    with patch.object(batch_detector,'_iter_windows',wraps=batch_detector._iter_windows) as reader:
        sampled,valid=sample_grid(target['path'],160,160,(33,47),{'window_rows':3})
        assert reader.call_args.args[-1]==3
    expected,ev=image_diff.resize_to((33,47),a.astype(np.float32),np.ones(a.shape,bool))
    np.testing.assert_allclose(sampled,expected,rtol=1e-6,atol=1e-5)
    np.testing.assert_array_equal(valid,ev)


@pytest.mark.parametrize('pan_size,nosr_size', [
    ((30831, 30948), (15768, 15823)),  # Existing production example in tourCompare.test.ts.
    ((203, 207), (104, 106)),
    ((10000, 10000), (5000, 4950)),  # Inclusive 1% boundary.
])
def test_rounded_production_grid_aspects(pan_size, nosr_size):
    # Check real production dimensions without allocating large fixture TIFFs.
    assert pan_size[0] * nosr_size[1] != pan_size[1] * nosr_size[0]
    batch_difference._check_grid_aspect(*pan_size, *nosr_size)


@pytest.mark.parametrize('pan_size,nosr_size', [
    ((10000, 10000), (5000, 4949)),  # Just over the 1% threshold.
    ((200, 200), (98, 100)),       # Deviation in the opposite direction.
])
def test_production_grid_aspects_over_tolerance_are_rejected(pan_size, nosr_size):
    with pytest.raises(ValueError, match='超过 1% 容差'):
        batch_difference._check_grid_aspect(*pan_size, *nosr_size)


def test_rounded_grids_detect_and_build_mask_on_original_pan_grid(tmp_path):
    pan = np.full((207, 203), 100, np.uint16)
    pan[50:90, 70:110] = 200
    tifffile.imwrite(tmp_path / 'PAN.tif', pan)
    tifffile.imwrite(tmp_path / 'scene_NOSR.tif', np.full((106, 104), 100, np.uint16))
    target = {**batch_manifest._image(tmp_path / 'PAN.tif'), 'item_id': 'rounded'}
    reference = batch_manifest._image(tmp_path / 'scene_NOSR.tif')
    detection = batch_detector.detect(target, reference, {
        'mode': 'diff', 'min_area_px': 1, 'blur': False, 'open_kernel': 1, 'close_kernel': 1})
    assert detection['candidate_count'] > 0
    assert detection['analysis']['registration'] == 'production-proportional-grid'
    assert detection['candidate_accounting']['unprocessed'] == 0
    mask_result = batch_mask.build_mask_result(detection, target, {'path': str(tmp_path / 'bundle' / 'mask.tif')})
    # Independent TIFF decoder; the host's tifffile/imagecodecs pair is old.
    with Image.open(mask_result['path']) as image:
        mask = np.asarray(image)
    assert mask.shape == pan.shape and mask.dtype == np.uint8
    assert np.all(mask[pan == 200] == 255)
    assert set(np.unique(mask)) == {0, 255}


def test_aspect_mismatch_still_fails_with_dimensions_and_deviation(tmp_path):
    target, _, _ = refs(tmp_path, (200, 200))
    tifffile.imwrite(tmp_path / 'scene_NOSR.tif', np.zeros((98, 100), np.uint16))
    reference = batch_manifest._image(tmp_path / 'scene_NOSR.tif')
    with pytest.raises(ValueError, match='PAN 200×200，NOSR 100×98，宽高比偏差 2.000%'):
        batch_difference.validate_pair(target, reference)


def test_matching_aspects_in_different_scenes_are_still_rejected(tmp_path):
    target, _, _ = refs(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    tifffile.imwrite(other / 'scene_NOSR.tif', np.zeros((80, 80), np.uint16))
    reference = batch_manifest._image(other / 'scene_NOSR.tif')
    with pytest.raises(ValueError, match='不属于同一生产场景'):
        batch_difference.validate_pair(target, reference)


def test_rounded_grids_keep_frozen_revision_identity_and_registration_guards(tmp_path):
    target, reference, _ = refs(tmp_path, (203, 207))
    # refs intentionally rounds width/height independently.
    with pytest.raises(ValueError, match='修订已变化'):
        batch_difference.validate_pair({**target, 'revision': 'stale'}, reference)
    with pytest.raises(ValueError, match='资源身份与冻结输入不一致'):
        batch_difference.validate_pair({**target, 'image_key': 'other'}, reference)
    with pytest.raises(ValueError, match='配对身份不一致'):
        batch_difference.validate_pair({**target, 'tourIdentity': {**target['tourIdentity'], 'sequence': 'other'}}, reference)
    actual_identity = batch_difference.image_identity
    def registered(path, sequence, role):
        return {**actual_identity(path, sequence, role), 'registration': {
            'crs': 'EPSG:4326', 'orientation': [1, 0, 0, -1],
            'extent': [0 if role == 'pan' else 1, 0, 100 if role == 'pan' else 101, 100]}}
    with patch.object(batch_difference, 'image_identity', side_effect=registered):
        with pytest.raises(ValueError, match='空间范围或方向不一致'):
            batch_difference.validate_pair(target, reference)


def test_source_drift_grid_mismatch_and_nodata_are_not_no_issue(tmp_path):
    target,reference,a=refs(tmp_path)
    tifffile.imwrite(reference['path'],np.zeros((81,80),np.uint16))
    with pytest.raises(ValueError):batch_detector.detect(target,reference,{'mode':'diff'})
    a=np.full(a.shape,-9999,np.int16)
    tifffile.imwrite(target['path'],a,extratags=[(42113,'s',0,'-9999',False)])
    tifffile.imwrite(reference['path'],np.full((80,80),-9999,np.int16),extratags=[(42113,'s',0,'-9999',False)])
    target={**batch_manifest._image(Path(target['path'])),'item_id':'i'}; reference=batch_manifest._image(Path(reference['path']))
    with pytest.raises(ValueError,match='有效像素'):batch_detector.detect(target,reference,{'mode':'diff'})


def test_unprocessed_candidates_never_become_partial_sr_mask():
    with patch.object(batch_detector,'detect',return_value={'provenance':'real','candidates':[{'candidate_id':'x'}],
        'candidate_accounting':{'total':513,'processed':512,'unprocessed':1}}),patch.object(batch_mask,'build_mask_result') as build:
        result=batch_worker._detection_job({'target_image':{},'mode':'demo'})
    assert result['incomplete'] and result['mask'] is None
    build.assert_not_called()


def test_large_image_memory_is_bounded_by_analysis_grid_and_strip(tmp_path):
    import json,subprocess,sys
    from backend.tests.test_batch_artifacts import _PEAK_RSS_PROBE
    probe=_PEAK_RSS_PROBE.split('path, width, height, rows =')[0]+'''
from pathlib import Path
from backend.services import batch_manifest
pan=Path(sys.argv[1]);nosr=pan.parent/'scene_NOSR.tif'
target={**batch_manifest._image(pan),'item_id':'memory'}
ref=batch_manifest._image(nosr)
result=batch_detector.detect(target,ref,{'mode':'diff','max_edge':128,'window_rows':32})
assert result['candidate_count']==0
print(json.dumps({'peak':peak_rss_bytes(),'analysis':result['analysis']['size']}))
'''
    peaks=[]
    for h in (4096,8192):
        scene=tmp_path/str(h);scene.mkdir()
        for path,shape in ((scene/'PAN.tif',(h,8192)),(scene/'scene_NOSR.tif',(h//2,4096))):
            pixels=tifffile.memmap(path,shape=shape,dtype=np.uint16);pixels.flush();del pixels
        process=subprocess.run([sys.executable,'-c',probe,str(scene/'PAN.tif')],cwd=Path(__file__).resolve().parents[2],
            capture_output=True,text=True,timeout=120)
        assert process.returncode==0,process.stderr
        peaks.append(json.loads(process.stdout.strip().splitlines()[-1])['peak'])
    print(json.dumps({'source_width':8192,'heights':[4096,8192],'peak_mib':[round(p/1048576,2) for p in peaks]}))
    assert peaks[1]-peaks[0]<24*1048576
    assert max(peaks)<512*1048576
