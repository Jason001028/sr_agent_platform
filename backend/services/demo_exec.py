"""Durable Batch demo receipts. Never starts a process or writes SR products."""
from pathlib import Path
import json
import os
import tempfile
import time

from backend.services.goal_lease import current_tool

MARKER = '_batch_demo_executor'


def work_root():
    root = Path(os.environ.get('SR_DEMO_WORK_DIR') or
                Path(tempfile.gettempdir()) / 'sr-platform-demo').resolve()
    if 'DiskArray' in root.parts:
        raise ValueError('演示工作目录不能位于生产盘阵')
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK):
        raise ValueError('演示工作目录不可写')
    return root


def submit(params, *, store):
    from backend.services.run_sr import task_fingerprint
    if params.get(MARKER) != 1:
        raise ValueError('缺少演示执行标记')
    fingerprint = task_fingerprint(params)
    root = work_root()
    # A namespaced fingerprint keeps demo receipts separate from real SR.
    # Remain exactly representable by JavaScript Number in SSE/API clients.
    job_id = (1 << 48) + int(fingerprint[:11], 16)
    receipt = {'job_id': job_id, 'status': 'SUBMITTED', 'config_xml': None,
               'batch_script': str(root / (fingerprint + '.demo.json')),
               'log_dir': str(root), 'provenance': 'mock'}
    # This is a JSON audit receipt, never an executable shell script.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root,
                                     suffix='.tmp', delete=False) as output:
        temporary = Path(output.name)
        json.dump(receipt, output, ensure_ascii=False)
    try:
        os.replace(temporary, receipt['batch_script'])
    finally:
        temporary.unlink(missing_ok=True)
    with store.worker_transaction():
        task = store.get_sr_task(fingerprint)
        if task is None:
            task = store.put_sr_task(fingerprint, params, job_id=job_id,
                                     batch_script=receipt['batch_script'], log_dir=str(root))
            store.set_preview_state(task['task_id'], 'skipped', '演示任务不生成 SR 成果或预览')
        elif task.get('job_id') != job_id:
            raise ValueError('演示任务回执身份冲突')
        execution = current_tool.get() or {}
        intent = execution.get('intent')
        if intent:
            store.update_tool_intent(intent['intent_id'], status='SUBMITTED',
                                     external_job_id=job_id, result=receipt)
    return receipt


def status(job_id, task):
    if int(task.get('job_id') or 0) != int(job_id):
        raise ValueError('演示 Job 身份不匹配')
    elapsed = max(0, time.time() - task['created_at'])
    state = 'PENDING' if elapsed < .1 else 'RUNNING' if elapsed < .2 else 'COMPLETED'
    return {'job_id': job_id, 'state': state, 'active': state != 'COMPLETED',
            'exit_code': '0:0' if state == 'COMPLETED' else None,
            'provenance': 'mock'}
