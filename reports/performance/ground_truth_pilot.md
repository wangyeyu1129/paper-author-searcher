# Ground Truth 人工审核辅助完成报告

本轮开发与校验已完成。Aarti Singh 2020–2022 的人工审核仍待进行，因此没有发布正式 Precision、Recall 或 F1。没有扩充数据集，也没有重新运行论文检索或 PDF 下载。

## 交付与当前状态

| 项目 | 结果 |
| --- | --- |
| 原始独立参考集合 | 187 条保持不变；原 manifest 中 10 个文件 SHA-256 全部一致 |
| 试点候选 | 37 条，逐条与原集合中的 Aarti Singh 子集相等 |
| 人工 confirmed | 0；所有自动资料仍为 candidate/uncertain |
| 疑似关联 | 22 对：6 对同出版物建议、15 对预印本关联建议、1 对不同正式版本检查 |
| 自动合并 | 0 |
| 核查清单 | 37 行均有原始来源和核查/关联链接；链接存在不等于已完成论文身份核实 |
| 原快照缺少直接论文链接 | 6 条，已单列提示并补充独立来源指引 |
| 完整正例全集 | 未确认，`complete_positive_list=false` |
| 正式指标 | `formal_metrics=null` |

候选类型仍是来源声明和待审核分类：17 conference、5 journal、10 preprint、3 workshop、1 unknown、1 other。它们包含重复及不同版本，不能据此推算教授实际正式论文数量。

审核数据在 [试点目录](../../data/ground_truth/aarti_singh_pilot_v1/README.md)，逐条清单见 [checklist.md](../../data/ground_truth/aarti_singh_pilot_v1/checklist.md)，可编辑人工字段见 `candidates.csv`。字段说明和可直接执行的离线命令见 [审核与评估指南](../../docs/ground_truth_pilot.md)。机器汇总见 [ground_truth_pilot.json](ground_truth_pilot.json)。

## 独立完整性与证据检查

对已覆盖的 19 个 PMLR/NeurIPS 目录快照校验原始响应哈希并检查作者角色：6 处论文作者署名对应已有参考记录。ICML 2020 中另有 5 处姓名出现属于卷编辑及站点元数据，不计为论文作者。这只证明现有快照的精确全名对账，没有证明所有署名变体或发表场所都完整。

检查 [教授官方网站](https://www.cs.cmu.edu/~aarti/pubs.html) 发现旧会议条目下的期刊版本说明，存在两项可能遗漏：

- [Regression with Comparisons: Escaping the Curse of Dimensionality with Ordinal Information](https://jmlr.org/papers/v21/19-505.html)，JMLR 2020，位于主页 ICML 2018 条目下。
- [Near-optimal discrete optimization for experimental design: a regret minimization approach](https://link.springer.com/article/10.1007/s10107-019-01464-2)，在线出版 2020、卷期 2021，位于主页 ICML 2017 条目下。两种年份都是本试点范围内，但只能计同一正式出版身份。

另有 Integrating Rankings 的 workshop 声明需要核对是否为独立出版或报告。以上全部列在 `completeness_findings.json`，未追加到 37 条候选。当前 DBLP 被访问页面阻挡，其他期刊、IEEE/ACM、workshop 等目录的完整覆盖仍需人工审计。

逐条风险包括书目范围的 ORCID 冲突、期刊题名变化、CSCW 展示与 PACM 期刊类型差异、缩写/连接词混入作者名、研究报告缺作者，以及 congress 摘要与后来的期刊版本混淆。[Workshop 官方日程](https://cfol-workshop.github.io/) 支持 Threshold Bandit 贡献的署名和展示，但不能单凭日程认定存在归档全文论文。补充资料均保持候选证据，没有自动更正或确认原数据。

## 工具行为

`pilot_review.py` 只从已有独立参考集合选择教授和窗口，输出清单及关系建议。没有具体教授判断或论文匹配特例。

`export_truth.py` 校验实际审核人、时间、证据、身份/元数据/类型/版本检查，按人工 canonical group 保留所有来源标识和已审核元数据变体；不同 DOI、年份或类型冲突阻止合并。预印本关联单独保存，不并入正式出版身份。未解决的候选、来源核查、其他 venue、身份风险和遗漏项阻止完整性批准；删除模板中的已知核查项也会被阻止。

`evaluate_pilot.py` 复用现有 Ground Truth 论文匹配，增加范围和版本检查。没有 DOI 的同题预印本不能直接算正式出版；缺少发表类型和 venue 的元数据匹配需要人工确认。保存 Agent 结果中未匹配或冲突的条目进入独立审核表，隐藏 Agent 的归属预测和评分。表绑定结果与真值的 SHA-256，旧表或修改来源事实会被拒绝。真实遗漏正例须独立修订参考集，不会自动抄入 Ground Truth。已发现的 DOI/版本冲突不能通过审核表直接覆盖。

参考集及独立结果核查全部完成后，输出独立的 Paper Discovery Recall、Ownership Precision、Ownership Recall 和 F1，按唯一已确认正式出版物计数，列出 FP/FN、范围外条目和匹配证据。既有 Evaluation/Performance 正式入口对该试点协议也设置了门禁，避免绕过独立结果核查；其他历史输入和参考诊断流程保留。

## 验证与剩余人工工作

在 Python **3.8.0** 中运行全量 `unittest discover`：**525 项全部通过**，其中本试点新增 **29 项**。测试涵盖 CSV/JSON 转换、CLI 离线链路、未审核门禁、完整性遗漏、人工重复分组、来源记录保留、标题/年份别名、预印本关系、冲突 DOI、独立负例审计、陈旧审计表、重复计数和指标公式。`git diff --check` 通过。

尚需真实人工完成 37 条记录、22 对关系中必要的版本裁决，以及独立来源/遗漏项核查。两项可能遗漏的核心期刊论文若确认缺失，需在后续授权的数据版本中补齐；本阶段不会为通过门禁扩充或隐藏它们。项目中未提供 Aarti Singh 的保存 Agent 检索结果，因此没有伪造其实际对照评估或 FP 标注。准备好人工真值和对应结果后，按指南生成独立结果审核表，再发布正式指标。
