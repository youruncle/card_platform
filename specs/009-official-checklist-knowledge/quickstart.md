# Validation
使用项目.venv运行pytest apps/api/tests/test_official_checklists.py apps/api/tests/test_knowledge_card_catalog.py；本地运行同步命令生成进度账本并验证重跑不重复。--apply后查询发布版本card实体数量与586的Roses补全。运行scripts/replay_official_knowledge.py读取持仓并输出报告。知识库管理入口显示账本百分比与错误；失败产品不得计入成功。


## 浏览改造验证（2026-10-22）
API：.venv/bin/python -m pytest apps/api/tests/test_knowledge_browser.py apps/api/tests/test_knowledge_browser_api.py apps/api/tests/test_knowledge.py apps/api/tests/test_knowledge_card_catalog.py
Web：在apps/web运行 node --import tsx --test tests/knowledge-browser.test.cjs tests/official-checklist-progress.test.cjs，再运行npm run typecheck和npm run build。
Mobile：在apps/mobile运行npm test -- tests/knowledge.test.tsx，npm run build。
浏览：从/knowledge打开厂商/赛季目录、中文别名搜索、系列卡牌清单、卡牌详情、缺图提示、已有图片原图与侧别切换；确认/knowledge/manage保留原管理能力。手机通过Bearer获取图片Blob并全屏查看。核对未登录/普通角色拒绝、错误版本/媒体路径及缺失/损坏来源报错。
本机发布资料只读回放：核对总数、跨厂商/年份样本和实际图像字节；不抓取新资料、不更改发布版本。实际证据填入browser-verification.md。
