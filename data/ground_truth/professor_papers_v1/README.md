# Professor Paper Ground Truth：候选数据 v1

建立日期：2026-10-11。主范围为 **2020–2022 年正式出版记录**，保留关联预印本及其他出版物供版本与纳入审核；13 条相关记录的来源日期不在该窗口内。数据独立来自教授公开书目和出版目录，未读取项目的 Resolver、Collector、归属或 PDF 运行结果作为参考答案。当前 **187 条均为 candidate、ownership=uncertain；人工 confirmed=0**。没有计算真实 Precision/Recall。

## 教授与来源

| 教授 | 当前机构（身份上下文） | 候选记录 | 官方身份与公开书目 |
| --- | --- | ---: | --- |
| Emma Brunskill | Stanford University，Computer Science | 73 | [主页](https://cs.stanford.edu/people/ebrun/)、[出版列表](https://cs.stanford.edu/people/ebrun/publications.html)、[DBLP 参考](https://dblp.org/pid/27/1277) |
| Aarti Singh | Carnegie Mellon University，Machine Learning Department | 37 | [主页](https://www.cs.cmu.edu/~aarti/)、[出版列表](https://www.cs.cmu.edu/~aarti/pubs.html)、[DBLP 参考](https://dblp.org/pid/64/5328) |
| Jacob Steinhardt | UC Berkeley，Statistics / EECS | 77 | [主页](https://jsteinhardt.stat.berkeley.edu/)、[出版列表](https://jsteinhardt.stat.berkeley.edu/publications/)、[DBLP 参考](https://dblp.org/pid/35/10625) |

三位教授有公开可核查的书目，方向覆盖强化学习、统计学习、鲁棒性与语言模型。采用固定历史窗口，控制审核规模；当前学校不作为每篇论文的历史机构证据。作者 ID、ORCID 和教授身份也需要独立人工核查；没有导入 Resolver 的身份结论。Aarti Singh 的公开 DBLP 档案报告 ORCID 冲突，此风险保留在每条候选中。

独立来源读取包括三位教授的书目、NeurIPS 2020/2021/2022 官方目录，以及 PMLR 卷 108、119、124、125、126、130、134、139、149、151、161、162、174、178、180、182（ICML、AISTATS、UAI、COLT、MLHC、CHIL）。按目录中实际署名筛选候选，再读取实际链接的 HTML 引用/摘要页。另保留作者书目中其他会议、期刊、工作坊和预印本声明。姓名出现在目录只产生候选，不确认作者身份。

## 文件

- [candidates.csv](candidates.csv)：UTF-8 BOM，适合 Excel、LibreOffice、Numbers；前部是来源事实，后部是空白人工审核字段。
- [candidates.json](candidates.json)：所有候选、来源事实、相关元数据观察、缺失/冲突提示及空白人工字段。
- [duplicate_version_review.csv](duplicate_version_review.csv)：63 组标准化标题相同的审核提示，**不代表同一出版物或版本**；不同年份、预印本、期刊扩展版须人工判断。
- [reference_records.json](reference_records.json)：191 条独立来源观察的结构化快照；保存标题、署名、年份、DOI、来源 URL、元数据字段及可取得的响应哈希。
- [source_inventory.json](source_inventory.json)：来源读取方式、HTTP 状态、内容类型、失败及摘要/引用页访问情况。
- [scope.json](scope.json)：教授选择、窗口、分类与版本政策。
- [professors/](professors/)：各教授的 `*.pending.json` 与现有 Evaluation Ground Truth 格式兼容，仍全部 uncertain；不应当作人工标注运行正式评估。

候选标签的来源类型观察为 conference_paper 101、journal_article 5、preprint 60、workshop_paper 5、unknown 13、other 3。10 条候选有来源实际提供的 DOI，其余为 null；未报告 DOI 不能解释为没有 DOI。保留全部实际列出的作者，不把缺失作者补成教授姓名。出版类型和字段仍需人工核实。

候选数量包含重复书目声明、不同版本和其他出版物，不能直接当作独立论文数量。只对完全相同的显式参考 URL 整理来源证据，4 组这样的观察保持 candidate；标题、年份或作者相同本身不会使其他条目自动合并或 confirmed。

## 访问限制与范围完整性

DBLP XML 三次请求均返回 HTTP 200 的反爬虫 HTML，解析记录数为 0；没有绕过验证或更换访问路径继续试探。DBLP 人员页面仅用于公开身份风险参考，**不是已完成的 DBLP 论文全集收集**。

CMU 的页面在 Python 网络环境中 DNS 失败；Aarti 的书目事实通过浏览工具读取官方页面，明确标记 `indexed_official_bibliography_read_via_web`。该方式不伪造原始 HTTP 哈希，也不能证明页面已被完整抓取。

104 个实际链接的元数据页中，100 个含引用字段，3 个缺引用字段，1 个 HTTP 失败；所有情况保留。来源访问保持 TLS 验证，未读取/保存 PDF 正文。目录并未覆盖 PMLR 所有卷、全部出版社或教授全职业生涯，`complete_positive_list` 始终为 false。需要人工审计漏项和版本后才能改变范围完整性声明。

本数据只保留公开书目事实和元数据证据，不再分发论文全文、摘要或完整个人页面；引用和页面权利仍归原来源。资料 URL 用于核查，不能将参考集中的逐篇链接注入检索/PDF 发现流程以提高评测成绩。

## 审核与复现

见 [人工审核指南](../../../docs/ground_truth_review.md)，其中定义正式论文、预印本、Poster、Slides、书籍章节和其他出版物的统一纳入政策。保留原始候选，另存人工审核副本。

```bash
cp data/ground_truth/professor_papers_v1/candidates.csv annotations.csv

# 完成人工审核后执行；只是检查字段和审核声明，不计算指标
.venv/bin/python -m scripts.ground_truth.review_candidates \
  --candidates data/ground_truth/professor_papers_v1/candidates.json \
  --annotations annotations.csv --output annotations.validated.json
```

默认候选表原样运行仍得到 187 个 candidate、0 个 confirmed。检查器不能证明人工实际访问过来源，必须遵守指南；不要用自动填充勾选字段代替审核。

已保存的独立参考快照可离线重建，须使用新目录以保护人工审核文件：

```bash
.venv/bin/python scripts/ground_truth/build_candidates.py \
  --scope data/ground_truth/professor_papers_v1/scope.json \
  --references data/ground_truth/professor_papers_v1/reference_records.json \
  --output-dir /tmp/professor_papers_rebuilt
```

构建器只读结构化独立来源快照，强制初始化 candidate/uncertain，不查询 API、不导入 Agent 运行结果、不修改论文检索算法。将来重采来源应建立新版本、记录新访问条件和哈希，保留旧版；网页可能变化，离线重建不等于重新核实。

Python 3.8 下全部 496 项测试通过，包含 15 项新增的候选标签保护、来源隔离、版本不误合并、人工字段校验和范围修正测试。187 条 CSV/JSON 逐行核对一致，三个单教授文件通过已有 Ground Truth 格式校验，原样审核表验证仍为 187 个 candidate。数据文件哈希见 [manifest.json](manifest.json)，用于冻结人工审核输入；没有计算正式指标。
