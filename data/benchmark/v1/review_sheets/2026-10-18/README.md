# 真值审核表（2026-10-18，8 人）

这 8 人覆盖了每个分层：资历、领域各 2 人，常见中文名 4 人、其他 4 人。审核完成后，门槛结果就可以从"未认证"变成正式结论（前提是每个分层至少 2 人已审核）。

| 文件 | 人 | 行数（真值 + 系统线索） | 来源 | 时间窗 |
|---|---|---:|---|---|
| `cs_wei_wang_ucla.csv` | Wei Wang（UCLA，计算机） | 8 + 0 | 仅 ORCID，**很可能不全** | 2024–2025 |
| `soc_raj_chetty.csv` | Raj Chetty（哈佛，经济学） | 8 + 0 | 个人主页 | 2020–2025 |
| `bio_le_cong.csv` | Le Cong（斯坦福，病理/遗传） | 14 + 12 | 仅 ORCID | 2024–2025 |
| `eng_sindy_tang.csv` | Sindy Tang（斯坦福，机械工程） | 14 + 5 | Stanford Profiles + ORCID | 2024–2025 |
| `bio_jia_liu.csv` | Jia Liu（哈佛，生物工程） | 24 + 0 | 仅 ORCID | 2020–2025 |
| `eng_siddharth_krishnan.csv` | Siddharth Krishnan（斯坦福，电子工程） | 7 + 2 | Stanford Profiles + ORCID | 2020–2025 |
| `cs_jing_huang.csv` | Jing Huang（斯坦福博士生，计算机） | 8 + 3 | 个人主页 | 2020–2025 |
| `soc_shakked_noy.csv` | Shakked Noy（MIT 博士生，经济学） | 3 + 21 | MIT 主页 | 2020–2025 |

用 Excel 或 Numbers 打开即可。保存时请保持 **CSV（UTF-8）** 格式，不要改列名。

## 每一行要做什么

只需要填两列：**`decision`** 和（需要时）**`reviewer_note`**。

`decision` 只能是下面三个值之一：

| 值 | 含义 |
|---|---|
| `belongs` | 这篇确实是此人的，并且在时间窗内 |
| `not_belongs` | 不是此人的（同名者、署名错误等） |
| `uncertain` | 无法判断，或不在评测范围（海报、幻灯片、数据集、时间窗外），**不计分** |

### `row_kind = truth`（来自本人或实验室书目的真值候选）

- 绝大多数应该是 `belongs`。
- 重点看 `review_flags` 列：
  - `target_name_not_found_in_byline`：作者列表里找不到此人，请核对。
  - `identifier_from_title_lookup`：DOI 或 arXiv ID 是按标题自动查出来的，请确认它指向的是同一篇论文。
  - `grouped_N_source_records`：预印本和正式版被合并成了一条，请确认确实是同一篇。

### `row_kind = system_suggestion`（系统判为"已验证"、但真值里没有的论文）

这些只是**线索**，用来帮你发现真值缺了什么，同时检查系统有没有认错人。

- 如果你**用独立来源**（本人主页、CV、Google Scholar、出版社页面）确认确实是此人的：填 `belongs`，并且 **`reviewer_note` 必须写明你看到的证据**，例如"Google Scholar 上有"或"CV 第 3 页"。没有写说明的话，导入时会被拒绝。这样做是为了保证真值独立于系统。
- 如果不是此人的：填 `not_belongs`，不需要说明。这类标注会直接用来计算精度，很有价值。
- 判断不了：填 `uncertain`，或者留空。

## 补充遗漏的论文

如果你在本人主页、CV 或 Google Scholar 上看到时间窗内的论文，而表里没有，请**在表格末尾新加一行**：

- `evaluation_id` 留空，`decision` 填 `belongs`；
- 填 `title`、`year`、`authors`（多位作者用 `; ` 分隔），有的话再填 `dois`；
- `reviewer_note` 写上来源。

## "列全了"的确认

审核完一个人后，如果你确认**时间窗内的论文已经全部列出**，请在回复里告诉我，例如"Wei Wang 列全了"。我会在导入时加上 `--complete-positive-list` 参数，这样才能正式计算精度。没有确认的人只计算召回率。

## 填完以后

把改好的 CSV 保存回原位置，告诉我是哪些人。我会逐个导入（`paper-benchmark review-import`），检查格式，然后重新评分。

## 审核结果（2026-10-18，AI 审核）

应你的要求，这 8 份表由 Claude 完成审核，**不是人工审核**：
- 真值文件里记录为 `annotation_state = assistant_reviewed`，reviewer 是 "Claude (AI review requested by the user, 2026-10-18)"。
- 评分时按已审核的标签计算，但门槛不会把它算作正式认证。
- 判断依据：Crossref 的作者列表和机构、研究者本人主页、ORCID。每条决定和理由见 [assistant_decisions.py](assistant_decisions.py)。
- Wei Wang 补充了本人主页的完整论文列表（真值从 8 条增加到 44 条）。
- 只有 Wei Wang 和 Raj Chetty 标为"已列全"。（2026-10-19 已撤销：两份真值其实不全，见 [2026-10-19-closed-world](../2026-10-19-closed-world/README.md)。）

你可以随时复核：修改 CSV 里的决定后重新导入，用 `--review-state human_reviewed` 并写上你的名字，就会变成正式的人工审核。
