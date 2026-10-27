"""Exact task lookup and unified Executor routing for the status tool."""
from unittest.mock import Mock, patch

import pytest

from backend.services.store import Store
from backend.tools.sr_job_status import run_sr_job_status


@pytest.mark.parametrize('executor', ['local', 'slurm'])
@pytest.mark.parametrize('known', [False, True])
def test_status_dispatch_with_and_without_task(executor, known, monkeypatch):
    monkeypatch.setenv('SR_EXECUTOR', executor)
    task = {'job_id': 42, 'config_xml': 'saved-config.xml'} if known else None
    store = Mock()
    store.get_sr_task_by_job.return_value = task
    expected = {'job_id': 42, 'state': 'UNKNOWN', 'active': False, 'exit_code': None}
    with patch('backend.tools.sr_job_status.store_mod.default_store', return_value=store), \
         patch('backend.services.run_sr.exit_code_file_for', return_value='saved-verdict' if known else None) as verdict, \
         patch('backend.services.run_sr.local_exec.status', return_value=expected) as local, \
         patch('backend.services.run_sr.slurm.job_status', return_value=expected) as slurm:
        assert run_sr_job_status(job_id=42) == {'ok': True, 'data': expected, 'error': None}
        store.get_sr_task_by_job.assert_called_once_with(42)
        store.list_sr_tasks.assert_not_called()
        verdict.assert_called_once_with('saved-config.xml' if known else None, 42)
        selected, other = (local, slurm) if executor == 'local' else (slurm, local)
        selected.assert_called_once_with(42, exit_code_file='saved-verdict' if known else None)
        other.assert_not_called()


def test_old_task_outside_recent_10000_is_found(tmp_path):
    store = Store(str(tmp_path / 'tasks.db'))
    try:
        original = store.put_sr_task('old', {}, job_id=42, config_xml='old-config.xml')
        with store.worker_transaction() as db:
            db.executemany(
                'INSERT INTO sr_tasks (fingerprint,params,status,job_id,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                [(f'new-{i}', '{}', 'submitted', 100+i, original['created_at']+i+1, original['updated_at']+i+1)
                 for i in range(10001)])
        assert all(t['job_id'] != 42 for t in store.list_sr_tasks(limit=10000))
        with patch('backend.tools.sr_job_status.store_mod.default_store', return_value=store), \
             patch('backend.tools.sr_job_status.run_sr.query_job_status', return_value={'job_id':42}) as query, \
             patch.object(store, 'list_sr_tasks', side_effect=AssertionError('must use exact lookup')):
            assert run_sr_job_status(job_id=42)['ok']
            query.assert_called_once_with(42, task=original)
    finally:
        store.close()


def test_service_error_keeps_tool_error_contract():
    store = Mock()
    store.get_sr_task_by_job.return_value = None
    with patch('backend.tools.sr_job_status.store_mod.default_store', return_value=store), \
         patch('backend.tools.sr_job_status.run_sr.query_job_status', side_effect=RuntimeError('executor unavailable')):
        result = run_sr_job_status(job_id=42)
        assert result['ok'] is False
        assert result['data'] is None
        assert 'executor unavailable' in result['error']
