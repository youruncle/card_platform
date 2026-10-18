# 第三批复现、修复与验证 — 026

2026-10-19（Asia/Shanghai）。固定第三批200张，累计600/877；不启动第四批。没有批量业务写回或新估值、知识导入。研究证据仍为91份insufficient_evidence、109份research_incomplete，200份独立价格均null；不报告价格误差或准确率。

## 复现依据与范围

实际读取线上valuation4，183个Python源码SHA与本地完全一致；Git HEAD不是该运行基线。保存原源码、主index哈希和脏树清单。封存审核目录只读。最终27个原provider缺失病例为205×3、208×11、247×8、248×4、331×1，未用审核叙述重建载荷。

全部363条保留来源：旧T0 170、T1 57、T2 128、拒绝8；当前v21最终363条均拒绝。281条没有视觉binding，82条均非v7；current paired facts=0。冻结输入缺confirmed_identity_version_id，不能把identity SHA不等单独归因真实身份变更。中间classifier或provider分层不等于正式价格资格。内置resolver离线纯函数回放不等于加载生产知识、人工权限及真实视觉模型的新请求。

当前原始识别载荷复现：206的24在同次caveats明确球衣角色却仍保留；修后null，不猜15。232原30继续剔除；同原证据下提案18修前null、修后18，属于保留真实卡号能力的反事实控制，不冒称新OCR。224原15仍未解决，未猜2。位置词已被当前正式worker清理，本次provider入口加固与其分别陈述。818真实job输入的罕用别名查询缺口通过现有首选短别名、单双主体插卡lane及预算整合修复。

## 真实原图模型与正常对照

19组保存原始正背和候选画廊实际调用当前模型：180、231、243、247、253、294、344第一/第三来源、880、860、226、233、261、292、830、836、858、861、865。逐图SHA和实际上传支持图绑定保留，无新市场研究、数据库写入或图绑定恢复。视觉源码/合同仍v7。

5组different、14组uncertain，全部最终拒绝。180/231/247/294的冲突与审核相容；正常261被模型将反光误判固定颜色/纹理，仍未解决。344第三GP1及其他正常配对继续uncertain；模型仍可能忽略背号/语言或使用“同上”及背面描述代替独立正面描述，被现门关闭。本次不放宽门槛补价，也不宣称自动模型正常召回已通过。正常同款能力由既有当前绑定语义夹具另行验证，不能把该夹具当真实模型成功。

## 独立性与研究边界

独立性方向42条现有原载荷（41保留+270一条已有补查）当前全部拒绝；41次关联按同URL/external ID去重38。10条补查原HTML因主价原币种未知不产sale，9条缺原provider不造。未改独立性产品代码；新增24项语义回归保留不同分子、条款图、照片复用风险、同卖家寄售代码、本件历史、T2已披露代理与explicit same区别。

131旧Codex版本与258可绑历史面不等于读字正确；4个旧背因当前缺背不能绑定，未偷用历史背面。306/308/311/312当前缺背，206/326各一来源受阻，864库PSA9无当前标签图证。109合同未解、币种/品相/独立卖家不足和全部null价格保持。原分区2旧回执被覆盖的限制沿用最终200份CLI依据，不补造历史stdout。

## 自动验证

最终工作树范围测试173 passed；独立审查109 passed、5组真实KnowledgeResolver运行查询探针通过（只替代远端为空响应，检验真实查询算法与诊断，不冒充新市场搜索），修复两个运行整合P2及单人别名P3后无未解发布阻碍。新增第三批51项回归（27角色/查询、24独立性）。最终候选源码再次回放363条，最终分层与修前完全一致，独立验证errors=[]。独立导出采用暂存任务测试加实际生产183源码及本次三文件叠加，173 passed；未从旧HEAD整仓构建。image_service提交仅包含角色函数差异，已在线但不属于本任务的正背识别改动未夹带提交，构建仍基于实际生产保留这些代码。隔离PostgreSQL完成全部迁移后，相关状态、成本知识、正背入库17 passed。没有连接生产数据库跑产品测试。

全量API调用实际启动后中止：1478 passed、119 failed、108 skipped、1 warning，189.25秒；收集约158230项，其中shared_player_aliases_and_rejection_audit为155062项。完整套件未跑完。119个已观察失败以导出的未修改生产183源文件逐项复跑，119个同样失败。未把旧首批138项或第二批32项已披露失败说成修复。审查修复前扩大查询/识别7文件对照27 failed、216 passed，修前生产与当时候选失败集合一致；不冒称最后查询整合修改后的全量结果。最终查询筛选4 failed、38 passed，与未修改生产源码同4项失败，最终173范围和独立109项已覆盖最后实现。

未运行：完整API余项、登录UI/浏览器E2E、全库重估、实物鉴定、付款流水、评级证书验证、手机启动。没有新的逐卡市场估价，原690次直连及补检索由审核封存材料承载，不将离线回放当新搜索。

## 发布实测

实际API已切换valuation5，revision为59c047a7892b3638996124581e1fe6904ffa3241；183份源SHA验证，任务3份变化、其他180份保留。正式v21/视觉v7/联合3不变，号码roles3。健康、就绪、Compose与运行worker真值通过。网页valuation2及14静态资产逐SHA不变，200卡身份/图/持有、396面SHA和877活动集合不变。详见deployment-batch03.md。手机复用已安装1.1.10(14)的待解锁启动链路，本批未重验设备。

## 接续

本批工程发布和运行门通过后，由父协调方决定下一批；本聊天不启动剩余277。继续每批≤200、完整审核后统一优化。109身份/图绑定未解、4缺背、自动模型正常配对仍待核及261反光误拒需显式携带，不能用发布成功宣称已解决。

## 本次全量尝试观察到的基线失败（逐项）

以下119项均在导出的生产源码重跑失败，具体stdout、XML和SHA保存在私有发布回执。

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
