# 验证

- 修复前临时库测试：匿名读取真实上传图片返回 200，断言预期 401 失败。
- 修复前云端通过 web:3000 代理验证：匿名/另一账户/所属账户均为 200。临时用户、卡牌和生成的图片已清理。
- 修复后本地 PostgreSQL 集成测试通过：匿名 401、跨账户 404、所属账户 200；GET/HEAD；Range/条件请求不可绕过权限；响应 private,no-store 和 Vary Cookie；旋转后新 URL 可读、旧 URL 404；孤立文件、符号链接、软删除图片不可读；退出后 401。
- 相关回归：test_e2e_postgres.py、test_private_uploads_postgres.py、test_auth.py、test_image_service.py、test_scanner_queue.py 共 34 passed，耗时 6.35 秒。存在 Starlette TestClient 的 httpx 弃用警告。
- 扩大 API 全套测试在约 80 秒后中止：1288 passed、1 skipped、1 failed。失败 test_e2e_postgres.py::test_full_investment_workflow 因测试库已有注册用户导致其账户非管理员（metrics 请求 403）；该测试在全新临时库首个执行后通过。未宣称完整 API 套件通过。
- Ruff 针对新增路由、main.py 和测试通过。
- 使用人工生成的非私人 PNG 验证图片上传和旋转，没有复制任何用户图片。未做真实扫描、模型识别和性能压力测试。
- 新 API 已部署，容器镜像与最新镜像 ID 一致，健康检查 200，注册开关 false。
- 云端通过 Next.js 代理重复测试：匿名/其他账户/所属账户为 401/404/200；Cookie 转发、HEAD、缓存策略、旋转新旧 URL 和软删除访问检查均通过。
- 浏览器使用独立 127.0.0.1 域名的临时账户登录，实际点击缩略图进入高清查看器，放大到 125% 并旋转，图片正常显示。该账户和图片已删除，不影响用户 localhost 管理员会话。
- 最后将图片测试参数化覆盖 front/back：图片鉴权、登录、图片处理、扫描队列共 34 passed（7.05 秒）；另有前述独立库端到端流程通过，总计 35 个相关测试。
- 最终云端账户检查仅有 1 个 admin。未改变公网端口或注册设置。数据库及 API 回滚镜像保留在服务器。
