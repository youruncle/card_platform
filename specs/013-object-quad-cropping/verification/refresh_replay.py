"""Re-run SAM only when a final review change alters its exact input geometry."""
import asyncio, hashlib, json, runpy
from pathlib import Path
import cv2, numpy as np
from card_platform.config import get_settings
from card_platform.flatbed_detection import detect_layout
from card_platform.flatbed_object_quads import resolve_object_layout
from card_platform.flatbed_ml import refine_with_sam2
from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
from card_platform.flatbed_object_quality import audit_object_crop_integrity
ROOT=Path(__file__).parent
baseline=runpy.run_path(str(ROOT/"baseline-quads.py"))
replay=runpy.run_path("scripts/replay_flatbed_layouts.py")
manifest=json.loads(Path("data/flatbed-replay-manifest.json").read_text())
report=json.loads((ROOT/"replay-final.json").read_text())
settings=get_settings()
reused, changed=0,0
async def main():
    global reused, changed
    for result in report["results"]:
        content=Path(result["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest()==result["sha256"]
        proposals=detect_layout(content,dpi=result["dpi"])
        previous=baseline["resolve_object_layout"](content,proposals)
        current=resolve_object_layout(content,proposals)
        # Exact dataclass equality covers corners, bounds, angle, evidence,
        # confidence and failure flags, not just the apparent card count.
        if previous.detections==current.detections:
            reused+=1
            continue
        changed+=1
        boxes, ml=await refine_with_sam2(content,list(current.detections),
            python_path=settings.flatbed_ml_python,worker_script=settings.flatbed_ml_worker_script,
            checkpoint=settings.flatbed_ml_checkpoint,timeout_seconds=settings.flatbed_ml_timeout_seconds)
        image=cv2.imdecode(np.frombuffer(content,np.uint8),cv2.IMREAD_COLOR)
        quality=assess_sheet_crop_quality(image,boxes)
        quality,integrity=audit_object_crop_integrity(quality,boxes,current.detections)
        unusable=sum(not q.usable for q in quality)
        rules=[manifest["geometry_rules_by_sheet"][sid] for sid in result["sheet_ids"]
            if sid in manifest["geometry_rules_by_sheet"]]
        failures=replay["_geometry_failures"](boxes,unusable,rules,current.signals)
        duplicates=sum(replay["overlap"](a,b)>.15 for i,a in enumerate(boxes) for b in boxes[i+1:])
        whole=any(d.width*d.height>.35 for d in boxes)
        unresolved=sum("quad_unresolved" in d.evidence for d in boxes)
        result.update(count=len(boxes),unusable_regions=unusable,duplicates=duplicates,
            whole_sheet_candidate=whole,unresolved_quads=unresolved,geometry_failures=failures,
            ml_refinement=ml,object_integrity=integrity,
            crop_quality_errors=sorted({v.code for q in quality for v in q.violations if v.severity=="error"}),
            boundary_continuity_warnings=sum(any(v.code=="content_crosses_crop_boundary" for v in q.violations) for q in quality),
            passed=len(boxes)==result["expected"] and not (unusable or duplicates or whole or unresolved or failures))
        result["detections"]=[dict(bounds=[round(v,6) for v in [d.x,d.y,d.width,d.height]],
            confidence=round(d.confidence,4),failure=d.failure,evidence=list(d.evidence),
            points=d.points) for d in boxes]
        print("refreshed",result["sheet_ids"][0],result["passed"],failures,flush=True)
    results=report["results"]
    critical=[r for r in results if any(sid in manifest["geometry_rules_by_sheet"] for sid in r["sheet_ids"])]
    n=len(results);regions=sum(r["count"] for r in results)
    metrics=dict(standalone_count_recall=sum(r["count"]==r["expected"] for r in results)/n,
        duplicate_free_rate=sum(not r["duplicates"] for r in results)/n,
        whole_sheet_rejection_rate=sum(not r["whole_sheet_candidate"] for r in results)/n,
        usable_region_rate=1-sum(r["unusable_regions"] for r in results)/regions,
        critical_geometry_pass_rate=sum(r["passed"] for r in critical)/len(critical))
    report.update(metrics=metrics,passed=sum(r["passed"] for r in results))
    report["regressions"]=[f"{key}:{metrics[key]:.4f}<{value:.4f}" for key,value in manifest["minimum_metrics"].items() if metrics[key]<value]
    report["regressions"]+=replay["_must_pass_regressions"](results,manifest["must_pass_sheets"])
    report["methodology"].update(exact_sam_inputs_reused=reused,changed_inputs_sam_rerun=changed,
        baseline_quad_source_sha256=hashlib.sha256((ROOT/"baseline-quads.py").read_bytes()).hexdigest())
    (ROOT/"replay-verified.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=replay["_json_default"]))
    print("final",report["passed"],"/",n,"reused",reused,"rerun",changed,report["regressions"])
asyncio.run(main())
