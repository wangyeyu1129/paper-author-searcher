# PDF 来源发现与复测

本轮修改复用 PDF Resolver、URL Validator、Downloader 与文件内容验证器。教授身份、论文归属、PDF 元数据和内容匹配规则未降低，也不读取 Ground Truth 的 PDF URL 作为检索答案。

## 来源与错误处理

- Semantic Scholar 按已知 DOI、Paper ID 或 arXiv ID 批量查询；`ids` 放在 JSON 请求体，`fields` 放在 query。响应数组必须与请求数量相符，null 表示未收录。批量格式异常可回退单篇请求。缺标识符或查询 404 可进行有数量上限的标题检索，仍要求现有 DOI/完整作者署名/年份等身份规则。
- Semantic Scholar 默认逻辑请求间隔 1 秒；429、超时、网络或服务错误触发来源级冷却，401/403 停止继续请求该来源。冷却期间的跳过与真实请求数分别记录，其他 Provider 正常执行。HTTP 重试由现有客户端管理；超出等待预算的 Retry-After 不提前重试，保留等待时长供来源调度。
- Crossref 只使用 API 登记的 `application/pdf` 全文链接和真实落地页，不从 DOI 猜测文件地址。Crossref 返回链接不代表匿名可访问，仍需 URL 验证。缺 DOI 或该 DOI 未收录时，标题检索也必须通过现有论文匹配规则。可选联系邮箱从 `CROSSREF_MAILTO` 读取。
- Unpaywall 解析所有 OA locations 的 PDF 与落地页，保留版本与许可；普通落地页不能直接作为 PDF。邮箱未配置时明确 disabled，API 故障不代表不存在合法 PDF。
- OpenAlex 非 OA/缺标记的已提供链接可交给页面探测；可信学术/机构来源必须实际返回匿名 PDF 且身份绑定通过，才成为可验证候选。不改写原始 OA 标记，不推断许可。
- arXiv ID 可来自已有 external IDs、实际 arXiv 链接以及注册的 arXiv DOI 命名空间；下载地址只使用 API 返回的 PDF link，保留显式版本要求。
- Europe PMC 按 DOI 查询；结果带 PMCID 时，再加一条 NCBI PMC Open Access 数据集的地址（`pmc_open_data_url_template`），不依赖 Europe PMC 网站。该网站可能连续数小时拒绝自动访问。不在数据集内的论文返回 404，不影响其他来源。
- 来源查询因限流、超时、网络或服务错误失败时，所有来源跑完后，对仍没有已验证链接的论文再问一次（`source_retry_rounds`、`source_retry_wait`）。新结果替换原来的失败记录。
- 作者主页、大学仓储、期刊和会议页面解析明确 PDF 引用元数据、alternate 链接和 ScholarlyArticle 结构化数据。`--bibliography-url` 可重复指定公开目录；标题线索只能引导访问文章页面，或保留未经确认的 PDF 候选。页面读取、缓存和后续链接数量有配置上限，触及上限会报告不完整。未知 HTML 页面、验证码和访问限制不会被当作成功。

HTTP(S) 页面和 PDF 探测复用下载模块的 DNS 检查及固定地址连接，禁止私网 DNS、非预期端口和不安全重定向。TLS 验证始终开启。来源地址、请求间隔、批量大小、冷却时间、检索数量、页面限制均在 `PDFConfig`；重试和超时在 `HttpConfig`。

## 在本地设置真实邮箱

在项目目录的 macOS zsh 终端执行以下命令，提示出现后输入你自己的真实联系邮箱。输入不回显，邮箱值不写入 shell 命令历史，也不需要写入项目文件。

```zsh
read -r -s "UNPAYWALL_EMAIL?请输入你的真实联系邮箱："
echo
export UNPAYWALL_EMAIL
```

变量只供该终端及其后续子进程使用。另一个终端或已经启动的 Codex 进程不会自动继承它。无需把邮箱发送到聊天；也不要将实际值写进 README、源码、命令行参数或提交到 GitHub。`.env` 和 `.env.*` 已忽略，但程序不会自动读取这些文件。

`--require-unpaywall` 仅检查配置存在且格式合法，不能证明联系人归属或保证接口正常。结果与错误诊断会脱敏邮箱；Unpaywall 请求本身按官方要求将邮箱提供给其 API。

## 对相同 100 篇进行复测

从保存的完整论文归档读取，继续按原顺序处理前 100 篇 ownership verified。不要使用包含新论文排序或改变归属状态的归档。输出到新的运行目录；PDF 不设 25 篇下载上限，以免遮蔽新增链接的内容验证结果。

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.workflow_cli \
  --input reports/performance/andrew_ng.ownership_scope.reviewed.json \
  --mode full --output-dir output/performance/source_discovery \
  --max-pdf-papers 100 --require-unpaywall --timeout 6 --retries 0
```

这里保留此前实测的短超时和零重试，用于比较来源发现变化。网络环境可能改变结果；生产运行可另用 `--timeout 20 --retries 2`，报告应注明条件变化。仅在系统存在该可信 CA 文件且 Python 需要时设置 `SSL_CERT_FILE`，不关闭证书验证。

如只查询 URL，把 `--mode full` 改为 `--mode pdf-urls`；这时没有新的 PDF 内容确认率，不能将 URL 数量当作内容确认数量。可另加公开 `--bibliography-url`，记录该外部输入；不要填人工 Ground Truth 中逐篇给出的 PDF 答案。

## 离线比较

将 `AFTER_RUN` 设置为上条命令实际打印的完整新运行目录。工具检查前后处理的论文标识、年份、作者、Author ID、归属状态和证据一致；合并旧多来源与目录增量基线，不重复计数。只接受 URL 与身份均 verified 的候选；内容确认要求本地文件大小/SHA-256 正确，并保留的 PDF 解析与内容验证均通过。

```bash
AFTER_RUN='output/performance/source_discovery/替换为实际新运行目录'
.venv/bin/python -m paper_author_searcher.pdf_discovery_comparison \
  --before output/performance/andrew_ng_confirmed/search_Andrew_Ng_20261011T154644Z_80a277855670 \
  --before output/performance/andrew_ng_catalog/search_Andrew_Ng_20261011T160213Z_6ada4b9dd1be \
  --after "$AFTER_RUN" \
  --output reports/performance/pdf_discovery.comparison.json
```

输出包含 URL 覆盖、有效文件覆盖、内容确认覆盖、下载尝试与失败、各来源贡献、获得/失去链接的论文标识和文件完整性核对。来源失败类别可重叠；新报告区分 API 记录 404 与 PDF URL 404，并记录冷却跳过、限流、超时和异常响应。完整返回某个来源的元数据，不表示教授的全部论文或所有合法 PDF 都已覆盖。自动内容确认也不是人工 Ground Truth。

接口依据：[Semantic Scholar 官方 API](https://api.semanticscholar.org/api-docs/)、[Unpaywall 官方 API](https://unpaywall.org/api)、[Unpaywall 数据格式](https://unpaywall.org/data-format)、[Crossref 官方 API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[arXiv 官方 API 文档](https://info.arxiv.org/help/api/user-manual.html)。
