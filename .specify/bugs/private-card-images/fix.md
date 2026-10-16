# 修复

新增 private_uploads.py，使用现有 current_user 校验 Session，再查询 collection_item 的 owner_id、deleted_at 和当前正反面 URL。GET/HEAD 均受保护；返回 FileResponse，保留图像 MIME 与 Range 支持，增加 Cache-Control: private, no-store 与 Vary: Cookie。拒绝子目录、无引用文件、符号链接和非图片后缀。main.py 移除 StaticFiles 挂载并注册新路由。

不修改 URL、前端图片组件、已有图片文件或卡牌数据库结构。后台本地文件读取不受影响。仅发布 main.py 和 private_uploads.py；部署前备份数据库并保留 before-private-images API 镜像。
