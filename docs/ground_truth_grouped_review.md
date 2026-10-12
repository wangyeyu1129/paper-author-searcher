# 按出版物分组进行 Ground Truth 人工审核

本轮使用 [Aarti Singh v2 分组审核页](../data/ground_truth/aarti_singh_pilot_v2/review/grouped_review.md)。原 37 条记录与 22 对关系均保留，两个此前遗漏的期刊出版物在独立核对教授书目和出版元数据后追加为候选。39 条记录均未人工 confirmed。

## 阅读顺序

先审核第 1–18 组会议和期刊，再审核 workshop、未知类型及报告。每组提供一个优先论文级来源、教授书目公共入口、完整来源署名、类型、年份、DOI、预印本及具体问题。不必为重复书目记录反复打开同一论文；组内表格保留其原始题名、年份、类型和 ID。

正式来源的事实可以支持“建议纳入”，不代表已核实教授身份。会议技术日程、DBLP 目录、引用页和仓储引用各自的证据范围会明确注明。只有日程不能认定归档全文；Crossref 保存的短题名不能擅自替换完整题名；原始类型可能是书目声明。若出处只提供缩写姓名，照实保留，不展开猜测。

共享预印本只提供导航。尤其 Two-Sample Testing 的 ISIT 2020 与 JMLR 2022 分开审核；不能因同一 arXiv 记录而合并正式出版物。已有关系仍未自动批准，也未填入人工 canonical group。

## 特别审核两项遗漏

第 11 组 [JMLR 2020](https://jmlr.org/papers/v21/19-505.html) 的四位正式作者是 Yichong Xu、Sivaraman Balakrishnan、Aarti Singh、Artur Dubrawski。教授书目旧 ICML 2018 条目中还包含 H. Muthakana，题名包含 Nonparametric；这些是另一版本的事实，不能自动成为期刊作者或题名别名。

第 13 组 [Mathematical Programming](https://link.springer.com/article/10.1007/s10107-019-01464-2) 作者为 Zeyuan Allen-Zhu、Yuanzhi Li、Aarti Singh、Yining Wang。在线出版 2020-01-10，卷期为 2021-03。本次主年份取 2021，两个日期都保留。两个年份均在窗口内；该期刊版本与 ICML 2017 独立，不以“旧会议论文”或“日期冲突”排除。

两条新候选及教授书目的补充引用均有记录，但人工归属、纳入和完整性尚未批准。覆盖表必须填实际新增候选 ID：`aarti_singh_c3161f48819a707e9bb6`、`aarti_singh_bd37f40b3f1251a4f972`。工具阻止将这两项独立支持的范围内遗漏标为 `out_of_scope`，也阻止指向无关候选冒充覆盖。如确有教授身份错误，须逐条明确判为 `not_belongs`、提供真实身份冲突证据，并使用 `not_target_author` 裁决，不将身份问题伪装成范围排除。

## 按组填写人工工作表

复制 `review/group_annotations.template.json` 为 `group_annotations.reviewed.json`。每项包含组 ID、成员 ID、建议元数据、具体问题，以及独立的 `human_review` 区域。只改人工字段，保留源文件和原分组。

1. 核对身份、全文出版记录、完整作者、类型、年份和版本。问题未解决，保持 `candidate`，或填写 `needs_review/uncertain/pending`，不要猜测。
2. 完成核查后，人工填写 `review_status="confirmed"`、真实 `reviewer`、带时区 `reviewed_at`、两个真实核查 URL（`evidence_urls_json` 是 JSON URL 数组的字符串）、四个 `*_checked`、`ownership`、`inclusion_decision`、`canonical_group_id`。确认来自你实际操作，工具不会自动生成这些值。
3. 同一组内成员确实是同一出版物时，可填写同一 canonical group；不同正式版本必须使用不同 group。分组建议有误时，不确认整组：改用 `candidates.csv` 的副本逐条裁决，仍可调用原转换工具。
4. 如接受整组页面上的建议题名、完整署名、正式年份、DOI、venue 和类型，**人工**将 `use_suggested_metadata` 设为 `true`。工具仅在你已明确 confirmed 且填写全部核查证据后使用这些元数据。也可保持 false，并手动填写 `reviewed_*` 更正字段。`reviewed_venue` 可修正正式 venue，旧 CSV 无此列仍兼容。
5. `preprint_reviews` 中的 10 条预印本单独裁决一次。仍按其自身作者、题名和年份，不从正式出版版本继承。共享于两组的预印本也只有一条人工决定。核心纳入标准仍不包括预印本。

机器建议与人工字段保持分开。将标题、完整作者和时间复制到人工字段之前需要实际核对。署名中的 `and` 等解析问题使用更正字段解决；原始数据仍留在来源记录里。

## 转回现有审核流程

在项目根目录执行。所有工具离线运行，不调用 Resolver、Collector 或 PDF 模块，不覆盖已有审核输出。

```bash
PYTHONPATH=src .venv/bin/python -m scripts.ground_truth.grouped_review \
  --candidates data/ground_truth/aarti_singh_pilot_v2/candidates.json \
  --grouped-review data/ground_truth/aarti_singh_pilot_v2/review/grouped_review.json \
  --group-annotations data/ground_truth/aarti_singh_pilot_v2/review/group_annotations.reviewed.json \
  --annotations-output data/ground_truth/aarti_singh_pilot_v2/annotations.reviewed.csv
```

该命令把你明确填写的组决定展开到其原始成员，分别处理预印本，不从任何自动建议推出人工 confirmed。缺少审核人、时间、真实证据或核查标志时拒绝确认。工作表绑定分组证据 SHA-256；源数据变更后需要新工作表，不能继续使用旧标注。

复制 coverage 模板为 `coverage_review.json`，逐个核实来源和缺口。两项期刊遗漏的决定保持 pending，直到你人工确认对应新增正例已纳入，才填 `already_covered` 和实际新增 ID。新增 candidate 并不自动完成完整性审核。第三项 workshop 声明仍需独立裁决。其他期刊、IEEE/ACM、workshop 与 DBLP 访问缺口仍需处理，不能仅因本轮追加两条就设置完整正例全集。

复制旧版 `relationships.csv` 作为人工关系表，原 22 对成员 ID 未改变。填写 `human_relation`、实际审核人、时间和证据 URL，留空不会生成关系。期刊/会议版本分开，预印本保持独立 group。题名别名使用 matching annotations 单独批准，不能因接受官方更正就自动接受错误的旧题名。

```bash
PYTHONPATH=src .venv/bin/python -m scripts.ground_truth.export_truth \
  --candidates data/ground_truth/aarti_singh_pilot_v2/candidates.json \
  --annotations data/ground_truth/aarti_singh_pilot_v2/annotations.reviewed.csv \
  --coverage data/ground_truth/aarti_singh_pilot_v2/coverage_review.json \
  --relationships data/ground_truth/aarti_singh_pilot_v2/relationships.reviewed.csv \
  --output data/ground_truth/aarti_singh_pilot_v2/evaluation.reviewed.json
```

可省略暂未使用的关系表，或增加 `--matching-annotations`。后续独立核查 Agent 未匹配记录、正式指标门禁及 Evaluation 命令沿用 [试点评估指南](ground_truth_pilot.md)。人工核查未完成时只输出暂存 Ground Truth 和待办项，不发布正式指标。
