# Stratified researcher benchmark v1

32 位公开研究者，按 资历 × 领域 × 姓名类型 分层，用来测量检索系统的召回率、精度和全文获取率，并作为上线门槛。

| 资历 \ 领域 | 计算机 | 生物医学 | 工程 | 社会科学 |
|---|---|---|---|---|
| 知名资深 | Wei Wang\*, Christopher Manning | Feng Zhang\*, Jennifer Doudna | Yi Cui\*, Daniela Rus | Yu Xie\*, Raj Chetty |
| 常规教职 | Yang Liu\*, Graham Neubig | Le Cong\*, Anshul Kundaje | Wei Lu\*, Sindy Tang | Xiang Zhou\*, Christian Wolf |
| 助理教授 | Huan Zhang\*, Kevin Ellis | Jia Liu\*, Brian Hie | Lu Lu\*, Siddharth Krishnan | Daniel Chen\*, Nina Roussille |
| 博士生 | Jing Huang\*, Julie Kallini | Zhiwei Xue\*, Marena Trinidad | Min Zhu\*, Jonathan Lee | Whitney Zhang\*, Shakked Noy |

\* 常见中文姓名（同名冲突高）。每个分层值 8 人，`common_chinese` / `other` 各 16 人。

## 文件

- `benchmark.json`：时间窗、分层维度、纳入的出版类型、上线门槛（指标、阈值、聚合方式、每层最少人数）、运行参数、外部 API 地址。代码里没有任何阈值。
- `roster.json`：每人的检索输入（只有姓名、学校、院系，与生产调用一致）、分层标签、真值来源、身份证据链接、个人时间窗。
- `transcriptions/*.txt`：从公开页面人工/脚本整理的原始行，可重新生成快照。
- `snapshots/<person_id>/`：真值来源快照（`manual` JSON、`bibtex`、ORCID works、Crossref/arXiv 标题查询候选）。构建真值完全离线。
- `truth/<person_id>.pending.json`：由快照生成的待审核真值；`truth/<person_id>.json`：人工审核后的正式真值（存在时优先使用）。

## 真值独立性与限制

- 真值只来自研究者本人或所在实验室/学校维护的书目（主页、CV、实验室列表、Stanford Profiles、ORCID），不读取本项目检索结果。
- 博士生的真值来自导师组的论文列表中包含该学生的条目，组外合作不在其中，需要审核时补充。
- 部分主页不完整或停在某一年（如 Yu Xie 只到 2023），对应个人时间窗已按来源覆盖范围设置并在 `notes` 说明。
- Crossref/arXiv 只用于给缺少标识符的条目补 DOI/arXiv ID：要求规范化标题完全一致且目标作者姓氏出现在署名中，并打 `identifier_from_title_lookup` 标记供审核。
- 所有条目初始为 `ownership=uncertain` + `proposed_ownership`；**在人工审核前，任何分数都只是 provisional，门槛最多给出 `not_certifiable`。**

审核、运行和评分步骤见 [docs/benchmark.md](../../../docs/benchmark.md)。
