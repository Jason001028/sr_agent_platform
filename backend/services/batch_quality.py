"""Human quality ledger. No submission, retries, executors or model decisions.

Review identity includes the exact Job, config/repair receipt, original PAN,
reference NOSR, produced PAN and immutable Mask + candidate geometry. Updating a
file invalidates the review; a new Job gets a new review with pending regions.
"""
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from backend.pathguard import ensure_allowed
from backend.services import batch_manifest, batch_mask, preview_jpg
from backend.services.spatial_identity import resource_identity
from backend.services.tour_identity import image_identity


class ReviewConflict(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _file(path, *, raster=False):
    p=Path(path).resolve(strict=True)
    stat=p.stat()
    result={"path":p.as_posix(),"image_key":resource_identity(p)["image_key"],
            "revision":preview_jpg.source_revision(p),"size":stat.st_size}
    if raster:
        ensure_allowed(p,kind="file")
        info=preview_jpg.probe_tiff(p)
        result.update(width=info["W"],height=info["H"],spp=info["spp"])
    return result


def make_binding(item,task,verification):
    cp=item["checkpoint"]; target=cp["target_image"]; ref=cp["reference_image"]; mask=cp["mask"]
    if verification["layers"]["contract"]["status"] != "passed" or verification["layers"]["artifacts"]["status"] != "passed":
        raise ReviewConflict("SR 执行契约及产物必须先验证通过")
    job=item.get("external_job_id") or cp.get("job_id")
    if str(task.get("job_id")) != str(job) or task.get("fingerprint") != cp.get("sr_fingerprint"):
        raise ReviewConflict("Job/提交身份不一致")
    output=_file(verification["layers"]["artifacts"]["path"],raster=True)
    reference=_file(ref["path"],raster=True)
    if reference["revision"] != ref["revision"] or reference["image_key"] != ref["image_key"]:
        raise ReviewConflict("NOSR 源版本已改变")
    if (output["width"],output["height"],output["spp"]) != (target["width"],target["height"],1):
        raise ReviewConflict("本次 PAN 产物尺寸/波段不符合冻结网格")
    if Path(output["path"]) != Path(target["path"]).resolve() or output["image_key"] == target["image_key"]:
        raise ReviewConflict("产物不是本次发布的新 PAN")
    mask_path=Path(mask["path"]).resolve(strict=True)
    if batch_manifest.artifact_root().resolve() not in mask_path.parents:
        raise ReviewConflict("Mask 不在受控工件目录")
    batch_mask.verify_mask(mask_path,width=target["width"],height=target["height"],sha256=mask["sha256"])
    config=Path(task["config_xml"]).resolve(strict=True)
    document=ET.parse(config).getroot()
    if (Path(document.findtext("MaskPath") or "").resolve()!=mask_path
            or Path(document.findtext("RepairNOSR") or "").resolve()!=Path(reference["path"])
            or Path(document.findtext("DatarootLQ") or "").resolve()!=Path(output["path"]).parent):
        raise ReviewConflict("Job 配置中的 Mask/NOSR/产物场景与审核对象不一致")
    config_sha=batch_mask._sha256(config)
    # Existing repair verifier binds its receipt to the config content digest.
    receipt=Path(output["path"]).parent/"Debug"/("_SRREPAIR_"+config_sha+".json")
    receipt_info=_file(receipt)
    receipt_info["sha256"]=batch_mask._sha256(receipt)
    receipt_doc=json.loads(receipt.read_text(encoding="utf-8"))
    backup=_file(receipt_doc["backup"],raster=True)
    frozen_stat=str(target["revision"]).rsplit("-",2)[-2:]
    if frozen_stat!=[str(v) for v in receipt_doc["backup_signature"][:2]]:
        raise ReviewConflict("修复备份不属于冻结的 PAN 输入版本")
    candidates=cp["detection"]["candidates"]
    if not candidates or len({c["candidate_id"] for c in candidates}) != len(candidates):
        raise ReviewConflict("Mask 区域清单缺失或身份重复")
    return {"version":"human-quality.v1","job_id":str(job),"fingerprint":cp["sr_fingerprint"],
            "task_params_sha256":_hash(task.get("params") or {}),
            "input":target,"nosr":reference,"output":output,"backup":backup,
            "mask":{**_file(mask_path),"sha256":mask["sha256"],"width":target["width"],"height":target["height"]},
            "config":{**_file(config),"sha256":config_sha},"receipt":receipt_info,
            "geometry_sha256":_hash(candidates),"regions":candidates}


def _decode(row):
    if row is None:
        return None
    keys=("review_id","goal_id","item_id","job_id","binding","regions","scene_decision","version","created_at","updated_at")
    out=dict(zip(keys,row)); out["binding"]=json.loads(out["binding"]); out["regions"]=json.loads(out["regions"])
    return out


def get_review(store,review_id):
    return _decode(store._db().execute("SELECT review_id,goal_id,item_id,job_id,binding,regions,scene_decision,version,created_at,updated_at FROM batch_quality_reviews WHERE review_id=?",(review_id,)).fetchone())


def enqueue(store,goal,item,lease,task,verification):
    binding=make_binding(item,task,verification)
    rid=_hash({"goal":goal["goal_id"],"item":item["item_id"],"binding":binding})
    regions=[{"region_id":c["candidate_id"],"decision":"pending","reason":"","reviewer":""} for c in binding["regions"]]
    with store.worker_transaction() as db:
        now=time.time()
        db.execute("INSERT OR IGNORE INTO batch_quality_reviews(review_id,goal_id,item_id,job_id,binding,regions,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (rid,goal["goal_id"],item["item_id"],binding["job_id"],json.dumps(binding,ensure_ascii=False),json.dumps(regions),now,now))
        cp={**item["checkpoint"],"phase":"AWAITING_REVIEW","quality_review_id":rid,"verification":verification}
        updated=store.transition_goal_item(goal["goal_id"],step_id=item["step_id"],expected_version=item["version"],
            status="AWAITING_REVIEW",lease_token=lease.token,checkpoint=cp,verification=verification,
            capacity_reserved=0,next_wake_at=None,error=None,
            extra_events=[("quality_review_ready",{"item_id":item["item_id"],"review_id":rid,"job_id":binding["job_id"],"regions":len(regions)})])
        if updated is None:
            raise ReviewConflict("Item changed before review enqueue")
    return updated


def validate_current(store,review,*,contract=True):
    item=store.get_goal_item(review["goal_id"],review["item_id"])
    if not item or item["checkpoint"].get("quality_review_id") != review["review_id"]:
        raise ReviewConflict("审核对象已被替代")
    b=review["binding"]
    if str(item.get("external_job_id")) != b["job_id"] or item["checkpoint"].get("sr_fingerprint") != b["fingerprint"]:
        raise ReviewConflict("Job 已改变，旧审核无效")
    cp=item["checkpoint"]
    if (_hash(cp.get("target_image"))!=_hash(b["input"])
            or cp.get("reference_image",{}).get("image_key")!=b["nosr"]["image_key"]
            or cp.get("reference_image",{}).get("revision")!=b["nosr"]["revision"]
            or Path(cp.get("mask",{}).get("path") or "").resolve()!=Path(b["mask"]["path"])):
        raise ReviewConflict("冻结的源影像/NOSR/Mask 引用已改变，旧审核无效")
    if _hash(item["checkpoint"].get("detection",{}).get("candidates")) != b["geometry_sha256"] or item["checkpoint"].get("mask",{}).get("sha256") != b["mask"]["sha256"]:
        raise ReviewConflict("Mask/区域身份已改变")
    for name in ("nosr","output","backup","mask","config","receipt"):
        old=b[name]; actual=_file(old["path"],raster=name in {"nosr","output","backup"})
        if actual["image_key"] != old["image_key"] or actual["revision"] != old["revision"]:
            raise ReviewConflict(f"{name} 已替换或不可用，旧版审核不得继承")
    if batch_mask._sha256(Path(b["mask"]["path"])) != b["mask"]["sha256"]:
        raise ReviewConflict("Mask 内容摘要已改变")
    if contract:
        from backend.services.goal_worker import sr_contract
        task=store.get_sr_task(b["fingerprint"])
        if not task or str(task.get("job_id")) != b["job_id"]:
            raise ReviewConflict("缺少对应 Job 记录")
        if (_hash(task.get("params") or {})!=b["task_params_sha256"]
                or Path(task.get("config_xml") or "").resolve()!=Path(b["config"]["path"])):
            raise ReviewConflict("Job 配置身份已改变")
        check=sr_contract(task,item["checkpoint"].get("job_status") or {})
        if not check.get("ok") or check.get("skip") or Path(check.get("output") or "").resolve() != Path(b["output"]["path"]):
            raise ReviewConflict("本次 Job 的 SR 产物契约已失效")
    return item


def review_for_item(store,item,*,verify=True):
    rid=item.get("checkpoint",{}).get("quality_review_id")
    review=get_review(store,rid) if rid else None
    if review and verify:
        validate_current(store,review)
    return review


def quality_layer(store,item):
    try:
        review=review_for_item(store,item)
        if not review:
            return {"status":"unverified","reason":"等待人工质量验收"}
        ok=review["scene_decision"]=="approved" and all(r["decision"]=="approved" for r in review["regions"])
        return {"status":"passed" if ok else "unverified","review_id":review["review_id"],
                "review_version":review["version"],"binding_sha256":_hash(review["binding"]),
                "reason":"人工逐区域及整景确认通过" if ok else "尚未完成逐区域和整景人工确认"}
    except (ValueError,OSError,KeyError) as e:
        return {"status":"failed","reason":str(e)}


def decide(store,goal_id,item_id,body):
    rid=body.get("review_id"); review=get_review(store,rid)
    if not review or review["goal_id"]!=goal_id or review["item_id"]!=item_id:
        raise KeyError("quality review not found")
    action=body.get("action"); decision=body.get("decision"); reason=body.get("reason",""); reviewer=body.get("reviewer","")
    key=body.get("request_key"); version=body.get("expected_version")
    if action not in {"region","scene"} or decision not in {"approved","rejected","pending"}:
        raise ValueError("invalid quality decision")
    if not isinstance(reason,str) or len(reason)>2000 or not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>100:
        raise ValueError("请提供审核人，原因最多2000字")
    if decision=="rejected" and not reason.strip():
        raise ValueError("驳回必须填写原因")
    if not isinstance(key,str) or not key or len(key)>200 or isinstance(version,bool) or not isinstance(version,int):
        raise ValueError("request_key and integer expected_version required")
    payload={k:body.get(k) for k in ("action","decision","reason","reviewer","region_id")}
    with store.worker_transaction() as db:
        previous=db.execute("SELECT payload FROM batch_quality_events WHERE review_id=? AND request_key=?",(rid,key)).fetchone()
        if previous:
            if json.loads(previous[0])!=payload:
                raise ReviewConflict("重复请求键对应不同审核内容")
            validate_current(store,get_review(store,rid))
            return get_review(store,rid)
        review=get_review(store,rid); item=validate_current(store,review)
        goal=store.get_goal(goal_id)
        if goal["status"] in {"CANCELLED","FAILED","BLOCKED","COMPLETED"} or item["status"] not in {"AWAITING_REVIEW","REVIEW_REJECTED"}:
            raise ReviewConflict("任务状态已改变，不能修改历史审核")
        if review["version"]!=version:
            raise ReviewConflict("审核版本冲突，请刷新")
        regions=review["regions"]; scene="pending"
        if action=="region":
            region=next((r for r in regions if r["region_id"]==body.get("region_id")),None)
            if region is None: raise KeyError("region not found")
            region.update(decision=decision,reason=reason,reviewer=reviewer,reviewed_at=time.time())
        else:
            if decision=="pending": raise ValueError("整景只能通过或驳回")
            if decision=="approved" and (not regions or any(r["decision"]!="approved" for r in regions)):
                raise ReviewConflict("所有必要区域通过后才可整景确认")
            scene=decision
        now=time.time()
        db.execute("UPDATE batch_quality_reviews SET regions=?,scene_decision=?,version=version+1,updated_at=? WHERE review_id=? AND version=?",
            (json.dumps(regions,ensure_ascii=False),scene,now,rid,version))
        status="VERIFYING" if scene=="approved" else "REVIEW_REJECTED" if scene=="rejected" else "AWAITING_REVIEW"
        cp={**item["checkpoint"],"phase":status}
        db.execute("UPDATE agent_goal_steps SET status=?,checkpoint=?,version=version+1,capacity_reserved=0,next_wake_at=?,updated_at=? WHERE step_id=? AND version=?",
            (status,json.dumps(cp,ensure_ascii=False),now if status=="VERIFYING" else None,now,item["step_id"],item["version"]))
        db.execute("INSERT INTO batch_quality_events VALUES(?,?,?,?)",(rid,key,json.dumps(payload,ensure_ascii=False),now))
        if status=="VERIFYING":
            db.execute("UPDATE agent_goals SET monitor_done=0,next_wake_at=? WHERE goal_id=?",(now,goal_id))
        store._append_goal_event_tx(db,goal_id,"quality_review_changed",{"item_id":item_id,"review_id":rid,"version":version+1,"scene_decision":scene,"status":status},step_id=item["step_id"])
    return get_review(store,rid)


def verify_goal(store,goal):
    reasons=[]
    for item in store.list_goal_items(goal["goal_id"]):
        if item["status"]=="SKIPPED": continue
        if item["status"]=="NO_ISSUE":
            d=item["checkpoint"].get("detection",{})
            if d.get("analysis",{}).get("calibrationStatus") == "experimental-unvalidated":
                reasons.append("智能对比未检出候选不等于人工质量通过；实验性参数尚未生产校准")
            if d.get("provenance")!="real" or d.get("detector_name")!="shared-difference" or d.get("candidate_accounting",{}).get("unprocessed",1): reasons.append("未验证的无异常结果")
            for key in ("target_image","reference_image"):
                try:
                    ref=item["checkpoint"][key]
                    if _file(ref["path"],raster=True)["image_key"]!=ref["image_key"]:reasons.append("无异常源影像已改变")
                except (KeyError,OSError,ValueError) as e:reasons.append(str(e))
            continue
        layer=quality_layer(store,item)
        if item["status"]!="SUCCEEDED" or layer["status"]!="passed": reasons.append(f"{item['item_id']}: {layer.get('reason')}")
    return {"ok":not reasons,"reasons":reasons,"quality_policy":"human"}
