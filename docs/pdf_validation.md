# PDF 内容验证与来源完整性

所有教授身份、论文归属以及已有 PDF 身份匹配阈值保持不变。缺 DOI 的内容确认仍要求不少于原配置的四词标题和完整作者署名；短标题不由出版社域名或 URL 状态放行。

PDF 内容解析区分科学标题与 DVI/TeX 等技术文件名，以及明确的书目系列前缀。首页多行标题允许恢复字内空白，但字符序列必须完全相同，且需要作者证据；不会做模糊标题匹配。作者脚注数字、姓氏在前的署名和兼容缩写复用现有作者签名规则；明确的中间名冲突仍阻止通过。作者证据限定在首页摘要/正文之前和 PDF 作者元数据，参考文献中的名字和 DOI 不作为本论文的身份证据。

原论文的年份、作者、DOI 和 venue 原样传递。已经通过 URL 身份验证的论文级引用记录可保留为 `publication_bindings`，其标题/年份/完整作者签名也一致时，可辅助核对出版社署名变体。不会因域名可信而确认内容。明确的发表年份差异保留待审核；来源已明确标为 preprint 的版本差异记录为辅助证据。已发现的 DOI/标题冲突仍按保守规则拒绝或待审核。

`validation.document_type` 区分 `full_text`、`preprint`、`poster`、`slides`、`supplementary_material` 和 `unknown`；`version_type` 保留来源声明，不能单独证明文档形式。判断依据为显式标题/元数据、摘要及页面结构，不用教授姓名或来源域名规则。类型未知时明确保留 unknown，分类是自动观察而非人工标注。

Poster、Slides 和附录可以与论文身份对应，但不默认算作完整论文。下载器保留文件和每次尝试证据，继续尝试下一条已验证候选；若最终只有此类文档，标为 `non_full_document` 和 needs_review。`identity_status` 与文件级 `validation_status` 分别保存。`full_text_verified_papers` 只统计内容确认且类型为 full_text/preprint 的文件，unknown 不在此计数中。

## 网络请求与报告

公开页面发现和 URL 探测共享响应缓存，包括重定向后的 URL 别名。仅 403/404/410 对单 URL 短时缓存；超时、429 和服务错误按主机冷却，不永久解释为资源不存在。请求和重定向均按主机调度；DOI 网关重定向后出版社限流，只冷却实际失败的出版社。Semantic Scholar 继续用批量/标题结果缓存，并逐次延长失败冷却。尊重 Retry-After，不绕过限流、TLS 或访问控制。

HTML 预览确实截断时从 64 KiB 扩展一次到默认 1 MiB；PDF 文件头探测仍只读取默认 1024 字节。超过最终页面/链接上限保留 incomplete，不能因为避免请求或压低错误数量就算搜索完成。缓存/读取大小、TTL、请求间隔和冷却上限集中在 `PDFConfig`。

报告区分：

- `errors`：实际来源不可用、网络故障、资源不可访问或阶段错误；复用输入中的失败另标历史问题。
- `warnings`：冷却跳过、页面或链接预算不足，保留查询不完整状态。
- `candidate_decisions`：普通候选过滤和 PDF 元数据身份冲突。
- `attempts[].validation`：每次实际下载的格式、内容身份及文档类型；拒绝内容和非全文独立于运行失败统计。
- `provider_request_statistics`：逻辑请求、缓存命中、冷却跳过及请求失败事件。每篇论文重复引用的 429 观察不是新一次请求；逻辑请求也不等于含内部重试/重定向的全部 HTTP 传输。

`search_completion_rate` 表示对处理范围完成所有配置的、有上限的来源查询；不代表已经穷尽互联网或所有合法版本。URL 通过探测、实际有效文件、自动内容确认、已确认全文分别统计。没有人工 Ground Truth 时，不称为内容准确率。

完整率定义由 Resolver 和离线比较共同维护（`configured_provider_status_v1`）。来源查询完整与是否取得全文互相独立：已取得全文但其他来源 deferred，搜索完整率仍不完整；所有来源查询完成但没有全文，也不代表搜索目标已满足。

新增 `search_sufficiently_satisfied`：只有 verified 论文归属、本地文件完整性、PDF 格式、内容身份及 full_text/preprint 类型全部确认才为 true。类型 unknown 为 null，其余证据不足为 false，具体原因保存在 `search_satisfaction_reason`。新 manifest 和工作流报告保留此指标；离线比较重新核对本地大小/SHA-256，输出 `search_sufficiently_satisfied_rate`。旧文件缺分类时该率为 null，不推算为零或成功，也不改变原完整率。

公开页面回退查询记住实际重定向边及缓存期限，避免在已知目的主机冷却时重复请求入口。多个不同 URL 一致导向同一冷却目标时，可按 `redirect_failure_budget` 暂停此回退来源；多目标网关不适用单一目标背压，冷却到期恢复，原候选不删除且查询状态保持不完整。独立 PDF 候选验证不启用这种未知路径的背压推断。后续子页面网络预算按整篇论文共享，缓存及请求前跳过不消耗该预算。

`public_resources.events` 只记录终止逻辑请求失败，`deferrals` 记录冷却决策。`cache_response_hits`/`cache_error_hits` 分开计数；`deferred_before_request`/`deferred_after_redirect` 区分跳过阶段。HTTP 层 `http_transport_statistics` 记录含内部重试的操作尝试、实际错误响应和网络/解析错误，但不会把缓存或 destination deferred 当作 HTTP 429 响应，操作尝试也不等于所有重定向跳数。共享 HTTP 客户端的计数按操作类型累计，可能包含较早阶段；PDF 阶段快照和下载阶段快照分开保存。

可对保存的两份运行生成离线诊断，不重新访问 API，也不修改原始输入：

```bash
.venv/bin/python -m paper_author_searcher.pdf_search_diagnostics \
  --before PATH_TO_BEFORE_RUN --after PATH_TO_AFTER_RUN \
  --output reports/performance/search_diagnostics.json
```

输出来源状态、仅冷却导致的不完整、完整率转换、请求分类、文件核对及独立全文满足指标。旧日志缺少 HTTP 重试或缓存细分时明确为不可确定。最新实测诊断见 [报告](../reports/performance/pdf_search_completion.md)。

## 离线复验

不访问 API，不改动原 PDF、manifest 或归属状态。先核对原文件大小与 SHA-256，使用原始已验证链接绑定和当前解析器，输出独立报告。

```bash
.venv/bin/python -m paper_author_searcher.pdf_revalidation \
  --manifest PATH_TO_RUN/manifest.json \
  --pdf-report PATH_TO_RUN/pdf_urls.json \
  --only-needs-review --output review_revalidation.json
```

去掉 `--only-needs-review` 可检查全部格式有效文件是否退化。缺文件、校验失败、缺对应元数据时不产生虚构验证结果。没有 OCR；扫描件或不可恢复的文本继续待审核。

## 同范围完整复测

在已配置真实 `UNPAYWALL_EMAIL` 的原终端执行。工具要求显式传入原 HTTP 超时和重试次数；不推测旧报告缺失的配置。使用原 Collector 存档，检查按原顺序选择的 verified 范围与基线完全相同，重新进行来源查询和下载，不重新运行 Resolver/Collector，也不使用人工参考集 PDF 答案。

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.pdf_validation_benchmark \
  --baseline-run output/performance/source_discovery/search_Andrew_Ng_20261011T193551Z_c9900b1fb016 \
  --output-dir output/performance/pdf_validation \
  --timeout 6 --retries 0
```

这里的 6 秒/0 次重试沿用此前提供的复测命令；旧 report 未保存该设置，若原终端实际使用不同值，请使用实际值。`SSL_CERT_FILE` 仅在该可信 CA 文件存在且 Python 需要时指定，TLS 始终开启。

工具创建独立运行目录，保留 papers、pdf_urls、manifest、report，以及 `comparison.json`。即使来源部分失败也输出比较，不把不完整检索误判为完成；重大中断或阶段失败不产出完整比较。比对元数据和归属指纹，核对文件大小及哈希，输出 URL 覆盖、有效文件覆盖、自动内容确认、待审核比例、搜索完成率和全文分类。旧文件未分类时全文指标为 null，而非推测为零。

邮箱从环境变量在内存读取，不写入报告、源码或命令参数。配置方法见 [pdf_discovery.md](pdf_discovery.md)。网络配额及页面状态会改变结果，新的 100 篇联网指标必须以实际新运行输出为准。

解析限制依据：[pypdf 文本提取说明](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)；限流处理依据：[Semantic Scholar 官方教程](https://webflow.semanticscholar.org/product/api/tutorial)。
