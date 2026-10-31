# 身份与图证

identity字段：brand_name、player_name、product_name、season、set_name、card_number、parallel_name、is_numbered、serial_total、grade_company、grade_value、condition、is_autograph、autograph_type、is_memorabilia、memorabilia_type、is_patch。

每个字段从目标图建立；昵称是搜索别名，不替代正式身份。赛季不是背面版权年份；完整卡号保留字母和前缀。产品必须完整区分Prizm/Prizm Draft、Chrome/Chrome Update等。卡面构图、纹理和背面卡号同时核对，Gold和Gold Shimmer同为/10仍是异版本。基础子系列已确认时set_name=base；未确认就null。确认普通基础平行可填parallel_name=Base，不能把未知写Base。

grade_company普通裸卡填raw；评级填图上公司及分数；签字认证分数不是整卡分数。签字需要区分贴签/卡签；物料区分球衣/Patch、比赛使用/球员穿过/未知，不从颜色数量推断使用属性。瑕疵、剪裁、重封装、划痕、边角或印刷问题影响condition；灯光反射不能证明无瑕疵。

目标字段未知、正背缺失或材质无法确认时可扩大检索，但不能把不确定版本认定exact。图上不清楚的流水号不用于确认重复；无流水的同图可疑也需标记。重复实卡的多次公开上架不能当独立成交；不同实卡来自同一卖家也不能冒充独立卖家样本。

示例：目标2022-23 Panini Prizm / Gold /10裸卡；候选Gold Shimmer /10归analog或reject，PSA10归analog，目标同款有8次出价的结束拍卖可列公开落槌观察。同一实卡再次上架合并并说明重上风险；仅此一张不形成估值。
