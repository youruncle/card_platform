# 两端共享代码

Web 与 iPhone 客户端直接编译本目录的 TypeScript 源码。此目录不依赖 React、Next、Capacitor、浏览器全局状态或任何凭证存储。

- `src/contracts.ts`：共享 API 响应类型。Web 的 `lib/api.ts` 保留兼容导出；Mobile 的页面类型从同一契约选择字段，不再维护另一套卡片/账户/识别字段定义。
- `src/recognition.ts`：人工确认使用的局部识别输入，允许尚未确定的字段缺失。
- `src/confirmation.ts`：Web 添加卡片和 Mobile 确认入库共享的身份字段转换、编号/赛季校验和签字信息处理。
- `src/search.ts`：两端共用搜索条件类型、表单解析和标签。

后端仍是权限、持久化、身份规范化、识别与估值的唯一权威。此处的表单校验用于及时提示，不能替代后端验证。不同用例的记录创建方式（桌面即时添加、手机任务确认）保留独立入口，但它们的身份载荷复用同一实现。

各端独立：Web 的 Cookie、Next 代理、扫描仪与批量管理；Mobile 的 Bearer、Keychain、原生 HTTP、相机、相册和 IndexedDB 草稿。不共享有平台副作用的代码，不跨应用导入组件。

修改公共字段或规则后必须运行 `scripts/check-client-parity.sh`。这验证共享行为、两端测试及两端生产构建；仍须按变更补充后端接口测试。契约目前是人工维护的 TypeScript，尚未建立 OpenAPI 自动生成，不声称类型检查能验证所有服务端运行时响应。

Docker Web 构建上下文现在为仓库根目录，例如 `docker build -f apps/web/Dockerfile .`；Dockerfile 专用 ignore 限制上下文，只传 Web 与共享源。生产/隧道 Compose 和空库打包脚本均已同步。不要复制一份共享源码进手机项目。

第二阶段补充：`capture.ts`统一面别、源图/配方、幂等表单、批量分组和私有图片尺寸路径；`sorting.ts`统一全库排序枚举和快照参数；`contracts.ts`中的CaptureJob允许缺失侧，ScannerRecognitionJob保持旧双面类型。正式检测、裁切应用、排序投影与权限仍由同一个API负责。相机帧与本地照片处理属于设备适配，不能另写身份/估值规则。

`supplement.ts`统一补拍后的主身份差异展示和显式勾选合并，默认只补图片。未勾选字段保留原值，已有审核、绑定或估值记录的身份修改仍由服务端限制到原有审核流程。
