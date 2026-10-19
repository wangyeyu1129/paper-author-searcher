# Aarti Singh 2020–2022 人工审核试点

最新审核入口为 [v2 按出版物分组审核](ground_truth_grouped_review.md)：原 37 条和 22 对关系保留，追加两条独立支持的期刊候选，共 39 条，仍全部 candidate。以下 v1 内容作为历史流程说明保留；正式转换与独立结果核查机制继续使用。

本次仅整理已有 37 条候选。原始 187 条参考数据未修改，教授身份、检索、归属判定和 PDF 模块未调整。所有建议仍为 `candidate`，当前没有人工确认的正例或正式指标。

## 审核文件

目录：`data/ground_truth/aarti_singh_pilot_v1/`。

| 文件 | 用途 |
| --- | --- |
| `checklist.md`、`checklist.csv`、`checklist.json` | 逐条标题、完整原始署名、年份、类型、来源和核查链接，以及具体风险提示 |
| `candidates.csv`、`candidates.json` | 37 条原始事实和人工标注字段；修改 CSV 的人工字段，保留 JSON 和事实列 |
| `relationships.csv`、`relationships.json` | 22 对疑似重复或版本关联；建议不是合并结论 |
| `completeness_findings.json` | 两项可能遗漏的正式期刊版本、一项额外 workshop 声明和来源缺口 |
| `directory_audit.json` | 19 个已覆盖目录的独立快照对账和 SHA-256 校验 |
| `coverage_review.template.json` | 来源、其他发表场所、身份风险和遗漏项的人工完整性签署表 |
| `matching_annotations.template.json` | 需要人工批准的标题/年份别名和额外预印本关联 |
| `evaluation.pending.json`、`evaluation_readiness.json` | 尚未完成审核的 Evaluation 输入和门禁状态；不含正式指标 |

核查链接分为原始论文链接与补充/关联记录链接。关联来源可以帮助找到正式出版记录，但不能证明两条记录就是同一出版物。清单保存原始缩写署名，不自动将缩写展开为身份确认。主页上的 DBLP ORCID 冲突是整份书目的身份风险，不表示所有论文都存在实质冲突。

## 纳入和版本标准

核心参考集包含 2020–2022 年、人工确认属于目标教授的正式会议论文、期刊论文和 review。Preprint、workshop、书籍章节作为独立关联记录保留，不进入本次核心分母。Poster、Slides、补充材料、论文集整体、编辑署名和非论文报告不作为正式论文正例。未知类型先保留待审核。

年份采用正式引用的卷期年份；若没有卷期年份，采用官方出版记录的年份。在线首次发表日期另外保留，不能与正式年份互相覆盖。预印本使用自身年份，不为与正式版本匹配而改成正式年份。不同会议/期刊版本使用不同 canonical group；同一出版物的重复书目记录可使用同一 group。

## 第一轮：盲审独立参考集

1. 复制 `candidates.csv` 为 `annotations.csv`。按 `checklist.md` 阅读教授主页和论文级官方记录，不参考 Agent 的 ownership/confidence 或下载成功与否。
2. 只填写人工字段。确认记录需要 `review_status=confirmed`、`ownership=belongs/not_belongs`、`inclusion_decision=include/exclude`、独立的 `canonical_group_id`、真实审核人和带时区的 ISO 时间，以及至少两个不同核查 URL。四个 `*_checked` 必须明确填 `true`。遇到争议填 `needs_review/uncertain/pending`。
3. 更正用 `reviewed_title`、`reviewed_authors_json`、`reviewed_year`、`reviewed_doi`、`reviewed_publication_type`。`reviewed_doi` 留空表示沿用原值，填文字 `null` 表示撤回错误 DOI。作者须核对完整署名，不能把机构、编辑或贡献者当成作者。
4. 对同一正式出版物的重复记录，使用相同 group；推荐两条都标 `include`，转换后只算一个正例。属于同一 group 的重复排除项也保留来源标识。DOI、年份或类型冲突会阻止合并。预印本始终使用不同 group 并标 `exclude`。
5. 复制 `relationships.csv` 后填写 `human_relation`：`same_publication`、`distinct_publications`、`preprint_of` 或 `unrelated`，同时填审核人、时间、`evidence_urls`（JSON URL 数组）。留空不会生成关系。两端必须先完成逐条审核，关系决定必须与 canonical group 一致。预印本关联不会加入正式论文匹配标识。
6. 标题变化若已更正，错误的原始标题不会自动变成可接受别名。复制 matching 模板，按下述格式批准确实代表同一正式出版物的别名。不能把预印本或期刊扩展版伪装成标题别名。

```json
{
  "metadata_aliases": [
    {
      "evaluation_id": "PROFESSOR_ID:CANONICAL_GROUP_ID",
      "same_publication": true,
      "metadata": {
        "title": "经独立核查的标题变体",
        "authors": ["经核查的完整作者名"],
        "year": 2021,
        "venue": "官方发表来源",
        "publication_type": "journal_article"
      },
      "year_difference_reason": "仅在别名年份不同于正式年份时，解释在线/卷期差异",
      "reviewer": "实际审核人",
      "reviewed_at": "带时区的实际审核时间",
      "evidence_urls": ["https://实际来源一", "https://实际来源二"]
    }
  ],
  "preprint_relations": []
}
```

这段是字段说明，不是已完成的人工标注。关系也可在 JSON 中填 `formal_evaluation_id`、`preprint_candidate_id` 和相同的人工签署字段；优先使用关系 CSV，避免手动写错 ID。

## 第二轮：独立完整性审核

复制 coverage 模板，逐个核查已覆盖目录与教授书目中的所有年份，包括旧条目下的期刊补充说明。审核 AAAI、JMLR/TMLR、IEEE、ACM、Annals of Statistics、材料期刊和 workshop 等缺口。确认目录编辑出现不能当作论文作者：ICML 2020 快照中的五处 Aarti Singh 出现属于卷编辑及站点元数据，已与六条论文作者记录分开。

当前两项可能遗漏的正式期刊版本是：

- [Regression with Comparisons: Escaping the Curse of Dimensionality with Ordinal Information](https://jmlr.org/papers/v21/19-505.html)，JMLR 2020。它位于教授主页的 ICML 2018 条目下，期刊标题有所变化。
- [Near-optimal discrete optimization for experimental design: a regret minimization approach](https://link.springer.com/article/10.1007/s10107-019-01464-2)，在线 2020、卷期 2021。它位于教授主页的 ICML 2017 条目下，期刊标题也不同。

另有同题 Integrating Rankings 的 workshop 声明需要确定是否为独立出版或报告。它不自动进入核心分母。以上都是待核查发现，没有新增到 37 条候选。

`human_checked` 表示人工核对了来源实际覆盖范围并处理了其缺口，不表示失败的来源已成功获取。DBLP 的访问页不能算出版目录。`additional_venue_audit_complete` 必须在其他场所覆盖也经过审核后填写。

每个遗漏项需要 `already_covered`（指向已有、已纳入候选 ID，并写解释）或 `out_of_scope`（真实范围排除理由）。真正的遗漏不能为了通过门禁标成范围外：保持 `pending`，提出后续独立数据集修订。本阶段不扩充数据。不得删除模板中的来源或遗漏项以通过门禁。

只有全部候选完成人工处理、身份风险和完整性缺口已解决，并填写真实签署证据，才能把 coverage 的 `approved` 设为 `true`。工具不会因 37 行都审核过而自动批准完整性。目前 `complete_positive_list` 保持 `false`。

## 转换与评估命令

以下命令在项目根目录执行，不请求 API、不下载 PDF，也不会覆盖已有输出。使用虚拟环境，或把 `.venv/bin/python` 换为已安装本项目的 Python 3.10+。

```bash
PYTHONPATH=src .venv/bin/python -m scripts.ground_truth.export_truth \
  --candidates data/ground_truth/aarti_singh_pilot_v1/candidates.json \
  --annotations data/ground_truth/aarti_singh_pilot_v1/annotations.csv \
  --relationships data/ground_truth/aarti_singh_pilot_v1/relationships.reviewed.csv \
  --coverage data/ground_truth/aarti_singh_pilot_v1/coverage_review.json \
  --matching-annotations data/ground_truth/aarti_singh_pilot_v1/matching_annotations.json \
  --output data/ground_truth/aarti_singh_pilot_v1/evaluation.reviewed.json
```

可省略未使用的 `--relationships` 和 `--matching-annotations`。未批准完整性时可导出已确认子集作为暂存结果，但 `formal_evaluation_ready=false`，不能用于正式试点评估。

已有 Evaluation 和 Performance 的正式 CLI 不接受携带该试点门禁字段的真值作为直接评分输入，避免绕过独立结果核查；其他既有 Evaluation 文件与诊断接口继续使用原流程。

```bash
PYTHONPATH=src .venv/bin/python -m scripts.ground_truth.evaluate_pilot \
  --result /path/to/saved_aarti_papers.json \
  --truth data/ground_truth/aarti_singh_pilot_v1/evaluation.reviewed.json \
  --audit-output /path/to/aarti_result_audit.pending.json \
  --output /path/to/aarti_evaluation.pending.json
```

工具根据 DOI、可靠论文 ID、标准化标题/年份/完整作者名单及人工批准的别名逐论文匹配，保留原始来源记录的匹配证据。不同 DOI 冲突、多个参考身份错误合并等情况需要先裁决或修复，不能通过审核表直接覆盖冲突。显式预印本与非核心版本不能因同题或同 DOI 自动算正式论文。

## 第三轮：独立核查 Agent 未匹配记录

审核 `aarti_result_audit.pending.json` 中的每条记录。表中隐藏 Agent 的 ownership 预测及评分，保留论文事实、原始来源和匹配原因。人工先判范围，再判真实归属：

| decision | 含义 |
| --- | --- |
| `uncertain` | 未审核或证据不足，阻止正式指标 |
| `belongs` | 人工确认属于目标教授且为核心范围论文；填参考集 `evaluation_id` |
| `not_belongs` | 人工证据证明属于其他作者；填 `canonical_group_id`，重复负例使用同组 |
| `out_of_scope` | 预印本、非核心类型、范围外年份等，不计入核心 ownership 分母 |

非 uncertain 决定都需要实际审核人、时间、两个证据 URL 和解释。未出现在参考集合中不等于 `not_belongs`。若确实是缺失正例，工具阻止正式指标，要求独立修订参考集；不会自动复制 Agent 记录作为答案。事实和匹配证据不能在审核 JSON 中篡改，审核绑定输入与参考 JSON 的 SHA-256，任一输入改变后必须重新生成审核表。

```bash
PYTHONPATH=src .venv/bin/python -m scripts.ground_truth.evaluate_pilot \
  --result /path/to/saved_aarti_papers.json \
  --truth data/ground_truth/aarti_singh_pilot_v1/evaluation.reviewed.json \
  --audit /path/to/aarti_result_audit.reviewed.json \
  --output /path/to/aarti_evaluation.final.json
```

所有门禁满足后才输出下列指标。重复记录按已确认出版身份只计一次，范围外记录单列。

| 指标 | 定义 |
| --- | --- |
| Paper Discovery Recall | 任一归属状态下找到的核心正例数 / 人工完整核心正例数 |
| Ownership Precision | verified 真正例 / (verified 真正例 + 人工确认的 verified 假正例) |
| Ownership Recall | verified 真正例 / 人工完整核心正例数 |
| Ownership F1 | `2TP / (2TP + FP + FN)` |

零分母输出 `null`。正式指标被阻止时 `formal_metrics=null`，报告列出剩余审核项，不输出“临时正式 Recall”。本阶段没有提供 Aarti Singh 的保存 Agent 检索结果，也没有捏造人工审核或运行新检索；真实评估在人工参考集和独立结果核查完成后执行。
