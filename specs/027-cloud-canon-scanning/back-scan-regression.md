# 正式背面扫描卡住

2026-10-21，用户截图：正面 8 张、背面 0 张，底部 0 对且无法继续。原批次 `c7623dc2` 云端只有前一次概览正面，没有背面 sheet 或配对请求。本机无残留扫描进程。

实际 Chrome 重新打开可复现：会话恢复正面，但设备连接状态需要重新连接；底部先展示 0 对。重新连接后点击背面正式 300 DPI，硬件完成，页面进入上传并长期等待。网页容器日志明确：`Request body exceeded 10MB ... /sheets/back. Only the first 10MB will be available unless configured.` 没有云端背面写入，不属于配对算法卡住。

Next 15.5.20 的实际 `getCloneableBody` 默认裁断 10 MiB，复制体长度小于 HTTP Content-Length，API 仍等待剩余 multipart。相同的 16 MiB multipart 回归修前收到 10,485,760 bytes，修后长度与 SHA 完整一致。

最小修复：设置 Next `experimental.middlewareClientMaxBodySize=100mb`，容纳 API 整版图默认 80 MiB 限制及 multipart；上传最多等待 180 秒，超时保留本机扫描图供重传；只有正面时展示等待背面及就近连接按钮，不提前显示 0 对入库结果。

保留线上 fusion2 API；网页基线是现运行镜像 revision `118892de`，在其独立工作树增量构建。没有修改云端卡库，也没有代替用户确认正背身份或提交入库。

## 发布与真实样本验证

网页 `0.6.3-20261021-canon2` 已部署，源码 `d69a0e1fda822a4e9c71e89b4ea001c4d66109fb`；实际镜像 `sha256:7693c17510f81a7365b6b77f191f9e9d0d4389351e79805f61661bb1ab963655`。仅替换 Compose 的网页镜像行。运行 API 容器 ID 与镜像均未变化，保持 fusion2、healthy；`.env.cloud` SHA 未变，实际环境和 settings 均为 worker=true、deepseek-flash、thinking=false。

- 真实背面扫描：300 DPI、2480×3507 PNG，13,574,879 bytes，SHA `6b41cc025bd6b1b78ed997edfc217cde14a2e3fa4f19ba043cd4b0bca8ed96ad`。已目视确认整版为 8 张背面，并从页面保存原图至本机 Downloads；发布后点击原页面“重试上传扫描图”，没有二次取图。
- 云上传 HTTP 201、配对 HTTP 200，后端保存的原图 SHA 与页面下载一致；分辨率为 actual/effective/requested=300、scan、verified_native_dimensions。正面仍是先前概览 200 DPI，没有混淆两次采集参数。
- 原批次现在正面 8 区域、背面 8 区域、8 条选中配对，浏览器实际显示“确认配对并录入 8 张卡片”，按钮可用。配对身份全部 unknown，仍需人工核对；没有确认或提交，卡库仍 877 张。
- 166 项网页测试通过；其中新 3 项（大图克隆、上传超时、仅正面恢复）均先观察失败再通过。Next 15.5.20 生产构建与类型检查通过，独立审查无 Critical/Important。
- 候选 Linux 网页通过实际 Next rewrite 转发 16,777,330 bytes multipart，接收端长度与 SHA 相同；102 个运行文件校验通过。首页、10 个初始资源及发布后 Canon 延迟 chunk HTTP 200。
- 候选测试容器和专用网络已清理；私有原图、构建/测试输出留本机 `.runtime/canon-back-20261021/`，远端发布包/清单及仅镜像回退信息留 `releases/0.6.3-20261021-canon2/`。主工作区同步同一修复，保留无关未提交修改。

## 验证界限

未验证真实 600 DPI、最终确认入库或身份字段正确性；8 对生成不代表身份已一致。未运行 API 全量测试或手机构建安装：本轮无 API/手机源码改动。本次真实背面已验证取图→上传→裁切数量→配对结果，人工核对和提交由用户继续。
