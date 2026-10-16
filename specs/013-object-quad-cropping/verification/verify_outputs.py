"""Audit real final warp polygons against pinned manual card corners."""
from pathlib import Path
import cv2
import numpy as np
import json
ROOT = Path(__file__).parent
GOLD = {
"front": [
[(.040,.038),(.450,.036),(.450,.257),(.040,.258)],
[(.526,.041),(.925,.027),(.946,.252),(.550,.266)],
[(.046,.325),(.486,.330),(.485,.550),(.047,.546)],
[(.044,.600),(.466,.597),(.475,.823),(.046,.827)]],
"back": [
[(.024,.065),(.469,.066),(.469,.284),(.024,.284)],
[(.550,.144),(.956,.091),(.997,.303),(.600,.352)],
[(.040,.338),(.466,.338),(.466,.558),(.040,.558)],
[(.051,.615),(.482,.610),(.486,.831),(.055,.837)]]}
summary = {}
for side, corners in GOLD.items():
    out=json.loads((ROOT/f"pipeline-{side}.json").read_text())
    polygons=[cv2.convexHull(np.array(p,np.float32)) for p in corners]
    matrix=[]
    for region in out["regions"]:
        actual=cv2.convexHull(np.array([[p["x"],p["y"]] for p in region["bounds"]["source_crop_points"]],np.float32))
        matrix.append([float(cv2.intersectConvexConvex(actual,p)[0])/cv2.contourArea(p) for p in polygons])
    values=np.array(matrix)
    owners=values.argmax(axis=0)
    coverage=values.max(axis=0).tolist()
    neighbours=[max(values[owner,j] for j in range(4) if j!=i) for i,owner in enumerate(owners)]
    assert len(set(owners)) == 4 and min(coverage)>=.98 and max(neighbours)<=.01
    summary[side]=dict(coverage=coverage,neighbour_overlap=neighbours,distinct_crops=4,
        quality=out["layout_hypothesis"]["crop_quality"],ml=out["layout_hypothesis"]["ml_refinement"])
    tiles=[]
    for i in range(4):
        path=next((ROOT/"crops").glob(f'*/*/{out["id"]}/region-{i}.jpg'))
        image=cv2.imread(str(path))
        scale=min(300/image.shape[1],430/image.shape[0])
        small=cv2.resize(image,None,fx=scale,fy=scale)
        tile=np.full((475,320,3),245,np.uint8)
        tile[35:35+small.shape[0],10:10+small.shape[1]]=small
        cv2.putText(tile,f"{side} {i+1}",(10,25),cv2.FONT_HERSHEY_SIMPLEX,.65,(30,30,30),1)
        tiles.append(tile)
    cv2.imwrite(str(ROOT/f"corrected-{side}.jpg"),np.hstack(tiles))
jordan=json.loads((ROOT/"pipeline-back-jordan.json").read_text())
assert len(jordan["regions"])==7
assert jordan["layout_hypothesis"]["crop_quality"]["usable_count"]==7
summary["jordan"]={"count":7,"quality":jordan["layout_hypothesis"]["crop_quality"]}
(ROOT/"final-geometry.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print({side:value.get("coverage",value.get("count")) for side,value in summary.items()})
