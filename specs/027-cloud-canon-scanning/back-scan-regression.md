# 正式背面扫描卡住

2026-10-21，用户截图：正面 8 张、背面 0 张，底部 0 对且无法继续。原批次 `c7623dc2` 云端只有前一次概览正面，没有背面 sheet 或配对请求。本机无残留扫描进程。

实际 Chrome 重新打开可复现：会话恢复正面，但设备连接状态需要重新连接；底部先展示 0 对。重新连接后点击背面正式 300 DPI，硬件完成，页面进入上传并长期等待。网页容器日志明确：`Request body exceeded 10MB ... /sheets/back. Only the first 10MB will be available unless configured.` 没有云端背面写入，不属于配对算法卡住。

Next 15.5.20 的实际 `getCloneableBody` 默认裁断 10 MiB，复制体长度小于 HTTP Content-Length，API 仍等待剩余 multipart。相同的 16 MiB multipart 回归修前收到 10,485,760 bytes，修后长度与 SHA 完整一致。

最小修复：设置 Next `experimental.middlewareClientMaxBodySize=100mb`，容纳 API 整版图默认 80 MiB 限制及 multipart；上传最多等待 180 秒，超时保留本机扫描图供重传；只有正面时展示等待背面及就近连接按钮，不提前显示 0 对入库结果。

保留线上 fusion2 API；网页基线是现运行镜像 revision `118892de`，在其独立工作树增量构建。没有修改云端卡库，也没有代替用户确认正背身份或提交入库。

发布与真实背面回放结果待补充。
