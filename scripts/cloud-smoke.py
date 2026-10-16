import json,secrets,urllib.request,urllib.error,http.cookiejar
# Run only against a fresh disposable cloud deployment; creates two test accounts.
BASE='http://localhost:13000'
def client():
 return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(c,path,data=None,method=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json','Origin':BASE})
 try:
  r=c.open(req,timeout=30);body=r.read();return r.status,json.loads(body) if body else None
 except urllib.error.HTTPError as e:
  return e.code,e.read().decode()
a,b,anon=client(),client(),client()
assert call(anon,'/v1/auth/me')[0]==401
users=[]
for c,tag in [(a,'a'),(b,'b')]:
 status,u=call(c,'/v1/auth/register',{'email':f'cloud-smoke-{tag}@example.invalid','password':secrets.token_urlsafe(24),'display_name':'Cloud smoke '+tag,'base_currency':'CNY'})
 assert status==201,(status,u);users.append(u)
 assert call(c,'/v1/auth/me')[0]==200
 assert call(c,'/v1/portfolio/items')==(200,[])
 assert call(c,'/v1/portfolio/summary')[0]==200
status,item=call(a,'/v1/portfolio/items',{'owner_id':users[1]['id'],'card':{'player_name':'Cloud Smoke Player','brand_name':'Cloud Smoke Brand','product_name':'Cloud Smoke Product','season_start':2026,'card_number':'SMOKE'}})
assert status==201,(status,item)
assert len(call(a,'/v1/portfolio/items')[1])==1
assert call(b,'/v1/portfolio/items?owner_id='+users[0]['id'])==(200,[])
assert call(b,'/v1/portfolio/items/'+item['id']+'/detail')[0] in (403,404)
assert call(anon,'/v1/portfolio/items/'+item['id']+'/detail')[0]==401
assert call(a,'/v1/auth/logout',{},'POST')[0] in (200,204)
assert call(a,'/v1/auth/me')[0]==401
print('PASS: registration, session, empty portfolio, summary, create card, forged owner protection, cross-account list/detail isolation, anonymous protection, logout')
