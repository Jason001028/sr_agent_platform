from unittest import mock

from backend.tools.batch_repair import prepare_batch_repair, query_batch_repair
from backend.services.goal_lease import current_tool


def test_planning_refuses_outside_paths_without_scanning():
    from backend.pathguard import PathDeniedError
    with mock.patch('backend.tools.batch_repair.ensure_allowed', side_effect=PathDeniedError('denied')):
        result = prepare_batch_repair([{'path': '/outside/PAN.tif', 'evidence': '待检测'}])
    assert result['ok'] is False


def test_query_reads_authoritative_snapshot_from_bound_store():
    store = mock.Mock()
    store.batch_snapshot.return_value = {'counts': {'detected': 7}, 'goal': {'mode': 'demo'}}
    token = current_tool.set({'store': store})
    try:
        assert query_batch_repair('batch-1')['data']['counts']['detected'] == 7
        store.batch_snapshot.assert_called_once_with('batch-1')
        store.close.assert_not_called()
    finally:
        current_tool.reset(token)
