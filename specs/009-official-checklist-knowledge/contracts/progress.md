# Contracts
GET /v1/knowledge/official-import/status：沿用knowledge admin鉴权，返回最近运行状态、scope、counts、percentage、updated_at；没有运行时返回idle，百分比null。
ScopedCardCatalog.enrich(identity: dict) -> tuple[dict, list[dict]]：不修改输入；仅补缺失共同字段，evidence包含source_url/locator/version；不猜parallel或限编。
CLI sync_official_checklists.py --catalog PATH --output PATH [--apply]：dry-run默认，apply执行已授权自动官方知识导入发布；失败返回非零并保留状态。
