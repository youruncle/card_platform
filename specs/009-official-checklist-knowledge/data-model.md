# Data Model
CatalogProduct: publisher, title, sport=basketball, season_start/end, source_url, directory_url, release_date nullable, date_basis, window_status(in_window/outside/unknown), status, checksum, rows, errors.
ParsedRow: card_number, player, team nullable, section, insert nullable, parallel nullable, source_page, source_line, raw_text. 不支持事实的字段不生成。
ImportRun: id, scope dates, discovery_complete per publisher, frozen product IDs, current stage, counts, success_percent nullable, processed_percent nullable, engineering_percent, updated_at.
Knowledge facts: 现有实体card attributes存标准字段与source locator；canonical名称包含完整产品/年份/section/卡号/球员防冲突。原文件不可变。
