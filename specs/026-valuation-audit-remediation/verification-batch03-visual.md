# 第三批视觉召回接续验证 — 026

2026-10-19。统一最终候选取得有限召回改善：同一19组原图中，10组正常对照有5组通过当前正式T0，9组异版/缺证负向对照全部rejected。重新执行的真实生产v7基线19组全部拒绝。**#261仍在独立固定颜色复核处误拒，第三批整体优化及下一批接续尚未放行。** 本文接续verification-batch03.md，不将先前工程发布等同模型问题完成。

## 范围与实际修复

实际生产valuation5的183份Python源码只有comparable_image_verifier.py变化。重排主提示为逐面独立观察，明确正面证据与背面文字的职责、not_printed与not_visible、完整背号/语言及金属反光；给真实上传图片紧邻image_ref标签。独立正面复核仍盲于标题、身份与首轮结论，增加逐区域区分可见油墨边界与反射的观察步骤及两张front标签。primary temperature=0。

模型观察合同由v7变为`paired-image-only-evidence-v9`以失效旧提示缓存；独立合同仍v2，formal仍v21。normalize_model_visual_decision、VersionMarkerPair、build_visual_match_facts、独立establishes_same及formal门均未修改；未知不提升same，不按ID、标题、产品、gold≈orange等映射兜底。没有新增生产模型调用、知识导入、估值、身份写回或市场研究。

最终源SHA256：`82ca4c56587f126c64872c04445cdbb2a8afb50fee738d6db1392fddde030bda`。生产发布须用同一SHA；私有目录`.runtime/releases/20261019-valuation-batch03-visual/`。

## 真实回放及证据边界

最终`verified-v9/`完整19组、26次实际qwen3-vl-plus调用；每次保存去除base64后的真实请求、完整原始provider响应、request/response SHA、模型、时间、实际上传压缩字节SHA和原图SHA。与重新执行的`baseline-v7/`19次原始响应分别保留，已复算所有请求/响应SHA。上传按现有max1536/JPEG90流程，上传字节SHA与原图SHA分列，不能假称原始字节原样发送。

使用未改动的冻结dossier身份及实际图片SHA，按market_comp.market_evidence_id取唯一原provider，携带真实几何和当前condition_review。没有原provider不重造。冻结JSON的NUMERIC字段source_reliability和match_confidence还原为真实仓储Decimal类型。该只读harness执行实际内置provider/classification/formal逻辑，但knowledge_runtime=None，未加载线上发布知识/人工权限全集，也不证明完整端到端估值或字段/OCR准确率。

| 样本:原case索引 | 对照 | 主比较 | 独立复核 | 当前绑定facts | 正式结果 |
|---|---|---|---|---|---|
| 180:0 | negative | different | not_called | False | rejected |
| 226:0 | normal | same | different | False | rejected |
| 231:0 | negative | different | not_called | False | rejected |
| 233:0 | normal | different | not_called | False | rejected |
| 243:0 | negative | same | different | False | rejected |
| 247:0 | negative | uncertain | not_called | False | rejected |
| 253:0 | negative | uncertain | not_called | False | rejected |
| 261:0 | normal | same | different | False | rejected |
| 292:0 | normal | same | same | True | T0 |
| 294:0 | negative | different | not_called | False | rejected |
| 344:0 | normal | different | not_called | False | rejected |
| 344:2 | negative | uncertain | not_called | False | rejected |
| 830:0 | normal | different | not_called | False | rejected |
| 836:0 | normal | same | same | True | T0 |
| 858:0 | normal | same | not_called | True | T0 |
| 860:0 | negative | different | not_called | False | rejected |
| 861:0 | normal | same | same | True | T0 |
| 865:0 | normal | same | same | True | T0 |
| 880:0 | negative | uncertain | not_called | False | rejected |

独立审查实际看过5组双方正背共20张原图，确认正常通过为#292、#836、#858、#861、#865。#836标题“轻印”原样保留，模型condition将候选裸卡说成封装内并不准确，不能据本轮同款准入证明细微品相；#865版权©2025不自动证明发行赛季。#253属于缺少候选背图的缺证负向，不能说已证明异版。5/10仅是此固定小样本结果，不外推总体准确率、成交真实性、价格或字段正确率。#226存储卡号30与实图18、#233存储75与实图2、#858旧产品/版本、#865旧赛季等已知冻结字段差异仍未写回；T0候选准入不等于旧身份已修复。

#261两正面实际相同设计/纹理，主比较已same，独立模型仍把候选金棕底边与目标橙底边当固定油墨冲突。v8和v9均真实复现该阻塞；v9响应自己记录虹彩、layout/texture/artwork均same，却以底边与姓名栏边界对齐证明fixed_color different。照片中区域几何边界不能单独证明色相来自固定油墨；本轮保持拒绝，不将该判断改写为same。其余正常误拒#226、#233、#344:0、#830也保留。后续需固定材质/多角度对照与负例矩阵，当前不宣称已解决这些样本。

## 旧证据更正与试验记录

原第三批旧replay-models.py未保存provider原始响应；traced_completion只记元数据，不能恢复不存在的raw。旧harness实际覆盖研究后的身份，而不是它宣称的独立冻结身份，并漏掉condition_review及几何；因此旧19组只保留为历史，不能冒充本轮严格基线。

#344原case0（130739831）背号GP1，是正常对照；case1（130765134）和case2（129320364）背号G1，是负例。原19固定集合含case0与case2。本轮纠正来源顺序，不重写封存review，不用高几何分数证明GP1/G1相同。

按时间保留全部试验：`baseline-v7/`（19次）、`prompt-probe/`（短主提示/标签试验，实际每份request为准）、`final-v8/`（初版统一提示）、`verified-v8/`（恢复原保护及not_printed澄清，19组25次）、`verified-v9/`（最终统一提示，19组26次）。`final-v8`的#836曾因harness把DB NUMERIC留作str而在最终分类抛TypeError；修正harness类型后重新完整回放，旧失败留存，不当产品故障。旧v8的3个T0及probe一次#261成功不作为最终候选成功证明。另有`independent-probe/`三组诊断追加提示试验：#261这轮主比较uncertain，未触发独立观察；#243形成facts但仍被实际formal的分母/版本约束拒绝；#292 T0。追加提示未用于本次源码或发布，不能代替统一最终19组，也不能声称该试验修复#261。

## 回归及独立审查

新增7项测试先失败后修复：主/独立真实上传标签、盲观察不泄漏标题/绑定、v7旧缓存不适用、背号/语种/分母/固定纹理冲突不能被same摘要覆盖。范围180 passed；独立Git树导出并叠加实际生产基线183源后181 passed（含原年份保护测试）；独立审查75项视觉定向passed，另年份保护1 passed。隔离PostgreSQL17 passed在提示最后调整前执行；此轮未改DB代码/迁移。

较广API在v8阶段以maxfail=120运行，1725 passed、120 failed、125 skipped后停止，未跑完158k总收集。120个观察节点在未修改实际valuation5基线为119 failed/1 passed；唯一差异是提示将“统计年份不是发行年份”写为同义的“生涯统计年不是发行年”。测试现兼容两种等价明确保护，源提示不变。最终v9候选逐项重跑120节点同为119 failed/1 passed，与基线失败集合完全一致。初版更早还遗漏两个关键提示保护测试，已恢复并独立验证；不隐藏初版失败。不能声称完整API全绿。

独立review完整核对原图、FK、请求/响应SHA、绑定、正常/负向及实际准入，最终报告留于私有review目录。最终v9对363条历史保留案例重新执行纯门回放并独立核验：全部rejected，27条缺provider继续关闭，不伪称这些旧记录证明正常召回。部署事实另见deployment-batch03-visual.md。

第三批200份价格仍null，109身份/绑定研究未解、4缺背、2受阻来源、864评级缺标签均保留。原封存6730文件不改；剩余277未启动。手机沿用唯一iphone等待：1.1.10(14)已安装，启动待解锁，本轮服务端提示改动不打新包，不声称手机启动已验证。

## 本轮观察到并在真实生产基线重现的119失败

- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[payload0-None-empty]`
- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[payload1-None-succeeded]`
- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[None-error2-failed]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_missing_card_kind_never_hides_later_hard_version_conflict[explicit-technology-parallel]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_base_and_explicit_colour_parallel_never_cross_match[target-base-seller-blue]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_terse_same_player_season_product_sale_remains_image_reviewable[court-kings-without-maker-or-sport-suffix]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_terse_same_player_season_product_sale_remains_image_reviewable[topps-chrome-without-sport-suffix]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_terse_same_player_season_product_sale_remains_image_reviewable[mosaic-without-sport-suffix]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_exact_sales_remain_usable_across_product_families[kobe-all-heart]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_exact_sales_remain_usable_across_product_families[mosaic-jam-masters]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_exact_sales_remain_usable_across_product_families[topps-chrome-fanatical]`
- `apps/api/tests/test_cardhobby_cross_brand_conflict_scan.py::test_target_product_or_bare_variant_words_are_not_other_insert_claims[identity_updates1-1998 SkyBox Metal Universe Jerry Stackhouse \u7ecf\u5178\u8001\u5361 \u7279\u5361 46]`
- `apps/api/tests/test_cardhobby_provider.py::test_runtime_query_budget_reserves_reviewed_knowledge_aliases`
- `apps/api/tests/test_cardhobby_provider.py::test_bowman_retrofractor_seller_vocabulary_is_exact_candidate`
- `apps/api/tests/test_cardhobby_provider.py::test_stained_glass_chinese_seller_alias_is_an_exact_insert_match`
- `apps/api/tests/test_cardhobby_provider.py::test_donruss_catalog_sport_suffix_may_be_omitted_by_seller`
- `apps/api/tests/test_cardhobby_provider.py::test_unanchored_target_keeps_plain_same_product_sale_as_analog`
- `apps/api/tests/test_cardhobby_provider.py::test_unnumbered_insert_rejects_explicit_red_diamond_parallel`
- `apps/api/tests/test_cardhobby_provider.py::test_checkerboard_finish_is_not_a_base_card_comparable`
- `apps/api/tests/test_cardhobby_provider.py::test_named_technology_finish_is_not_a_base_card_comparable[\u53d8\u5f62\u866b\u6298]`
- `apps/api/tests/test_cardhobby_provider.py::test_named_technology_finish_is_not_a_base_card_comparable[\u9f99\u9cde\u6298\u5c04]`
- `apps/api/tests/test_cardhobby_provider.py::test_named_technology_finish_is_not_a_base_card_comparable[\u94f6\u8109\u51b2\u6298]`
- `apps/api/tests/test_cardhobby_provider.py::test_named_technology_finish_is_not_a_base_card_comparable[Amoeba]`
- `apps/api/tests/test_cardhobby_provider.py::test_named_technology_finish_is_not_a_base_card_comparable[Dragon Scale]`
- `apps/api/tests/test_cardhobby_provider.py::test_player_alias_can_match_and_generates_broad_query`
- `apps/api/tests/test_cardhobby_provider.py::test_team_checklist_accepts_common_cardhobby_misspelling`
- `apps/api/tests/test_cardhobby_provider.py::test_nba_hoops_basketball_matches_common_cardhobby_seller_title`
- `apps/api/tests/test_cardhobby_provider.py::test_multi_player_query_uses_individual_short_aliases_and_strict_gate`
- `apps/api/tests/test_cardhobby_provider.py::test_ud_game_floor_seller_alias_is_exact_insert_evidence`
- `apps/api/tests/test_cardhobby_provider.py::test_signature_class_star_cast_seller_title_is_exact_identity`
- `apps/api/tests/test_cardhobby_provider.py::test_unicode_player_name_and_hoopnotic_use_short_seller_queries`
- `apps/api/tests/test_cardhobby_provider.py::test_rookie_focus_short_title_is_exact_only_with_full_identity_anchors`
- `apps/api/tests/test_cardhobby_provider.py::test_named_maze_finish_is_not_accepted_as_rookie_focus_base`
- `apps/api/tests/test_cardhobby_provider.py::test_coloured_cracked_ice_without_zhe_suffix_is_still_a_parallel`
- `apps/api/tests/test_cardhobby_provider.py::test_sparse_autograph_insert_is_retained_only_as_an_image_review_candidate`
- `apps/api/tests/test_cardhobby_recall_budget.py::test_full_page_does_not_hide_second_page_after_three_title_matches`
- `apps/api/tests/test_cardhobby_recall_budget.py::test_page_budget_includes_second_pages`
- `apps/api/tests/test_cardhobby_recall_budget.py::test_full_pages_do_not_starve_later_query_families`
- `apps/api/tests/test_comparable_budget_anchors.py::test_twelve_weak_image_candidates_cannot_starve_explicit_insert_proxy[Topps Chrome Update-Clutch Gene-CG-10]`
- `apps/api/tests/test_comparable_budget_anchors.py::test_twelve_weak_image_candidates_cannot_starve_explicit_insert_proxy[Donruss-All Heart-3]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5f53\u9009FMVP-High Honors-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5f53\u9009FMVP-High Honors-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5f53\u9009FMVP-Victory Portraits-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5f53\u9009FMVP-Victory Portraits-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u6367\u676f-High Honors-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u6367\u676f-High Honors-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u6367\u676f-Victory Portraits-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u6367\u676f-Victory Portraits-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5956\u676f-High Honors-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5956\u676f-High Honors-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5956\u676f-Victory Portraits-False]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_explicit_insert_name_outweighs_short_descriptive_alias[\u5956\u676f-Victory Portraits-True]`
- `apps/api/tests/test_comparable_insert_normalization.py::test_source_title_retains_exact_insert_despite_award_description`
- `apps/api/tests/test_comparable_version_texture.py::test_persisted_visual_result_needs_current_target_and_identity_binding`
- `apps/api/tests/test_direct_cardhobby_evidence_persistence.py::test_direct_and_refresh_normalizers_preserve_identical_sale_facts`
- `apps/api/tests/test_full_set_valuation_gate.py::test_normal_raw_card_calculation_must_run_final_source_gate`
- `apps/api/tests/test_graded_card_baseline.py::test_same_card_ungraded_sale_reaches_low_confidence_pricing_without_shipping_or_grade_premium`
- `apps/api/tests/test_graded_card_baseline.py::test_untyped_t2_keeps_existing_analog_policy_and_same_grade_sales_take_precedence`
- `apps/api/tests/test_graded_card_baseline.py::test_same_card_basis_outweighs_high_price_ordinary_t2_analogs`
- `apps/api/tests/test_hierarchical_valuation.py::test_2000_fleer_showcase_base_allows_intrinsic_holofoil_wording`
- `apps/api/tests/test_hierarchical_valuation.py::test_rc_parallel_is_rookie_and_matching_serial_total_is_not_card_number`
- `apps/api/tests/test_hierarchical_valuation.py::test_nba_hoops_catalog_suffix_does_not_reject_seller_title`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u5c04\u7ebf\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u5f69\u8679\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u788e\u95ea\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u788e\u70b9\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u7d2b\u70b9\u70b9\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u6ce2\u7eb9\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_unnamed_insert_parallel_rejects_chinese_variant_vocab[\u68cb\u76d8\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_bowman_seller_short_name_reaches_visual_base_classification`
- `apps/api/tests/test_hierarchical_valuation.py::test_high_confidence_compatible_visual_can_confirm_unnumbered_insert`
- `apps/api/tests/test_hierarchical_valuation.py::test_geometry_verified_template_can_replace_omitted_insert_seller_text`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_and_explicit_base_promote_missing_card_number_to_t0`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_promotes_intrinsic_encore_holofoil_base_to_t0`
- `apps/api/tests/test_hierarchical_valuation.py::test_sweet_stroke_material_rejects_other_sweet_sigs_inserts`
- `apps/api/tests/test_hierarchical_valuation.py::test_collin_three_window_uses_text_and_visual_identity_gates`
- `apps/api/tests/test_hierarchical_valuation.py::test_prefixed_card_number_ignores_unrelated_seller_suffix`
- `apps/api/tests/test_hierarchical_valuation.py::test_bowman_retrofractor_allows_intrinsic_silver_finish`
- `apps/api/tests/test_hierarchical_valuation.py::test_unknown_knowledge_insert_falls_back_to_legacy_aliases`
- `apps/api/tests/test_hierarchical_valuation.py::test_bowman_retrofractor_rejects_explicit_gold_numbered_parallel`
- `apps/api/tests/test_hierarchical_valuation.py::test_generic_refractor_word_does_not_reject_matching_insert_finish`
- `apps/api/tests/test_hierarchical_valuation.py::test_contenders_optic_base_insert_accepts_silver_finish_but_rejects_cracked_ice`
- `apps/api/tests/test_hierarchical_valuation.py::test_base_card_without_insert_can_be_t0`
- `apps/api/tests/test_hierarchical_valuation.py::test_optional_manufacturer_and_sport_wrappers_match_parent_product`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_coloured_technology_parallel_is_not_treated_as_generic_finish`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_can_resolve_ambiguous_silver_wording_for_a_base_card`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_colour_finish_stays_a_hard_parallel_conflict`
- `apps/api/tests/test_hierarchical_valuation.py::test_checkerboard_finish_stays_a_hard_parallel_conflict`
- `apps/api/tests/test_hierarchical_valuation.py::test_named_technology_finish_stays_a_hard_parallel_conflict[\u53d8\u5f62\u866b\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_named_technology_finish_stays_a_hard_parallel_conflict[\u9f99\u9cde\u6298\u5c04]`
- `apps/api/tests/test_hierarchical_valuation.py::test_named_technology_finish_stays_a_hard_parallel_conflict[\u94f6\u8109\u51b2\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_named_technology_finish_stays_a_hard_parallel_conflict[Amoeba]`
- `apps/api/tests/test_hierarchical_valuation.py::test_named_technology_finish_stays_a_hard_parallel_conflict[Dragon Scale]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base \u7ea2\u94bb\u77f3\u6298]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base Silver Refractor]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base Gold Foil]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base Refractor 12/25\u7f16]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base \u6298\u5c04 Twilight SSP]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[\u661f\u5ea7\u5e73\u884c Base \u6298\u5c04]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[\u6708\u5149\u7c89 base \u5341\u4e8c\u5bab]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[zodiac\u5e73\u884c Base Refractor]`
- `apps/api/tests/test_hierarchical_valuation.py::test_explicit_base_does_not_override_material_or_named_variant[Base \u94f6\u95ea\u7248 \u84dd\u5e73\u884c]`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_cannot_resolve_an_explicit_coloured_parallel_as_base`
- `apps/api/tests/test_hierarchical_valuation.py::test_rookie_focus_short_alias_is_exact_with_player_season_and_product`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_and_card_code_turn_seller_year_disagreement_into_t1`
- `apps/api/tests/test_hierarchical_valuation.py::test_geometry_can_audit_but_not_hide_an_explicit_parallel_conflict`
- `apps/api/tests/test_hierarchical_valuation.py::test_strong_geometry_and_exact_text_demote_false_visual_conflict_to_t1`
- `apps/api/tests/test_hierarchical_valuation.py::test_reviewed_marketplace_insert_alias_reaches_comparable_classifier`
- `apps/api/tests/test_hierarchical_valuation.py::test_unicode_season_dash_and_chinese_player_alias_match`
- `apps/api/tests/test_hierarchical_valuation.py::test_base_target_rejects_explicit_parallel_variant`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_promotes_typed_chrome_autograph_when_seller_omits_code`
- `apps/api/tests/test_hierarchical_valuation.py::test_same_image_promotes_distinctive_insert_when_seller_omits_insert_name`
- `apps/api/tests/test_hierarchical_valuation.py::test_chrome_autograph_insert_does_not_treat_silver_finish_as_parallel`
- `apps/api/tests/test_hierarchical_valuation.py::test_omitted_insert_needs_high_confidence_same_template_to_be_exact`
- `apps/api/tests/test_identity_evidence_regressions.py::test_reported_case_field_roles_and_bounded_discovery[862]`
- `apps/api/tests/test_image_service.py::test_card_vision_service_combines_front_and_back`
- `apps/api/tests/test_market_data_repository.py::test_insufficient_refresh_revaluation_requires_retained_evidence`
- `apps/api/tests/test_market_data_repository.py::test_adopt_reviewed_evidence_moves_only_explicit_acceptances`
- `apps/api/tests/test_parent_child_image_discovery.py::test_parent_and_child_stay_distinct_but_strong_anchors_can_request_image`


材料接续口径更正：原T027最低固定样本条件已由v9的5个真实正常成功及全部负向对照关闭满足，不要求隐含10/10召回。剩余五例的已证实观察错误、物性unknown、原商品补查与具体输入缺口见[verification-batch03-material.md](verification-batch03-material.md)。本报告此前“T027未通过”的管理口径由该接续记录更正；不把剩余缺陷说成已修，不自动放行下一批。
