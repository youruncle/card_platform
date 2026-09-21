# Validation Guide

1. .venv/bin/python -m pytest -q apps/api/tests/test_portfolio_search.py
2. PYTHONPATH=apps/api/src:. scripts/test-e2e.sh （隔离数据库，不向真实卡库写测试数据）
3. .venv/bin/ruff check apps/api/src apps/api/tests && .venv/bin/python -m pytest -q
4. apps/web: node --import tsx --test tests/*.test.cjs；node node_modules/typescript/bin/tsc --noEmit；node node_modules/next/dist/bin/next build；node scripts/sync-standalone-assets.mjs。
5. 登录现有账号，核心持仓下输入杜兰特/KEVIN DURANT，对照同一集合；查 ID 519；高级筛选品牌、签字否、编号等，再清空。检查分页、空结果、窄屏。
6. 只读服务查询测耗时；重启本地 API/Web，确认 /health、/health/ready、首页和静态文件 200。
