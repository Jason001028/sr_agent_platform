import asyncio
from types import SimpleNamespace
import pytest
from backend.services.heavy_work import HeavyWork
from backend.services.evidence_worker import busy,evidence_loop
from backend.services.store import Store


def test_disabled_performs_no_db_or_raster_work(monkeypatch):
    monkeypatch.setenv('SR_EVIDENCE_REPORT_ENABLED','0')
    asyncio.run(evidence_loop(SimpleNamespace()))


def test_global_jobs_block_evidence(tmp_path):
    store=Store(tmp_path/'test.db')
    try:
        assert not busy(store)
        store.put_sr_task('direct',{},status='UNKNOWN',job_id=99)
        assert busy(store)
    finally:store.close()


def test_evidence_never_jumps_foreground_or_background():
    async def scenario():
        work=HeavyWork();order=[]
        # Schedule in reverse priority before the drain gets a turn.
        calls=[asyncio.create_task(work.run(lambda name=name:order.append(name),evidence=name=='evidence',background=name=='batch')) for name in ['evidence','batch','interactive']]
        await asyncio.gather(*calls)
        assert order==['interactive','batch','evidence']
        await work.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('required,review,expected',[
    (False,None,'computing'),(True,None,'waiting_input'),
    (True,{'decision':'approve','mask_sha256':'mask'},'computing'),
    (True,{'decision':'approve','mask_sha256':'old-mask'},'waiting_input'),
    (True,{'decision':'exclude','mask_sha256':'mask'},'waiting_input'),
])
def test_mask_review_admission_matches_compute_readiness(tmp_path,required,review,expected):
    from backend.services.evidence_store import report_compute_readiness
    store=Store(tmp_path/'review.db')
    cp={'review_required':required,'review':review,'mask':{'sha256':'mask'}}
    try:
        goal=store.create_batch_goal(title='review',request_key='review',content_sha256='review',checkpoint={'mode':'production'},
            items=[{'item_id':'i','ordinal':1,'name':'PAN','status':'MASK_READY','checkpoint':cp}])
        items=store.list_goal_items(goal['goal_id'])
        assert report_compute_readiness(goal,items,{})==expected
        assert busy(store)==(expected=='computing')
    finally:store.close()
