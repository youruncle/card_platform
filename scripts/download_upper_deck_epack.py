"""Resume Upper Deck's public Basketball catalog and checklist API."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json,time
import httpx
ROOT=Path(__file__).resolve().parents[1]/'data/official-checklists'
CACHE=ROOT/'upper-deck'
BASE='https://www.upperdeckepack.com/'

def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(path)

def run():
    CACHE.mkdir(parents=True,exist_ok=True)
    products=[];excluded=[]
    with httpx.Client(timeout=30,follow_redirects=True) as c:
        def fetch(url,path,body=None):
            if path.exists():return json.loads(path.read_text())
            for attempt in range(3):
                try:
                    r=c.post(url,json=body) if body else c.get(url)
                    r.raise_for_status()
                    if r.url.host!='www.upperdeckepack.com':raise ValueError('Non-official redirect')
                    data=r.json();write(path,data);time.sleep(1);return data
                except httpx.HTTPError:
                    if attempt==2:raise
                    time.sleep(2**attempt)
        directory=fetch(BASE+'Store/api/Epack/FilteredProducts?genre=Basketball&page=1&secondary=0',CACHE/'epack-basketball-directory.json')
        ids=list(directory['ProductIds'])
        for page in range(2,directory['Pages']+1):
            extra=fetch(BASE+f'Store/api/Epack/FilteredProducts?genre=Basketball&page={page}&secondary=0',CACHE/f'epack-basketball-directory-{page}.json');ids.extend(extra['ProductIds'])
        metadata=fetch(BASE+'Store/api/Epack/Products',CACHE/'epack-products.json',{'GetFullMetadata':True,'ProductIds':ids})
        for item in metadata:
            genres=[m['Content'] for m in item['Metadata'] if m['Type']=='Genre']
            if 'Basketball' not in genres or 'UDA Memorabilia' in genres:
                excluded.append(dict(title=item['Title'],reason='not_basketball_trading_cards',product_id=item['ProductId']));continue
            ref=item['EpackProductRefId'];url=BASE+'api/Checklist/GetChecklist?productId='+ref
            p=dict(key='upper-deck-epack-'+ref,title=item['Title'],publisher='Upper Deck',official_product_title=item['Title'],source_url=url,directory_url=BASE+'Checklists/Basketball',document_format='upper_deck_epack',release_date=item['ReleaseDate'][:10],date_basis='official_epack_ReleaseDate',date_scope='e-Pack availability; not asserted as original physical release',ownership_evidence=dict(url=BASE+'Store/api/Epack/Products',product_id=item['ProductId'],genres=genres,metadata_checksum=sha256((CACHE/'epack-products.json').read_bytes()).hexdigest()),retrieved_at=datetime.now(timezone.utc).isoformat())
            try:
                doc=fetch(url,CACHE/f'epack-{ref}.json')
                if not doc:raise ValueError('Official checklist API returned empty object')
                raw=CACHE/f'epack-{ref}.json';(ROOT/'raw'/f"{p['key']}.pdf").write_bytes(raw.read_bytes())
                p.update(status='downloaded',checksum=sha256(raw.read_bytes()).hexdigest())
            except Exception as e:p.update(status='failed',error=str(e))
            products.append(p);write(ROOT/'upper-deck-epack-history.json',products)
        write(CACHE/'epack-discovery.json',dict(directory_products=len(ids),basketball_card_products=len(products),excluded=excluded,complete=len(ids)==directory['TotalItems'],full_30_year_denominator=None))
        print('e-Pack products',len(products),'excluded memorabilia',len(excluded))
if __name__=='__main__':run()
