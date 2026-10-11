# Andrew Ng：人工身份选择后的检索与证据修正

2026-10-11。用户明确选择 `openalex:A5112456378`，已据此继续检索和下载。只使用该 Author ID，不合并竞争档案 `A5102617494`，也不将候选关联的 Semantic Scholar ID 自动加入统一身份。Resolver 原始 ambiguous、用户确认事件与逐篇归属状态分别保留。

人工清单已准备：[Hinton 28 项](hinton.ground_truth.pending.json)、[Ng 27 项](andrew_ng.ground_truth.pending.json)，共 15 项独立公开引用及 40 项检索结果审核。所有人工标签仍为 uncertain、审核者和审核日期为空；系统状态与自动建议不能替代人工判定。清单及标注入口见 [ground_truth_review.md](ground_truth_review.md)。正式 80% 目标尚未验收。

## 归属证据问题与修正

初次按选择身份收集到 354 条 OpenAlex 原始记录，4 页全部返回，无元数据分页数量上限；去重为 352 条，初次系统状态为 342 verified、10 needs_review。检查审核样本时发现循环证据：OpenAlex 逐论文作者的 ORCID 标记为 `resolved_author`，来自数据库作者实体，被当成独立的论文署名 ORCID；来源档案中的其他机构也可能被误当作已确认目标机构。

在发现问题后停止初次 PDF 流程，尚未下载该轮文件。修正后从完整原始证据离线重算，再启动新的正常 PDF 检索。没有修改数值匹配阈值，没有使用研究领域差异删除论文，也没有补造缺失数据。

- `paper_authorship` ORCID 才能单独提供正面的逐论文 ORCID 证据；`resolved_author`、`author_profile` 和未知 scope 的 ORCID 保留，但不能单独验证论文或建立合作者种子。实际论文机构、已确认的跨来源工作身份及固定种子合作者仍可提供其他证据。
- 论文机构优先与教授已匹配学校比较，不将来源作者档案的全部机构历史自动视为可信目标机构。明确提供机构上下文的旧归档/库调用仍可读取。
- 重分析保存结果时恢复实际选定候选的 requested/matched university 等字段，避免误取未选中的排名第一候选。

| 同一归档、同一评估规则 | 修正前 | 修正后 |
| --- | ---: | ---: |
| 原始记录 / 去重条目 | 354 / 352 | 354 / 352 |
| verified | 342 | 258 |
| needs_review | 10 | 94 |
| rejected | 0 | 0 |
| Ng 7 项公开参考：发现覆盖 | 7 / 7 | 7 / 7 |
| Ng 7 项公开参考：verified 覆盖 | 7 / 7 | 6 / 7 |

这是去除不独立证据后的变化，不是人工准确率提升的数值证明。258 个 verified 中，238 个有逐论文机构证据，20 个走固定已验证合作者辅助路径；这些规则仍可能受到数据库混合作者档案的影响，需要正负例人工标注。Hinton 同规则离线重算仍为 341 verified、232 needs_review，原有数据完整保留。

94 项待审核诊断：84 项存在不可独立使用的作者档案 ORCID，50 项缺失逐论文目标机构，9 项非标准/冲突出版类型，1 项署名不足。原因可重叠。审核样本中看似不相关的旧生化论文、医学章节和陶瓷条目转入待审核，原因是证据不独立或不足，未直接标注为人工误检。真实公开参考中的 `Building high-level features using large scale unsupervised learning` 也保留待审核，显示严格验证可能隔离真实论文；候选发现覆盖仍为 7/7。

## 100 篇 PDF 实测与目录修复

默认只处理前 100 个归属 verified 条目；94 个待审核条目跳过，158 个符合归属条件但未纳入本次 PDF 样本。所有记录仍在归档。HTTP 超时 6 秒、重试 0，下载上限每轮 25。数量只描述已处理范围，不代表教授发表全集或全部可公开获取 PDF。

多来源初次实测：23 篇有已验证 URL，共 32 个不同已验证 URL；15 篇仅有未验证候选、62 篇未找到可确认 URL。实际下载 22 个格式有效文件，20 个内容身份确认、2 个待审核，1 篇下载超时。当前 Unpaywall 未配置真实邮箱；Semantic Scholar DOI 查询出现 54 次错误，来源故障没有终止其他来源。86 篇来源查询不完整，不能将没有 URL 解释为没有合法公开版本。

随后发现旧目录 HTML 的通用解析遗漏：`<dd>` 未结束省略 `</dt>` 的题名采集，作者/年份混入标题；目录重定向后相对链接的基准路径也不正确。已修复，并允许用原始 OpenAlex secondary locations 实际提供的期刊名进行目录路由，不填充缺失主 venue，不注入人工参考文件中的 URL。

官方目录提供卷与文章链接，例如 [JMLR 卷目录](https://jmlr.org/papers/) 和 [旧版卷页面](https://jmlr.org/papers/v3/)。检索仍要求精确题名选择引用页面，读取实际提供的 `citation_pdf_url`，再进行响应/PDF 文件头和元数据身份验证，不猜测 PDF 路径。

仅重测发生变化的 journal_catalog Provider，处理集合与之前同为 100 篇，paper key 指纹一致，新增 2 个验证 URL及 2 个有效文件。其中一个内容确认；`Latent dirichlet allocation` 的文件可解析、正文题名一致，但 PDF 标题元数据冲突，继续保留 needs_review。没有为短标题或这一论文放宽内容验证。

| 同一 100 篇处理范围 | 多来源首测 | 加入目录增量后的唯一论文计数 |
| --- | ---: | ---: |
| 有已验证 PDF URL | 23 | 25 |
| 实际下载、格式有效 | 22 | 24 |
| 内容身份确认 | 20 | 21 |
| 内容待审核 | 2 | 3 |
| 下载失败 | 1 | 1 |
| 内容确认 / 处理论文 | 20 / 100 | 21 / 100 |
| 内容确认 / 下载尝试 | 20 / 23 | 21 / 25 |

合计 34 个不同已验证 URL，24 个不同 SHA-256 文件。增量文件与原文件的 paper key 无重合，两份原始 manifest 分别保留，没有重复累计同一篇论文。内容确认指自动证据规则通过，不等于人工认证。

7 项待确认参考中，PDF URL 覆盖由 0/7 增至 2/7，内容确认由 0/7 增至 1/7；其中 6 项实际处理、1 项归属待审核未处理。这一小样本上参考链接覆盖仍不足，作者公开页面旧 HTTP 链接、缺 DOI、数据源限流和未适配的会议目录均有影响。未为改善指标直接采用已知参考答案。没有独立负例及代表性完整真值，不能报告总体作者准确率或正式召回达标。

下载失败条目 `Automated coronary calcium scoring using deep learning with multicenter external validation` 在 nature.com 两个候选请求中超时，manifest 保留每次尝试。三个内容待审核文件隔离在各自 `papers/needs_review/`，保留格式与内容判断证据。已取得的 24 个文件均复核实际大小与 SHA-256 一致；失败条目没有本地文件，不纳入成功文件校验数。

全流程 title、authors、year、DOI、来源 Author ID 和归属证据传递无丢失；全部 352 个输入条目都有年份。104 项输入缺 DOI，继续保留缺失。所有文件名使用已有年份和稳定哈希。Python 3.8.0 下 366 项自动化测试全部通过，新增回归覆盖 ORCID 来源 scope、候选学校恢复、旧 HTML 目录、目录重定向和 secondary location 路由。

## 归档与复现

- [机器运行与文件校验汇总](andrew_ng.followup.json)
- [归属证据前后对比](andrew_ng.ownership_scope.comparison.json)
- [多来源实测的参考指标及错误诊断](andrew_ng.selected.reference_metrics.json)
- [目录增量的参考指标与相同范围对比](andrew_ng.catalog.reference_metrics.json)
- [修正后的完整论文归档](andrew_ng.ownership_scope.reviewed.json)
- [人工身份选择事件](andrew_ng.identity_selection.json)

```text
output/performance/
    andrew_ng_confirmed/search_Andrew_Ng_20261011T154644Z_80a277855670/
        papers.json
        pdf_urls.json
        manifest.json
        report.json
        papers/                  # 20 个内容确认 PDF
            needs_review/       # 2 个内容待审核 PDF
    andrew_ng_catalog/search_Andrew_Ng_20261011T160213Z_6ada4b9dd1be/
        papers.json
        pdf_urls.json
        manifest.json
        report.json
        papers/                  # 1 个内容确认增量 PDF
            needs_review/       # 1 个内容待审核增量 PDF
```

使用已收集、已重算的归档运行当前所有 PDF Providers；不会重新请求教授身份或论文列表。下面不设置 PDF/下载数量上限，未加入 needs_review：

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.workflow_cli \
  --input reports/performance/andrew_ng.ownership_scope.reviewed.json \
  --mode full --output-dir output/performance --timeout 20 --retries 2
```

只复现 100 篇范围时，加 `--max-pdf-papers 100 --max-downloads 25`。可信 CA 环境变量仅在该系统文件存在且 Python 需要它时使用，TLS 验证始终开启。目录增量单独查询，不重复请求未变动的来源；联网可用性和接口数据会变化，结果不保证完全相同。

人工审核完成后，使用 `performance_cli` 对归档、对应 PDF 报告和实际 manifest 离线评分；保持同一真值范围、版本政策和匹配规则。待审核清单不是教授正例全集，不能将 `complete_positive_list` 自动改为 true。
