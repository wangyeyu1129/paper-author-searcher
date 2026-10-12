# Aarti Singh 分组审核候选集 v2

从 [分组审核页](review/grouped_review.md) 开始。原 37 条逐条保留，新增两条独立正式出版来源支持的期刊候选，共 **39 条**。全部为 `candidate/uncertain`，人工 confirmed 为 0，`complete_positive_list=false`。

审核页以 **23 个非预印本出版物审核组** 展示：10 个会议、8 个期刊类建议组（其中 1 个来源明确标为 review）在前，随后 3 个 workshop、1 个未知类型和 1 个研究报告组。另有 10 条预印本附在相关组下；共享预印本不会把会议与期刊版本合并。这些是审核单位和自动类型建议，不能称为已经确认的正式论文数量。

官方题名、完整来源署名、年份、类型、DOI、教授书目、预印本及具体冲突集中在同一组。源记录没有去重或被删除，原 22 对关系仍在 `relationships.json`。教授书目只有一个公共入口；多数正式组已提供论文级出版元数据，先检查本页事实，疑点再打开对应网页。部分来源只报告缩写署名，不推断完整名字。

## 新增的两条期刊候选

| candidate_id | 官方期刊标题 | 正式年份 | DOI | 需要人工裁决 |
| --- | --- | --- | --- | --- |
| `aarti_singh_c3161f48819a707e9bb6` | Regression with Comparisons: Escaping the Curse of Dimensionality with Ordinal Information | 2020 | 未报告 | 期刊四位作者与 ICML 2018 书目五位作者不同，题名也不同，不复制会议署名 |
| `aarti_singh_bd37f40b3f1251a4f972` | Near-optimal discrete optimization for experimental design: a regret minimization approach | 2021 卷期，2020 在线 | 10.1007/s10107-019-01464-2 | 同一期刊出版物只计一次，保留在线/卷期日期，与 ICML 2017 分开 |

来源：[JMLR 官方出版记录](https://jmlr.org/papers/v21/19-505.html)、[Springer 官方出版记录](https://link.springer.com/article/10.1007/s10107-019-01464-2)，均由教授书目的旧会议条目下的期刊引用补充说明支持。新增记录具有独立元数据观察和响应 SHA-256，未来自 Agent 检索结果。没有新增未经核实的其他论文或预印本。

## 文件与审核入口

- `review/grouped_review.md`：主要阅读入口。
- `review/grouped_review.json`：机器证据、原始候选、分组建议和所有关联。
- `review/group_annotations.template.json`：按组填写的人工工作表，人工字段为空。10 条预印本单独填写，不重复审核共享记录。
- `candidates.json`、`candidates.csv`：兼容已有审核工具的全部 39 条候选；原始事实保持不变。
- `coverage_review.template.json`：来源和遗漏项人工审核。两条遗漏已指向新增候选，但仍为 pending；不能标为范围外隐藏。
- `revision_provenance.json`：37 条到 39 条的追加轨迹。
- `evaluation.pending.json`：当前无人工确认正例的暂存格式，不可正式评分。
- [来源观察集](../aarti_singh_review_sources_v2.json)：本轮直接出版元数据请求和官方网页观察，不包含人工标注或 Agent 结果。

详细操作及命令见 [按组审核指南](../../../docs/ground_truth_grouped_review.md)。原 `professor_papers_v1` 和 `aarti_singh_pilot_v1` 作为历史快照保留，不覆盖旧标注文件。
