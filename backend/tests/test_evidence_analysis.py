import pytest
from backend.services.evidence_analysis import validate_result


def observation(**kwargs):
    return dict(assessment='indeterminate', evidence_ids=['context:output'],
                observation='局部纹理较弱',recommendation='人工核对原始影像',limitations='仅观察此窗口',**kwargs)


def test_semantic_constraints():
    ids=[('context:output','hash'),('context:nosr','hash')]
    assert validate_result(observation(),ids)['assessment']=='indeterminate'
    value=observation();value['evidence_ids']=['invented']
    with pytest.raises(ValueError,match='fabricated'):validate_result(value,ids)
    value=observation();value['assessment']='improved'
    with pytest.raises(ValueError,match='before_required'):validate_result(value,ids)
    value=observation();value['observation']='恢复了真实细节'
    with pytest.raises(ValueError,match='unsupported_claim'):validate_result(value,ids)
    value=observation();value['extra']='x'
    with pytest.raises(ValueError):validate_result(value,ids)


def test_actual_images_enter_transport(quality,monkeypatch):
    import asyncio
    asyncio.run(_actual_images(quality,monkeypatch))


async def _actual_images(quality,monkeypatch):
    from backend.services import batch_quality,evidence_store as ledger
    from backend.services.evidence_report import generate_window
    from backend.services.evidence_analysis import analyze
    q=quality;monkeypatch.setenv('SR_LLM_MOCK','0');monkeypatch.setenv('SR_EVIDENCE_STORAGE_LIMIT_BYTES',str(1024**3))
    review=batch_quality.review_for_item(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))
    rid=ledger.create_report(q.store,q.goal,q.store.list_goal_items(q.goal['goal_id']),[review],'ready')
    task=ledger.claim(q.store);manifest=generate_window(q.store,task,0)
    ledger.finish(q.store,task,state='ready',manifest={'windows':[manifest]})
    import json
    calls=[]
    async def transport(cfg,wire,tools,**kwargs):
        calls.append(wire)
        assert tools==[]
        blocks=wire[1]['content']
        assert [b['text'].split(' ',1)[0] for b in blocks if b['type']=='text']==['context:nosr','context:before','context:output']
        assert all(b['image_url']['url'].startswith('data:image/png;base64,') for b in blocks if b['type']=='image_url')
        return {'content':json.dumps(observation())}
    await analyze(q.store,rid,task['row_id'],transport=transport)
    await analyze(q.store,rid,task['row_id'],transport=transport)
    assert len(calls)==1

from backend.tests.test_batch_quality import quality
