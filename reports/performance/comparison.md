# 真实检索与 PDF 覆盖对照：2026-10-11

已完成无页数限制的论文检索、相同 30 篇的优化前后对照，以及扩大到 100 篇的 PDF 测试。教授和论文归属阈值未调整。没有收到独立人工 Ground Truth；本报告的参考集合是从公开页面提取、等待人工核实的真实引用记录，**不能宣称正式达到 80% 人工真值召回率**。

首轮性能优化在 Python 3.8.0 实际运行 357 项自动化测试全部通过，新增测试覆盖指标分母、完整列表负例、状态冲突、评估版本与处理范围、缓存 ID 不一致、来源故障、文件完整性、候选身份隔离和出版方署名补充证据。用户随后明确选择 Ng 的身份；后续归属证据与目录解析修正的 366 项测试全部通过，结果见 [Ng 后续报告](andrew_ng.followup.md)。

## 身份与全量元数据

Hinton 当前真实 Resolver 使用姓名、University of Toronto、研究方向 machine learning，返回 resolved，原始分数 0.970，OpenAlex/Semantic Scholar ID 与原保存身份一致。本轮下游对照复用先前已确认身份，不把待审核候选强行确认。

| 对象 / 独立候选 | OpenAlex 返回 | Semantic Scholar 返回 | 去重条目 | 归属状态 |
| --- | ---: | ---: | ---: | --- |
| Hinton：优化前 | 428，5 页完整 | 462，5 页完整 | 573 | 341 verified、232 needs_review、0 rejected |
| Hinton：优化后 | 428，5 页完整 | 462，5 页完整 | 573 | 341 verified、232 needs_review、0 rejected |
| Ng：竞争候选 1 | 13，1 页完整 | 无该候选确认 ID | 13 | 全部 needs_review，身份未确认 |
| Ng：竞争候选 2 | 354，4 页完整 | 4，1 页完整 | 354 | 全部 needs_review，身份未确认 |

“分页完整”仅表示该数据库对指定 Author ID 已返回到末页，不表示实际教授发表全集完整。Hinton 的 2 条原始 OpenAlex 记录被拒绝，合并条目仍保留其他记录及证据；表中 0 rejected 是条目数，不是原始记录数。

上表记录人工选择前的 Ng 诊断。两个竞争候选原始分数均为 0.870：`A5102617494` 与 `A5112456378`；后者候选关联 Semantic Scholar `2067948334` 和来源 ORCID。数据不足以确认两组属于同一人，来源机构也有差异。当时分别保存诊断、保持 ambiguous，未自动选择或下载。随后用户明确选择 `openalex:A5112456378`；后续流程仅使用这一 Author ID，不自动授权其他来源的候选别名。候选局部证据、Resolver 原始歧义和用户确认事件分别保留。

DBLP 身份请求返回 HTTP 200、`text/html` 防爬页面，保留错误；不绕过验证。Hinton 没有确认的 DBLP ID，该来源不可用，整体报告 partial。当前未配置真实 Unpaywall 邮箱；该 Provider 明确 disabled，不使用虚构邮箱。

## 相同 30 篇的对照

通过稳定论文 key 检查，两次实际处理集合完全相同。全部输入仍保存 573 条，另外 311 篇归属 verified 未执行 PDF 检索、232 篇归属待审核被默认隔离。最多下载 10 篇未触及上限。HTTP 测试超时 6 秒、重试 0，两组保持一致；网络可用性变化不能完全归因于代码。

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 实际处理的归属 verified 论文 | 30 | 30 |
| 有已验证 PDF URL 的论文 | 3 / 30（10.0%） | 5 / 30（16.7%） |
| 实际取得且严格解析有效的文件 | 3 | 5 |
| 内容身份已确认的文件 | 2 / 30（6.7%） | 4 / 30（13.3%） |
| 下载后内容仍待审核 | 1 | 1 |
| 下载尝试失败 | 0 / 3 | 0 / 5 |
| 内容确认 / 已尝试下载 | 2 / 3（66.7%） | 4 / 5（80.0%） |
| Semantic Scholar PDF 查询错误观察 | 26 | 0 |
| 全流程错误观察（含未配置来源） | 120 | 95 |

新增链接来自真实期刊卷目录中的引用页面，而非猜测 DOI/PDF 路径。下载验证同时读取了已绑定引用页面的完整署名，解决出版方省略中间名、数据库显示中间名的证据传递问题；仍要求文件中的独特标题与全部署名一致。不降低作者归属阈值、不修改论文归属。

原来的短标题论文仍在 needs_review：PDF 内部标题字段是出版标识而不是文章题名，且缺少可确认 DOI。保守判定没有为了增加数量而放宽。

## 扩大到 100 篇

使用同一完整论文集合，检索前 100 篇归属 verified 的论文；下载上限 25 实际未触及。

- 18 篇有已验证 URL，共保留 26 个验证通过的候选 URL；不能把 26 当作论文数。
- 12 篇仅有未验证候选，70 篇没有可确认 URL。
- 实际下载 18 个文件；18 个严格 PDF 格式检查通过，9 个内容身份 verified，9 个 needs_review，下载失败 0 个。
- 内容确认覆盖为本次处理范围的 9/100；内容确认 / 下载尝试为 9/18，50%。仅格式成功是 18/18，100%，不能用它替代身份确认成功率。
- 实际文件大小和 SHA-256 全部复核一致；没有仅依据 manifest 状态宣称文件已取得。
- 73 篇的来源查询不完整；241 篇符合自动处理归属条件但未纳入本次 PDF 样本，明确保留 not_processed。无 URL 不代表不存在合法公开版本。

各来源贡献有重叠：arXiv 为 8 篇提供验证通过的 URL，OpenAlex 3 篇，Semantic Scholar 7 篇，保存元数据 9 篇，公开落地页 7 篇，期刊目录 2 篇。不能将这些数相加作为总论文覆盖。

9 个内容待审核文件保存在 `papers/needs_review/`。主要原因是标题元数据冲突、文本提取后的题名无法严格确认、完整署名在前部无法得到足够证据；不因文件可解析或 DOI 出现就直接确认。所有原文件和判断证据保留。

## 待人工审核参考集合

参考页面依据 [Hinton 的公开书目](https://www.cs.toronto.edu/~hinton/papers.html)、[Ng 的公开书目](https://ai.stanford.edu/~ang/papers.php)，引用元数据实际读取自 JMLR、arXiv、PMLR 和 ACL 公开页面，例如 [JMLR](https://jmlr.org/papers/v15/srivastava14a.html)、[PMLR](https://proceedings.mlr.press/v15/coates11a.html)、[ACL](https://aclanthology.org/P11-1015/)。没有生成虚构论文、作者或人工审核者。全部 ownership=uncertain，annotation_status=pending_human_review。以下使用自动建议进行**临时诊断**，不是人工验收分数：

| 参考范围 | 发现覆盖 | 归属 verified 覆盖 | 验证 URL / 实际内容确认 |
| --- | ---: | ---: | ---: |
| Hinton 8 项：优化前 | 8 / 8 | 7 / 8 | 0 / 8；0 / 8 |
| Hinton 8 项：优化后 | 8 / 8 | 7 / 8 | 2 / 8；2 / 8 |
| Ng 7 项：候选 1 单独诊断 | 0 / 7 | 0 / 7（身份未确认） | 未执行 |
| Ng 7 项：候选 2 单独诊断 | 7 / 7 | 0 / 7（身份未确认） | 未执行 |

Hinton 参考中仅 2 项进入了这两次 PDF 样本；其余大部分在较后位置，另 1 项归属待审核，不作自动下载。扩大到前 100 篇仍只处理了其中这 2 项，因此参考 PDF 覆盖不能外推到全部论文。已评分 verified 参考的 precision 为 7/7，decision coverage 为 7/8；没有独立负例、562 条输出未评分，不能解释为所有 341 条 verified 均正确。

两侧都使用同一 Evaluation 匹配规则，支持逗号署名、缩写及缺省中间名，并阻止明确中间名冲突和多候选歧义。之前过严格的参考署名匹配会漏掉已有记录；**修正评估器识别已有记录不是检索召回提升**。

去重诊断在 16 条可匹配参考原始记录中发现 5 对同组记录、3 对分散条目，没有观察到已评分错误合并；标注仅覆盖 16/890。这 3 对可能涉及版本差异，仍需人工核实出版物范围，暂未为改善指标强行合并。教授 ID 真值也未人工提供，Resolver 的准确率 unavailable。

## needs_review 与未获取原因

Hinton 的 232 个待审核条目中：232 项目标 ORCID 缺失、209 项缺失逐论文目标机构、37 项署名证据不足或不兼容、25 项涉及非标准或冲突类型、9 项大规模作者列表。原因重叠，不能求和；缺 ORCID 本身不等于作者错误，也不是放宽规则的依据。

例如真实参考中的一篇已被两个来源发现，但预印本和另一来源条目未取得足够跨来源身份确认，仍保留 needs_review；96 人署名的报告不允许靠共同作者重合自动验证。研究领域差异不作直接删除依据。

扩大样本中的未确认 URL 诊断包括 51 项限流、14 项访问限制、12 项链接不存在、9 项 HTML 响应、6 项网络问题；62 项无候选且查询不完整。计数来自失败或未确认条目，可重叠。下载内容未确认另有 9 项。每篇在 JSON 中保留 URL、状态、来源、HTTP 观察、原始错误和身份/内容证据；没有公开链接、无法完成查询和真实下载失败分开记录。

## 文件与复现

完整机器报告：[相同范围对比](hinton.comparison.json)、[运行与限制](benchmark_runs.json)、[扩大样本的参考指标和诊断](hinton.expanded.reference_metrics.json)。待审核参考及抽样检索结果共 55 项，见 [人工待确认清单](ground_truth_review.md)；全部人工标签仍是 uncertain。人工审核完成后，使用 [性能评估 CLI](../../docs/performance.md) 对同一归档重新评分，不必重新请求 API。

扩大测试目录：`output/performance/expanded/search_Geoffrey_E_Hinton_20261011T144354Z_60bb94842e3a/`。

```text
search_Geoffrey_E_Hinton_20261011T144354Z_60bb94842e3a/
    professor.json
    papers.json
    pdf_urls.json
    manifest.json
    report.json
    papers/
        9 个内容已确认 PDF
        needs_review/
            9 个内容待审核 PDF
```

不设测试数量上限的完整运行命令：

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.workflow_cli \
  --name 'Geoffrey Hinton' --university 'University of Toronto' \
  --research 'machine learning' --mode full \
  --output-dir output/performance --timeout 20 --retries 2
```

该 CA 环境变量只在系统有此可信 CA 文件且 Python 需要它时使用。Ng 身份已由用户明确选择，当前正式 80% 目标仍待独立人工标注验收。可配置真实 Unpaywall 邮箱以补充合法公开来源，不能保证来源可用或论文全集完整。
