# 审计产物数据契约（设计）

这些是评估文件的逻辑结构，不是生产表迁移。当前交付的export_baseline.py和JSON为初版；逐阶段trace在R1实现。使用 `schema_version` 与 `run_id` 区分版本，不把旧字段缺失回填为零。

## AuditRun

字段：run_id、schema_version、frozen_at（带时区）、source_policy=cardhobby_only、scope_hash、git_head、dirty_file_hashes、config_fingerprint（非密钥）、identity/alias/model/prompt/gate/pricing版本、cache_policy、budget、artifact_hashes、started_at/finished_at。

输出仅在work/新目录，不覆盖旧运行；离线不联网，shadow只读数据库。owner标识仅在私有运行中使用，不出现在仓库摘要。公开报告不包含cookie、凭据、采购内容和卖家个人联系方式。

## CardBaseline

键：run_id+collection_item_id；报告用display_id。

字段：identity_projection、confirmed_identity及版本/revocation、scanner_identity及其usage、valuation_input_identity、原正背图路径/哈希、latest_job、latest_snapshot（历史独立保存）、effective_status、current_value、never_had_snapshot、used/rejected_candidate_ids。

规则：证据不足时current_value=null，即使historical_snapshot有价；字段缺失不等于False；effective_status遵从系统“最新估值晚于任务则成功”的时序逻辑。解析最终身份时沿用worker优先级，禁止使用purchase_price建立参考估值。

## CandidateEvidence

键：provider=cardhobby + item_id + source_content_hash；URL主域/www/大小写变体按商品ID归并，distinct item_id不保证独立实卡。

字段：query_id、raw_response_ref、fetched_at、http_status、title、description、front/back/detail_image_refs及SHA256、ended_at、bid_count、price_cny、shipping_cny、sale_type、sale_state、payment_proof、seller_pseudonym、duplicate_physical_card_group、raw_field_paths。

sale_state枚举：confirmed_transaction / ended_auction / active_listing / no_bid / unknown。
规则：ended_auction须过去带时区结束时间、bid_count>0、price>0、auction；不得从状态或卖家催款文字推断已付款。来源截图/正文不可取得时为unavailable，而不是空正文。价格正文和近似美元折算单独字段，运费不计入card_value。

## StageTrace（R1拟实现）

键：run_id+card_id+stage+attempt+candidate_id/query_id/call_id。

stage固定七类：image_recognition / identity_resolution / alias_query / retrieval / same_card / sale_confirmation / pricing。
status：not_run / succeeded / failed / blocked / unknown；reason_codes为数组。
字段：input/output_hash、artifact_refs、started_at、elapsed_ms（单调钟差值≥0）、retry_count、cache_hit、provider、model、prompt_version、input/cached/output_tokens、usage_missing_reason、cost_amount、cost_currency、price_version、budget_stop_reason。

费用=按对应价格版本逐调用计算后求和；缓存token按提供商定义拆分；usage缺失则cost=null，不用0。查询回包先留存再解析，解析失败保留行级原因；每张卡请求尝试与成功分开计数。总排队、执行、人工审阅耗时分别汇总。

## IndependentReview

字段：review_id、reviewer_method、blinded_to_system_value、target_hashes、candidate_hashes、observed_fields（value/side/region/text_role/confidence）、identity_verdict（same/different/uncertain）、condition_compatibility、sale_verdict、reference_status、reference_interval、observed_price_range、reasons。

reference_status：sufficient_public_auction / undetermined / not_reviewed。reference_interval为CNY [L,H]且0<L≤H；仅充分标签可非null。历史观测范围允许单点，但不能当作参考区间。新增市场记录不自动继承旧same。

## SampleSet / Metrics

固定sample-set.json记录60张卡、分层、development/holdout（40/20），已看14张均在development。按card_definition分组防止泄漏；R1人工核对后若发现跨definition同一实卡/同身份，则合组并在新版本划分，不能回头调原测试结果。

同一版本D/U冻结后比较；异常价格阈值与区间算法在解盲前固定。报告实际n、unknown、正确/错卡、额外成本。案例挑选带目的性，不能把14张定向样本推算账户错误率。
