# Deployment — 2026-10-20

API and web are published as `0.6.3-20261020-deepseekweb1` at https://cardsp.cn/ . Implementation revision `33a84b4e` includes this research migration and the concurrently published cost-evidence fusion commit `6f41bcd7`.

- API image: `sha256:28c1935528e351eb16b6373097bf52b51bd4c0a74650820c71760792d3e93950`.
- Web image: `sha256:ab1ea048e321b905e065f1b77c8813fc0dfce83100c13c5c337e827a1bd2860d`. Its filesystem/assets are identical to the then-current published web image; only release metadata changed because this feature changes backend behavior.
- All 191 package files, health, native model settings, official Search/Fetch, private capture persistence, worker=true and public resources checked. No migration or real business test records.
- Search credentials were updated only on the server, paired with the official endpoint; no whole local .env was uploaded. Legacy provider configuration remains only in restricted backups.
- Persistent evidence directory is the existing private scanner volume's `.web-research`; captures are mode 0600 and survive model failures/cancellation.

Personal iPhone package **1.1.12 (16)** passed Xcode Release build and strict codesign verification. Its six bundled public resources match the prior verified app. Physical device currently unavailable; installation, installed-version confirmation and launch remain pending. Candidate and continuation metadata live in private `.runtime/deepseek-web-research-20261020/`; do not downgrade a newer installed version or clear app data.

## Backup and subsequent rollback

Restricted server backup: `/home/ubuntu/card-platform/backups/deepseek-web-20261020/`. Consistent database dump is 353086515 bytes and pg_restore catalog verification passed. Original configuration/image metadata and the newer pre-activation fusion1 snapshot are retained; backups contain secrets and are not committed or copied to clients.

No database restore is part of code rollback. Future rollback must retain this native research/visual migration and worker=true; deepseekweb1 is the compatible baseline. Do not restore a legacy Qwen image/environment independently or overwrite the intervening cost-fusion changes. The staged reviewed overlay, expected manifest and guarded activation tooling are retained in `/home/ubuntu/card-platform/staging/deepseek-web-20261020/`.

Validation scope and existing baseline failures are recorded in [validation.md](validation.md).
