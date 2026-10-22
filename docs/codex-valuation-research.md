# Codex逐卡估值研究

本流程用于外部Codex独立研究，不冒充正式卡淘provider。每卡主动搜索eBay和卡淘历史成交；缓存回放及0查询不算本次检索。卡片身份从当前正背图建立，已有库身份、采购成本及旧估值不能替代图证或市场价。

先用`PYTHONPATH=apps/api/src .venv/bin/python scripts/codex_sales_research.py prepare --identity <identity.json> --front <front> --back <back> --item-id <uuid> --out <private/research.json>`生成绑定SHA256和操作指令的研究包。不复制图片或写数据库，文件应保存在Git忽略私有目录。

按包内instructions实际检索两市场，保存执行日期、查询及响应capture_ref。无结果、403、超时、模型权限错误分别记录；两市场都搜过不意味着两市场都有可用成交。每条商品原文和卡面均保存本地捕获，card_images标出card_front/card_back、SHA256；不能拿通用条款图做实卡去重。

商品逐一记录：platform、url、checked_at、capture_ref、identity、identity_confirmed、images_reviewed、card_images、finish（target/candidate/comparable）、condition（target/candidate/comparable）、sale_status、bid_count、actual_price_known、price_basis、price、currency、sold_at、shipping、shipping_status、seller_id、lot=false、duplicate_check、serial_number/serial_total。作品名/球员、产品、赛季、子集、完整卡号、平行颜色与纹理、总编和评级必须核实。标题错误与实图结论应分别保留，不能自动把错误标题当视觉冲突。

挂牌、撤架、未知BestOffer金额、lot、异平行/异评级不进入同款成交。完整流水相同、同卡面图、同卖家寄售编号或再上风险只计一次；持仓本件历史单列。品相微瑕不得当完美品相价。原币种、原价、运费分列；汇率附base/quote/rate/date/url，无有日期来源不合并币种。

完成后运行`PYTHONPATH=apps/api/src .venv/bin/python scripts/codex_sales_research.py validate <private/research.json>`，检查本地捕获实际存在及卡面哈希。结构通过不能证明研究叙述真实，审核人仍须查看原始捕获。价格证据不足输出null，不凭经验或采购成本补价。当前合同以至少三条近期独立卖家同款公开成交提供观察区间，单市场阻塞须明示；公开落槌不等于取得付款流水。

业务写回须另有明确范围授权。历史Codex快照保留但不能当本轮新研究。`import_codex_multimodal.py`与`import_codex_investment_research.py`的旧硬编码区间已停用，禁止重新导入。正式估值的逻辑版本与研究合同版本分别记录。

公开卡淘直连研究须保存实际请求头与参数，沿用官网/正式provider的Accept、X-Requested-With和Referer。仅User-Agent的审核helper可能得到HTTP200/result=1的空模型：Keyword、Total、TotalCount、PageIndex均null，StatusType=0、列表空；这是查询未证实生效，不能记为市场无成交。有效响应也可能Keyword=null、StatusType=0，不能仅据这两字段误判。2026-10-19对照捕获显示相同参数配正式请求头可返回60条，原空壳捕获继续保留；新捕获单独记录，不能追认原请求成功。
