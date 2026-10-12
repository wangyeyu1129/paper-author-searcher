# Andrew Ng：PDF 验证与来源完整性优化

后续进展：用户已完成新的 100 篇联网复测，取得 50 篇验证 URL、49 份格式有效文件、44 篇自动内容确认，来源完整率为 7%。原因、独立全文满足指标和本轮调度修复见 [pdf_search_completion.md](pdf_search_completion.md)。下面保留之前 47 份文件的离线交付记录。

2026-10-11。基线为用户已完成的运行 `output/performance/source_discovery/search_Andrew_Ng_20261011T193551Z_c9900b1fb016`。本轮已完成代码、针对原 20 篇 needs_review 的离线复验及全部 47 份下载文件的回归复验。用户选择在已配置邮箱的原终端执行完整联网复测，因此本报告尚不包含新的来源覆盖率或搜索完成率，不将离线复验说成新的下载。

## 指标与范围

原存档有 352 条论文，本次固定范围为按原顺序处理的前 100 篇 ownership verified。教授身份、论文归属及元数据不改动。所有 47 份本地 PDF 的大小和 SHA-256 核对通过；旧 manifest、PDF URL 报告及文件均未重写。

| 指标 | 原运行 | 当前本地复验 |
|---|---:|---:|
| 有验证通过的 PDF URL | 48/100（48%） | 来源未重检，不能报告新覆盖率 |
| 实际格式有效下载 | 47/100（47%） | 47 份原文件均完整，不是新增下载 |
| 下载成功率（有效文件/尝试论文） | 47/48（97.92%） | 新下载未执行 |
| 自动内容确认（处理范围） | 27/100（27%） | 43/100（43%） |
| 自动内容确认（47 份有效文件） | 27/47（57.45%） | 43/47（91.49%） |
| 待审核比例（47 份有效文件） | 20/47（42.55%） | 4/47（8.51%） |
| 完成全部配置来源查询 | 13/100（13%） | 完整联网复测待执行 |
| 自动确认且类型为全文/预印本 | 未分类，不能推算 | 34/100（34%） |

43 篇是自动确认对应论文的文件数量，其中 9 篇文档类型仍 unknown；只有另 34 篇同时满足全文/预印本分类。不把格式有效、URL 已验证或标题匹配等同于已获得完整论文。以上是固定检索范围的覆盖与自动判定，未使用人工 Ground Truth，不称为人工核实的准确率。

机器结果：[原基线](pdf_validation_search.baseline.json)、[20 篇定向复验](pdf_validation.review20.json)、[47 份文件复验](pdf_validation.all_downloaded.json)、[本地比较](pdf_validation.local_comparison.json)。其中逐文件保留原/新判断、校验结果、提取的首页证据、来源出版记录、文档类型及依据。

## 内容判定的变化

原 20 篇 needs_review 中 16 篇通过当前内容核对，4 篇继续待审核；原 27 篇 verified 没有退化。改善来自可追溯证据提取：DVI/TeX 文件名不再误作不同的科学标题，首页脚注数字得到清理，署名次序和兼容缩写复用已有完整作者签名规则，系列前缀与标题分开，字内空白仅按完全相同字符序列恢复。并非降低标题长度、作者匹配或 DOI 冲突要求。

| 仍需审核的条目 | 原因 |
|---|---|
| Latent dirichlet allocation | 缺 DOI 的三词标题，低于原四词要求；修复 DVI 标题后仍不放行 |
| Multimodal Deep Learning | 同样受原缺 DOI 短标题规则限制 |
| Dynamic Pooling and Unfolding Recursive Autoencoders for Paraphrase Detection | 保存的署名为 Jeffrey Pennin，PDF 为 Jeffrey Pennington；不擅自改写作者或放宽签名 |
| Learning Syntactic Patterns for Automatic Hypernym Discovery | 单页超大版面且元数据明确为 Poster；对应论文的身份可匹配，但不是论文全文 |

全部文件的类型观察为 full_text 35、preprint 2、poster 1、unknown 9。Poster 文件继续保留；下载器会尝试其他已验证候选，而非直接把该文件算作全文。没有新增 rejected；实际内容冲突继续由既有拒绝规则处理，合成回归测试检查其不会因本轮格式修复而放行。

## 搜索完整性修改

原报告有 87 篇检索不完整，Public Pages 记录 212 次元数据预览上限观察，Semantic Scholar 和 CiteSeerX 共记录 146 次 HTTP 429 观察。这些是按论文保存的诊断，可以重复引用相同请求/冷却原因，不应解释为 146 次独立网络请求。

本轮共享公开页面与 URL 探测缓存，缓存重定向别名，按主机及重定向目标调度请求；短时缓存单 URL 的确定失败，网络故障不永久缓存为缺失。429/超时/服务异常按来源冷却并逐次延长，尊重 Retry-After。出版社在 DOI 重定向之后失败时，只冷却实际失败主机，不封禁全部 DOI 查询。Semantic Scholar 批量结果和查询缓存保留，连续失败延长来源冷却，其他 Provider 仍继续。

HTML 预览确有截断时从 64 KiB 扩展一次到最多 1 MiB；期刊目录保留独立的较大读取预算，PDF 文件头仍只轻量读取。最终大小/链接预算不足及冷却跳过继续标记 incomplete，不把减少请求数量当作搜索完整。

普通候选过滤、PDF 身份冲突、读取/链接预算不足、来源不可用和网络故障分别记录。正常过滤和内容拒绝不再计作运行失败；拒绝文件保留在隔离目录。新报告另列逻辑请求失败事件和冷却跳过，可区别“多个论文受同一次失败影响”与新请求失败。

## 完整联网复测

在你已设置真实 `UNPAYWALL_EMAIL` 的原终端执行，不需要把邮箱发送给 Codex 或写进项目。命令使用保存的 Collector 结果，检查同一 100 篇范围，再进行新查询和下载，不重新请求教授/论文列表、不读人工参考集 PDF 答案。

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.pdf_validation_benchmark \
  --baseline-run output/performance/source_discovery/search_Andrew_Ng_20261011T193551Z_c9900b1fb016 \
  --output-dir output/performance/pdf_validation \
  --timeout 6 --retries 0
```

6 秒/零重试沿用此前提供的终端复测命令。旧 JSON 没有保存实际 HTTP 配置，因此不能从旧报告证明该设置；若你当时实际采用不同值，应按实际值替换。新报告会保存 timeout/retries，邮箱仅保存“已配置”的布尔值；TLS 保持验证。

新独立目录包含 `papers/`、`papers.json`、`pdf_urls.json`、`manifest.json`、`report.json` 和 `comparison.json`。比较同时报告 URL 覆盖、有效文件、内容确认、待审核比例、来源完成率及全文类型。来源部分失败也保留比较；中断或重大失败不假装完成。完整复测前不能声称新的 URL 覆盖率和搜索完成率已经提升。

## 回归与限制

Python 3.8 全部 463 项单元测试通过，保留原有自动化测试。新增覆盖：DVI/标题空白与系列前缀、完整作者签名和冲突、正文引用排除、发表年份冲突、Poster/Slides/附录与未知分类、非全文候选回退、实际失败主机冷却、渐进退避、大小有界的缓存/HTML 扩展、类型统计、离线完整性检查、错误分类及原范围复测。`git diff --check` 通过。

扫描件没有新增 OCR；未知文档类型不会推测为全文，短标题与冲突署名仍需审核。网页超出上限或来源冷却仍可能造成检索不完整。新联网结果受到 API 配额、当前页面状态及网络影响，需要用户实际运行生成；本轮离线改善不能证明总体搜索召回或人工核实的内容准确率提高。
