# 独立 Ground Truth 标注与人工审核

本阶段提交的是待人工核实的参考集合。自动收集、教授主页声明、同名署名或数据库身份均不能直接形成 confirmed。机器来源证据保存在 `reference_evidence`，人工证据另写 `annotation_evidence`，两者分开。

初版数据与访问记录见 [数据说明](../data/ground_truth/professor_papers_v1/README.md)。正式正例窗口为 2020–2022 年；关联预印本可早于该窗口，保留其引用页报告的年份并人工核对提交/修订日期。不能为适应窗口修改实际年份。

## 一致的纳入政策

| 类型 | 主论文集合 | 判断依据 |
| --- | --- | --- |
| `conference_paper` | 纳入候选；人工确认后成为正例 | 正式归档会议论文，核对官方论文集、作者、论文级标识和年份；短论文可纳入，单纯摘要/展示不算全文论文 |
| `journal_article` | 纳入候选；人工确认后成为正例 | 正式期刊文章，按出版方推荐书目信息和年份；accepted/in-press 的日期不是必然的正式出版年份 |
| `review` | 纳入候选；人工确认后成为正例 | 出版方或原文明确为学术综述；题目含 “review” 不足以确定类型 |
| `preprint` | 作为独立相关版本，进入扩展集 | 公开预印本的实际 ID、版本和日期；不是另一个正式发表正例；不能与正式版按标题自动合并 |
| `workshop_paper` | 初版扩展集 | 核对是否正式归档、是否为完整论文；若要纳入主集合，先统一修订范围政策，不能逐篇随意例外 |
| `book_chapter` | 初版扩展集 | 检查章节标题、章节作者、书名、编辑角色和章节 DOI；编辑整本书不代表撰写每一章 |
| `poster`、`slides` | 排除主论文集合，保留审核记录 | 教授制作展示材料不等于另发表一篇论文；它们与对应正式论文使用不同 ID，保留关联说明 |
| `supplementary_material` | 排除独立正例 | 归属附属材料；不能把补充材料当成另一篇完整论文 |
| `book`、`proceedings`、`dataset`、`software`、`editorial`、`thesis`、`blog`、`other` | 排除主集合，保留审核记录 | 逐一核对实体和贡献者角色；介绍 dataset/software 的正式学术论文仍可按其正式论文类型纳入 |
| `unknown` | 不进入已确认集合 | 证据不足时保留 candidate 或 needs_review，不推测成正式论文 |

纳入不以 DOI 是否存在、PDF 是否能取得或研究方向是否相近为条件。会议年份依据官方归档论文集；期刊依据正式推荐引用年份，在线与卷期日期差异作为核查证据。DOI 注册年份不自动代替出版年份。

## 字段与标签

原始字段包括 `candidate_id`、目标教授、title、authors、year、doi、venue、publication_type、来源 URL、来源观察和缺失/冲突提示。作者名单只反映来源实际署名；空数组明确表示来源缺失。原始 `publication_type` 是来源声明/提取建议，尚未经过人工确认。

CSV 的原始事实列保持不变；修改事实使用 `reviewed_title`、`reviewed_authors_json`、`reviewed_year`、`reviewed_doi`、`reviewed_publication_type`。空白表示保留原字段，`reviewed_doi=null` 明确表示人工移除错误 DOI。作者修正使用 JSON 字符串数组。不要仅因教授姓名已知而补写作者，也不要根据 DOI 拼接或猜测其他标识符。

| 人工字段 | 用法 |
| --- | --- |
| `review_status` | 初始 candidate；完成明确判定为 confirmed；证据不足为 needs_review |
| `ownership` | uncertain / belongs / not_belongs；未审条目必须 uncertain |
| `inclusion_decision` | 初始 pending；confirmed 明确 include 或 exclude；未解决为 uncertain/pending |
| `canonical_group_id` | 人工分配的出版物 ID；相同正式记录可同组，预印本、期刊扩展版、Poster、Slides 保持独立组 |
| `reviewer`、`reviewed_at` | 实际审核者和实际审核时间，时间含时区；不能用构建时间冒充人工审核时间 |
| `evidence_urls_json` | 人工实际核查的 URL 数组，至少两条；一般包括教授/机构书目和论文级出版社/会议记录，不应只是两个搜索结果页 |
| `identity_checked` | 已核查目标教授及逐论文署名身份，而不是只看姓名相同 |
| `metadata_checked` | 已核查标题、完整作者、年份、DOI/可靠 ID 和角色；缺 DOI 不直接判错 |
| `type_checked`、`version_checked` | 已判定出版类型及重复/版本关系 |
| `review_notes` | 记录核实理由、缺失字段、不同来源冲突、否定证据或无法确认的原因 |

四个检查字段仅在实际完成人工检查后写 `true`。confirmed 需要明确归属、纳入和类型、canonical group、审核者/时间和来源；检查脚本拒绝只改状态的条目。confirmed 表示人工完成判定，可能为 not_belongs 或 exclude，不必然是主论文正例。

## 逐篇人工流程

1. 复制 `candidates.csv` 为审核副本，按教授分工；先冻结本批范围、版本政策和原文件哈希。在人工核查结束前，不向审核者展示 Agent 的 verified、needs_review、PDF 成功状态或匹配分数。
2. 核对教授官方身份页。核对论文级完整作者、机构/历史任职、合作者、可证实的 ORCID 等。Aarti Singh 的 DBLP ORCID 冲突必须复核，不能把整个 DBLP 档案直接认作她的论文集合。
3. 打开论文级出版社或官方会议页面，记录实际题名、署名、年份和稳定 ID；再对照教授书目。仅有主页声明、缺少出版记录或出现冲突时可以保留 needs_review，不勉强确认。
4. 判定出版类型，依据上表统一决定 include/exclude。检查是否仅为展示、非归档摘要、编辑论文集、报告或附属材料；缺信息不补造。
5. 处理 [重复/版本队列](../data/ground_truth/professor_papers_v1/duplicate_version_review.csv)。同标题提示只是排查线索；根据 DOI、官方论文条目、完整署名及版本记录确定组。不同会议与期刊扩展版使用独立 ID。即使年份/作者一致，也不能只凭标题强行归并。
6. 填写人工字段、证据 URL 和说明。未查完仍 candidate；不能判定则 needs_review/uncertain。建议第二位审核者复核所有冲突、合并、类型未知和身份混淆条目，并在 notes 或独立复核日志记录结果。复核分歧先仲裁再确认。
7. 运行下面的离线检查。脚本只检验标注一致性，不验证人是否真正阅读过来源；未提供行仍保持 candidate，不删除。检查结果中的 confirmed 仅来自明确人工输入，不由来源元数据推断。

```bash
.venv/bin/python -m scripts.ground_truth.review_candidates \
  --candidates data/ground_truth/professor_papers_v1/candidates.json \
  --annotations annotations.csv --output annotations.validated.json
```

## 建立最终评估集合前的审计

`annotations.validated.json` 是多人多教授的审核工作文件，不是正式 Evaluation 输入。各教授 `*.pending.json` 仍是未修改的原候选。下一阶段按人工 canonical groups 整理每位教授的单独真值文件：主集合只保留已确认属于该教授、符合年份和出版类型的条目；明确不属于教授的、同范围的学术论文可进入独立负例集。扩展类型、排除条目和未解决条目另存，不能把类型排除简单标为“作者不属于教授”。

同一已确认出版物的不同来源 ID 列入 `source_records`，经人工确认的题名/作者/年份/venue 变体列入 `metadata_aliases`。不把预印本 DOI、会议 DOI、期刊扩展 DOI 任意合为同一出版物；不能加入从待评估 Agent 输出抄来的未经独立验证 ID。不要把人工参考 URL 作为检索或 PDF 发现的答案输入。

最后审计三位教授官方书目的窗口条目、DBLP 尚不可用的部分、未覆盖会议/期刊、访问失败、重复与同名风险。必须同时记录纳入、排除、未解决和来源缺口。**只有人工确认声明范围的正例全集完整后，才可设置 `complete_positive_list=true`**；它不是“候选表每行填完”的同义词。当前保持 false。

审核完成并冻结后，可按现有 [Evaluation 格式](evaluation.md) 整理单教授文件，填写实际 reviewer、reviewed_at、`metadata.annotation_state=human_reviewed`。正式 Precision/Recall 需要明确评估范围与正负例来源；缺负例或缺范围全集时不得宣称测得总体准确率或全生涯召回。当前不运行真实正式指标。

教授主要来自美国高校的 CS/ML、固定三年窗口，属于初版受限样本。不得据此推断其他国家、学科、年代或所有教授的系统表现。后续扩充应独立采集、使用新数据版本并保留审计轨迹。
## Aarti Singh 试点入口

2020–2022 年的 37 条候选已形成独立审核包，见 [试点审核与正式评估指南](ground_truth_pilot.md) 和 [数据目录](../data/ground_truth/aarti_singh_pilot_v1/README.md)。该流程可验证人工标注、转换 Ground Truth、核查 Agent 未匹配记录并控制正式指标发布；所有实际记录目前仍未人工确认。

