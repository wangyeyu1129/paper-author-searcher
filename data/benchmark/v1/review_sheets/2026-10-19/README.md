# 真值审核表（2026-10-19，其余 24 人，AI 审核）

应用户要求，2026-10-18 未审核的 24 人由 Claude 完成审核，**不是人工审核**。连同 2026-10-18 的 8 人，32 人现在都有审核过的真值：

- 真值文件 `truth/<id>.json` 记为 `annotation_state = assistant_reviewed`，reviewer 是 "Claude (AI review requested by the user, 2026-10-19)"。
- 评分按审核后的标签计算，但 `gate.require_human_review = true`，所以门槛**不会**把 AI 审核算作正式认证。
- 没有任何人标为"已列全"（`complete_positive_list`）：我无法独立确认时间窗内的论文已全部列出。

## 审核了什么

| 行 | 数量 | 决定 |
|---|---:|---|
| 真值候选（来自本人/实验室书目、ORCID） | 595 | 582 belongs，13 uncertain |
| 系统线索（`calibrated-minimal-20261018` 与 `calibrated-with_hints-20261018` 判为 verified、但真值里没有） | 382 | 238 belongs，44 合并为已有条目的另一版本，79 uncertain，21 not_belongs |

两个 run 的线索都导出了，因此这两个 run 中所有 verified 输出都有标注，精度是完整测量的。`keyed-hints-20261018` 可能还有少量未标注的输出（未标注的不计入精度）。

## 判定规则

证据只来自 Crossref / DataCite 记录（署名、机构、ORCID）和研究者自己维护的书目，不使用本系统的检索结果。逐条决定和理由写在每个 CSV 的 `reviewer_note` 中。

真值候选默认 `belongs`（来源本身就是本人书目）。以下情况例外：

- 勘误、更正通知 → `uncertain`。
- 书籍 → `uncertain`。
- 解析残留（如 Yi Cui 的 "E.C"）→ `uncertain`。
- 同一论文的另一版本（如预印本对应正式版）→ `uncertain`，并注明是哪一行的版本。

系统线索要满足以下任一条件才算 `belongs`：

1. Crossref 署名带有此人的 ORCID。
2. Crossref 机构与此人单位一致。**常见中文名**必须写到具体院系，例如 Tsinghua 计算机系、Harvard Sociology、Michigan EECS；只写学校不算。
3. 与本人书目中的合作者**全名**重合：至少 2 人；作者不超过 15 人时 1 人即可。
4. 名字在该领域没有同名者（Neubig、Manning、Rus、Ellis、Hie、Kallini、Roussille、Kundaje、Doudna、Trinidad），Crossref/DataCite 署名有此人且主题一致。

其余规则：

- 数据集、软件、会议摘要、书评、政策简报、博士论文 → `uncertain`（不计分）。
- 已有真值条目的另一版本 → `same_as`。
- 同名者的论文 → `not_belongs`：领域和单位都不同，且没有共同合作者。例如：
  - Yang Liu 名下清华车辆、化工、电子系的论文；
  - Xiang Zhou 名下 Michigan 生物统计的论文；
  - Feng Zhang 名下的心理学论文。
- 证据不足 → `uncertain`。

## 重新生成

```bash
python3 evidence/fetch_crossref.py <dir>/crossref.json   # 联网，免费 API
python3 evidence/fetch_datacite.py <dir>/crossref.json   # arXiv / Zenodo DOI
python3 evidence/analyse.py <dir>                        # 生成 <dir>/analysis.json
python3 evidence/decide.py <dir> data/benchmark/v1/review_sheets/2026-10-19
```

`evidence/crossref.json` 是本次使用的 Crossref/DataCite 记录快照。

## 已知局限

- 补进真值的论文都来自系统输出。系统和书目同时漏掉的论文仍然不在真值里，所以召回率偏乐观。
- 同一论文的不同版本如果标题差别较大，可能没有合并。真值行之间只能标为 `uncertain`，不能合并，这会让召回率略偏保守。
- Doudna 实验室页面的解析在部分 DOI 后面留下了 `.pmid:` 后缀。条目里另有正确的 DOI，不影响匹配，但解析器需要修。

你可以随时复核：改 CSV 里的决定，用 `--review-state human_reviewed` 加上你的名字重新导入，就会变成正式的人工审核。
