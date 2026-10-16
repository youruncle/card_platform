"""Run production SAM/warp against immutable sources without altering submitted scans."""
import asyncio, json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID
from card_platform.flatbed_api import service
ROOT = Path("specs/013-object-quad-cropping/verification")
SOURCE = Path("data/flatbed-scans/c6e6987e-eec0-40a0-a9de-92ce5d51a7cc/c8f1d7f1-34ba-40ce-9d78-8d414f46541d")
owner = UUID("c6e6987e-eec0-40a0-a9de-92ce5d51a7cc")
session = UUID("c8f1d7f1-34ba-40ce-9d78-8d414f46541d")
async def main():
    reports=[]
    for side,sheet in [("front","eb670aba-74aa-47e7-81d8-38cca6a65695"),("back","984db2bd-75aa-4189-a7c4-f50c59472636"),("back-jordan","5157cbd8-5734-4214-bbd4-2200b132ec75")]:
        async def execute(sql, params=None):
            result=AsyncMock()
            if sql.startswith("SELECT * FROM flatbed_scan_session"):
                result.fetchone.return_value={"id":session,"owner_id":owner,"dpi":300}
            elif sql.startswith("SELECT COALESCE"):
                result.fetchone.return_value={"revision":1}
            elif sql.startswith("SELECT r.bounds,r.signals"):
                result.fetchall.return_value=[]
            return result
        db=AsyncMock()
        db.execute.side_effect=execute
        app=service()
        app.root=ROOT/"crops"
        out=await app.store_sheet(db,owner,session,"front" if side=="front" else "back",next(Path("data/flatbed-scans").glob("*/*/"+sheet+"/original.png")).read_bytes(),"image/png")
        (ROOT/f"pipeline-{side}.json").write_text(json.dumps(out,default=str,ensure_ascii=False,indent=2))
        print(side,len(out["regions"]),out["layout_hypothesis"]["object_integrity"], flush=True)
        reports.append(out)
asyncio.run(main())
