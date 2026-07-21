# Card Platform

篮球球星卡投资管理系统。首版围绕三件事构建：

1. 记录可审计的个人卡库、买卖交易、成本和盈亏；
2. 汇总球员表现、伤病、新闻及市场价格信号；
3. 用可解释的评分模型和 AI 建议辅助买入、持有与卖出决策。

## 技术架构

- Web：Next.js + TypeScript
- API：FastAPI + Python
- Database：PostgreSQL
- Image Storage：本地持久化卷（可通过 `UPLOAD_DIR` 替换为挂载目录）

详细设计见 [docs/architecture.md](docs/architecture.md) 和 [docs/data-model.md](docs/data-model.md)。

## 目录

```text
apps/
  api/             FastAPI 服务与领域逻辑
  web/             Next.js 管理界面
db/
  migrations/      PostgreSQL 迁移
docs/               架构、数据模型和路线图
infra/              本地基础设施
```

## 本地启动（当前阶段）

```bash
cp .env.example .env
docker compose -f infra/compose.yaml up -d
```

### 加密同步 `.env`

仓库持续忽略明文 `.env`，只提交使用强密码加密后的 `.env.age`。不要用 `git add -f .env`，也不要把解密密码写入仓库；请将密码保存在密码管理器中。

首次加密（macOS）：

```bash
brew install age
./scripts/env-secrets.sh encrypt
git add .env.age
```

在另一台机器克隆仓库并安装 `age` 后，运行：

```bash
./scripts/env-secrets.sh decrypt
```

脚本会在终端中读取密码并恢复权限为 `600` 的 `.env`。若更新了环境变量，重新执行 `encrypt` 并提交新的 `.env.age` 即可。密码一旦遗失，密文无法恢复。

执行数据库迁移：

```bash
for migration in db/migrations/*.sql; do psql "$DATABASE_URL" -f "$migration"; done
```

启动 API：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e 'apps/api[dev]'
.venv/bin/uvicorn card_platform.main:app --reload
```

API 文档默认位于 `http://localhost:8000/docs`。主要接口包括：

- `POST /v1/users`：创建或更新收藏者账户
- `POST /v1/portfolio/items`：录入卡片，可同时写入首笔买入
- `GET /v1/portfolio/items`：持仓、剩余成本、估值和盈亏
- `GET /v1/portfolio/items/{id}/detail`：交易与估值完整历史
- `POST /v1/portfolio/items/{id}/transactions`：买入、卖出、费用或调整
- `POST /v1/portfolio/items/{id}/valuations`：添加估值快照
- `POST /v1/portfolio/items/{id}/market-comps`：添加可比成交证据
- `GET /v1/portfolio/items/{id}/market-comps`：查看可比成交历史
- `POST /v1/portfolio/items/{id}/market-estimate`：生成稳健市场估值
- `POST /v1/portfolio/items/{id}/analysis`：生成评分、价格判断和投资建议
- `POST /v1/portfolio/items/{id}/analysis/auto`：自动综合球员、卡片与市场证据生成投资建议
- `POST /v1/portfolio/items/{id}/images`：上传卡片正面或背面图片
- `POST /v1/cards/recognize`：使用 Qwen3-VL-Flash 识别单张卡片图片
- `POST /v1/cards/recognize-pair`：联合卡片正反面识别为一条草稿
- `POST /v1/cards/recognize-batch`：批量识别最多 20 张单卡图片
- `POST /v1/analyses/{id}/narrative`：基于已锁定数值和证据生成 AI 深度解读
- `GET /v1/portfolio/summary`：组合汇总
- `PUT /v1/players/{id}/metrics`：录入或更新 NBA 指标快照
- `POST /v1/players/{id}/events`：录入新闻、伤病、交易或球队事件
- `POST /v1/players/{id}/potential`：生成版本化球员潜力评估
- `GET /v1/players/{id}/intelligence`：读取球员数据、事件和潜力总览
- `POST /v1/players/{id}/sync`：同步 NBA 统计及配置的新闻/伤病源

组合汇总只使用与账户基准币一致的估值计算未实现盈亏；未估值或币种不一致的卡会计入 `unvalued_item_count`，不会被错误计算为亏损。

分析接口使用版本化六因子模型，输出综合分、各因子贡献、价格合理性、买入评级、建议持有周期、目标卖出区间、置信度、催化剂和风险。可比成交不足时不会给出“强力买入”。

设置 `AI_API_KEY` 后，深度解读使用 DeepSeek 的 OpenAI 兼容 Chat Completions API 和 JSON 结构化输出，默认模型为 `deepseek-v4-flash`。未配置密钥时接口返回明确标记为 `rules` 的确定性降级解释，不会冒充 AI 结果。AI 只能解释证据，不能修改规则引擎给出的评级、合理价、目标价或持有期。

卡片图片识别使用独立的 Qwen3-VL-Flash provider，需要设置 `VISION_API_KEY`。图片通过 Base64 Data URL 发送到百炼 OpenAI 兼容接口；未配置密钥时返回 503，不会使用伪识别结果。前端将能力拆为单卡、正反面、批量三个模式，所有识别结果均先作为可编辑草稿，再由用户确认录入。图片仅接受 JPEG、PNG、WebP，默认单张不超过 10 MiB；批量模式每次最多 20 张。生产环境通过 `card_platform_uploads` 卷持久化。

设置 `CARD_SEARCH_ENABLED=true` 后，每次视觉提取会额外使用 `CARD_SEARCH_MODEL`（默认 `qwen-plus`）强制联网核验。系统最多构造 3 条身份查询，并将搜索限制在厂商、Checklist、评级与成交类站点；只有带有效来源 URL 且存在字段证据的候选才能修正视觉草稿。查询词、字段证据、候选、冲突和来源链接会展示给用户，最终仍需人工确认。批量模式会对每张卡分别产生一次视觉调用和一次搜索调用，可关闭该开关控制成本。

NBA 同步默认连接 `NBA_STATS_BASE_URL`。新闻与伤病开箱使用 Google News RSS 搜索，也可通过 `NEWS_RSS_URL_TEMPLATE` 和 `INJURY_RSS_URL_TEMPLATE` 替换；模板使用 `{player}` 作为已编码球员名占位符。未配置的 provider 会返回 `not_configured`；单一来源失败不会丢失其他来源已同步的数据，每次执行均记录在 `data_sync_run`。

## 端到端测试

安装 PostgreSQL 16 和 API 开发依赖后，可用独立临时数据库执行全部迁移与全链路测试：

```bash
chmod +x scripts/test-e2e.sh
./scripts/test-e2e.sh
```

脚本不会使用或修改已有数据库；它会创建临时数据库集群，验证完成后自动停止并清理。

## 批量导入初始收藏

复制并补全 `data/initial_collection.template.csv`。模板已包含最初的五张收藏，但故意保留了未确认的币种、部分赛季和卡号；导入器会拒绝这些不完整数据。

```bash
python scripts/import_collection.py data/initial_collection.template.csv \
  --owner-id <账户 UUID> --dry-run

python scripts/import_collection.py data/initial_collection.template.csv \
  --owner-id <账户 UUID>
```

## 生产部署

复制生产环境变量并设置强密码、真实域名与可选数据源：

```bash
cp .env.production.example .env.production
docker compose --env-file .env.production -f infra/compose.prod.yaml up -d --build
```

生产编排包含 PostgreSQL、版本化迁移、FastAPI、Next.js 和独立图片持久化卷。迁移服务用 `schema_migration` 记录已应用版本，可安全重启。API 的存活检查为 `/health`，数据库就绪检查为 `/health/ready`。

若部署在公网，应在入口反向代理或私有访问层增加身份认证和 TLS；当前产品定位为个人卡库，应用本身不提供多租户公开注册安全边界。

## 产品边界

当前定位为个人投资卡库，不包含公开多租户认证、自动交易或绕过第三方条款的网页抓取。NBA、新闻和伤病通过可替换 provider 同步；市场成交证据由用户录入并保留来源链接。
