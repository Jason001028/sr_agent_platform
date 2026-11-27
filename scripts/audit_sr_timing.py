"""Read the latest SR job's executor, pipeline and publication timings."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen


QUEUE_URL = 'http://127.0.0.1:8000/api/queue'
SUMMARY = re.compile(r'Pipeline Timing Summary\s*\(tile=(\d+)\s+workers=(\d+)\)')
TIMING = re.compile(r'^\s*(.+?)\s+(\d+(?:\.\d+)?)s(?:\s+\([^)]*%\))?\s*$')
LOG_TIME = re.compile(r'^(\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+-\s+INFO:')
LOOP = re.compile(r'tile loop total:\s*([\d.]+)s.*?SR:\s*(\d+) tiles.*?bicubic:\s*(\d+) tiles')
ROI = re.compile(r'Local SR mask loaded, ROI pixels:\s*(\d+)\s*/\s*(\d+)\s*\(([\d.]+)%\)')
REPAIR_TIMING = re.compile(r'^\[REPAIR TIMING\] (\w+): (\d+\.\d+)s(?: (.*))?$')
REPAIR_WINDOWS = re.compile(r'^\[REPAIR WINDOW OUTPUT\] (\d+) windows written$')
INFER_START = re.compile(r'^\[REPAIR INFER START\] (\d+\.\d+)$')


def read_log(path):
    result, pending = {}, None
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            infer_start = INFER_START.match(line)
            if infer_start:
                result['infer_process_started'] = float(infer_start[1])
                continue
            repair = REPAIR_TIMING.match(line)
            if repair:
                name, elapsed, detail = repair.groups()
                timing = {'wall_seconds': float(elapsed)}
                for field in (detail or '').split():
                    key, value = field.split('=', 1)
                    timing[key] = int(value) if value.isdigit() else value
                result.setdefault('repair_timing', {})[name] = timing
                continue
            windows = REPAIR_WINDOWS.match(line)
            if windows:
                result['repair_output_windows'] = int(windows[1])
                continue
            match = SUMMARY.search(line)
            if match:
                pending = {'tile_size': int(match[1]), 'workers': int(match[2]), 'stages_seconds': {}}
                continue
            if pending is not None:
                timing = TIMING.fullmatch(line)
                if timing:
                    name, value = timing.groups()
                    if name == 'total':
                        result['summary'] = dict(pending, total_seconds=float(value))
                        pending = None
                    else:
                        pending['stages_seconds'][name] = float(value)
                    continue
                if not line.strip() or set(line.strip()) <= {'-', '='}:
                    continue
                pending = None
            clock = LOG_TIME.match(line)
            if clock:
                epoch = datetime.strptime(clock[1], '%y-%m-%d %H:%M:%S').timestamp()
                if ' Start [GPU ' in line and ' SR ' in line:
                    result['model_started'] = epoch
                elif re.search(r'\bRun .+ finished\.\s*\[\s*GPU', line):
                    result['model_finished'] = epoch
            match = LOOP.search(line)
            if match:
                result['tile_loop'] = {'wall_seconds': float(match[1]), 'sr_tiles': int(match[2]),
                                       'bicubic_tiles': int(match[3])}
            match = ROI.search(line)
            if match:
                result['roi'] = {'pixels': int(match[1]), 'total': int(match[2]), 'percent': float(match[3])}
            for name in ('SR_EXECUTOR', 'SR_SCRIPT', 'CUDA_VISIBLE_DEVICES'):
                if line.startswith(name + '='):
                    result[name] = line.partition('=')[2].strip()
            if ('PAN repair failed:' in line or 'SR contract NOT satisfied' in line
                    or 'VRAM low' in line or 'Mask shape' in line):
                result.setdefault('diagnostics', []).append(line.strip())
    return result


def queue_task(url, job_id):
    with urlopen(url, timeout=10) as response:
        tasks = [t for t in json.load(response)['tasks'] if t.get('job_id') is not None]
    if job_id is not None:
        task = next((t for t in tasks if str(t['job_id']) == job_id), None)
        if task is None:
            raise ValueError('Job {} is absent from the local queue'.format(job_id))
        return task
    if not tasks:
        raise ValueError('No submitted SR jobs in the local queue')
    # Retry reuses the queue row and its created_at. The generated batch script
    # is rewritten for each submission, including a retry of an old row.
    def attempt_time(task):
        try:
            return Path(task['batch_script']).stat().st_mtime
        except (OSError, TypeError, KeyError):
            return task.get('started_at') or task.get('created_at') or 0
    return max(tasks, key=attempt_time)


def job_logs(task):
    batch = Path(task['batch_script'])
    base = Path(task.get('log_dir') or batch.parent)
    job = str(task['job_id'])
    local_log = base / '{}.{}.out'.format(batch.stem, job)
    if local_log.is_file():
        return [local_log]
    return [p for p in (base / (job + '.out'), base / (job + '.err')) if p.is_file()]


def local_times(task):
    batch = Path(task['batch_script'])
    for directory in dict.fromkeys((Path(task.get('log_dir') or batch.parent), batch.parent)):
        try:
            rows = json.loads((directory / '.local_jobs.json').read_text(encoding='utf-8'))['jobs']
        except (OSError, ValueError, KeyError):
            continue
        for row in rows:
            if str(row['job_id']) == str(task['job_id']) and Path(row.get('script') or '').resolve() == batch.resolve():
                return {'source': 'local executor state', 'submitted': row.get('created_at'),
                        'started': row.get('started_at'),
                        'ended': row.get('updated_at') if row.get('state') == 'exited' else None}
    return None


def slurm_times(job_id):
    try:
        run = subprocess.run(['sacct', '-n', '-P', '-X', '-j', str(job_id),
                              '-o', 'JobIDRaw,Submit,Start,End'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if run.returncode != 0:
        return None
    for line in run.stdout.splitlines():
        parts = line.strip().split('|')
        if len(parts) >= 4 and parts[0] == str(job_id):
            times = {'source': 'Slurm sacct'}
            for name, value in zip(('submitted', 'started', 'ended'), parts[1:4]):
                try:
                    times[name] = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').timestamp()
                except ValueError:
                    times[name] = None
            return times
    return None


def publication(task, started, ended):
    if not task.get('config_xml'):
        return {}
    config = Path(task['config_xml'])
    try:
        data = config.read_bytes()
        xml = ET.fromstring(data)
        scene = xml.findtext('DatarootLQ')
        if not scene:
            return {}
    except (OSError, ET.ParseError):
        return {}
    debug = Path(scene) / 'Debug'
    result = {'scene': scene, 'repair_mode': xml.findtext('RepairMode')}
    verdict_path = debug / '_SREXIT_{}.txt'.format(task['job_id'])
    try:
        verdict = dict(line.split('=', 1) for line in verdict_path.read_text(encoding='utf-8').splitlines() if '=' in line)
        if str(task['job_id']) == verdict.get('job_id'):
            result.update(verdict=verdict.get('verdict'), reason=verdict.get('reason'),
                          verified_at=float(verdict['written_at']))
    except (OSError, ValueError, KeyError):
        pass
    if result['repair_mode'] == 'pan-nosr-v1':
        receipt = debug / ('_SRREPAIR_' + hashlib.sha256(data).hexdigest() + '.json')
        try:
            published_at = receipt.stat().st_mtime
            meta = json.loads(receipt.read_text(encoding='utf-8'))
            # A retry can reuse the same config hash. Associate the receipt only
            # when it also falls inside this attempt's executor window.
            if started is not None and ended is not None and started - 1 <= published_at <= ended + 1:
                result.update(published_at=published_at, output_bytes=meta.get('size_bytes'))
        except (OSError, ValueError):
            pass
    return result


def seconds_between(start, end):
    if start is None or end is None or end < start:
        return None
    return round(end - start, 3)


def audit(task, paths):
    native = {}
    repair_timing = {}
    for path in paths:
        parsed = read_log(path)
        repair_timing.update(parsed.pop('repair_timing', {}))
        if parsed.get('diagnostics') and native.get('diagnostics'):
            parsed['diagnostics'] = native['diagnostics'] + parsed['diagnostics']
        native.update(parsed)
    executor = local_times(task) if task.get('batch_script') else None
    if executor is None and task.get('batch_script') and native.get('SR_EXECUTOR') != 'local':
        executor = slurm_times(task['job_id'])
    if executor is None:
        executor = {'source': 'platform polling', 'started': task.get('started_at'), 'ended': task.get('finished_at')}
    started, ended = executor.get('started'), executor.get('ended')
    infer_started = native.get('infer_process_started')
    platform_started = task.get('started_at')
    if platform_started is not None:
        executor['platform_started'] = platform_started
    events = publication(task, started, ended)
    finish = native.get('model_finished')
    verdict = events.get('verified_at')
    published = events.get('published_at')
    durations = {
        'queue_wait': seconds_between(executor.get('submitted'), started),
        'executor_start_to_platform_running': (
            seconds_between(started, platform_started) if executor['source'] != 'platform polling' else None),
        'executor_wall': seconds_between(started, ended),
        'launch_to_infer_process': seconds_between(started, infer_started),
        'infer_process_to_model_start_log': seconds_between(infer_started, native.get('model_started')),
        'launch_to_model_start_log': seconds_between(started, native.get('model_started')),
        'native_pipeline_wall': native.get('summary', {}).get('total_seconds'),
        'native_tile_loop_wall': native.get('tile_loop', {}).get('wall_seconds'),
        'model_finish_log_to_publication': seconds_between(finish, published),
        'publication_to_verdict': seconds_between(published, verdict),
        'model_finish_log_to_verdict': seconds_between(finish, verdict),
    }
    durations['executor_wall_outside_native_total'] = seconds_between(durations['native_pipeline_wall'], durations['executor_wall'])
    notes = ['Native SR infer, tile fill and pt log are worker accumulations and may overlap; use tile loop wall for elapsed time.',
             'Launch to model start log includes process startup and may include repair staging. It overlaps the native config stage.',
             'Model finish log to verdict includes any repair composition, publication, cleanup and verification in that interval.']
    if not native.get('summary'):
        notes.append('No complete native timing summary in this job log. It may be running, failed early, or use another SR script.')
    result = {'job_id': task.get('job_id'), 'state': task.get('state'), 'logs': list(map(str, paths)),
              'clock': executor, 'duration_seconds': durations, 'native': native,
              'publication': events, 'notes': notes}
    if repair_timing:
        result['repair_timing'] = repair_timing
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', help='inspect a specific job instead of the most recent attempt')
    parser.add_argument('--queue-url', default=QUEUE_URL)
    parser.add_argument('--log', action='append', type=Path, help='direct log input when the local queue is unavailable')
    args = parser.parse_args()
    if args.log:
        task, paths = {'job_id': args.job_id}, args.log
    else:
        task = queue_task(args.queue_url, args.job_id)
        paths = job_logs(task)
    print(json.dumps(audit(task, paths), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('SR timing audit failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
