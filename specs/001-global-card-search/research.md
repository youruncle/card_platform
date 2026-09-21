# Research

- Decision: 服务端轻量投影匹配，完整对象仅取当前页。Rationale: 现有完整查询含图片识别/估值/账本，先全量展开开销高；几百到数千张私有卡片可直接扫描投影。Alternatives: 全库送前端不可扩展且暴露过多数据；独立搜索集群过重；数据库 trigram 扩展为当前需求增加部署依赖。
- Decision: 复用已发布知识库和现有球员别名，搜索专用补充常见名字；规范化后部分匹配，英文词仅长度至少五且无数字时允许一处编辑差异。Rationale: 可解释、无需联网、不改变成本/估值身份质量门。
- Decision: 过滤模型与分页接口保持可选参数兼容旧调用。字符串分类支持数组 OR，各字段 AND，数字范围含边界，未知不算零。
- Local evidence: page.tsx 当前 useMemo 仅筛选本页；repository.list_items 已有 limit/offset；知识库有 aliases_for；无需外部技术依赖或研究。
