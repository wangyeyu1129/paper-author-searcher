# 人工抽检：16 位常见中文名研究者的真值

这些人同名者最多，AI 审核最容易出错，所以先抽检他们。门槛（`require_human_review`）只认人工审核过的真值。只要还有人是 AI 审核，门槛最多给出 `not_certifiable`。

每份表都**已经预填**了 AI 的决定（`decision` 列）和理由（`reviewer_note` 列）。你只需要改不同意的行。

## 怎么审

用 Excel 或 Numbers 打开 CSV，保存时保持 **CSV（UTF-8）** 格式，不要改列名。

`decision` 只能是下面三个值之一：

| 值 | 含义 |
|---|---|
| `belongs` | 是此人的论文，且在时间窗内 |
| `not_belongs` | 不是此人的（同名者） |
| `uncertain` | 判断不了，或不在评测范围（数据集、摘要、重复版本），不计分 |

改了决定的行，请在 `reviewer_note` 写上你的依据，例如"CV 第 2 页"或"作者是清华车辆学院的 Yang Liu"。

时间有限时，按以下顺序优先看：

1. **`review_flags` 含 `suggested_by_system_not_in_truth` 的行**（共 218 行）。这些论文来自系统输出，是 AI 根据 Crossref 证据补进真值的。如果有错，召回率会虚高。
2. **`not_belongs` 的行**（41 行）。这些是 AI 判定的同名者论文。如果判错了，精度会虚低。
3. **`uncertain` 的行**（71 行）。其中可能有本该计分的论文。

真值行（`row_kind = truth`）来自本人主页、CV 或 ORCID，一般没问题。主要看两种情况：作者列表里没有此人，或者明显是另一篇论文的另一个版本。

## 导入

审完一个人就导入一次，默认记为人工审核：

```bash
paper-benchmark review-import --person cs_yang_liu_tsinghua --sheet data/benchmark/v1/review_sheets/human_spotcheck/cs_yang_liu_tsinghua.csv --reviewer '你的名字'
```

导入后，这个人的整份真值都记为人工审核，所以表中每一行都要真正看过。只有确认时间窗内的论文已经全部列出时，才加 `--complete-positive-list`。

## 表格

| 文件 | 行数 | belongs | not_belongs | uncertain | 来自系统线索 |
|---|---:|---:|---:|---:|---:|
| `bio_feng_zhang.csv` | 32 | 21 | 2 | 9 | 20 |
| `bio_jia_liu.csv` | 24 | 23 | 0 | 1 | 0 |
| `bio_le_cong.csv` | 24 | 18 | 0 | 6 | 10 |
| `bio_zhiwei_xue.csv` | 1 | 1 | 0 | 0 | 0 |
| `cs_huan_zhang.csv` | 68 | 67 | 0 | 1 | 11 |
| `cs_jing_huang.csv` | 11 | 10 | 0 | 1 | 3 |
| `cs_wei_wang_ucla.csv` | 139 | 86 | 21 | 32 | 95 |
| `cs_yang_liu_tsinghua.csv` | 93 | 76 | 15 | 2 | 24 |
| `eng_lu_lu.csv` | 69 | 66 | 0 | 3 | 9 |
| `eng_min_zhu.csv` | 10 | 8 | 0 | 2 | 2 |
| `eng_wei_lu.csv` | 21 | 16 | 0 | 5 | 11 |
| `eng_yi_cui.csv` | 34 | 33 | 0 | 1 | 0 |
| `soc_daniel_chen.csv` | 2 | 2 | 0 | 0 | 0 |
| `soc_whitney_zhang.csv` | 3 | 3 | 0 | 0 | 0 |
| `soc_xiang_zhou.csv` | 23 | 14 | 3 | 6 | 11 |
| `soc_yu_xie.csv` | 47 | 45 | 0 | 2 | 22 |
| **合计** | **601** | **489** | **41** | **71** | **218** |

表格是从当前真值导出的。如果之后真值又有变动，重新导出即可：

```bash
paper-benchmark review-export --person <id> --output <file>.csv --prefill-from assistant_reviewed
```
