"""LOCAL ONLY: prepare source overlays, never build/deploy images or read secrets."""
from hashlib import sha256
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).parent / 'security'
FROZEN_WEB = Path('/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/partial-intake-20261030/build-source')
API_FILES = ('knowledge_api.py', 'knowledge_repository.py', 'knowledge_browser.py',
             'knowledge_atlas_api.py', 'knowledge_atlas_corrections.py', 'knowledge_atlas_lifecycle.py')
WEB_FILES = ('app/knowledge/manage/page.tsx', 'app/knowledge/manage/atlas-manager.css',
             'app/knowledge/manage/legacy/page.tsx', 'components/knowledge-atlas-manager.tsx',
             'lib/knowledge-atlas.ts', 'lib/api.ts', 'tests/knowledge-atlas-manager.test.cjs')


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def prepare(output):
    OUT = output.resolve()
    assert OUT.is_relative_to(ROOT / '.runtime'), 'Source candidate must remain in task runtime directory'
    api_base = json.loads((EVIDENCE / 'deployment-baseline.json').read_text())
    web_base = json.loads((EVIDENCE / 'web-deployment-baseline.json').read_text())
    assert api_base['image_id'] == 'sha256:119b9ba3827c6db5ff544085374f12343174cae9df817d09251db413f9bddc65'
    assert digest(FROZEN_WEB / 'apps/web/.next/BUILD_ID') == web_base['runtime']['/app/.next/BUILD_ID']['sha256']
    # Every existing target starts at exactly the source verified in production.
    import subprocess
    for name in API_FILES[:3]:
        if name == 'knowledge_browser.py':
            # Already-integrated schema7 baseline is captured directly from production.
            continue
        baseline = subprocess.check_output(['git','show','64cf55d:apps/api/src/card_platform/'+name],cwd=ROOT)
        assert sha256(baseline).hexdigest() == api_base['runtime']['files'][name]['sha256']
    assert not OUT.exists(), 'Use a fresh source candidate directory; never overwrite a reviewed one'
    package = OUT / 'api-overlay/card_platform'
    package.mkdir(parents=True)
    for name in API_FILES:
        shutil.copy2(ROOT/'apps/api/src/card_platform'/name, package/name)
    # This namespace shim is for local tests only; explicitly excluded from image overlay.
    (package/'__init__.py').write_text("from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n")
    source = OUT/'web-source'
    shutil.copytree(FROZEN_WEB, source, ignore=shutil.ignore_patterns('.next','node_modules','*.tsbuildinfo','__pycache__','.DS_Store'))
    before = inventory(source)
    for rel in WEB_FILES:
        target = source/'apps/web'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'apps/web'/rel,target)
    after = inventory(source)
    changed = sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    expected = sorted('apps/web/'+rel for rel in WEB_FILES)
    assert changed == expected, (changed,expected)
    manifest = {
        'source_candidate_root':str(OUT),
        'status':'source_prepared_not_image_built_or_deployed',
        'api_parent_image':api_base['image_id'], 'web_rollback_image':web_base['image_id'],
        'web_source_build_id':(FROZEN_WEB/'apps/web/.next/BUILD_ID').read_text(),
        'api_overlay':{name:digest(package/name) for name in API_FILES},
        'excluded_test_shim':'api-overlay/card_platform/__init__.py',
        'web_source_changed':changed, 'web_unchanged_file_count':sum(before[k]==after.get(k) for k in before),
        'rollback_policy':{'web':'revert web independently; retain compatible API',
          'api':'forward recover/rebuild this immutable overlay on its parent image; do not revert to old reader-only API',
          'database':'no schema/data rollback; audit proposals and immutable version snapshots retained',
          'automatic_publish':'continues carrying published overlays; manual unpublished drafts remain drafts'},
        'full_before_sha256':before, 'full_after_sha256':after,
    }
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (EVIDENCE/'compatible-source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k not in {'full_before_sha256','full_after_sha256'}},indent=2))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'.runtime/atlas-compatible-source-final')
    prepare(parser.parse_args().output)
