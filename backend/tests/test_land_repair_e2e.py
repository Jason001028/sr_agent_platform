"""Repair plan through Batch Worker and real TIFF publication with fake SR."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import tifffile

from SR_code.variants import repair_sr, verify_sr_run
from backend.services import batch_detector, batch_quality, mss_land, repair_application, slurm
from backend.services.store import Store
from backend.tests import test_batch_worker
from backend.tests.test_mss_water import XML, geo
from backend.tests.test_pan_repair import FakeGdal


@pytest.fixture
def case(monkeypatch):
    worker = test_batch_worker.BatchWorkerTest()
    worker.setUp()
    try:
        with monkeypatch.context() as patch:
            # The fixture already intercepts scheduler submit and status.
            patch.setenv('SR_BATCH_PRODUCTION', '1')
            patch.setenv('SR_SLURM_FAKE', '0')
            patch.setattr(slurm, 'slurm_available', lambda: True)
            patch.setitem(sys.modules, 'verify_sr_run', verify_sr_run)
            profile_path = worker.root / 'profile.json'
            profile_path.write_text(json.dumps({
                'profile_id': 'synthetic', 'verification_ref': 'synthetic-control-points',
                'satellite': 'JXGF07D03', 'sensor': 'PMS',
                'registration_method': mss_land.METHOD,
                'land_threshold': -0.2, 'water_threshold': 0.3,
                'shoreline_pan_pixels': 1, 'registration_error_pan_pixels': 0.5,
            }), encoding='utf-8')
            patch.setenv('SR_MSS_EXEC_PROFILE', str(profile_path))
            yield worker, patch
    finally:
        worker.tearDown()


def scene(case, index, *, water_only=False):
    name = case.scene(index)
    pan = case.root / (name + '_PAN') / 'PAN.tif'
    before = np.full((24, 40), 65, np.uint16)
    before[10, 31] = 65535
    tifffile.imwrite(pan, before, extratags=geo(1) + [(42113, 's', 0, '65535', False)],
                     metadata=None)
    tifffile.imwrite(pan.parent / (pan.parent.name + '_NOSR.tif'),
                     np.full((12, 20), 12, np.uint16), metadata=None)
    mss_dir = case.root / (name + '_MSS')
    mss_dir.mkdir()
    spectral = np.zeros((12, 20, 4), np.uint8)
    spectral[:, :, 1], spectral[:, :, 3] = 90, 20
    if not water_only:
        spectral[:, 8:10, 1], spectral[:, 8:10, 3] = 25, 50
        spectral[:, 10:, 1], spectral[:, 10:, 3] = 10, 100
    tifffile.imwrite(mss_dir / 'MSS.tif', spectral, photometric='rgb',
                     extrasamples=['unspecified'], extratags=geo(2), metadata=None)
    xml = mss_dir / (mss_dir.name + '_meta.xml')
    xml.write_text(XML.replace('>SAT<', '>JXGF07D03<').replace(
        '<ProductInfo>', f'<ProductInfo><SceneID>{mss_dir.name}</SceneID><ProductType>MSS</ProductType>'),
        encoding='utf-8')
    return pan, before, xml


def mock_detector(monkeypatch):
    original = batch_detector.detect

    def deterministic_candidates(target):
        # Keep the shared DetectionResult contract and exercise real MaskBuilder.
        result = original(target, config={'mode': 'center'})
        prototype = result['candidates'][0]
        result.update(provenance='real', detector_name='shared-difference',
                      analysis={'source': 'real-dn-grid'},
                      candidate_accounting={'total': 2, 'processed': 2, 'unprocessed': 0},
                      candidates=[
                          {**prototype, 'candidate_id': 'water', 'bbox': [5, 6, 12, 17]},
                          {**prototype, 'candidate_id': 'coast', 'bbox': [17, 6, 34, 17]},
                      ])
        return result

    monkeypatch.setattr(batch_detector, 'detect', deterministic_candidates)


def fake_inference(argv):
    config = ET.parse(argv[-1]).getroot()
    private = Path(config.findtext('DatarootLQ'))
    assert config.find('LandConstraint') is None
    assert tifffile.imread(config.findtext('MaskPath')).any()
    suffix = config.findtext('Suffix')
    # Exercise the existing cubic output resampling branch with fake GDAL.
    tifffile.imwrite(private / f'PAN_{suffix}.tif', np.full((23, 39), 999, np.uint16))
    debug = private / 'Debug'
    debug.mkdir()
    (debug / 'PAN_SRLOG.txt').write_text('Run finished.\n', encoding='utf-8')


def prepare(case, scenes):
    return repair_application.prepare_repair(
        case.store, session_id='land-test', mode='production',
        sources=[{'path': str(pan), 'name': pan.parent.name} for pan, _, _ in scenes])


@pytest.mark.parametrize('count', [1, 2])
def test_confirmed_land_plan_publishes_only_eligible_pixels(case, count):
    case, monkeypatch = case
    scenes = [scene(case, index, water_only=index == 2) for index in range(1, count + 1)]
    mock_detector(monkeypatch)
    receipts = []

    def fake_executor(script, *args, **kwargs):
        receipts.append(repair_sr.run(Path(script).with_suffix('.xml'), 'fake-sr',
                                      gdal=FakeGdal(), execute=fake_inference))
        return case._sbatch(script, *args, **kwargs)

    monkeypatch.setattr(slurm, 'sbatch_submit', fake_executor)
    plan = prepare(case, scenes)
    assert plan['status'] == 'READY', plan.get('blocking')
    plan_id = plan['plan_id']
    repair_application.confirm_repair(case.store, plan_id, session_id='land-test')
    started = repair_application.start_repair(case.store, plan_id, session_id='land-test')
    gid = started['goal']['goal_id']
    assert repair_application.start_repair(case.store, plan_id, session_id='land-test')['reused']
    case.run_until(lambda: bool(receipts) or bool(case.statuses(gid) & {'BLOCKED', 'FAILED'}))
    assert len(receipts) == 1, case.items(gid)
    case.run_until(lambda: bool(case.statuses(gid) & {'AWAITING_REVIEW', 'FAILED', 'BLOCKED'}))
    assert 'AWAITING_REVIEW' in case.statuses(gid), case.items(gid)
    assert case._submitted_count() == len(case.submits) == 1
    for index, ((pan, before, _), item) in enumerate(zip(scenes, case.items(gid))):
        evidence = item['checkpoint']['land_constraint']
        final = tifffile.imread(evidence['final_mask']['path']) != 0
        classes = tifffile.imread(evidence['classification']['path'])
        original = tifffile.imread(evidence['candidate_mask']['path']) != 0
        np.testing.assert_array_equal(final, original & (classes == mss_land.LAND))
        if index == 0:
            assert final.any()
            assert evidence['stats']['excluded'] == evidence['stats']['clipped'] == 1
            assert receipts[0]['land_constraint']['mask_sha256'] == evidence['final_mask']['sha256']
        else:
            assert not final.any()
            assert not item['checkpoint'].get('job_id')
        np.testing.assert_array_equal(tifffile.imread(pan), np.where(final, 999, before))
    case.store.close()
    case.store = case.state.store = Store(case.root / 'test.db')
    item = next(item for item in case.items(gid) if item['status'] == 'AWAITING_REVIEW')
    review = batch_quality.review_for_item(case.store, item)
    for region in review['regions']:
        review = batch_quality.decide(case.store, gid, item['item_id'], {
            'review_id': review['review_id'], 'expected_version': review['version'],
            'action': 'region', 'region_id': region['region_id'],
            'decision': 'approved', 'reviewer': 'fixture-reviewer',
            'request_key': 'review-' + region['region_id'],
        })
    batch_quality.decide(case.store, gid, item['item_id'], {
        'review_id': review['review_id'], 'expected_version': review['version'],
        'action': 'scene', 'decision': 'approved', 'reviewer': 'fixture-reviewer',
        'request_key': 'review-scene',
    })
    case.run_until(lambda: case.store.get_goal(gid)['status'] == 'COMPLETED')
    assert case._submitted_count() == len(case.submits) == 1


def test_confirmed_land_plan_rejects_mss_drift(case):
    case, monkeypatch = case
    planned = scene(case, 1)
    mock_detector(monkeypatch)
    plan = prepare(case, [planned])
    assert plan['status'] == 'READY', plan.get('blocking')
    plan_id = plan['plan_id']
    repair_application.confirm_repair(case.store, plan_id, session_id='land-test')
    planned[2].write_text('<changed/>', encoding='utf-8')
    with pytest.raises(repair_application.RepairPlanConflict):
        repair_application.start_repair(case.store, plan_id, session_id='land-test')
    assert case._submitted_count() == 0


def test_scheduled_land_plan_blocks_after_approval_expires(case):
    case, monkeypatch = case
    planned = scene(case, 1)
    mock_detector(monkeypatch)
    plan = prepare(case, [planned])
    assert plan['status'] == 'READY', plan.get('blocking')
    plan_id = plan['plan_id']
    repair_application.confirm_repair(case.store, plan_id, session_id='land-test')
    gid = repair_application.start_repair(case.store, plan_id, session_id='land-test')['goal']['goal_id']
    item = case.items(gid)[0]
    checkpoint = {**item['checkpoint'], 'land_expires_at': 1}
    db = case.store._db()
    db.execute('UPDATE agent_goal_steps SET checkpoint=? WHERE step_id=?',
               (json.dumps(checkpoint), item['step_id']))
    db.commit()
    case.run_until(lambda: case.statuses(gid) == {'BLOCKED'})
    assert case._submitted_count() == len(case.submits) == 0
    np.testing.assert_array_equal(tifffile.imread(planned[0]), planned[1])


def test_review_subset_keeps_land_constraint(case):
    case, monkeypatch = case
    pan, _, _ = scene(case, 1)
    mock_detector(monkeypatch)
    plan = repair_application.prepare_repair(
        case.store, session_id='land-test', mode='production', strategy='review',
        sources=[{'path': str(pan), 'name': pan.parent.name}])
    pid = plan['plan_id']
    repair_application.confirm_repair(case.store, pid, session_id='land-test')
    gid = repair_application.start_repair(case.store, pid, session_id='land-test')['goal']['goal_id']
    item = case.items(gid)[0]
    prior = item['checkpoint']['land_constraint']
    item = case.store.review_batch_item(
        gid, item['item_id'], item['version'], 'exclude', region_id='water')
    item = case.store.review_batch_item(
        gid, item['item_id'], item['version'], 'approve', region_id='coast')
    cp = item['checkpoint']
    revised = cp['land_constraint']
    assert cp['review_original_land_constraint'] == prior
    assert revised['revision'] == prior['revision']
    initial_mask = tifffile.imread(prior['final_mask']['path'])
    final_mask = tifffile.imread(revised['final_mask']['path'])
    assert final_mask.any()
    assert not np.any((final_mask != 0) & (initial_mask == 0))
    assert cp['review']['mask_sha256'] == revised['final_mask']['sha256']
    test_batch_worker.batch_worker._verify_submission_inputs(item, 'production')


def test_land_preview_rejects_changed_mask(case):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import repair

    case, monkeypatch = case
    pan, _, _ = scene(case, 1)
    mock_detector(monkeypatch)
    owner = case.store.create_session()
    plan = repair_application.prepare_repair(
        case.store, session_id=owner, mode='production',
        sources=[{'path': str(pan), 'name': pan.parent.name}])
    row = plan['land_constraint']['items'][0]
    app = FastAPI()
    app.state.store = case.store
    app.include_router(repair.router)
    with TestClient(app) as client:
        assert client.get(row['preview_url']).status_code == 200
        evidence = case.store.get_repair_plan(plan['plan_id'])['land_items'][row['item_id']]['evidence']
        Path(evidence['final_mask']['path']).write_bytes(b'changed')
        assert client.get(row['preview_url']).status_code == 409
