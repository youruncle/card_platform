# 开发数据迁移

此目录提供一份可在另一台开发机恢复的 PostgreSQL 快照；配套卡片图片位于
`apps/api/uploads/`。

## 数据范围

- 备份文件：`data/backups/card_platform-dev-20260816.dump`
- 包含卡片、交易、不可变身份版本、身份研究、市场证据与绑定、估值任务、影子估值及正式估值历史
- 包含应用用户和密码哈希，现有用户名/密码可继续使用
- 不包含登录 Session、认证审计日志、识别 benchmark 数据、API Key 或 `.env`
- `data/benchmarks/` 中的大型研究 dump 不随开发数据提交

## 在新机器恢复

先启动 PostgreSQL，并创建一个空数据库。下面示例沿用项目的默认本地账户：

```bash
createdb \
  --host localhost \
  --port 5432 \
  --username card_platform \
  card_platform

pg_restore \
  --host localhost \
  --port 5432 \
  --username card_platform \
  --dbname card_platform \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  data/backups/card_platform-dev-20260816.dump
```

如果 `card_platform` 已有数据，请不要直接覆盖。先改用一个新数据库名验证，或在确认不再需要旧数据后自行删除并重建空库。

复制环境配置并确保数据库和图片目录指向仓库内的数据：

```bash
cp .env.example .env
```

`.env` 中至少应为：

```dotenv
DATABASE_URL=postgresql://card_platform:card_platform@localhost:5432/card_platform
UPLOAD_DIR=apps/api/uploads
```

DeepSeek 和千问的 API Key 不在仓库中，需要在新机器单独填写，或按照主 README 的
`.env.age` 流程安全迁移。

恢复后验证：

```bash
psql "$DATABASE_URL" -c "select count(*) from collection_item;"
psql "$DATABASE_URL" -c "select count(*) from card_identity_version;"
psql "$DATABASE_URL" -c "select count(*) from market_evidence;"
psql "$DATABASE_URL" -c "select count(*) from valuation_shadow_result;"
```

从仓库根目录启动 API：

```bash
.venv/bin/uvicorn card_platform.main:app --reload
```

图片 URL 使用 `/uploads/<文件名>`，API 会从 `UPLOAD_DIR=apps/api/uploads` 提供这些文件。

## 快照基线

创建日期：2026-08-16（Asia/Shanghai）。恢复验证时预期至少包含：

- 14 条卡片记录，其中 8 条有效持仓
- 28 个身份版本
- 52 条市场证据
- 42 条市场成交记录
- 37 条影子估值
- 16 条正式估值历史

业务数据恢复后仍应执行仓库中新增的迁移；当前快照已经应用至 `0058_identity_research.sql`。
