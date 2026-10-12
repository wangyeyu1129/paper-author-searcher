# Aarti Singh 人工审核试点

范围：正式出版年份 2020–2022，以及已有清单中的相关预印本。

37 条候选全部保持 `candidate`，人工 confirmed 为 0。生成了 22 对疑似重复/版本关系，均未自动合并。原始 `professor_papers_v1` 的 187 条记录和文件校验值保持不变。

从 [逐条审核清单](checklist.md) 开始，在 `candidates.csv` 的副本中填写人工字段。论文级核查链接及风险提示见 `checklist.csv`。两项可能遗漏的核心期刊版本、一项 workshop 声明单独列在 `completeness_findings.json`，没有追加到候选集。19 个 PMLR/NeurIPS 目录快照经 SHA-256 对账：六条论文作者记录已在候选集，另有五处 ICML 2020 编辑/站点元数据出现，不当作论文作者。

完整流程、字段标准和命令见 [人工审核与评估指南](../../../docs/ground_truth_pilot.md)。

`evaluation.pending.json` 是无人工确认正例的暂存 Ground Truth，不可正式评估。`evaluation_readiness.json` 明确记录审核门禁和 `formal_metrics=null`。`complete_positive_list=false`，不得通过只审核现有条目、删除遗漏项或复制 Agent 检索结果来宣称完整。
