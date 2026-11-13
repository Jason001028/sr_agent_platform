"""Message-bound read-only repair queries. Never resolve paths or execute SR."""
import re
from backend.tools.contract import Tool, ok, err


def safe(value):
    """Bound text and redact absolute paths even in executor error messages."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return re.sub(r'(?:[A-Za-z]:[\\/]|\\\\|/)[^\s,;]+', '[redacted-path]', str(value))[:512]


def bound_tools(snapshot, store, session_id):
    frozen = snapshot or {}

    def authorized(goal_id):
        goal = store.get_goal(goal_id) if store is not None and session_id else None
        return bool(goal and goal.get('kind') == 'batch' and goal.get('session_id') == session_id)

    def item_summary(item, detail=False):
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
                mask_review={k:safe((cp.get('review') or {}).get(k)) for k in ('decision','mask_sha256','reviewed_at')},
                artifact_refs={'mask_sha256':safe(mask.get('sha256')), 'overlay_sha256':safe((mask.get('overlay') or {}).get('sha256'))},
                rejection_reasons=[safe(r.get('reason')) for r in regions if r.get('decision') == 'rejected'][:5],
                state_note='提交、执行终态、产物契约验证、人工审核分别记录；未知不等于通过。只报告持久记录，不重验磁盘产物。')
        return data

    def workspace():
        from backend.services.viewer_context import repair_context_summary
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
        if goal_id and authorized(goal_id):
            actual = store.batch_snapshot(goal_id)
            data['batch'] = {k: actual.get(k) for k in ('counts', 'progress')}
            data['batch']['source'] = 'session_server_snapshot'
            data['batch']['status'] = actual.get('goal', {}).get('status')
            quality = actual.get('quality') or {}
            data['batch']['waiting_review'] = quality.get('waiting_scenes', 0)
            data['batch']['rejected'] = quality.get('rejected_scenes', 0)
            data['batch']['exceptions'] = [item_summary(i) for i in store.list_goal_items(
                goal_id, statuses=['FAILED', 'BLOCKED', 'REVIEW_REJECTED'], limit=3)]
        elif goal_id:
            data['batch'] = {'available': False}
        return ok(data)

    def items(goal_id, status=None, limit=10, cursor=0):
        if not authorized(goal_id):
            return err('当前会话没有可读取的关联批次')
        rows = store.list_goal_items(goal_id, statuses=[status] if status else None,
                                     after_ordinal=cursor, limit=limit + 1)
        page = rows[:limit]
        return ok({'items': [item_summary(i) for i in page],
                   'ordinal_base': 1, 'order': 'frozen_manifest_including_excluded',
                   'status_filter': status, 'ordinals_renumbered': False,
                   'next_cursor': page[-1]['ordinal'] if len(rows) > limit else None})

    def item(goal_id, item_id):
        if not authorized(goal_id):
            return err('当前会话没有可读取的关联批次')
        row = store.get_goal_item(goal_id, item_id)
        return ok(item_summary(row, True)) if row else err('repair item not found')

    goal = {'type': 'string', 'minLength': 1, 'maxLength': 512, 'description': '当前会话已知的持久化标识，不猜最新任务。'}
    def schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    return [
        Tool('get_repair_workspace', '只读当前消息冻结的修复草稿数量、选中/排除数、版本及影像；关联批次状态从本会话数据库读取。空工作区不是全库检索理由。不提交 SR。', schema({}, []), workspace),
        Tool('list_repair_items', '按冻结Manifest原始顺序查询，ordinal从1开始，含排除/SKIPPED项；UI重排和status筛选不重编号。第N景用cursor=N-1、limit=1且不传status，核对ordinal=N后取item_id再get_repair_item；筛选后第N项需按筛选分页累计。只读数据库，不读Manifest、不扫描场景库。', schema({'goal_id': goal, 'status': {'type':'string','description':'精确持久状态，如 FAILED 或 AWAITING_REVIEW；定位原始第N景时不传。'}, 'limit': {'type':'integer','minimum':1,'maximum':50,'description':'每页数量，最多50；定位原始序号时取1。'}, 'cursor': {'type':'integer','minimum':0,'description':'排他原始ordinal；初始0，第N景用N-1，后续页用next_cursor。'}}, ['goal_id']), items),
        Tool('get_repair_item', '只读已知Goal/Item的检测、Mask、SR执行、产物契约和人工审核记录；不读取文件。item_id必须来自已知引用或list_repair_items返回，不得由序号拼接；第N景先按从1开始的原始ordinal定位。提交成功不等于执行/验证/人工审核通过。', schema({'goal_id':goal,'item_id':goal}, ['goal_id','item_id']), item),
    ]
