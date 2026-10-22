# Data Model
CatalogProduct: publisher, title, sport=basketball, season_start/end, source_url, directory_url, release_date nullable, date_basis, window_status(in_window/outside/unknown), status, checksum, rows, errors.
ParsedRow: card_number, player, team nullable, section, insert nullable, parallel nullable, source_page, source_line, raw_text. 不支持事实的字段不生成。
ImportRun: id, scope dates, discovery_complete per publisher, frozen product IDs, current stage, counts, success_percent nullable, processed_percent nullable, engineering_percent, updated_at.
Knowledge facts: 现有实体card attributes存标准字段与source locator；canonical名称包含完整产品/年份/section/卡号/球员防冲突。原文件不可变。


## 浏览派生模型（2026-10-22）
KnowledgeProduct: opaque id, canonical name, manufacturer/brand, nullable season/sport/league, aliases, proven card/image counts, text status, source and optional cover. 同名不同赛季不得合并。
KnowledgeCard: published entity/attachment ID, product scope, player/number/section/parallel/serial and explicit nullable attributes, sources and explicitly bound card images.
KnowledgeMedia: asset ID/hash, snapshot-owned local path, product/card binding, type/side/pair, source and visibility; optional published parent product name/manufacturer/seasons for gallery navigation. Unknown identity/pair stays null; design samples never count as card imagery.
BrowserIndex: version_id + snapshot checksum + schema version identify rebuildable SQLite cache; immutable source snapshot remains authoritative. See contracts/browser.md and shared TypeScript types for response constraints.
