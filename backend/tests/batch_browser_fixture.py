"""Real application for browser acceptance; only the external SR transport is fake.

The Node runner owns an independent scheduler ledger which survives API crashes.
All manifests, masks, snapshots, control, SSE, reports and worker code are real.
Never import this module from production.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from backend.tests import allowed_roots_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--scheduler', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / 'work').mkdir(exist_ok=True)
    os.environ.update({
        **allowed_roots_env(root),
        'SR_AGENT_DB': str(root / 'app.db'),
        'SR_BATCH_ARTIFACT_DIR': str(root / 'artifacts'),
        'SR_SCENE_PATH_TEMPLATE': str(root) + '/{name}',
        'SR_SCENES_ROOT': str(root / 'empty-scenes'),
        'SR_EXECUTOR': 'slurm', 'SR_SLURM_FAKE': '1',
        'SR_SLURM_WORK_DIR': str(root / 'work'),
        'SR_BATCH_POLL_SEC': '0.05', 'SR_BATCH_MONITOR_POLL_SEC': '0.1',
        'SR_BATCH_LEASE_SEC': '1', 'SR_QUEUE_POLL_SEC': '60',
        'SR_LLM_MOCK': '1',
    })
    names = []
    for i in range(1, 61):
        name = f'JXGF07D03_PMS_20260101010101_200516571_101_{i:04d}_001_L1'
        names.append(name)
        directory = root / (name + '_PAN')
        if directory.exists():
            continue
        directory.mkdir()
        (directory / (directory.name + '_meta.xml')).write_text(
            '<meta><SolarAzimuth>1</SolarAzimuth></meta>', encoding='utf-8')
        Image.new('I;16', (32, 32), 7).save(directory / 'PAN.tif')
        Image.new('I;16', (16, 16), 7).save(directory / (directory.name + '_NOSR.tif'))
    (root / 'fixtures.json').write_text(json.dumps({'names': names}), encoding='utf-8')

    def external(route, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(args.scheduler + route, data=data,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    from backend.services import slurm, run_sr
    slurm.sbatch_submit = lambda script, **kw: external('/submit', {'script': str(script)})['job_id']
    run_sr.query_job_status = lambda jid, **kw: external(f'/jobs/{jid}')

    from backend.api.app import create_app
    from fastapi.responses import FileResponse
    app = create_app()
    dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'

    @app.get('/{asset:path}')
    def frontend(asset: str):
        target = (dist / asset).resolve()
        if not target.is_relative_to(dist) or not target.is_file():
            target = dist / 'index.html'
        return FileResponse(target)

    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
