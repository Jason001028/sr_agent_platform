"""Message-bound read-only repair queries. Never resolve paths or execute SR.

The three tools here are the **only** owners of ``get_repair_workspace`` /
``list_repair_items`` / ``get_repair_item``; ``backend.tools.repair`` keeps thin
unbound wrappers for REST/CLI that reuse the same projections below.
"""
import re
from backend.tools.contract import Tool, ok, err

#: Detail queries project the most actionable regions, not the full mask geometry.
REGION_DETAIL_LIMIT = 20
#: Batch summary keeps the review tail bounded; the Panel uses REST for the full list.
QUALITY_SUMMARY_LIMIT = 10
ACTIVE_JOB_LIMIT = 20
RECENT_JOB_LIMIT = 10


def safe(value):
    """Bound text and redact absolute paths even in executor error messages."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return re.sub(r'(?:[A-Za-z]:[\\/]|\\\\|/)[^\s,;]+', '[redacted-path]', str(value))[:512]


def _statuses(status):
    if not isinstance(status, str):
        return None
    values = [value.strip() for value in status.split(',') if value.strip()]
    return values or None


def _project_regions(review, limit=REGION_DETAIL_LIMIT):
    """Rejected first, then pending, then approved; no geometry or reviewer identity."""
    rows = [row for row in (review or {}).get('regions') or [] if isinstance(row, dict)]
    order = {'rejected': 0, 'pending': 1, 'approved': 2}
    rows.sort(key=lambda row: (order.get(row.get('decision'), 3),
                               str(row.get('region_id') or '')))
    shown = [{key: safe(row.get(key)) for key in ('region_id', 'decision', 'reason')}
             for row in rows[:limit]]
    return shown, len(rows) > limit


def item_summary(store, item, *, detail=False, verify=False):
    from backend.services.batch_snapshot import item_job_id, is_excluded
    from backend.services.batch_quality import review_for_item
    cp = item.get('checkpoint') or {}
    job_id = item_job_id(item)
    task = store.get_sr_task_by_job(job_id) if job_id is not None else None
    review = review_for_item(store, item, verify=False)
    regions = (review or {}).get('regions') or []
    data = {k: safe(item.get(k)) for k in ('item_id', 'ordinal', 'name', 'status', 'version', 'error')}
    data['source'] = 'session_database_records'
    data['excluded'] = is_excluded(item)
    data['exclusion_reason'] = safe(cp.get('exclusion_reason'))
    data.update(phase=safe(cp.get('phase')), job_id=job_id,
                job_status=safe(cp.get('sr_status') or (task or {}).get('status')),
                review_status=safe((review or {}).get('scene_decision')),
                pending_regions=sum(r.get('decision') == 'pending' for r in regions))
    if detail:
        detection = cp.get('detection') or {}
        mask = cp.get('mask') or {}
        verification = item.get('verification') or {}
        review_valid = None
        review_invalid_reason = None
        if review is not None and verify:
            from backend.services.batch_quality import validate_current
            try:
                validate_current(store, review)
                review_valid = True
            except (ValueError, OSError, KeyError) as exc:
                review_valid = False
                review_invalid_reason = safe(str(exc))
        shown_regions, regions_truncated = _project_regions(review)
        data.update(candidate_count=len(detection.get('candidates') or []),
            detection={k: safe(detection.get(k)) for k in ('method', 'algorithm_version', 'status')},
            mask={k: safe(mask.get(k)) for k in ('mask_id', 'sha256', 'version', 'status', 'contract_version', 'builder_version')},
            mask_present=bool(mask),
            input_identities={k: {f: safe((cp.get(k) or {}).get(f)) for f in ('image_key', 'revision', 'source_revision', 'sha256', 'size')}
                              for k in ('target_image', 'reference_image')},
            verification={k: safe(verification.get(k)) for k in ('ok', 'success_scope', 'provenance', 'reason')},
            verification_layers={name:{k:safe((verification.get('layers',{}).get(name) or {}).get(k))
                                       for k in ('status','reason')}
                                 for name in ('submit','terminal','contract','artifacts','quality')},
            approved_regions=sum(r.get('decision') == 'approved' for r in regions),
            rejected_regions=sum(r.get('decision') == 'rejected' for r in regions),
            verification_reasons=[safe(reason) for reason in verification.get('reasons',[])][:3],
            submitted=job_id is not None,
            review_id=safe((review or {}).get('review_id')),
            review_version=(review or {}).get('version'),
            review_valid=review_valid, review_invalid_reason=review_invalid_reason,
            regions=shown_regions, regions_truncated=regions_truncated,
            region_count=len(regions),
            mask_review={k:safe((cp.get('review') or {}).get(k)) for k in ('decision','mask_sha256','reviewed_at')},
            artifact_refs={'mask_sha256':safe(mask.get('sha256')), 'overlay_sha256':safe((mask.get('overlay') or {}).get('sha256'))},
            rejection_reasons=[safe(r.get('reason')) for r in regions if r.get('decision') == 'rejected'][:5],
            state_note='提交、执行终态、产物契约验证、人工审核分别记录；未知不等于通过。查询只报告持久记录，verify=true 时才校验审核绑定与文件修订。')
    return data


def items_page(store, goal_id, *, status=None, limit=10, cursor=0):
    """Ordinal-ordered page; ``cursor`` is the exclusive original ordinal."""
    try:
        limit = max(1, min(int(limit), 50))
        cursor = max(0, int(cursor))
    except (TypeError, ValueError):
        raise ValueError('limit/cursor must be integers')
    rows = store.list_goal_items(goal_id, statuses=_statuses(status),
                                 after_ordinal=cursor, limit=limit + 1)
    page = rows[:limit]
    return {'items': [item_summary(store, item) for item in page],
            'ordinal_base': 1, 'order': 'frozen_manifest_including_excluded',
            'status_filter': status, 'ordinals_renumbered': False,
            'has_more': len(rows) > limit,
            'next_cursor': page[-1]['ordinal'] if len(rows) > limit else None}


def item_detail(store, item, *, verify=True):
    return item_summary(store, item, detail=True, verify=verify)


def _job_row(row):
    return {key: safe(row.get(key)) for key in
            ('item_id', 'ordinal', 'name', 'status', 'job_id', 'sr_status',
             'task_status', 'unknown', 'error', 'verification_ok', 'updated_at')}


def batch_summary(snapshot, *, quality_limit=QUALITY_SUMMARY_LIMIT,
                  active_limit=ACTIVE_JOB_LIMIT, recent_limit=RECENT_JOB_LIMIT):
    """Bounded, path-redacted projection of ``batch_snapshot.v1`` for model tools.

    The Panel keeps the full document from REST; the model only needs the
    authoritative counters plus the review tail it can act on.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    goal = snapshot.get('goal') if isinstance(snapshot.get('goal'), dict) else {}
    quality = snapshot.get('quality') if isinstance(snapshot.get('quality'), dict) else {}
    producer = snapshot.get('producer') if isinstance(snapshot.get('producer'), dict) else {}
    quality_items = [row for row in quality.get('items') or []
                     if isinstance(row, dict)
                     and row.get('status') in ('AWAITING_REVIEW', 'REVIEW_REJECTED')]
    order = {'REVIEW_REJECTED': 0, 'AWAITING_REVIEW': 1}
    quality_items.sort(key=lambda row: (order.get(row.get('status'), 2),
                                        int(row.get('ordinal') or 0)))
    active = [row for row in snapshot.get('active_jobs') or [] if isinstance(row, dict)]
    recent = [row for row in snapshot.get('recent_jobs') or [] if isinstance(row, dict)]
    current = producer.get('current') if isinstance(producer.get('current'), dict) else None
    goal_row = {key: safe(goal.get(key)) for key in
                ('goal_id', 'title', 'status', 'phase', 'mode', 'version',
                 'monitoring_done', 'monitor_state', 'last_seq',
                 'created_at', 'updated_at', 'finished_at')}
    control = goal.get('batch_control') if isinstance(goal.get('batch_control'), dict) else {}
    goal_row['batch_control'] = {key: safe(control.get(key)) for key in
                                 ('pause_requested', 'cancel_requested',
                                  'pause_requested_at', 'cancel_requested_at',
                                  'last_action', 'last_action_at') if key in control}
    return {
        'contract_version': snapshot.get('contract_version'),
        'goal': goal_row,
        'counts': snapshot.get('counts') or {},
        'progress': snapshot.get('progress') or {},
        'producer': {
            'current': ({key: safe(current.get(key)) for key in
                         ('item_id', 'ordinal', 'name', 'status', 'candidate_count',
                          'job_id', 'sr_status', 'has_mask', 'overlay_ready')}
                        if current else None),
            'detecting_count': producer.get('detecting_count'),
            'preparing_count': producer.get('preparing_count'),
            'prepared_count': producer.get('prepared_count'),
        },
        'active_jobs': [_job_row(row) for row in active[:active_limit]],
        'active_jobs_total': len(active),
        'active_jobs_truncated': len(active) > active_limit,
        'recent_jobs': [_job_row(row) for row in recent[:recent_limit]],
        'recent_jobs_total': len(recent),
        'recent_jobs_truncated': len(recent) > recent_limit,
        'quality': {
            'waiting_scenes': quality.get('waiting_scenes', 0),
            'rejected_scenes': quality.get('rejected_scenes', 0),
            'items': [{key: safe(row.get(key)) for key in
                       ('item_id', 'ordinal', 'name', 'status', 'review_id',
                        'job_id', 'region_count')}
                      for row in quality_items[:quality_limit]],
            'items_total': len(quality_items),
            'items_truncated': len(quality_items) > quality_limit,
        },
        'report': snapshot.get('report') or {},
        'server_time': snapshot.get('server_time'),
    }


def bound_tools(snapshot, store, session_id):
    frozen = snapshot or {}
    from backend.services.viewer_context import repair_context_summary

    def authorized(goal_id):
        goal = store.get_goal(goal_id) if store is not None and session_id else None
        return bool(goal and goal.get('kind') == 'batch' and goal.get('session_id') == session_id)

    def workspace():
        batch = frozen.get('batch_goal') or {}
        goal_id = batch.get('goal_id')
        focused = frozen.get('repair_item') or {}
        def observation_summary(key):
            value = frozen.get(key)
            if not isinstance(value, dict):
                return None
            return {k:safe(value.get(k)) for k in ('draft_id','version','loaded','total','selected','excluded','selected_name','source_name','selection_semantics','loading')}
        data = {'source': 'message_workspace_snapshot',
                'context': repair_context_summary(frozen),
                'image': {k:safe(v) for k,v in frozen['current'].items()} if isinstance(frozen.get('current'),dict) else None,
                'captured_at': frozen.get('captured_at'),
                'draft': {k: (frozen.get('batch_draft') or {}).get(k) for k in ('draft_id','version','total','count','selected','excluded')},
                'repair_selection': observation_summary('repair_selection'), 'qc_draft': observation_summary('qc_draft'),
                'selected_item_id': focused.get('item_id') if focused.get('goal_id') == goal_id else batch.get('item_id'),
                'goal_id': goal_id, 'batch': None}
        observed_workspace = frozen.get('workspace') or {}
        data['total'] = int(observed_workspace.get('total') or 0) + int(observed_workspace.get('local_only') or 0)
        data['local_only'] = int(observed_workspace.get('local_only') or 0)
        data['scenes'] = int(observed_workspace.get('total') or 0)
        data['resources_truncated'] = bool(observed_workspace.get('resources_truncated'))
        if goal_id and authorized(goal_id):
            actual = store.batch_snapshot(goal_id)
            data['batch'] = {k: actual.get(k) for k in ('counts', 'progress')}
            data['batch']['source'] = 'session_server_snapshot'
            data['batch']['status'] = actual.get('goal', {}).get('status')
            quality = actual.get('quality') or {}
            data['batch']['waiting_review'] = quality.get('waiting_scenes', 0)
            data['batch']['rejected'] = quality.get('rejected_scenes', 0)
            data['batch']['exceptions'] = [item_summary(store, i) for i in store.list_goal_items(
                goal_id, statuses=['FAILED', 'BLOCKED', 'REVIEW_REJECTED'], limit=3)]
        elif goal_id:
            data['batch'] = {'available': False}
        return ok(data)

    def items(goal_id, status=None, limit=10, cursor=0):
        if not authorized(goal_id):
            return err('当前会话没有可读取的关联批次')
        try:
            return ok(items_page(store, goal_id, status=status, limit=limit, cursor=cursor))
        except (ValueError, OSError, KeyError) as exc:
            return err(str(exc))

    def item(goal_id, item_id, verify=True):
        if not authorized(goal_id):
            return err('当前会话没有可读取的关联批次')
        try:
            row = store.get_goal_item(goal_id, item_id)
            return ok(item_detail(store, row, verify=verify)) if row else err('repair item not found')
        except (ValueError, OSError, KeyError) as exc:
            return err(str(exc))

    goal = {'type': 'string', 'minLength': 1, 'maxLength': 512, 'description': '当前会话已知的持久化标识，不猜最新任务。'}
    def schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    return [
        Tool('get_repair_workspace', '只读当前消息冻结的修复草稿数量、选中/排除数、版本及影像；关联批次状态从本会话数据库读取。空工作区不是全库检索理由。不提交 SR。', schema({}, []), workspace),
        Tool('list_repair_items', '按冻结Manifest原始顺序查询，ordinal从1开始，含排除/SKIPPED项；UI重排和status筛选不重编号。第N景用cursor=N-1、limit=1且不传status，核对ordinal=N后取item_id再get_repair_item；筛选后第N项需按筛选分页累计。只读数据库，不读Manifest、不扫描场景库。',
            schema({'goal_id': goal,
                    'status': {'type':'string','maxLength':200,'description':'精确持久状态，多个用英文逗号分隔，如 FAILED,AWAITING_REVIEW；定位原始第N景时不传。'},
                    'limit': {'type':'integer','minimum':1,'maximum':50,'description':'每页数量，最多50；定位原始序号时取1。'},
                    'cursor': {'type':'integer','minimum':0,'description':'排他原始ordinal；初始0，第N景用N-1，后续页用next_cursor。'}},
                   ['goal_id']), items),
        Tool('get_repair_item', '只读已知Goal/Item的检测、Mask、SR执行、产物契约和人工审核记录；不读取文件。item_id必须来自已知引用或list_repair_items返回，不得由序号拼接；第N景先按从1开始的原始ordinal定位。提交成功不等于执行/验证/人工审核通过。区域详情只列前20条决定，不返回几何或审核人身份。',
            schema({'goal_id':goal,'item_id':goal,
                    'verify':{'type':'boolean','default':True,'description':'默认 true：校验审核绑定与文件修订是否仍有效（只读摘要，不执行 SR），结果见 review_valid/review_invalid_reason；false 只读数据库，review_valid 为 null。'}},
                   ['goal_id','item_id']), item),
    ]
