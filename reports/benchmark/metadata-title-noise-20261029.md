# 原文覆盖率：PDF 元数据里的无效标题不再否决身份（2026-10-29）

数据集 `stratified_researchers_v1`，32 人，最少输入。真值由 AI 审阅（未经人工认证）。

## 问题

文件校验时，如果 PDF 元数据里的标题和目标论文不同，就判为“待审”，即使第一页标题、DOI、作者都对得上。

在 `ft-oa-fb-rt` 里，有 39 篇文件因此没有确认。这些元数据“标题”多数根本不是标题：
- 记录号：`68605761`、`342294`、`102977`；
- 文件名：`mcedit_fetal_heart_manuscript_medrxiv`、`pnas.2409913121`、`Microsoft Word - Tuck_Adler_HamAB_bioRxiv_v4.docx`；
- 占位符：`My title`、`Article`、`CoverSheet`；
- 写法问题：HTML 转义（`CRISPR&ndash;Cas12a`）、LaTeX 转义（`B\'ezier`）、排版吞掉的空格（`BaselinesOutperform`）。

## 改动

元数据标题在比较前先还原 HTML 转义。然后：
- **不足 3 个词**（按书写的词数和规范化后的词数，取较小者）：视为技术性元数据，不否决身份（`metadata_title_minimum_words`，默认 3）。
- **办公文档扩展名**（`.docx`、`.doc`、`.odt`、`.rtf`）：和原有的 `.tex`、`.pdf` 一样，视为技术性元数据。
- **去掉 LaTeX 转义和空格后与目标标题相同**：视为匹配。

保持不变的保护：
- 3 个词及以上的真实标题仍会否决，例如 “Unrelated scientific findings”。
- “Supplement to …”（补充材料）、“Author Correction: …”（更正）仍判为不同文件。
- 身份确认仍要求第一页出现目标标题，并且有 DOI、arXiv 编号或完整作者署名中的至少一项佐证。

## 方法

离线复验，不联网：用新代码重新校验 `ft-oa-fb-rt` 已下载的全部 1151 个文件，写入副本 `ft-oa-fb-rt-rv` 并评分。

## 结果

| | 修改前 `ft-oa-fb-rt` | 修改后 `ft-oa-fb-rt-rv` |
|---|---:|---:|
| **宏平均原文覆盖率** | 55.8% | **58.1%** |
| 计算机 | 76.8% | 78.8% |
| 生物医学 | 59.4% | **63.7%** |
| 工程 | 47.5% | 50.6% |
| 社会科学 | 36.6% | 36.6% |
| 常见中文姓名 | 50.1% | 52.4% |
| 新通过校验的文件 | | 32 |
| 由通过变为不通过的文件 | | **0** |

论文召回 89.1%、身份精度 100% 不变。

### 精度核对

逐个检查了 24 份不同的新确认文件（32 条里有重复条目）：
- 第一页的标题和作者都与目标论文一致。
- 每份都有 DOI、arXiv 编号或完整署名作佐证。
- **没有发现误认。**

## 仍未解决

还有 9 篇仍被元数据标题否决：
- **正确的保守判断：** 补充材料 2 篇，作者更正 1 篇。
- **换过标题或写法不同的版本：** 2 篇（“Learning with uncertainty…” 与 “Leveraging Uncertainty…”；“p-norm” 与 “Lp-norm”）。
- **带前缀或注记的标题：** 2 篇（“JoVE - …”、“Bridge2AI Curriculum Publication (JAMIA edit)”）。
- **出版社网站名：** 2 篇，“Science Journals — AAAS” 有 4 个词，超过阈值。为了保留对短的真实标题的保护，阈值没有再调高。
