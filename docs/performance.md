# 真实场景评估与来源优化

`performance_cli` 在已有 Evaluation 上增加 PDF URL 与本地文件指标，读取保存的 JSON，不请求 API、不重跑身份和归属算法。`reference_import` 只准备公开页面中的真实引用元数据；人工标注和自动参考集合分开保存。

## 完整检索

```bash
python3 -m paper_author_searcher.workflow_cli \
  --name 'Geoffrey Hinton' --university 'University of Toronto' \
  --research 'machine learning' --mode full --output-dir output/performance \
  --timeout 20 --retries 2
```

不传 `--max-pages`、`--max-pdf-papers`、`--max-downloads` 时，分页和后续处理没有测试数量限制，仍遵守 HTTP 大小上限、超时和限流。身份 ambiguous 时停在候选确认阶段；必须在检查候选证据后明确指定 `--select-author SOURCE:AUTHOR_ID`。不要根据候选论文数量自动选择身份。

可从 Collector 的 `papers.json` 继续，跳过重复论文检索：

```bash
python3 -m paper_author_searcher.workflow_cli \
  --input output/SEARCH_DIRECTORY/papers.json --mode full \
  --output-dir output/performance --timeout 20 --retries 2
```

缺少新 OA 字段的旧数据仍可使用；要刷新 API 字段应从保存的教授身份重新收集，不能用离线推断填充原始证据。URL 报告中的未处理条目会明确标记，不当作来源失败。

## 人工 Ground Truth

沿用 [Ground Truth 格式](evaluation.md) 和 [空模板](../schemas/ground_truth.template.json)。确认范围、版本政策、年份区间、出版类型，并从独立的作者主页、机构页面和出版社记录核对 DOI、年份、完整作者与来源 ID。应包含较早和较新、常见和低引用论文，以及明确不属于目标教授的负例；热门论文正例小样本不能证明总体作者匹配准确率。

`metadata.annotation_state=human_reviewed`、非空 `reviewer` 和 `reviewed_at` 表示有人完成了审核。每条记录使用 `belongs`、`not_belongs` 或 `uncertain`；已判定记录的 `annotation_status` 不能仍为 `pending_human_review`。只有确认指定范围的正例列表完整时，才设 `complete_positive_list=true`。教授身份 ID 真值也须独立人工确认，不能复制 Resolver 输出充当正确答案。

准备待审核参考文件的输入格式为 `{"pages": [{"url": "https://publisher.example/paper", "context": "independent bibliography source"}]}`：

```bash
python3 -m paper_author_searcher.reference_import \
  --pages reference_pages.json --name 'Professor name' \
  --output reference.pending.json --timeout 20 --retries 2
```

它保留引用页面、原始作者名与公开 PDF 链接，所有 `ownership` 都是 `uncertain`，审核信息为空。`proposed_ownership` 仅是基于页面署名的自动建议，不能当作人工判定。元数据缺失会记录导入错误；这些参考 PDF 链接不注入生产检索，防止测试答案泄漏。

本轮已准备 [55 项人工待确认清单](../reports/performance/ground_truth_review.md) 和每位教授的整合 JSON。独立公开引用与检索结果审核通过 `review_pool` 分开；后者可核查误检，不能单独估计系统未发现的论文。全部人工标签仍为 uncertain。审核后另存为 reviewed 文件，再运行下面的离线评估；不要修改原始 pending 档案或直接采用系统状态作为人工判断。

## 离线指标与对比

```bash
python3 -m paper_author_searcher.performance_cli \
  --collection output/SEARCH_DIRECTORY/papers.json \
  --ground-truth ground_truth.reviewed.json \
  --pdf-report output/SEARCH_DIRECTORY/pdf_urls.json \
  --manifest output/SEARCH_DIRECTORY/manifest.json \
  --output performance.after.json --before performance.before.json
```

`--before` 可省略。比较要求完全相同的 Ground Truth 文件、标注范围及评估匹配规则版本，包含各指标的分子、分母和绝对差值。`same_pdf_processing_scope` 通过论文 key 的指纹检查处理集合。处理样本也应相同；扩大数量后的结果单独报告，避免把数量变化归因于算法。评估器升级后应重新评分两侧保存数据。

| 指标 | 分子 / 分母 |
| --- | --- |
| `paper_discovery_recall` | 三种归属状态中发现的正例 / 标注正例 |
| `verified_paper_recall` | 归属 verified 正例 / 标注正例 |
| `author_match_precision` | 归属 verified 正例 / 已评分 verified 正负例 |
| `author_decision_accuracy` | 正确确认或拒绝 / 已判定正负例；排除待审核与状态冲突 |
| `author_decision_coverage` | 已判定正负例 / 已检索到的已标注论文 |
| `validated_pdf_url_coverage` | PDF 响应及论文身份均验证的正例 / 标注正例 |
| `validated_pdf_url_rate_among_processed` | 验证 URL 正例 / 实际处理过 PDF 检索的正例 |
| `actual_download_coverage` | 本地文件格式与身份已验证的正例 / 标注正例 |
| `actual_download_success_rate` | 本地内容确认的正例 / 已尝试下载的正例 |
| `download_format_success_rate` | 格式有效正例 / 已尝试下载的正例 |

本地文件还需存在、路径安全且实际大小和 SHA-256 与 manifest 一致。只得到 URL、只有 `%PDF` 文件头、可解析但身份待审核都不能算实际下载成功。未提供 PDF 报告或 manifest 时，对应指标 unavailable；空分母为 null。Precision、Recall、F1、误检、漏检、重复遗漏及错误合并仍保留在 `ownership_evaluation`。

若尚未完成人工审核，可加 `--reference-only` 进行临时诊断：它临时使用自动建议，不修改参考文件，报告明确标记 `provisional_reference_not_human_ground_truth`，即使数值超过 80%，`formally_met` 仍为 false。不要把这类结果当成正式验收分数。

## 原因与来源证据

`needs_review_diagnostics` 分别记录缺失作者详情、目标 Author ID 缺失或冲突、缺失逐论文机构、ORCID 缺失、署名不足、异常作者列表、非标准出版类型和未确认的教授身份。这些是已有证据的分类，可能重叠，不改变任何判定。

PDF 和下载结果新增 `failure_reasons`，包括 `access_restricted`、`rate_limited`、`url_not_found`、`network_error`、`not_pdf_response`、`source_identity_mismatch`、`download_failed`、`downloaded_paper_identity_unconfirmed` 等。未配置 Unpaywall、来源错误时标为 `search_incomplete`；只有完成已配置来源且未发现公开链接时，才使用 `no_public_pdf_found_in_queried_sources`。这不代表互联网上不存在合法公开版本。数量限制和归属不适合自动处理分别是 `not_processed_limit`、`ownership_not_eligible`。

## 优化边界

- Collector 保存 OpenAlex 全部 OA locations 和 Semantic Scholar `openAccessPdf`。PDF Providers 复用与原论文 ID 一致的原始元数据，旧数据缺字段时才查询 API；保留空值，不编造 URL。
- arXiv 缺已知 ID 时可按完整标题检索，仍要求关联证据和轻量 PDF 验证；标题相同不直接确认新 ID。请求间隔遵守配置，版本冲突保持待审核。
- 独立的 `journal_catalog` Provider 从配置的期刊卷目录找精确标题的公开引用页面，再读取实际提供的 `citation_pdf_url`。路由可使用原始 OpenAlex 其他 locations 中实际提供的期刊名，主 venue 缺失继续为空。支持 HTML 省略 `</dt>` 的目录与重定向后目录的相对链接，不把作者/年份当成标题。默认支持 JMLR 的目录结构，可通过 `PDFConfig.journal_catalogs` 注入适用目录；其他目录格式需独立适配，不猜路径。
- 下载内容验证允许使用已验证引用页面的完整署名作补充，但仍要求文件中的独特标题及全部署名一致。文件标题/DOI 冲突不被这一补充覆盖。
- 原有 Professor Resolver、Ownership Validator、Deduplicator 阈值不变。Evaluation 的完整作者签名规范化用于评估匹配，明确的中间名冲突与多候选歧义仍阻止匹配。

人工选择 Author ID 只确认检索身份。Ownership Validator 区分 `paper_authorship` ORCID 与 `resolved_author` / `author_profile` ORCID：后两者来自数据库作者实体，不能独立证明每篇论文归属，也不能凭此建立合作者种子。来源未知的旧 ORCID 保守审核。正面 ORCID 证据的适用 scope 由 `OwnershipConfig.paper_orcid_scopes` 集中管理；Providers 原始 scope 与缺失字段不被改写。

逐论文机构只与已匹配目标学校比较；来源作者档案中的其他、未经确认的历史机构保留为上下文，不自动用作正面归属证据。明确提供机构上下文的库调用与旧归档仍可读取。保存结果重分析会恢复实际选定候选的学校字段，避免使用排名第一但未选中的候选或丢失 requested/matched university。

`candidate_diagnostics` 可分别抓取排名前两位未确认候选的元数据，用于分析遗漏：

```bash
python3 -m paper_author_searcher.candidate_diagnostics \
  --identity professor.ambiguous.json --output-dir candidate_diagnostics
```

该命令不合并候选、不确认教授、不给论文自动 verified，也不检索或下载 PDF。诊断数量和已知参考覆盖不能替代用户身份选择。
