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

仓库附带的开发数据（卡片、身份版本、市场证据、估值和图片）恢复方式见
[data/README.md](data/README.md)。

启动 API：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e 'apps/api[dev]'
.venv/bin/uvicorn card_platform.main:app --reload
```

API 文档默认位于 `http://localhost:8000/docs`。主要接口包括：

首次启用登录系统时，在登录页选择“注册 / 认领旧账户”，使用原卡库邮箱设置密码；
原有卡片会保留并自动归入该账户。首个设置凭证的账户会成为管理员。完成首次设置后，
生产环境应将 `AUTH_REGISTRATION_ENABLED=false` 并重启 API。登录态使用服务端 Session
和 HttpOnly Cookie，不再使用浏览器中的 `owner_id` 作为身份凭证。

- `POST /v1/auth/register`：注册或为尚无凭证的旧账户设置密码
- `POST /v1/auth/login`：登录并建立服务端会话
- `GET /v1/auth/me`：读取当前登录用户
- `POST /v1/auth/logout`：退出当前会话
- `GET /v1/auth/sessions`：查看当前账户的登录设备
- `POST /v1/auth/change-password`：修改密码并撤销其他设备会话

- `POST /v1/portfolio/items`：录入卡片，可同时写入首笔买入
- `GET /v1/portfolio/items`：持仓、剩余成本、估值和盈亏
- `GET /v1/portfolio/items/{id}/detail`：交易与估值完整历史
- `POST /v1/portfolio/items/{id}/transactions`：买入、卖出、费用或调整
- `POST /v1/portfolio/items/{id}/valuations`：添加估值快照
- `POST /v1/portfolio/items/{id}/market-comps`：添加可比成交证据
- `GET /v1/portfolio/items/{id}/market-comps`：查看可比成交历史
- `POST /v1/portfolio/items/{id}/market-estimate`：生成稳健市场估值
- `GET /v1/portfolio/items/{id}/automated-valuation`：查看异步估值状态、逻辑和成交证据
- `POST /v1/portfolio/items/{id}/automated-valuation/retry`：重新发起后台估值
- `POST /v1/portfolio/items/{id}/analysis`：生成评分、价格判断和投资建议
- `POST /v1/portfolio/items/{id}/analysis/auto`：自动综合球员、卡片与市场证据生成投资建议
- `POST /v1/portfolio/items/{id}/images`：上传卡片正面或背面图片
- `POST /v1/cards/recognize`：使用 Qwen3-VL-Flash 识别单张卡片图片
- `POST /v1/cards/recognize-pair`：联合卡片正反面识别为一条草稿
- `POST /v1/cards/recognize-batch`：批量识别最多 20 张单卡图片
- `POST /v1/cards/recognize-auto`：自动判断一张图片包含单卡或多卡，并逐卡提取卡片及采购信息
- `POST /v1/scanner/jobs`：自动裁切高拍仪正反面图片并加入后台识别队列
- `GET /v1/scanner/jobs`：读取当前账户的高拍仪识别任务及进度
- `GET /v1/scanner/camera-controls`：读取 NETUM 的自动/手动曝光和原生曝光范围
- `PUT /v1/scanner/camera-controls`：切换曝光模式或写入手动曝光时间
- `POST /v1/scanner/jobs/{id}/retry`：重试失败的高拍仪识别任务
- `DELETE /v1/scanner/jobs/{id}`：删除当前账户的扫描任务及其私有图片
- `POST /v1/portfolio/items/{id}/images/{side}/rotate`：人工旋转正面或背面并记录纠错样本
- `POST /v1/analyses/{id}/narrative`：基于已锁定数值和证据生成 AI 深度解读
- `GET /v1/portfolio/summary`：组合汇总
- `PUT /v1/players/{id}/metrics`：录入或更新 NBA 指标快照
- `POST /v1/players/{id}/events`：录入新闻、伤病、交易或球队事件
- `POST /v1/players/{id}/potential`：生成版本化球员潜力评估
- `GET /v1/players/{id}/intelligence`：读取球员数据、事件和潜力总览
- `POST /v1/players/{id}/sync`：同步 NBA 统计及配置的新闻/伤病源

组合汇总只使用与账户基准币一致的估值计算未实现盈亏；未估值或币种不一致的卡会计入 `unvalued_item_count`，不会被错误计算为亏损。

新卡保存后会在同一数据库事务中写入 `valuation_job`。应用内 Worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 异步领取任务，通过联网检索收集真实成交，以身份匹配度和时间衰减计算加权分位数估值。列表在任务期间显示“估值中”，完成后可点击价格查看区间、置信度、模型版本、检索词、汇率和逐条来源；没有可靠成交时显示“证据不足”，不会使用买入价伪造市场价格。

国内成交优先通过卡淘公开 `NewCommodity/SearchCommodity` 查询：限定篮球单卡、`Status=-2`（“售出的宝贝”）并按结束时间倒序拉取。系统使用 `LowestPrice`、`NewPostageMoney`、`OutCount` 和 `EffectiveDate` 还原结束价、运费、出价次数与时间；只接受有出价记录，并严格过滤多卡 Lot、不同赛季、不同限编、签字/非签字、物料/非物料和裸卡/评级卡混用。卡淘 `Status=-2` 证据在界面中明确标为“有出价的结束拍卖（未确认付款）”，不会伪装成支付完成记录；接口无可靠同款时才降级到通用联网检索。卡淘命中后跳过大模型市场检索，避免无关说明并缩短估值耗时。

分析接口使用版本化六因子模型，输出综合分、各因子贡献、价格合理性、买入评级、建议持有周期、目标卖出区间、置信度、催化剂和风险。可比成交不足时不会给出“强力买入”。

设置 `AI_API_KEY` 后，深度解读使用 DeepSeek 的 OpenAI 兼容 Chat Completions API 和 JSON 结构化输出，默认模型为 `deepseek-v4-flash`。未配置密钥时接口返回明确标记为 `rules` 的确定性降级解释，不会冒充 AI 结果。AI 只能解释证据，不能修改规则引擎给出的评级、合理价、目标价或持有期。

卡片图片识别使用独立的 Qwen3-VL-Flash provider，需要设置 `VISION_API_KEY`。图片通过 Base64 Data URL 发送到百炼 OpenAI 兼容接口；未配置密钥时返回 503，不会使用伪识别结果。前端保留单卡、正反面和多文件批量三个入口，其中单卡入口会自动判断上传内容实际包含一张还是多张卡：识别到一张时直接进入校正表单，识别到多张时自动展开逐卡队列，并将商品价、运费、购买日期、卖家、订单号和商品编号绑定到对应草稿。图片仅接受 JPEG、PNG、WebP，默认单张不超过 10 MiB；多文件批量模式每次最多 20 张。生产环境通过 `card_platform_uploads` 卷持久化。

切换到高拍仪扫描模式时会自动请求摄像头权限、优先连接 NETUM/KornVision 设备并启动预览；无需再手动点击启动按钮，只有连接失败时才显示重新连接入口。高拍仪入口会在提交时检测卡片或评级盒四角，执行透视矫正和紧边裁切；检测置信度不足时保留原图，避免误裁。裁切器支持卡片只占约 3.5% 画面的远景，使用更大尺度的前景聚合从暗色扫描垫和反光卡面中恢复卡砖轮廓；正反面位置接近时，一面可靠检测到的归一化四角可以传递给低对比度的另一面。裁切评分仍会排除占据大部分画面或同时贴住多个边缘的扫描垫/相机边框，并继续选择内部卡套候选。裁切完成即返回队列编号并清空正反面拍摄状态，摄像头保持连接，可马上拍摄下一张。后台 Worker 同时保留未经裁切的原始帧和不可变的裁切原图，先用 macOS Apple Vision 的文字基线做 0/90/180/270 四方向判定；本地信号不可用或置信度不足时，将同一原图的四个旋转候选交给 Qwen 选择正立版本，然后再用标准化展示图完成视觉识别和联网核验。界面每 2 秒刷新状态并显示判向角度，成功后可查看、校正并录入；详情页提供正反面独立左右旋转按钮，人工纠错会保存为后续校准样本。失败任务最多可重试 3 次。队列原始帧、裁切原图和展示图均存放在非公开的 `SCANNER_JOB_DIR`，只能由已登录且拥有该任务的账户读取；生产 Compose 使用独立的 `card_platform_scanner_jobs` 持久卷。

macOS 本地开发可运行 `scripts/install_nssdk_macos.sh` 安装 NETUM 官方 NSSDK。默认安装位置为 `~/Library/Application Support/CardPlatform/nssdk.framework`；也可以通过 `SCANNER_NSSDK_LIBRARY_PATH` 指向 framework 可执行文件。安装后扫描界面会显示硬件自动/手动曝光控制，手动曝光使用设备原生范围并采用对数滑块方便压低反光。Linux/Docker 环境不加载 macOS framework，页面会自动退回软件亮度调节。

扫描任务成功录入卡库后会自动从后台队列删除，随后优先打开下一条已识别完成的任务继续确认；如果只剩后台处理中任务，界面会继续停留在队列等待，不会跳回卡片列表。用户也可以随时手动删除等待中、识别完成或失败的任务。卡库列表、扫描队列、批量识别预览、卡片详情、图鉴候选和识别盲测中的缩略图均可点击进入统一高清查看器；查看器支持悬停局部放大、滚轮缩放、拖拽平移、左右旋转、还原和打开原始图片。

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
