"""Validated evidence budgets; no secrets and no source I/O."""
import os


def load_settings():
    specifications={
        'context_edge':('SR_EVIDENCE_CONTEXT_EDGE',512,64,1024),
        'source_max_edge':('SR_EVIDENCE_SOURCE_MAX_EDGE',2048,512,2048),
        'detail_edge':('SR_EVIDENCE_DETAIL_EDGE',512,64,512),
        'max_detail_windows':('SR_EVIDENCE_MAX_DETAIL_WINDOWS',2,0,2),
        'max_read_bytes':('SR_EVIDENCE_MAX_READ_BYTES',33554432,4096,33554432),
        'model_max_bytes':('SR_EVIDENCE_MODEL_MAX_BYTES',8388608,4096,8388608),
        'model_concurrency':('SR_EVIDENCE_MODEL_CONCURRENCY',1,1,1),
        'storage_limit_bytes':('SR_EVIDENCE_STORAGE_LIMIT_BYTES',0,0,2**63-1),
    }
    settings={}
    for key,(env,default,low,high) in specifications.items():
        try:value=int(os.environ.get(env,str(default)))
        except ValueError as exc:raise ValueError('invalid_evidence_setting:'+env) from exc
        if not low<=value<=high:raise ValueError('invalid_evidence_setting:'+env)
        settings[key]=value
    return settings
