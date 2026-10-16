"""Download official links resolved from the Topps public checklist archive."""
from hashlib import sha256
import json
from pathlib import Path
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]/'data/official-checklists'
BASE='https://cdn.shopify.com/s/files/1/0662/9749/5709/files/'
LINKS=[
('2022-23 Bowman University Best Basketball','202223BowmanUBestBasketballChecklist.pdf'),
('2022-23 Bowman University Chrome Basketball','2023BowmanUChromeBasketballChecklist.pdf'),
('2021-22 Topps Bowman University Basketball','BowmanUBasketballChecklist.pdf'),
('2021 Topps Finest Basketball','2021_FINEST_BASKETBALL.pdf'),
('2024 Topps Chrome McDonalds All American Basketball','MCD2426-2024ToppsChromeMcDonaldsAllAmericanBasketballChecklist.pdf?v=1738964373'),
('2023-24 Topps Chrome NBL Basketball','NBL2402-24NBLCChecklist.pdf'),
('2023-24 Topps Mercury Victor Wembanyama Basketball','2023-24-topps-mercury-checklist.pdf'),
('2023-24 Topps NBL Basketball','NBL2401-23-24toppsnblchecklists.pdf'),
('2023-24 Topps Overtime Elite Chrome Basketball','OEB2401-ToppsOvertimeEliteChecklists.pdf'),
('2022 Topps Chrome McDonalds All American Basketball','2022McDonaldsAllAmericanChecklist.pdf'),
('2023 Topps Chrome McDonalds All American Basketball','MCD2326-2023McDonaldsAllAmericanChecklist.pdf'),
('2023 Topps Chrome NBL Basketball','NBL2302-2023NBLChromeChecklist.pdf'),
('2022-23 Topps Finest Overtime Elite Basketball','OEB2303-2023FinestOTEchecklist.pdf'),
('2022-23 Topps Overtime Elite Chrome Basketball','OEB2301-2022-23ToppsChromeOTE-Checklist.pdf'),
('2021-22 Topps Overtime Elite Chrome Basketball','202122ToppsChromeOvertimeEliteBasketballChecklist.pdf'),
('2021-22 Topps Overtime Elite Inception Basketball','OTEInceptionChecklist.pdf'),
('2024 Bowman U Best Basketball','2024BowmanUBestBasketballChecklist.pdf'),
('2023-24 Bowman University Chrome Basketball','NIL2402-2023-24BowmanUniversityChromeBasketballChecklistFinal.pdf'),
('2023-24 Topps Chrome Basketball Hobby','NBA2402-24TCBKChecklistsHOBBYONLY.pdf'),
('2023-24 Topps Chrome Basketball Retail','NBA2402-24TCBKRetailChecklist.pdf'),
('2023-24 Topps Chrome Basketball Sapphire Edition','NBA2402-24TCBKSapphireChecklist.pdf'),
('2023-24 Topps Cosmic Chrome Basketball','NBA2409-CheckList_24CCBK_VERSION4.pdf'),
('2023-24 Topps Finest Basketball','NBA2403-CheckList_24TFBK_VERSION6.pdf'),
('2023-24 Topps G League Basketball','GLB2401-CheckList_24GLBK_VERSION2.pdf'),
('2023-24 Topps Midnight Basketball','NBA2414-CheckList_24TMBK_VERSION3.pdf'),
]

if __name__=='__main__':
    products=[]
    with httpx.Client(timeout=60) as client:
        for title,file in LINKS:
            url=BASE+file;key='topps-'+sha256(url.split('?')[0].encode()).hexdigest()[:16]
            p=dict(key=key,title=title,source_url=url,publisher='Topps',directory_url='https://www.topps.com/pages/checklists',release_date=None,window_status='unknown',status='pending')
            target=ROOT/'raw'/f'{key}.pdf'
            for attempt in range(3):
                try:
                    if not target.exists():
                        r=client.get(url);r.raise_for_status()
                        if not r.content.startswith(b'%PDF'): raise ValueError('Not PDF')
                        target.write_bytes(r.content)
                    p['status']='downloaded';break
                except Exception as exc:
                    p.update(status='failed',error=str(exc));time.sleep(attempt+1)
            products.append(p)
            (ROOT/'topps-history.json').write_text(json.dumps(products,ensure_ascii=False,indent=2))
            print(title,p['status'],flush=True)
