import asyncio
from types import SimpleNamespace
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
