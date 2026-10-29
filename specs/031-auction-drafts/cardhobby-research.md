# 卡淘拍卖草稿能力只读核验

核验时间：2026-10-30（北京时间）。范围为官方公开页面与脚本、云端当前唯一已连接账户的已有订单、商品页面和卖家表单 GET。没有调用登录、短信、卖家提交、图片上传、发布、定时发布或删除接口；没有更改数据库、会话存储、云端配置或手机设置。

## 结论

**发现了官方网页前端的独立草稿保存和读取代码，不能再表述为“未发现任何草稿接口”。但尚未验证可安全调用的完整写入、图片上传和回读契约，本版本远端草稿功能应保持不可用。** 当前只能承诺本站草稿保存和复制；不能显示“已保存卡淘草稿”。

官方[交易规则](https://www.cardhobby.com.cn/Market/Article?serviceAgreementType=3)描述商品刊登中的仓库选项，并区分卖家资格。此处的仓库不应自动等同于无需完整刊登信息的草稿。当前账号能够 GET [卖家录入页](https://cardhobby.com.cn/NewCommodity/CreateCommodity)，但可读取表单本身不能证明账号具备完整卖家写入资格。

## 官方代码中实际看到的契约

来源为官方 [util.js](https://cardhobby.com.cn/Scripts/axios/util.js) 和 [vue-CreateCommodity.js](https://cardhobby.com.cn/Scripts/axios/vue-CreateCommodity.js?v=20260821)。均真实下载并计算 SHA-256，没有仅凭搜索结果推断。

| 能力 | 官方前端映射与行为 | 本次验证范围 |
| --- | --- | --- |
| 独立保存草稿 | `POST /NewCommodity/SaveDraftCommodity`；提交 `FormData` JSON | 静态代码已确认，未写入 |
| 读取草稿 | `GET /NewCommodity/GetCommodityById?id={DraftId}&memberId={MemberId}` | 静态代码已确认，无合法现有草稿 ID 可回读 |
| 草稿列表 | `GET /NewCommodity/GetCommodityByPage` | 使用已存会话 GET 无查询参数，HTTP 200，正文非 JSON；未猜测分页参数 |
| 保存商品 / 仓库 / 发布 | `POST /NewCommodity/SaveCommodity` | 仅审查源码，未调用 |
| 删除草稿 | `POST /NewCommodity/DeleteDraftCommodity` | 仅发现映射，未调用 |

`formSubmit` 对 `SaveType=1` 与 `SaveType=2` 调用 `SaveCommodity`，其余分支调用 `SaveDraftCommodity`，注释将0标为草稿。草稿调用确实存在，并非只有孤立的接口字符串；但实际返回的表单按钮只有1与2，分别覆盖发布/定时发布和仓库路径，没有观察到0的可见按钮。因此不能将仓库按钮当作已验证的独立草稿入口。

草稿分支使用的字段包括 `MemberId`、`SaveType`、`Title`、`SolrTitle`、`Description`、`Id`、`DraftId`、`PropertyJson`、`NewCommunityImages`。这只是源码出现字段，不等于服务器必填字段全集或可安全最小载荷。客户端以 `Result` 和 `Msg` 判断草稿成功，把 `Msg` 写入 `DraftId` 并清空商品 `Id`；读取分支把 `Data` 合入表单。尚未读取真实草稿数据，无法确认草稿状态字段、所有权错误响应、版本语义、幂等性或保证绝无上架副作用。

请求工具默认发送 `application/json`，`Authorization` 默认空字符串，同域会话由浏览器携带。卖家 HTML 未发现 CSRF 隐藏字段，但这不能证明服务端无需其他校验。没有为核验调用任何令牌签发或认证写入。

生产图片上传代码使用 COS SDK `putObject`，临时上传凭据来自浏览器已有 `cosData` 或页面配置的令牌地址，图片字段写入 `NewCommunityImages`。URL、图片顺序、正背面标识需按真实契约验证。另有 `/upload/UploadImage?type=1` 的 multipart 代码，但源码明确将其作为本地测试分支，不能据此直接集成生产上传。未提取、输出或保存任何 COS 密钥。

## 本人订单样例与商品描述

云端已有完整订单归档当前为空/未完成（0页，无账户绑定），因此没有把空归档伪装为可用样例。随后直接在云端进程内恢复已有加密会话，只用 `CardHobbySession.request` GET 读取订单页；没有调用会进行重登或会话落盘的账户服务 `_read`。

订单页返回 HTTP 200，识别为本人订单表，当前页面解析171个商品行。本次只取前三个标题做结构统计，原文不保存到本报告或本地证据文件：长度分别96、83、97个字符；三者都包含赛季/年份、英文和中文，均出现卖家品相用语。此样本量只用于模板格式参考，不代表全站风格或标题长度限制。当前卡身份、品相、运输、累计和承诺不能从他人订单标题继承。

对应三个商品详情页均 HTTP 200，并显示商品描述入口及商品图；HTML 中 `content` 节点未直接提供描述正文，故没有把页面一般文字当作已完整提取的商品描述。本次未绕过页面脚本去猜测描述接口。基础模板应使用当前卡已确认身份和本人填写的品相备注，不复制卖家文案。

## 证据与可恢复步骤

本地只保留去标识统计、响应哈希及官方静态契约摘要，目录为 `.runtime/auction-drafts-20261030/`：

- `private-evidence.json`：云端归档为空/未完成的实际状态；数据库事务设为只读。
- `live-evidence.json`：已存会话订单页、三个标题结构、三个商品页和卖家页的响应哈希；不含订单号、商品号、价格、联系人、标题原文或会话值。
- `seller-form-evidence.json`：可见按钮绑定与静态表单结构；不含表单用户值。
- `draft-read-evidence.json`：精确已知草稿列表 GET 路径返回200非JSON，未声称取得草稿列表。
- `public-script-evidence.json`、`draft-interface-evidence.json`、`draft-contract-summary.json`：公开脚本哈希、接口映射和静态契约。

官方脚本捕获 SHA-256：

- `util.js`：`16b019a0032e9acf0027b92df592e0d83511ea0f6aa180d3fffb3baf9bf125dc`（48,681字节）。
- `vue-CreateCommodity.js?v=20260821`：`a25667a9e6e658dc170d10fe6bb23402e5acdf6522774957e578aed6904b85cc`（181,431字节）。

浏览工具无法打开上述脚本；使用直接 HTTPS GET 成功恢复捕获。现有云端会话工具读取其中一个静态脚本失败，亦由公开 GET 恢复，未反复登录。没有找到经核实的官方卖家草稿 SDK 文档；这与“官网前端没有相关能力”是不同结论。

后续接入需要在获得明确的卖家草稿验证范围后，确认完整最小载荷、当前账号卖家资格、图片上传授权与图片绑定、现有草稿列表参数、独立状态/所有权检查，以及写入后的同草稿回读。只能允许独立草稿端点；不得以 `SaveCommodity` 的仓库或发布参数替代，也不得把静态客户端成功判断冒充真实服务器验证。上述工作本次均未通过真实写入验收。
