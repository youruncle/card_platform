from pathlib import Path
import shutil, json, tarfile
root=Path.cwd(); out=Path('/tmp/card-platform-cloud-release')
if out.exists(): shutil.rmtree(out)
out.mkdir()
for folder, extensions in [('packages/card-core/src',{'.ts'}),('apps/api/src',{'.py'}),('apps/web/app',{'.ts','.tsx','.css','.ico'}),('apps/web/components',{'.ts','.tsx','.css'}),('apps/web/lib',{'.ts','.tsx'}),('apps/web/scripts',{'.mjs'}),('db/migrations',{'.sql'})]:
 for p in (root/folder).rglob('*'):
  if p.is_file() and p.suffix in extensions:
   dest=out/p.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
for f in ['apps/api/pyproject.toml','apps/api/Dockerfile','apps/web/Dockerfile','apps/web/Dockerfile.dockerignore','apps/web/package.json','apps/web/pnpm-lock.yaml','apps/web/pnpm-workspace.yaml','apps/web/tsconfig.json','apps/web/next-env.d.ts','apps/web/next.config.ts']:
 p=root/f
 if p.exists():
  dest=out/f;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
(out/'apps/web/pnpm-workspace.yaml').write_text('allowBuilds:\n  esbuild: true\n  sharp: true\n')
p=out/'apps/web/Dockerfile';p.write_text(p.read_text().replace('RUN corepack enable','RUN corepack enable && corepack prepare pnpm@11.25.0 --activate'))
(out/'apps/web/public').mkdir(exist_ok=True)
for name in ['og.png','cardhobby-connector.zip']:
 shutil.copy2(root/'apps/web/public'/name,out/'apps/web/public'/name)
p=out/'apps/api/src/card_platform/data/nba_player_aliases_v1.json';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({'entities':[],'sources':[],'relations':[],'coverage':[]}))
p=out/'apps/web/Dockerfile';p.write_text(p.read_text().replace('ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL', 'ENV API_INTERNAL_URL=http://api:8000 \\\n    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL'))
(out/'infra').mkdir(exist_ok=True)
shutil.copy2(root/'infra/compose.tunnel.yaml',out/'infra/compose.tunnel.yaml')
(out/'scripts').mkdir(exist_ok=True)
for name in ['cloud-migrate.sh','cloud-backup.sh']:
 shutil.copy2(root/'scripts'/name,out/'scripts'/name)
p=out/'apps/api/Dockerfile';p.write_text(p.read_text().replace('/app/uploads /app/scanner-jobs','/app/uploads /app/scanner-jobs /app/flatbed-scans /app/secrets'))
p=out/'apps/api/Dockerfile';p.write_text(p.read_text().replace('RUN pip install --no-cache-dir .','RUN pip install --no-cache-dir --index-url https://mirrors.cloud.tencent.com/pypi/simple .[checklists]'))
with tarfile.open('/tmp/card-platform-cloud-release.tar.gz','w:gz') as t:
 for p in out.rglob('*'):
  if p.is_file(): t.add(p,arcname=str(p.relative_to(out)))
print(out)
