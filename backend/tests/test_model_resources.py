import asyncio
import multiprocessing
import time
import pytest
from backend.services.store import Store
from backend.services import model_resources as resources


def _claim_in_process(path, barrier, results):
    store=Store(path)
    try:
        store._db();barrier.wait(timeout=15)
        results.put(resources.acquire(store,'endpoint','owner-'+str(multiprocessing.current_process().pid),'evidence',time.time()+60))
    finally:store.close()


def test_two_processes_cannot_hold_the_same_model_resource(tmp_path):
    path=str(tmp_path/'shared.db');store=Store(path);store._db();store.close()
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    workers=[ctx.Process(target=_claim_in_process,args=(path,barrier,results)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        assert sum(results.get(timeout=20) for _ in workers)==1
        for worker in workers:worker.join(timeout=15);assert worker.exitcode==0
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(timeout=5)
        results.close();results.join_thread()


def test_lease_expiry_and_stale_release_are_token_fenced(tmp_path):
    a=Store(tmp_path/'shared.db');b=Store(tmp_path/'shared.db')
    try:
        assert resources.acquire(a,'endpoint','old','evidence',110,now=100)
        assert not resources.acquire(b,'endpoint','new','chat',120,now=109)
        assert resources.acquire(b,'endpoint','new','chat',130,now=111)
        resources.release(a,'endpoint','old')
        assert b._db().execute('SELECT token FROM model_resource_leases').fetchone()[0]=='new'
    finally:a.close();b.close()


def test_waiting_chat_blocks_new_evidence_and_cancel_removes_waiter(tmp_path):
    a=Store(tmp_path/'shared.db');b=Store(tmp_path/'shared.db')
    async def scenario():
        url='http://model/v1';key=resources.resource_key(url)
        assert resources.acquire(a,key,'first','evidence',time.time()+60)
        async def waiting_chat():
            async with resources.model_slot(b,url,deadline=time.monotonic()+30):pass
        chat=asyncio.create_task(waiting_chat());await asyncio.sleep(.01)
        assert a._db().execute('SELECT COUNT(*) FROM model_resource_waiters').fetchone()[0]==1
        resources.release(a,key,'first')
        # The chat's DB waiter has priority even before its next poll executes.
        assert not resources.acquire(a,key,'second','evidence',time.time()+60)
        chat.cancel()
        with pytest.raises(asyncio.CancelledError):await chat
        assert a._db().execute('SELECT COUNT(*) FROM model_resource_waiters').fetchone()[0]==0
        async with resources.model_slot(a,url,kind='evidence',deadline=time.monotonic()+1,wait=False):pass
        assert a._db().execute('SELECT COUNT(*) FROM model_resource_leases').fetchone()[0]==0
    try:asyncio.run(scenario())
    finally:a.close();b.close()


def test_resource_identity_groups_endpoint_models_without_secrets():
    assert resources.resource_key('HTTP://user:secret@MODEL:80/v1/')==resources.resource_key('http://model/v1')
    assert resources.resource_key('http://model/v1')!=resources.resource_key('http://other/v1')


def test_admission_timeout_cleans_waiter_without_releasing_another_owner(tmp_path):
    from backend.agent.request_budget import DeadlineExceeded
    a=Store(tmp_path/'shared.db');b=Store(tmp_path/'shared.db');url='http://model/v1';key=resources.resource_key(url)
    async def scenario():
        assert resources.acquire(a,key,'busy','evidence',time.time()+60)
        with pytest.raises(DeadlineExceeded):
            async with resources.model_slot(b,url,deadline=time.monotonic()+.02):pytest.fail('admitted while busy')
        assert a._db().execute('SELECT COUNT(*) FROM model_resource_waiters').fetchone()[0]==0
        assert a._db().execute('SELECT token FROM model_resource_leases').fetchone()[0]=='busy'
    try:asyncio.run(scenario())
    finally:a.close();b.close()


def test_crashed_chat_waiter_expires_without_blocking_evidence(tmp_path):
    store=Store(tmp_path/'shared.db')
    try:
        with store.worker_transaction() as db:
            db.execute('INSERT INTO model_resource_waiters VALUES(?,?,?)',('crashed','endpoint',100))
        assert resources.acquire(store,'endpoint','evidence','evidence',200,now=101)
    finally:store.close()


def test_cancel_keeps_lease_until_transport_cleanup_finishes(tmp_path,monkeypatch):
    from backend.agent import async_transport
    from backend.config import Config
    a=Store(tmp_path/'shared.db');b=Store(tmp_path/'shared.db')
    cfg=Config('http://model/v1','test','model',1024,0,5)
    async def scenario():
        entered=asyncio.Event();closing=asyncio.Event();closed=asyncio.Event()
        async def network(*args,**kwargs):
            try:entered.set();await asyncio.Event().wait()
            finally:closing.set();await closed.wait()
        monkeypatch.setattr(async_transport,'_request',network)
        chat=asyncio.create_task(async_transport.request(cfg,[],[],consume=None,parse=None,store=a))
        await entered.wait();chat.cancel();await closing.wait()
        assert not resources.acquire(b,resources.resource_key(cfg.llm_base_url),'evidence','evidence',time.time()+60)
        closed.set()
        with pytest.raises(asyncio.CancelledError):await chat
        assert resources.acquire(b,resources.resource_key(cfg.llm_base_url),'evidence','evidence',time.time()+60)
    try:asyncio.run(scenario())
    finally:a.close();b.close()
