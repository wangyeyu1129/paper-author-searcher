# 锁定身份与多源检索（Phase 1 / Phase 2）

本页说明输入格式、身份发现、发表列表提取、各数据源与本地索引。所有阈值、地址、来源顺序都在 `Settings` 的对应分区里（`discovery`、`profile_links`、`extraction`、`name_searches`、`listed_works`、`dblp_dump`、`catalogs`），可用 JSON 配置文件或 `PAPER_SEARCH__<分区>__<字段>` 覆盖。评测结果见 [reports/benchmark/phase1-2-evaluation-20261027.md](../reports/benchmark/phase1-2-evaluation-20261027.md)。

## 输入格式

| 字段 | 必填 | 作用 |
|---|---|---|
| `name`、`university` | 是 | 姓名与学校 |
| `department`、`research_interests`、`university_aliases` | 否 | 院系、研究方向、学校别名 |
| `homepage`、`publication_pages` | 否 | 主页、实验室页、CV PDF；页面上的发表条目成为锚点和本人发表列表 |
| `orcid` | 否 | ORCID 记录的作品列表成为本人发表列表 |
| `email` | 否 | 先按邮箱查 ORCID 注册表；邮箱域名证明页面属于该机构 |
| `scholar_url`、`profile_urls` | 否 | Google Scholar、ORCID、OpenAlex、Semantic Scholar、DBLP、OpenReview 档案链接 |

命令行对应 `--homepage`、`--publication-page`、`--orcid`、`--email`、`--scholar-url`、`--profile-url`（可重复）。档案链接由 `profile_links.ProfileLinkParser` 按 `ProfileLinkConfig.patterns`（来源、正则）解析：OpenAlex / Semantic Scholar / DBLP 链接直接锚定该 ID（锚点类型 `profile`，与 ORCID 同等优先）；Google Scholar 没有公开 API，只记录用户 ID 作为证据。

## 身份发现（调用方没给的线索）

`identity_discovery.IdentityDiscovery` 依次运行：

1. **ORCID 注册表检索**（免费）：先按邮箱，再按 姓 + 名 + 机构。只有当注册表里恰好一个姓名兼容、机构匹配的人，**且**其 ORCID 记录本身有一条**当前**（无结束年份或未过期）的就职或教育经历在所请求的机构、院系（若给出）一致时，才采用。"注册表中唯一" 不等于 "唯一的人"：真正的 Yi Cui 没有列出 Stanford 的 ORCID，而同校的 Yi Shun Cui 有，只检查唯一性会认错人。
2. **ORCID 个人网站**：所给或所发现 ORCID 记录中的个人网站。
3. **网页搜索**（需密钥）：Brave / Tavily / Exa 三选一（`discovery.web_search`，密钥 `BRAVE_API_KEY` / `TAVILY_API_KEY` / `EXA_API_KEY`）。没有密钥时跳过。

候选页面由 `PageRanker` 打分：标题含姓名、URL 含姓名、标题或摘要含机构（或主机名即邮箱域名）、发表/CV 标记、学术主机，各项 0–1，按配置权重加权求和，低于 `minimum_page_score` 的丢弃；聚合站点（ResearchGate、LinkedIn、Scholar 等）计 0 分。发现的页面只采用其中**写到本人姓名**的条目；每个页面会跟随一次同站点的 "Publications" / CV 链接。发现结果记录在档案的 `discovered` 字段，离线重放会恢复。

### ORCID 与数据库聚类

OpenAlex 为每个合并后的作者聚类只记一个 ORCID，混入同名者的聚类可能挂着别人的 ORCID。因此：

- 本人发表列表中的论文若只能归到带其他 ORCID 的作者，先记为 "有争议"；同一作者持有至少 `anchors.minimum_contested_works`（默认 3）篇不同的本人论文时，锚定该作者（锚点类型 `contested`），解析时不再把它的 ORCID 算作冲突。少于此数则仍以 ORCID 为准。
- 一个分组只要含有由本人 ORCID 确认的记录（带该 ORCID，或其 ORCID 已被本人论文推翻），其中带其他 ORCID 的成员就会被移出，每个 ORCID 各成一组。同名者常因共享 DOI 的混合 Semantic Scholar 档案被并入。

## 发表列表提取

- 规则解析（默认）：块元素为条目，DOI / arXiv 链接与强调或引号中的标题；PDF CV 按段落/编号行切分。
- LLM 提取（设置 `ANTHROPIC_API_KEY` 后启用）：页面可见文本（或 CV 文本）按整行贪心打包成块，每块一次结构化输出请求（`PUBLICATION_SCHEMA`），系统提示缓存，拒绝时服务端回退模型。提取结果与规则解析结果按 work key 去重合并；标识符和标题照样校验。答复按请求哈希缓存在 `<http_cache_dir>/llm`，重跑和重放不再计费。模型与 effort 在 `extraction` 分区。

## 数据源

| 来源 | 方式 | 用途 |
|---|---|---|
| OpenAlex、Semantic Scholar | 作者 ID | 身份、收集、逐篇反查 |
| DBLP | **官方数据 dump** 的本地索引 | 身份、收集、逐篇反查 |
| ORCID | 公开 API | 发现、本人发表列表 |
| Crossref | 作者 + 机构检索；DOI / 标题反查 | 姓名检索、逐篇反查 |
| arXiv | 作者检索；ID / 标题反查 | 姓名检索、逐篇反查 |
| OpenReview | 档案（需 `OPENREVIEW_TOKEN`） | 本人发表列表 |
| Europe PMC（含 PubMed） | 作者 + 机构检索；DOI / 标题反查 | 姓名检索、逐篇反查 |
| CORE | DOI / 标题反查（需 `CORE_API_KEY`） | 逐篇反查 |
| ACL Anthology、CVF、PMLR | 官方批量书目的本地库 | 逐篇反查 |
| 机构知识库（OAI-PMH） | 收割到本地库 | 本机构知识库按姓名检索、逐篇反查 |

姓名检索（`name_searches.sources`）共享 `PagedNameSearch` 模板：游标翻页、每条只取姓名（及印出的 ORCID）与本人一致的唯一作者、失败隔离。Crossref 按相关度排序而非过滤，所以只保留本人署名中印有该机构的论文，遇到一整页都不符合就停止。这些记录没有作者 ID，由归属验证通过稳定合作者或本人发表列表判定。

**逐篇标题反查**：本人发表列表（主页、CV、ORCID、OpenReview）中每一篇未被收集到的论文，按 `listed_works.sources` 的顺序依次在 OpenAlex → DBLP → 本地书目库 → Semantic Scholar → Crossref → arXiv → Europe PMC → CORE 查询（DOI、arXiv ID 或规范化标题），第一个找到并且能唯一确定本人署名的来源作答（责任链）。缺少密钥或本地库的来源自动跳过。

## 本地索引

DBLP 的 API 对自动化客户端返回反爬页面；会议书目没有检索 API。两者都改为一次性建立本地 SQLite 库，之后的查询不联网：

```bash
# DBLP（约 1.1 GB 下载，建库约 8 分钟，峰值内存约 50 MB，库约 6.4 GB）
curl -L -o output/dblp/dblp.xml.gz https://dblp.org/xml/dblp.xml.gz
paper-dblp-index --dump output/dblp/dblp.xml.gz --index output/dblp/dblp.sqlite
export PAPER_SEARCH__DBLP_DUMP__INDEX_PATH=output/dblp/dblp.sqlite

# ACL Anthology、PMLR（ICML 2020–2025、AISTATS 2021–2025）、CVF（CVPR/ICCV/WACV 2021–2025），约 1 分钟
paper-catalog-index --store output/catalogs/catalogs.sqlite
export PAPER_SEARCH__CATALOGS__STORE_PATH=output/catalogs/catalogs.sqlite
```

- DBLP 索引流式解析（每个顶层元素存入后即清除），记录 XML 以 zlib 压缩，作者与 work key 以 64 位 BLAKE2b 哈希按行号引用；每次读取都按姓名或 work key 复核，哈希碰撞不会返回错误记录。`DblpDumpClient` 以 API 的原格式回答 `/search/author/api` 与 `/pid/<pid>.xml`，原有的 DBLP Provider 不改代码即可使用。索引带 schema 版本，旧版本会被拒绝并提示重建。
- 书目库按 work key 与与姓名顺序无关的姓名键索引；BibTeX 逐条流式解析（ACL 导出 97.6 MB，解析峰值约 2 MB）。每个来源在一个事务内整体替换，中途失败会回滚到原有条目。
- 机构知识库：在 `catalogs.oai_repositories` 中配置 `(机构, OAI-PMH 地址[, set])`，可选 `oai_from` 限定日期。默认不收割任何知识库。

路径未设置时这些来源不启用；设置了但文件不存在会直接报错，不会悄悄回退。

## 收集阶段的拆分档案合并（已移除）

曾有一个收集阶段的合并步骤：按合作者网络与机构，把同名的其他作者 ID 并入本人身份。评测中它对 28 人提出了 268 个片段，一个都没有接受；对最少输入下 158 篇未找到的真值论文逐篇核对，只有 1 篇位于被提出的片段之下，即这一步最多只能多找到 1 篇。因此已删除，不再为它翻页消耗 API 配额。解析阶段同来源的拆分档案合并（`MatchingConfig.merge_split_profiles`，见 [identity_resolution.md](identity_resolution.md)）不受影响。
