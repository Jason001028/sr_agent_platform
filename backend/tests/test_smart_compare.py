"""Synthetic TIFF integration; these are not production/GDAL acceptance tests."""
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import tifffile
from scipy.ndimage import gaussian_filter

from backend.services import image_diff, smart_compare, preview_jpg, batch_manifest, batch_detector
from backend.services.blur_detection import BlurConfig, analyze_texture, apply_scene_masks


@pytest.fixture
def pair(tmp_path):
    reference = np.random.default_rng(841).uniform(20000, 50000, (96, 96)).astype(np.uint16)
    target, _ = image_diff.resize_to((192, 192), reference, np.ones(reference.shape, bool))
    target[48:144, 48:144] = gaussian_filter(target, 2)[48:144, 48:144]
    paths = [tmp_path/'PAN.tif', tmp_path/(tmp_path.name+'_NOSR.tif')]
    for p, a in zip(paths, (target.astype(np.uint16), reference)):
        tifffile.imwrite(p, a, rowsperstrip=8)
    specs = [{"id": i+1, "sceneId": str(i), "name": p.name, "sourceRevision": preview_jpg.source_revision(p),
              "sourceWidth": a.shape[1], "sourceHeight": a.shape[0], "thumbWidth": a.shape[1], "thumbHeight": a.shape[0], "pixelKey": str(i)}
             for i, (p,a) in enumerate(zip(paths,(target,reference)))]
    body = {"method":"smart", "image_a":specs[0], "image_b":specs[1], "autoMask":True,
            "minAreaPx":16, "openKernel":1, "closeKernel":1}
    return paths, body, lambda sid: paths[int(sid)]


def test_source_grid_compare_and_identity(pair):
    paths, body, resolve = pair
    with patch.object(smart_compare.batch_difference, 'sample_grid', wraps=smart_compare.batch_difference.sample_grid) as sample:
        result = smart_compare.analyze_grid({**body,"compare":True}, resolve)
    assert sample.call_count == 2
    assert result['source'] == 'source-grid'
    assert result['sourceIdentity']['referenceSide'] == 'B'
    assert result['regions']
    assert all(not r['maskEligible'] for r in result['regions'])
    assert len(result['comparison']['summary']) == 3
    assert all(0 <= p['iou'] <= 1 for p in result['comparison']['overlaps'])
    assert all(p['needs_review'] for p in result['autoMask']['polygons'])
    json.dumps(result, allow_nan=False)


def test_native_roi_halo_and_mask_contract(pair):
    _, body, resolve = pair
    result = smart_compare.analyze_roi({**body,'roi':{'x':40,'y':40,'w':112,'h':112},
                                      'smartOptions':{'frequencyReview':True}}, resolve)
    assert result['analysisGrid']['origin'] == [40,40]
    assert result['analysisGrid']['width'] == 112
    assert result['analysisPrecision']['nativeRefined']
    assert result['regions']
    assert all(r['diagnostics']['frequency']['status'] == 'reviewed' for r in result['regions'])
    assert result['autoMask']['sources'][0]['transform']['offset'] == [40,40]
    json.dumps(result, allow_nan=False)


def test_reversed_grid_roles_and_revision_guard(pair):
    _, body, resolve = pair
    original = smart_compare.analyze_grid(body,resolve)
    reverse = smart_compare.analyze_grid({**body,'image_a':body['image_b'],'image_b':body['image_a']},resolve)
    assert reverse['sourceIdentity']['referenceSide'] == 'A'
    assert reverse['differenceMap'] == original['differenceMap']
    with pytest.raises(image_diff.DiffError,match='修订'):
        smart_compare.analyze_grid({**body,'image_a':{**body['image_a'],'sourceRevision':'wrong'}},resolve)


def test_batch_smart_uses_same_core_and_review_guard(pair):
    paths, _, _ = pair
    target = {**batch_manifest._image(paths[0]),'item_id':'smart-test'}
    reference = batch_manifest._image(paths[1])
    result = batch_detector.detect(target,reference,{'mode':'diff','method':'smart','min_area_px':16,'open_kernel':1,'close_kernel':1})
    assert result['analysis']['algorithmVersion'].startswith('smart-compare')
    assert result['requires_geometry_review']
    assert result['analysis']['calibrationStatus'] == 'experimental-unvalidated'


def test_scene_masks_are_explicit_and_never_erase_evidence():
    a = np.random.default_rng(5).normal(size=(96,96)); b = gaussian_filter(a,1)
    evidence = analyze_texture(a,b)
    apply_scene_masks(evidence,{'cloud':{'mask':np.ones(a.shape,bool),'registered':False}},BlurConfig())
    assert evidence['maskAvailability']['cloud'] == 'unverified-registration'
    before = evidence['score'].copy()
    apply_scene_masks(evidence,{'water':{'mask':np.ones(a.shape,bool),'registered':True,'sourceIdentity':'fixture'}},BlurConfig())
    np.testing.assert_allclose(evidence['score'],before*.5)
    assert evidence['maskAvailability']['shadow'] == 'unverified'


def test_context_halo_crop_matches_full_map():
    a = np.random.default_rng(7).normal(size=(160,160)); b=a.copy()
    b[50:110,50:110]=gaussian_filter(a,2)[50:110,50:110]
    full=image_diff.analyze_arrays(a,b,method='smart',reference_side='A')
    cropped=image_diff.analyze_arrays(a,b,method='smart',reference_side='A',roi={'x':40,'y':40,'w':80,'h':80})
    np.testing.assert_array_equal(smart_compare._mask(cropped),smart_compare._mask(full)[40:120,40:120])


def test_smart_rejects_implicit_direction_and_oversized_roi(pair):
    with pytest.raises(image_diff.DiffError,match='referenceSide'):
        image_diff.analyze_arrays(np.ones((16,16)),np.ones((16,16)),method='smart')
    _, body, resolve=pair
    with pytest.raises(image_diff.DiffError,match='2048'):
        smart_compare.analyze_roi({**body,'roi':{'x':0,'y':0,'w':3000,'h':20}},resolve)


def test_native_supplement_has_explicit_precision_and_cancel(pair):
    _,body,resolve=pair
    result=smart_compare.analyze_native_scan(body,resolve)
    assert result['source']=='source-tiled'
    assert result['analysisPrecision']['tiles']==1
    assert result['analysisPrecision']['nativeScanned']
    assert all(not r['maskEligible'] for r in result['regions'])
    def cancelled(): raise image_diff.DiffError('cancelled')
    with pytest.raises(image_diff.DiffError,match='cancelled'):
        smart_compare.analyze_grid({**body,'_cancel_check':cancelled},resolve)


def test_native_tiled_seams_match_single_native_score(tmp_path):
    # Cross the 512px tile seam with one defect. Calibration is shared and
    # every tile includes a halo; quantization tolerance is one display byte.
    reference=np.random.default_rng(91).uniform(10000,50000,(160,320)).astype(np.uint16)
    target,_=image_diff.resize_to((320,640),reference,np.ones(reference.shape,bool))
    target[80:260,470:570]=gaussian_filter(target,2)[80:260,470:570]
    paths=[tmp_path/'PAN.tif',tmp_path/(tmp_path.name+'_NOSR.tif')]
    for p,a in zip(paths,(target.astype(np.uint16),reference)):tifffile.imwrite(p,a,rowsperstrip=8)
    specs=[{'id':i,'sceneId':str(i),'name':p.name,'sourceRevision':preview_jpg.source_revision(p)} for i,p in enumerate(paths)]
    body={'method':'smart','image_a':specs[0],'image_b':specs[1],'smartOptions':{'calibrationScale':1.},'openKernel':1,'closeKernel':1,'minAreaPx':1}
    resolve=lambda sid:paths[int(sid)]
    tiled=smart_compare.analyze_native_scan(body,resolve)
    single=smart_compare.analyze_roi({**body,'roi':{'x':0,'y':0,'w':640,'h':320}},resolve)
    def heat(r):
        import io,base64
        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(base64.b64decode(r['differenceMap'].split(',')[1]))),dtype=float)
    np.testing.assert_allclose(heat(tiled)[:,500:524],heat(single)[:,500:524],atol=1)


@pytest.mark.parametrize('kind',['brightness','contrast','sharpen','noise','salt','flat-water','cloud','shadow','stripe','edge-preserving'])
def test_synthetic_scenarios_are_finite_and_explicitly_uncalibrated(kind):
    a=np.random.default_rng(117).normal(30000,3000,(128,128))
    b=a.copy()
    if kind=='brightness':b+=300
    elif kind=='contrast':b=b*.8+1000
    elif kind=='sharpen':b+=a-gaussian_filter(a,1)
    elif kind=='noise':b+=np.random.default_rng(118).normal(0,1000,b.shape)
    elif kind=='salt':b[::8,::8]=65000
    elif kind=='flat-water':a[30:90,30:90]=b[30:90,30:90]=28000
    elif kind=='cloud':b[30:90,30:90]=60000
    elif kind=='shadow':b[30:90,30:90]*=.4
    elif kind=='stripe':b[:,60:64]=gaussian_filter(a,2)[:,60:64]
    elif kind=='edge-preserving':
        from scipy.ndimage import median_filter
        b[30:90,30:90]=median_filter(a,3)[30:90,30:90]
    r=analyze_texture(a,b)
    assert np.isfinite(r['score']).all() and 0<=r['score'].min()<=r['score'].max()<=1
    assert r['calibrationStatus']=='experimental-unvalidated'
    if kind in ('brightness','contrast','sharpen','flat-water'):
        assert r['score'].mean()<.01
