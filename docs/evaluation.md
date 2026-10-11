# 离线 Evaluation

Evaluation 读取已有 JSON 和独立人工标注，不调用 API，不重跑 Resolver、去重或归属规则，也不修改匹配阈值。它检验保存结果中的决策，而不是把 `verified` 当作人工真值。

```bash
PYTHONPATH=src python3 -m paper_author_searcher.evaluation_cli \
  --result papers.reviewed.json \
  --ground-truth ground_truth.json \
  --output evaluation.json
```

安装项目后也可以使用 `paper-evaluate`。指定 `--output` 时终端显示指标摘要，文件保存完整诊断；未指定时终端输出完整报告。输入可以是 Collector 的 `collection` 包装结果、直接的 collection 对象，或独立 Resolver 结果。只有 Resolver 结果时不计算论文指标，避免把未检索解释为论文漏检。

库接口：

```python
import json
from paper_author_searcher import Evaluator, GroundTruth

with open('papers.reviewed.json', encoding='utf-8') as file:
    saved_result = json.load(file)
with open('ground_truth.json', encoding='utf-8') as file:
    ground_truth = GroundTruth.from_dict(json.load(file))
report = Evaluator().evaluate(saved_result, ground_truth)
```

## Ground Truth 格式

复制 [空模板](../schemas/ground_truth.template.json) 后填写人工核实的记录；[JSON Schema](../schemas/ground_truth.schema.json) 描述格式。模板没有真实教授或论文标注。运行时使用内置校验，无需安装 JSON Schema 库。

顶层字段：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 当前为 `1` |
| `papers` | 人工标注数组；允许空列表，此时没有可计算的论文正确率 |
| `complete_positive_list` | 默认 `false`；只有人工确认范围内正例列表完整时才设为 `true` |
| `metadata` | 可记录 reviewer、reviewed_at、scope、资料来源与版本政策；原样写入报告 |
| `professor` | 可选的独立教授身份真值，见下文 |

每个 `papers` 元素：

| 字段 | 要求与含义 |
| --- | --- |
| `evaluation_id` | 必填、非空、列表内唯一；表示人工确认的同一出版物身份 |
| `ownership` | 必填：`belongs`、`not_belongs` 或 `uncertain` |
| `doi` / `dois` | 单个 DOI 或人工确认属于同一出版物的 DOI 数组；允许 DOI URL |
| `source_records` | 数组，每项为 `{"source": "<provider namespace>", "paper_id": "<exact native ID>"}` |
| `external_ids` | 可靠标识符映射，例如 DOI、arxiv、pubmed 或数据库 namespace；值为字符串 |
| `title`, `year`, `authors` | 标题字符串、整数年份、作者姓名字符串数组；无稳定 ID 时三者必须完整 |
| `venue` | 可选；元数据匹配时，双方有明确且不同的 venue 不自动匹配 |
| `metadata_aliases` | 人工确认的元数据变体数组；每项可含 title、year、authors、venue |
| `annotation_evidence` | 人工核实证据字符串数组，例如机构主页或出版社作者信息链接 |

至少提供稳定 ID，或者完整 title/year/authors。程序拒绝同一个稳定标识符分配给多个 `evaluation_id`。无 ID 时，支持逗号署名顺序、姓名缩写和中间名省略的完整作者集合匹配，同时要求标题、年份精确一致；明确冲突的中间名不会匹配。缺失作者、年份变化和题名变体需要人工补充可靠 ID 或 `metadata_aliases`。多个真值均满足缩写时仍保持歧义。这些评估匹配规则不会改变 Resolver 或 Ownership Validator 的判定。

`evaluation_id` 同时定义去重真值：多个数据库记录属于同一出版物时，在该元素内列出它们的标识符。应保留独立的预印本、会议版、期刊扩展版时，为它们建立不同元素和 ID。只有人工确认可按评估范围视为同一出版物时，才将不同 DOI 列入同一个 `dois`。在 `metadata.scope` 明确版本政策、年份、出版类型及目标教授，避免用一个范围不完整的列表判断全部候选。

可选 `professor` 包含 `author_ids`（source 到 ID 字符串数组的映射）、`orcid` 和 `author_ids_complete`（默认 false）。仅纳入独立人工确认的身份 ID，不要直接复制 Resolver 输出作为真值。完整身份列表可计算输出 ID precision；不完整列表只计算已知 ID recall，未知输出 ID 不直接判错。报告分别记录首选身份与候选 ID 覆盖，ORCID 明确冲突会否定自动身份确认。没有教授标注时该部分显示 unavailable。

## 独立匹配规则

先匹配规范化 DOI、来源论文 ID 和外部 ID。数据库 namespace 不区分大小写，原生 ID 按原值精确匹配；应使用归档中的 ID 表示方式，例如完整 URL，不自动把 URL 与短 ID 当作相同。无稳定 ID 命中时，匹配规范化标题、精确年份和完整作者集合；作者顺序不影响匹配。

多个真值 ID 同时命中、同元数据对应多个版本，或已知 DOI 存在冲突时进入人工确认，不计为确定的正负例。每个匹配保留规则、真值候选、来源论文 ID 和 Author ID。程序使用每条合并前 `records` 匹配，而不是只匹配代表论文，才能发现错误合并。

默认 `complete_positive_list=false` 时，未命中与 `uncertain` 都未评分，不视作 False Positive。需要明确标注 `not_belongs` 来测量误检。设为 true 后，完全未匹配但有可靠 ID 或完整元数据的条目可作为列表外负例；冲突或歧义仍待确认。该模式要求人工确认真值与输出范围一致，且为真实正例补全可匹配的标识符和别名。

## 指标与诊断

归属指标按唯一人工 `evaluation_id` 计算，重复输出不会重复增加 TP。`ownership` 以整体 `verified` 为正预测：

- Precision = TP / (TP + FP)。FP 是输出 verified 但真值为 not_belongs 的出版物，或完整列表模式下可确定的列表外条目。
- Recall = TP / 全部人工 belongs 出版物。未取得、needs_review 和 rejected 的正例均是 verified 范围下的 FN。
- F1 = 2TP / (2TP + FP + FN)。分母为零时指标为 JSON `null`，不伪造 0 或 1。

`all_candidates` 包含 verified、needs_review、rejected 三类保留候选；其 recall 与 verified recall 分开，可以判断遗漏来自检索还是严格归属筛选。优先读取未过滤的 `all_papers`，否则读取 `papers`。旧归档如果只保存过滤后的列表，无法恢复被删条目，应重新提供完整归档。

`by_status` 同时报条目数、未评分条目数和每个状态的正例 yield；其中 precision 表示该状态中真实属于教授的比例，**不是 rejected 判断正确率**。正确拒绝比例另报为 `rejection_precision`。同一真值在多条输出中状态冲突时，报告 `status_conflicts`；至少一条 verified 就计入 verified 覆盖。

去重按人工已识别的原始记录对计算：同一 ID 且在同组是正确合并；不同 ID 在同组是错误合并；同一 ID 在不同组是重复遗漏。报告 pairwise precision/recall/F1、`incorrect_merges`、`missed_duplicates` 和原始记录标注覆盖率。未知记录对不默认算作去重正确。两篇真实论文错误合并可能不影响归属 recall，仍会在去重指标中暴露。

`false_positives` 保留 verified 误检证据；`false_negatives` 分开列出候选遗漏和 verified 遗漏，并附已取得的待审核/拒绝记录或未解决匹配。`manual_confirmation` 包含归属 needs_review、未标注、标识符冲突或版本歧义记录。

`judgment_coverage` 与去重 `annotation_coverage` 必须和指标一起查看。部分标注下 precision 仅代表已评分部分，不能证明全部输出准确；recall 仅相对于导入的正例列表，不能证明真实发表全集覆盖率。Evaluation 不读取 API：来源失败、分页完整性、错误与不可解析原始记录从归档原样报告。候选指标覆盖保留的可解析记录；不可解析记录单独计数，不能据此推断其归属。

单元测试全部使用明确的合成数据，验证已知计数、空分母、未知标签、版本冲突、去重错误和离线行为；不提供真实教授的人工评价分数。
