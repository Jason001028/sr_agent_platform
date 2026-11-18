"""Cross-instance admission for bounded model requests sharing one endpoint."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from backend.agent.request_budget import DeadlineExceeded

SCHEMA = '''
CREATE TABLE IF NOT EXISTS model_resource_leases (
 resource_key TEXT PRIMARY KEY, token TEXT NOT NULL, kind TEXT NOT NULL, lease_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS model_resource_waiters (
 token TEXT PRIMARY KEY, resource_key TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS model_resource_waiting ON model_resource_waiters(resource_key,expires_at);
'''
GRACE_SECONDS = 30


class ModelBusy(ValueError):
    pass


def resource_key(base_url):
    # Credentials and model names are not resource identities. Models on the
    # same configured endpoint share capacity; never persist endpoint secrets.
    url=urlsplit(base_url)
    host=(url.hostname or '').lower()
    port=url.port
    if port and (url.scheme.lower(),port) not in {('http',80),('https',443)}:host+=':'+str(port)
    endpoint=urlunsplit((url.scheme.lower(),host,url.path.rstrip('/'),'',''))
    return hashlib.sha256(endpoint.encode()).hexdigest()


def acquire(store,key,token,kind,lease_until,*,now=None):
    now=time.time() if now is None else now
    with store.worker_transaction() as db:
        if kind!='chat' and db.execute('SELECT 1 FROM model_resource_waiters WHERE resource_key=? AND expires_at>? LIMIT 1',(key,now)).fetchone():
            return False
        old=db.execute('SELECT lease_until FROM model_resource_leases WHERE resource_key=?',(key,)).fetchone()
        if old and old[0]>now:return False
        db.execute('INSERT INTO model_resource_leases VALUES(?,?,?,?) ON CONFLICT(resource_key) DO UPDATE SET token=excluded.token,kind=excluded.kind,lease_until=excluded.lease_until',
                   (key,token,kind,lease_until))
    return True


def release(store,key,token):
    with store.worker_transaction() as db:
        db.execute('DELETE FROM model_resource_leases WHERE resource_key=? AND token=?',(key,token))
        db.execute('DELETE FROM model_resource_waiters WHERE token=?',(token,))


@asynccontextmanager
async def model_slot(store,base_url,*,kind='chat',deadline,wait=True):
    """Hold until the actual HTTP context closes, including cancellation.

    The resource expiry exceeds the caller's absolute request deadline by a
    cleanup margin. A waiting chat is visible across instances before polling.
    A crashed owner/waiter therefore expires without extending an HTTP budget.
    """
    key=resource_key(base_url);token=uuid.uuid4().hex
    remaining=deadline-time.monotonic()
    if remaining<=0:raise DeadlineExceeded('model admission deadline reached')
    expiry=time.time()+remaining+GRACE_SECONDS
    try:
        if kind=='chat':
            with store.worker_transaction() as db:
                db.execute('DELETE FROM model_resource_waiters WHERE expires_at<=?',(time.time(),))
                db.execute('INSERT INTO model_resource_waiters VALUES(?,?,?)',(token,key,expiry))
        while True:
            if time.monotonic()>=deadline:raise DeadlineExceeded('model admission deadline reached')
            if acquire(store,key,token,kind,expiry):break
            if not wait:raise ModelBusy('waiting_model_idle')
            await asyncio.sleep(min(.05,max(0,deadline-time.monotonic())))
        yield
    finally:
        release(store,key,token)
