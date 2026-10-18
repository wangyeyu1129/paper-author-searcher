# 身份解析问题：诊断与修复结果（2026-10-18）

所有数字都基于 **provisional 真值**（尚未人工审核），并受本环境 **没有 OpenAlex / Semantic Scholar API key** 的限制：OpenAlex 免费额度在运行中途耗尽（401/429），Semantic Scholar 频繁 429。下面的数字是下界和方向性证据，不是上线认证。

## 1. 基线（修复前，metadata 模式，仅姓名 + 学校）

报告：[baseline-metadata-20261018.md](baseline-metadata-20261018.md)

- 整体 verified recall **22.4%**，discovery recall 30.7%，precision 57.2%（闭世界下界）。
- 13/32 人停在 `identity_confirmation_required`；另有数人虽已解析，但 OpenAlex 401 让论文收集停在第一页。

## 2. 诊断（`paper-benchmark diagnose-identity`）

报告：[baseline-metadata-20261018.identity.md](baseline-metadata-20261018.identity.md)

| 诊断 | 人数 | 说明 |
|---|---:|---|
| resolved_complete | 10 | 身份正确；召回低主要因为 OpenAlex 401 截断收集 |
| stuck_true_competitor | 5 | 本人档案存在，但同名者分数接近（Yang Liu、Kundaje、Jia Liu、Yi Cui、Rus） |
| stuck_split_profiles | 4 | 本人被拆成多个档案（Neubig 4 个、Huan Zhang 3 个、Wei Lu 2 个等） |
| resolved_fragment | 1 | 只拿到部分档案（Kevin Ellis） |
| inconclusive_source_errors | 12 | 检索/列表请求失败，无法判断（含社会科学全部 8 人） |

另发现：仅按姓名检索时，常见名（如 UCLA 的 Wei Wang）本人不在前 25 个结果中；加上机构过滤后可以找到。

## 3. 已实现的修复

1. 诊断工具 + `inconclusive` 标签（不把请求失败误判成"没找到"）。
2. 机构限定检索（OpenAlex 姓名 + 曾任职机构）。
3. 同来源拆分档案合并（合作者 + 主题 + 学校，链式合并但拒绝硬冲突）。
4. 外部锚点：ORCID、已知论文（DOI / arXiv / 标题）、主页链接；锚点档案加分、合并、按 ID 补取；arXiv 通过 OpenAlex 的 `10.48550/arXiv.*` DOI 查询。
5. 输入扩展：`--orcid`、`--homepage`、`--email`、`--known-paper`。
6. 歧义时收集竞争档案（全部 needs_review，不产生 verified）。
7. 可选 Claude 裁决（默认关闭）。
8. 记录/重放 HTTP 缓存 + dev/test 分组的离线校准。
9. 报告新增 `identity_resolution_rate`。

## 4. 校准结论（`paper-benchmark calibrate`）

报告：[baseline-metadata-20261018.calibration.md](baseline-metadata-20261018.calibration.md)

| | dev 人数 | dev 已解析 | dev coverage | test 人数 | test 已解析 | test coverage | test purity |
|---|---:|---:|---:|---:|---:|---:|---:|
| 默认参数 | 11 | 6 | 53.4% | 9 | 5 | 55.6% | 100% |
| 推荐参数（margin 0.03，合作者 ≥5） | 11 | 8 | 69.8% | 9 | 7 | 55.6% | 100% |

推荐参数在 dev 上提升 coverage，但在 test 上 coverage 不变，**暂不修改默认值**。另一个关键观察：候选档案的合作者列表很大（几百人），同校不同人之间也会有几个共同合作者，单靠"共同合作者数量"合并有误合并风险。锚点比调阈值有效得多（见下）。

## 5. 锚点的实测效果（Huan Zhang，提供主页）

| 输入 | 身份 | 选中的档案 | discovery recall | verified recall | precision |
|---|---|---|---:|---:|---:|
| 仅姓名 + 学校 | ambiguous（停止） | — | 0% | 0% | – |
| + 主页 | **resolved**（20 个锚点） | 8 个 OpenAlex + 9 个 Semantic Scholar ID | **42.1%** (24/57) | 5.3% (3/57) | 100% (3/3) |

这次收集中有 3 个 OpenAlex 档案仍在第二页被 401 截断，因此 42.1% 是下界。

## 6. 剩余瓶颈与下一步

1. **API key（阻塞）**：需要你在 OpenAlex 设置页申请免费 key（`OPENALEX_API_KEY`），以及 Semantic Scholar key（`SEMANTIC_SCHOLAR_API_KEY`）。有了 key 之后，全量重跑 minimal 与 with_hints 两种输入，诊断和校准都会变得完整。
2. **归属验证过严**：身份正确之后，大量论文仍停在 needs_review（Huan Zhang 24 篇检索到，只有 3 篇 verified）。这属于 Phase 3（归属判定）。
3. 合作者重叠应改为与档案规模相关的度量，避免大型合作组造成误合并；需要在完整数据上校准。

## 7. 配置 API key 后的完整复测（2026-10-18 晚）

OpenAlex 与 Semantic Scholar 的 key 都已配置。OpenAlex 每日免费额度 1 美元、预付余额为 0（不会扣费）。四次完整运行加诊断的估算花费合计约 0.10 美元，其余请求都来自缓存。

| 指标（宏平均，provisional 真值） | 基线（无 key） | 仅姓名+学校 | 仅姓名+学校，校准后 | 带提示 | 带提示，校准后 |
|---|---:|---:|---:|---:|---:|
| identity_resolution_rate | – | 43.8% | 56.2% | 81.2% | **84.4%** |
| verified_paper_recall | 22.4% | 27.3% | 38.5% | 51.9% | **54.5%** |
| paper_discovery_recall | 30.7% | 67.2% | 66.8% | 76.9% | **77.0%** |
| author_match_precision（下界） | 57.2% | 52.6% | 52.2% | 49.5% | 50.1% |

- 诊断（有 key 后不再有 inconclusive）：resolved_complete 17、resolved_fragment 12、target_not_retrieved 2、resolved_wrong_person 1。拆分档案（fragment）仍是最主要的剩余问题。
- 校准在 test 组上可以泛化（coverage 33.1% → 44.9%，purity 保持 100%），已采用为新默认值：`ambiguity_margin=0.03`、`split_profile_coauthors=5`。
- 精度看起来低，主要因为 provisional 真值不完整（例如 Daniela Rus 只有来自 ORCID 的 20 篇真值，系统 verified 了 97 篇），在闭世界假设下会被当作误检。**在人工审核真值之前，精度数字没有意义。**
- 仍未解析的 5 人：Wei Wang、Graham Neubig、Yi Cui、Raj Chetty（档案拆分，竞争档案的论文已收集为 needs_review），以及 Zhiwei Xue、Jonathan Lee（博士生，论文很少）。

运行记录：`calibrated-minimal-20261018`、`calibrated-with_hints-20261018`（报告见同目录 `.md`）。
