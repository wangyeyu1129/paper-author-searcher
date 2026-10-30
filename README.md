# Paper Author Searcher

Python >= 3.10 的论文检索工具，接入 OpenAlex、Semantic Scholar 和 DBLP，支持教授身份确认、论文收集、归属验证、去重、公开 PDF 检索与下载，以及离线评估。元数据与 URL 阶段无第三方依赖；PDF 解析需要安装 `download` extra。

真实场景指标、人工标注格式、未获取 PDF 的原因和全量运行命令见 [性能评估](docs/performance.md)，本轮实际检索和优化前后对照见 [测试报告](reports/performance/comparison.md)。代码重构中每项性能、内存改动的测量与取舍见 [优化记录](docs/optimization.md)。`output/` 下的运行产物（论文、PDF、manifest、HTTP 缓存）只保存在本地、不进入版本库，结论摘要在 `reports/`。

人工待确认的参考论文与分层审核队列见 [审核清单](reports/performance/ground_truth_review.md)。人工选择 Author ID 不替代逐篇归属验证；作者档案 ORCID 不单独作为论文归属正面证据。

## 运行

```bash
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --name 'Geoffrey Hinton' --university 'University of Toronto'
```

默认输出候选教授的姓名、匹配学校、各来源 author ID、confidence、status、简短证据及来源错误。没有验证学校时学校字段为 null，不把输入学校写成已验证信息。使用 `--verbose` 输出完整 profile、全部 candidates 和 source_errors。

可选参数：`--department`、可重复的 `--research` 和 `--university-alias`。也可 `python3 -m pip install -e .` 后使用 `professor-resolve`。

```python
from paper_author_searcher import ProfessorQuery
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.settings import Settings

container = ServiceContainer(Settings.load(), RunOptions(mode='metadata'))
result = container.resolver.resolve(ProfessorQuery(
    name='Andrew Ng', university='Stanford University',
    research_interests=['machine learning']))
print(result.to_dict())
```

服务之间的依赖全部由 `composition.ServiceContainer`（组合根）注入：库中的类不自行构造 HTTP 客户端、数据源或子服务，缺失某阶段所需服务时会明确报错（`MissingStageService`），不会悄悄回退到默认实现。测试中可直接向构造函数传入替身。

## 结构与迁移配置

- `models.py`：统一输入、候选和输出模型。
- `config/`：各模块的默认参数，按关注点分为 `identity`（匹配、锚点、同名裁决、发现）、`sources`（HTTP 与各来源）、`collection`（收集、归属、精度检查、去重）、`fulltext`（原文链接、下载、存储）。
- `matching.py`：姓名、学校、研究方向比较与标识符规范化。
- `identity.py`：结构化跨来源身份证据，不依赖展示字符串。
- `resolver.py`：收集、分组、评分与歧义判定。
- `http.py`：请求、重试、响应类型校验和解析。
- `providers.py`：来源字段适配与身份元数据补充。
- `cli.py`：参数和输出展示。

配置均为不可变 dataclass，可使用 `dataclasses.replace` 修改。`ProfessorResolver(config=MatchingConfig(...))` 注入策略，默认 OpenAlex adapter 同步使用该姓名兼容阈值；自定义 provider 可传入 `source_config=SourceConfig(...)` 与配置后的 `HttpClient(config=HttpConfig(...))`。原有构造参数、统一输出和 `providers=[]` 的用法保留。

新来源只需实现 `AuthorProvider` 协议的 `name` 和 `search(query)`，返回 `AuthorCandidate`，然后通过 `ProfessorResolver(providers=[...])` 注入。生产逻辑没有针对具体教授或 author ID 的特殊分支；测试数据使用独立的合成记录；联网检查由用户输入驱动，不内置真实教授或预期 Author ID。

## 身份验证

学校证据分为 `matched`、`unknown`、`mismatch`。空 affiliations 是信息缺失，不按学校冲突扣分；有机构但未匹配输入学校时降低评分。这只表示已观察信息不相符，并不能证明教授从未在该校任职。OpenAlex 同时使用历史机构、最近机构及论文中属于该候选本人的 authorship 机构，避免把共同作者的学校算给候选人。

同名同校不再足以跨来源合并。兼容姓名之外，需要一致 ORCID、至少两个共同 DOI、一个共同 DOI 加至少两个共同作者，或共同学校加至少三个共同作者和至少两个一致研究主题。冲突 ORCID 或明确冲突的中间名阻止合并。同一来源的不同 ID 仅在兼容姓名且 ORCID 一致时合并，其余保持独立；合并后的 `author_id_aliases` 保留全部 ID，兼容字段 `author_ids` 保留每来源一个代表 ID。同一来源重复记录不增加跨来源验证分。完整链接分组避免通过中间候选传递合并。较丰富的共同 DOI 证据优先于单篇片段，但这些规则仍是启发式，不能保证消除数据源自身的混合身份。

`identity_confidence` 是启发式证据评分，不是校准后的正确率。姓名贡献至多 0.55，学校匹配至多 0.30（观察到不匹配扣 0.20，缺失不扣分），最近学校 0.02，跨来源验证 0–0.08（单篇共同论文不增加排序分），院系和研究方向各 0.025。学校未知/未匹配或者只有姓名缩写时评分至多 0.69，保持 `needs_review`。

- `resolved`：评分至少 0.8，有学校与兼容姓名证据，没有接近的独立候选。
- `ambiguous`：有学校匹配，但前两名评分差小于 0.10；首选置信度降至至多 0.69。
- `needs_review`：学校或姓名证据不足。即使 ORCID/DOI 验证了两条记录属于同一作者，也不能在缺少目标学校证据时自动确认目标教授。

学校简称、译名通过 `university_aliases` 指定；研究方向使用词形相似度，不进行跨语言语义推断。历史机构不代表现任学校。CLI 仅 resolved 返回 0，其余状态返回 2。

## 网络与来源诊断

密钥环境变量：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`。客户端保留 SSL 验证，设置超时、响应大小上限及 429/5xx 有限重试。

DBLP 使用 `/search/author/api?q=...&format=json` 和 `/pid/<id>.xml`。检查 HTTP 状态码、Content-Type、空响应及格式错误；HTTP 200 的 HTML 防爬页面明确报错，不作为 JSON 解析，也不绕过防爬。XML 获取失败保留搜索候选和 metadata_errors；某一来源失败不丢弃其他来源结果。

OpenAlex 默认搜索 25 个候选，并为最多 8 个姓名兼容候选查询每人最多 50 篇高引用论文的 DOI、authorships、topics；Semantic Scholar 使用返回论文的 DOI、共同作者和标题；DBLP 使用作者 XML。只请求身份所需元数据，不请求全文。候选数及 enrichment 上限可通过 provider 构造参数调整。采样与搜索截断可能遗漏验证证据，重复记录仍可能导致 ambiguous。

若 Python 缺少系统 CA 配置，且 `/etc/ssl/cert.pem` 包含可信 CA，可设置 `SSL_CERT_FILE=/etc/ssl/cert.pem`。

## 配置

所有可调参数集中在 `paper_author_searcher.settings.Settings`（各分区即 `config/` 中的 dataclass：`matching`、`anchors`、`http`、`sources`、`collection`、`ownership`、`deduplication`、`pdf`、`download`）。优先级从低到高：

1. 代码默认值；
2. JSON 配置文件：`--settings path.json` 或 `$PAPER_SEARCH_SETTINGS`，例如 `{"http": {"timeout": 30}, "collection": {"maximum_contenders": 5}}`；
3. 环境变量覆盖：`PAPER_SEARCH__<分区>__<字段>`，例如 `PAPER_SEARCH__HTTP__TIMEOUT=30`、`PAPER_SEARCH__HTTP__RETRY_STATUSES=429,503`。

值会按字段类型转换，并重新执行各分区自身的校验；未知分区或字段直接报错，不会被静默忽略。命令行参数（如 `--timeout`）仍优先于以上三层。

凭据只从环境变量读取（`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`、`UNPAYWALL_EMAIL`、`CROSSREF_MAILTO`），配置文件中出现 `credentials` 会被拒绝；`repr` 与 `Settings.to_dict()` 只显示 set/unset，不输出值。只有 `settings.py`（和日志引导）读取环境，有架构测试保证。

## 数据源注册表

数据源按名字注册在 `source_catalog.py`（`IDENTITY_SOURCES`、`PAPER_SOURCES`、`PDF_SOURCES`），由配置决定启用哪些、按什么顺序：

```json
{"sources": {"identity_sources": ["openalex", "semantic_scholar"]},
 "pdf": {"sources": ["existing_metadata", "openalex", "arxiv", "public_pages"]}}
```

新增数据源：写好适配器，在 `source_catalog.py` 用 `@PDF_SOURCES.register('name')` 注册工厂函数，再把名字加入配置即可，无需改动 Resolver / Collector / Workflow。配置中出现未注册的名字会在构建时直接报错。

## 日志

命令结果写到 stdout，诊断信息（进度、重试、限流、预算拒绝、被隔离的来源失败）通过 `logging` 写到 stderr，管道输出不会被污染。所有命令都支持：

- `--log-level {DEBUG,INFO,WARNING,ERROR}`，默认读 `$PAPER_SEARCH_LOG_LEVEL`，否则为 `INFO`；`DEBUG` 时被隔离的失败会附带 traceback。
- `--log-format {text,json}`，默认读 `$PAPER_SEARCH_LOG_FORMAT`；`json` 每行一个对象，便于日志采集。

作为库嵌入时，包只挂 `NullHandler`，由宿主应用决定日志配置。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
# 独立、显式联网测试；需要可访问公开 API，可按需设置上述密钥和 CA 环境变量
PYTHONPATH=src python3 tests/live_check.py --name "<professor name>" --university "<university>"
```

Mock 测试覆盖多语言和不同研究领域的合成作者同名隔离、缺失学校与不匹配学校、历史机构、ORCID 冲突、DOI 规范化、共同作者、OpenAlex 本人机构提取、CLI 输出以及 DBLP HTML/JSON/HTTP 错误。联网检查使用传入的姓名和学校，验证输出结构、置信度范围以及学校未知时不能 resolved 等规则，不断言固定 Author ID。它是接口集成检查，不能证明真实身份匹配正确；独立的真实身份评估需要人工核验的数据集。数据源重复身份仍可能导致 ambiguous，来源异常会明确显示。

接口参考：[OpenAlex Authors](https://help.openalex.org/data/authors/)、[OpenAlex Authorships](https://help.openalex.org/data/authorships/)、[Semantic Scholar API](https://api.semanticscholar.org/api-docs/)、[DBLP Search API](https://dblp.org/faq/How+to+use+the+dblp+search+API.html)。

## 歧义诊断

`match_score` 保留评分规则计算的原始分数，`identity_confidence` 则应用证据不足和歧义限制。排序及前两名差距使用原始分数，不使用被压到 0.69 的最终 confidence。`ambiguity` 在默认 CLI 中也显示前两名的全部 Author ID、原始分数、学校证据、分数差距及未能合并的原因。它表示尚未确认是同一人的竞争记录，并不证明两者一定是不同教授。

`requested_university` 是用户输入；`university` 和 `matched_affiliations` 使用来源中实际观察到、匹配输入的机构名称。历史机构证据仍不等于现任任职验证。

DBLP 官方作者搜索格式仍是 `/search/author/api?q=...&format=json&h=...`，见 [官方 API FAQ](https://dblp.org/faq/13501473.html)。HTML 防爬响应无法当作 JSON 使用；当前实现将来源标记为出错并继续其他 Provider，不绕过验证。可通过显式注入 OpenAlex 与 Semantic Scholar 的 provider 列表暂时不启用 DBLP。

## 第二阶段：Paper Collector

新增 `paper_collector.py` 负责身份门控、收集调度和保守去重；`paper_providers.py` 负责论文来源适配，复用已有 Provider 的 HTTP 客户端、API 地址和密钥。Resolver 原有行为保持不变。当前阶段只获取元数据，不下载 PDF。

```bash
# 自动解析；resolved 后按已验证 ID 获取论文并保存结果
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --name "<professor name>" --university "<university>" \
  --collect-papers --output papers.json

# 查看候选与其 ID
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --name "<professor name>" --university "<university>" --list-candidates

# 人工确认某个返回的 ID；只获取此 ID，不扩展到其他候选
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --name "<professor name>" --university "<university>" \
  --collect-papers --select-author "openalex:<author ID>" --output papers.json
```

`--select-author` 必须出现在本次 Resolver 候选中，且要求 `--collect-papers`。`ambiguous` 和 `needs_review` 默认不会发起论文请求，会显示待选择候选。人工确认仅作用于所选 ID；不会把 profile 的其他 ID 或其他候选视为已人工确认。自动收集只接受 `resolved`，使用 Resolver 已验证的 `author_id_aliases`；不进行作者姓名论文搜索。直接使用库 API 时应传入真实 Resolver 返回的 profile，不要手工伪造 resolved 状态或未验证 aliases。

`collection.paper_count` 是全部去重条目数量，包含待审核和拒绝条目；不能当作已验证发表数量。`verified_paper_count` 单独报告归属 verified 的条目，`papers` 是可过滤的视图列表。每篇论文的 `records` 保留合并前元数据，包括来源、论文 ID、原始 Author ID、作者、DOI 和 URL。顶层 `source/source_paper_id/source_author_id` 指向代表记录，所有来源见 `records`。`collection_identity` 说明实际收集的身份与确认方式，避免人工选择后误读原本首选 profile。

OpenAlex 使用 cursor 遍历所有页面，Semantic Scholar 使用返回的 next offset，DBLP 使用官方 PID XML 完整导出（不使用按姓名的论文搜索）。所有可解析原始记录均保留。OpenAlex/Semantic Scholar 的准确目标 Author ID 只构成数据库归属声明，不能单独验证论文；DBLP 要求根 PID 相符，仅 editor 或缺失目标 PID 的记录由归属验证器单独判定。

`CollectionConfig` 管理分页大小和可选 `maximum_pages`，默认不设置页数/论文数限制。达到手动页数限制、分页 token 重复、响应格式错误、超时/限流或无法解析元数据会使来源 `complete=false`。论文归属待审核不改变 API 分页完成状态。已成功取得的前页保留。每个来源状态包含 `pages_fetched`、`records_received`、`rejected_records` 和 `error`。

全局 `complete=true` 仅表示所有配置来源的已确认 ID 都完成了本轮分页检索且没有解析失败；它不能证明数据库收录了教授全部论文。缺少某来源已确认 ID 或来源失败也标记不完整，不能把未查询的来源算作零篇。部分结果仍会输出和保存，CLI 返回 2；完整本轮收集返回 0。默认配置三个来源，DBLP 不可用时会保留 OpenAlex/Semantic Scholar 结果并标记不完整。库 API 可显式配置来源范围。

去重优先规范化 DOI 和稳定数据库/外部论文 ID。缺少任一 DOI 时，结合标题、年份、可兼容作者署名与来源元数据；仅在全名或有依据的首字母署名兼容时匹配作者。跨数据库类型代码先标准化，避免 article 与 JournalArticle 阻止真正重复。不同 DOI 或明确不同出版形式保留独立记录，并在同标题/兼容作者时附上可能的 `related_versions` 链接，不宣称已证实同一研究成果。完整链接比较防止无 DOI 中间记录合并冲突 DOI。作者/venue 缺失、题名变化与不同版本仍可能留有重复项；所有合并记录、规则证据及版本标识符保留。

测试包括 mock 分页、末页失败保留前页、错误作者 ID、DBLP PID/editor 校验、DOI 与无 DOI 去重、版本冲突、aliases、歧义门控、人工选择、JSON 保存以及不完整退出码。真实 API 仅做了小页 smoke check，不代表完整论文覆盖率评估。

接口参考：[Semantic Scholar 作者论文分页](https://api.semanticscholar.org/api-docs/snippets)、[DBLP 官方作者完整导出](https://dblp.org/faq/17334565.html)。

## Paper Ownership Validator 与审核输出

`paper_ownership.py` 独立验证每条来源记录和合并条目的归属。`verified` 是满足当前保守证据规则，非人工认证或统计正确率；`needs_review` 隔离证据不足或相互冲突的条目；`rejected` 表示明确的目标作者 ORCID/署名/作者关系冲突，仍保留原始数据。

Author ID 加同名不足以 verified。优先检查逐论文目标 Author ID、原始署名、ORCID 与实际论文机构；来源 ID 按自己的 namespace 验证，不比较不同数据库的 ID 字符串是否相同。正面证据包括：兼容目标署名及独立逐论文 ORCID 与教授身份一致；明确目标 ID、兼容署名及论文机构与已验证机构一致；或稳定论文标识符与两个已确认来源的逐论文目标 ID、兼容署名同时一致。数据库作者档案 ORCID（`resolved_author`、`author_profile`）与未知 scope 的 ORCID 不单独作为正面证据，来源档案中未确认的历史机构也不替代目标学校。标准条目的有限共同作者证据保留为辅助路径；种子集合固定，不从待审核论文循环自我增强。仅靠相似标题去重不会提升归属状态。混合 verified/rejected 来源决策、冲突 ORCID 或非标准条目需要审核。姓名缩写、缺失作者 ID 或机构不一致并不自动证明错误归属。

研究方向差异不构成拒绝依据，避免排除跨学科成果。来源关联声明和教授 profile 仍可能有错误；多个来源也可能复制相同错误。本规则提高可审查性，不能保证自动判定全部正确，仍需独立标注集评估 precision/recall。

新请求保存 `author_details`（可获取的姓名、Author ID、ORCID 和机构）、`external_ids`、`raw_metadata` 及作者关系观察结果。每条来源记录保留 `ownership_status` / `ownership_evidence`，合并条目也保留整体归属决策和去重证据。不可解析数据保存在来源 `rejected_records.raw_metadata`，与归属 rejected 的论文分开统计。

`paper_types.py` 只解释明确来源类型，不从标题或研究方向猜测类型。支持 research_paper、review、preprint、book_chapter、dataset、editorial、book 等；缺失、不支持或冲突的分类为 unknown，并记录原始标签与分类证据。

`statistics` 分开报告收到的原始记录数、保留的来源记录数、去重条目数、条目/来源记录的 verified / needs_review / rejected 数量以及不可解析记录数。`sources` 分别报告 `fetch_status`、`pagination_complete`、`complete`、页数、原始数量、归属统计和错误。`professor_identity_status` 与逐篇归属状态分开。数据库完整返回不代表教授全部发表的论文已取得。

```bash
# 正常收集；仅在 papers 视图中显示已验证学术论文
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --name "<professor>" --university "<university>" --collect-papers \
  --academic-only --ownership-status verified --output papers.json

# 离线重算既有 JSON，不调用 API、不补造旧记录缺失的 ORCID 等证据
PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --reanalyze-json papers.json --output papers.reviewed.json
```

过滤时 `papers` 是视图，`all_papers` 保留完整归档（包含待审核/拒绝/非学术条目）；统计始终针对完整集合。`--ownership-status` 可重复使用。离线处理旧结果时证据可用性较低，因此常有较多 needs_review；需要重新获取 richer authorship 元数据或人工核验，不通过调整阈值强行确认。

## 逐论文作者身份证据

OpenAlex 选择 `authorships`、`ids` 和 `is_authors_truncated`，保存每位作者的来源 ID、ORCID、`raw_author_name`、署名顺序、通讯作者标记、机构、原始机构字符串及其映射。作者列表截断时使用同一 work ID 查询单篇详情；失败后保留原始论文、截断状态与错误，不将目标不在截断片段中解释为明确不属于该教授。ORCID 来自解析后的作者实体，原始署名来自论文记录，两者独立保留。通用逗号逆序署名格式会用于比较，原始值保持不变。

Semantic Scholar 作者论文列表请求 `authors` 获取实际返回的 Author ID 和姓名，以及论文 `externalIds`。该端点不支持 `authors.externalIds` / `authors.affiliations`，因此每个被检索的目标 ID 单独请求一次作者档案；只有逐论文列表明确包含此 ID 的作者才附上档案 ORCID 和机构。档案以 `author_profile` 保存，ORCID 标明 `orcid_scope=author_profile`，机构放在 `profile_affiliations`，不会当作论文发表时的机构。未提供的数据继续为空，不请求引用/参考文献作者作为论文作者，也不为每篇重复请求同一作者档案。

合并后的 `author_details` 带来源 namespace 和论文 ID；`records` 保留每份原始详情；`source_identifiers` 保留各来源论文/作者 ID、外部 ID、目标出现观察和作者列表完整性。冲突外部 ID 不任意覆盖，在各来源记录中保留。`target_author_present=true` 表示数据库作者列表中的 ID 观察，不是独立人工归属确认；缺失信息为 null，明确完整列表中没有目标 ID 才为 false。`statistics.source_record_authorship_counts` 报告证据可用情况。

`OwnershipConfig.maximum_coauthor_authors` 默认 50，控制共同作者辅助路径，未改变原有姓名匹配阈值。超过上限的记录不能靠共同作者重合或缺少独立身份依据的跨来源 ID 列表自动 verified，也不能成为共同作者种子；真实大规模合作论文仍可凭更强的身份依据确认。论文集、Peer Review、decision letter 等显式类型或通用标题结构进入角色审核，不自动删除，也不据此推断 unknown 的论文类型。来源类型在同一合并条目中出现非标准分类时整体需要审核；明确的 editor/reviewer/contributor 角色不会被当作 author。

```bash
# 正常 API 重新获取旧归档中已确认身份的论文元数据；建议使用新输出文件
SSL_CERT_FILE=/etc/ssl/cert.pem PYTHONPATH=src python3 -m paper_author_searcher.cli \
  --refresh-json papers.reviewed.json --output papers.authorship.json
```

`SSL_CERT_FILE` 只在该可信 CA 文件存在、当前 Python 需要配置 CA 时使用，不关闭 TLS 验证。自动刷新仍要求保存的身份 resolved；已保存的人工确认仅对原来选择的单一 ID 生效。`--reanalyze-json` 保持离线，不补造旧 JSON 缺少的作者 ID、ORCID 或机构。

来源 `pagination_complete` 与作者详情获取错误分开记录；详情扩展失败会留下 `metadata_errors` 并标记结果不完整，已取得记录仍保留。即使所有查询的 Author ID 都完成分页，也不证明教授论文全集或逐篇真实归属。真实归属准确率仍须使用人工 Ground Truth 和 Evaluation 验证。

字段与访问限制参考：[OpenAlex Authorships](https://help.openalex.org/data/authorships/)、[OpenAlex 作者列表截断说明](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/authors/limitations.md)、[Semantic Scholar 官方 API](https://api.semanticscholar.org/api-docs/graph)。

## 离线 Evaluation

新增独立 `Evaluator`，从已有结果和人工 Ground Truth 计算归属 Precision/Recall/F1、全部候选与 verified 召回率、三种归属状态统计，以及去重 pairwise 指标、错误合并与重复遗漏。完整 JSON 保留误检、漏检、人工确认列表与匹配证据，并报告原有来源分页状态。评估不调用 API、不调整生产匹配阈值。

```bash
PYTHONPATH=src python3 -m paper_author_searcher.evaluation_cli \
  --result papers.reviewed.json --ground-truth ground_truth.json \
  --output evaluation.json
```

从 [Ground Truth 空模板](schemas/ground_truth.template.json) 开始填写人工核实资料；格式、版本政策和指标口径见 [Evaluation 文档](docs/evaluation.md) 与 [JSON Schema](schemas/ground_truth.schema.json)。默认未知记录待确认，不作为负例。Precision 仅基于已评分记录，Recall 相对于已标注正例，因此必须同时查看标注覆盖率。未提供真实教授人工标注，也不把现有 verified 数量当作正确率。

## PDF Resolver：只检索 URL，不下载文件

独立 `PDFResolver` 读取 Paper Collector 的保存结果，不重新运行教授解析或论文收集。默认只处理 `ownership_status=verified`，显式 `--include-needs-review` 才处理待审核条目；被拒绝条目始终跳过。原有归属状态与证据保留，PDF 验证成功也不会将论文归属从 needs_review 改为 verified。

```bash
# 已安装项目时也可使用 pdf-resolve 命令
PYTHONPATH=src python3 -m paper_author_searcher.pdf_cli \
  --input papers.authorship.json --output papers.pdf_urls.json

# 小规模验证：按保存顺序处理前 5 篇符合归属条件的论文
PYTHONPATH=src python3 -m paper_author_searcher.pdf_cli \
  --input hinton_papers.authorship.json --output hinton_papers.pdf_urls.sample.json \
  --max-papers 5 --timeout 8 --retries 1

# 明确包含待审核论文；Unpaywall 需要真实联系邮箱
PYTHONPATH=src python3 -m paper_author_searcher.pdf_cli \
  --input papers.authorship.json --output papers.pdf_urls.review.json \
  --include-needs-review --require-unpaywall
```

若当前 Python 需要指定可信 CA，可使用现有的 `SSL_CERT_FILE=/etc/ssl/cert.pem` 配置（该文件必须存在）。TLS 验证保持开启。未提供真实邮箱时 Unpaywall 单独标记 disabled，其他来源继续执行；支持现有的 `OPENALEX_API_KEY` 和 `SEMANTIC_SCHOLAR_API_KEY`。

检索依次使用保存的 OpenAlex/Semantic Scholar PDF 元数据、OpenAlex locations、Semantic Scholar openAccessPdf、Crossref 已登记全文链接、Unpaywall 全部 OA locations、arXiv 官方 API、配置的期刊公开目录，以及公开学术页面。Semantic Scholar 优先批量查询已知论文标识符；缺 DOI 时可查询标题，但标题相同不足以验证身份。期刊目录只用于发现精确题名的引用页面，PDF 地址来自页面实际 metadata。Unpaywall、Crossref 和 OpenAlex 返回的落地页继续交给公开页面 Provider。

公开页面支持 `citation_pdf_url`、Dublin Core/EPrints 引用元数据、明确的 alternate PDF 和 ScholarlyArticle JSON-LD，也可通过重复的 `--bibliography-url` 指定作者、学校或会议公开目录。目录标题链接只提供发现线索，单凭标题不会成为已验证 PDF。原始元数据提供的旧 HTTP 路径优先尝试相同路径的 HTTPS；公开机构/学术来源中 OA 标记缺失的直接链接，必须有论文身份绑定且匿名响应确实为 PDF。原始 OA 标记及未知许可仍保留。不会通过 DOI 猜测 PDF 路径；网页、登录页和 HTML 中间页不算 PDF，不绕过访问限制。

邮箱通过 `UNPAYWALL_EMAIL` 环境变量读取，不写入结果 JSON。使用 `--require-unpaywall` 可在未配置或格式不合法时提前停止，避免复测时意外禁用该来源；代码不能验证邮箱归属，需要用户提供自己的真实联系邮箱。安全设置方式、相同论文范围的复测与离线对比命令见 [PDF 来源发现](docs/pdf_discovery.md)。

`PDFConfig` 集中管理探测字节数、HTML 元数据读取上限、重定向上限、arXiv 标题查询和请求间隔、期刊目录及版本偏好。`HttpConfig` 继续管理超时、限流重试和最大退避。每次 PDF 探测只读取最多 1024 字节并关闭响应，即使服务器忽略 Range 也不会读取完整正文或写入 PDF 文件；引用页面先读取 65536 字节，确有截断时最多扩展一次到 1 MiB，期刊目录预览上限默认 2 MiB。共享页面缓存同时受 TTL、条目数及总字节数限制。

候选 `validation_status=verified` 同时要求公开来源依据、论文元数据一致性和实际 `%PDF-` 文件头。Content-Type 为 PDF 或 URL 以 `.pdf` 结尾均不能单独确认。DOI 冲突、已知 arXiv 标识符冲突或来源标题冲突会阻止确认；没有稳定标识符时要求标准化标题、年份和完整作者署名集合相符。PDF 文件头确认但论文对应关系不足时保留 unverified。版本来自来源明确标注，缺失为 unknown；同一 URL 的版本声明冲突也保留 unknown，各来源声明仍保存在 provenance。

输出模型 `PaperPDFResult` 保留 title、doi、ownership_status、ownership_evidence、pdf_candidates、best_pdf_url、pdf_source、access_status、validation_status、version_type、error_details 和各 Provider 状态。每个候选保留来源论文 ID、元数据 URL/DOI、版本、许可证（缺失保持 null）、最终重定向 URL、HTTP 状态、MIME、实际读取字节数、文件头确认标记和身份/验证证据。不同版本的 URL 全部保留，同一 URL 的多来源证据合并；best_pdf_url 只从已验证候选中选取。

`statistics` 单独报告归档总条目、实际处理、有效 PDF 论文、仅未验证链接、未找到、归属跳过、数量限制导致的未处理、检索不完整及不同已验证 URL 数量。各来源贡献按每篇论文的唯一候选 URL 计数，同一个 URL 可由多个来源贡献，因此来源数量不可简单相加；还报告查询状态和错误次数。归档总数或处理数量不代表教授全部论文/可用 PDF 的数量。

有 Provider 查询失败时 CLI 返回 2，但仍保存已取得结果和完整错误信息；数量限制明确标记未处理条目。`not_found` 只表示本次处理未确认有效或待验证 PDF 候选，须结合来源失败状态查看，不能作为论文不存在公开 PDF 的证明。

轻量验证确认的是“文件头为 PDF，公开来源元数据将此资源与论文关联”。不解析 PDF 全文，无法独立证明完整文件可解析、内部内容完全一致或法律上的再分发权限；许可证原样保留。已有链接可能失效，公开接口可能限流或缺少作者版本，检索不能保证覆盖所有公开版本。

接口依据：[OpenAlex Locations](https://help.openalex.org/data/locations/)、[Semantic Scholar 官方 API](https://api.semanticscholar.org/api-docs/graph)、[Unpaywall 数据字段](https://unpaywall.org/data-format)、[Unpaywall API 邮箱要求](https://data.unpaywall.org/products/api)、[arXiv API](https://info.arxiv.org/help/api/user-manual.html)。

本次小规模真实测试及访问限制见 [测试报告](reports/pdf_url_sample.md)；全部候选、HTTP 文件头观察和未处理状态见 [URL JSON](hinton_papers.pdf_urls.sample.json)。在归档顺序的前 5 篇已确认归属论文中，1 个 URL 同时通过文件头及论文元数据核对；该样本不能用于推算全部条目的 PDF 覆盖率。

## PDF Downloader

新增独立 `PDFDownloader`、`PDFFileValidator` 和下载 CLI。只读取已有 PDF Resolver JSON，不重新解析教授身份或检索论文。默认要求 ownership=verified，且候选的 URL 验证与元数据身份状态均为 verified；`--include-needs-review` 仅放行待审核的论文归属，不能放行未经验证的 URL。rejected 论文始终跳过，原有归属状态不改变。

```bash
# 在当前 Python 环境安装下载阶段的可选依赖
python3 -m pip install -e '.[download]'

python3 -m paper_author_searcher.download_cli \
  --input papers.pdf_urls.json --output-dir downloaded_papers \
  --max-downloads 3 --timeout 15 --retries 2 --max-file-mb 50

# 明确允许待审核归属；候选 URL 仍须已验证
python3 -m paper_author_searcher.download_cli \
  --input papers.pdf_urls.json --output-dir downloaded_papers \
  --include-needs-review
```

已安装项目时也可运行 `pdf-download`。使用本项目 `.venv` 时，将 `python3` 替换为 `.venv/bin/python`；当前环境需要的可信 CA 配置可继续使用 `SSL_CERT_FILE=/etc/ssl/cert.pem`。下载依赖固定为 `pypdf>=5.9,<6`，原有其他阶段不必安装此可选依赖。

HTTP 流式写入临时 `.part` 文件，默认限制单文件 50 MiB，逐块计算 SHA-256，核对实际大小和完整响应。重定向和 DNS 结果逐一检查，禁止本地/内网目标、凭据 URL 与非配置端口；连接固定到已经检查的公网 IP，同时保留原主机名的 TLS 验证。下载器不使用环境代理，不处理登录或验证码。

文件头、EOF/cross-reference 及所有页面内容流通过严格 PDF 解析后才保存为 `.pdf`。文件格式与论文内容身份分别记录：内容中的 DOI/标题一致，或没有 DOI 时独特标题与完整作者署名一致，才确认身份；证据不足进入 `needs_review/`，明确的不同论文进入 `quarantine/`。程序不执行 PDF 的 JavaScript、动作或嵌入文件。

文件名清理跨平台非法字符并带稳定论文哈希，按字节截断长标题，采用独占文件名预留避免覆盖。每篇论文完成后原子更新 `manifest.json`，保留所有输入条目、失败、跳过、候选尝试与历史结果。重跑会重新检查文件 SHA-256 和内容解析，并跳过完整文件；不同 URL 的同内容 PDF 共用一个本地文件，仍逐论文核对身份。没有实现 Range 分段续传，中断的未完成下载从零重新开始。

`--max-downloads` 限制本次尝试的论文条目数，已完成文件的复用不占此额度。候选失败会继续其他已验证候选和其他论文。CLI 返回 0 表示本次无失败/内容待审核，2 表示存在失败或内容待审核且 manifest 已保存，130 表示用户中断；即使返回 0，也需查看归属、URL 和数量限制导致的跳过记录。

新 PDF Resolver 报告保留 authors/year/source/source_paper_id/external_ids。旧报告缺失的作者、年份不推测补齐，文件名使用 `unknown_year`。详细字段、配置和限制见 [下载模块文档](docs/download.md)；实际下载与重跑结果见 [真实测试报告](reports/pdf_download_sample.md)。

## End-to-end integration

统一工作流复用现有全部阶段，支持 `metadata`、`pdf-urls`、`full` 三种模式。

```bash
python3 -m pip install -e '.[download]'
python3 -m paper_author_searcher.cli --mode full \
  --name 'Geoffrey Hinton' --university 'University of Toronto' \
  --output-dir output --max-downloads 3
```

身份 ambiguous 时会展示候选并停止；用保存的 `professor.json` 和 `--select-author SOURCE:AUTHOR_ID` 明确选择后继续。默认只处理归属 verified 的论文，`--include-needs-review` 是明确授权，仍保留原有归属状态。

限量测试可加 `--max-pages 1 --page-size 5 --max-pdf-papers 2 --max-downloads 1`。PDF 下载数量限制不会自动限制前面的检索请求。每次执行生成独立目录，保存原始论文证据、PDF URL、PDF 文件、manifest 和阶段报告；支持 `--input` 读取旧 JSON 或报告续跑。

```bash
python3 -m paper_author_searcher.cli --mode full \
  --input output/SEARCH_DIRECTORY/report.json \
  --resume-dir output/SEARCH_DIRECTORY --max-downloads 3
```

下载文件位于 `papers/`，根目录的 `manifest.json` 使用 `file_root="papers"`。新数据在 PDF 与下载阶段完整保留年份、作者、来源 Author ID 和 `paper_metadata`；旧 JSON 缺失的字段会明确报告，不编造年份或署名。

详细命令、输出结构、续跑策略和统计含义见 [docs/workflow.md](docs/workflow.md)。

PDF 内容验证、文档类型识别、来源冷却和相同范围复测见 [docs/pdf_validation.md](docs/pdf_validation.md)。本轮 20 篇待审核及全部 47 份现有文件的离线复验、完整联网复测命令和限制见 [验证报告](reports/performance/pdf_validation.md)。

后续 100 篇真实运行的来源完整率下降诊断、重定向缓存修复和独立 `search_sufficiently_satisfied` 指标见 [搜索完整性报告](reports/performance/pdf_search_completion.md)。该指标确认实际取得验证全文，与所有来源是否完成查询分开统计。

独立评估数据见 [Professor Paper Ground Truth 候选集](data/ground_truth/professor_papers_v1/README.md)：3 位教授、2020–2022 年主窗口，共 187 条未确认候选及版本审核队列，未使用 Agent 运行结果构造答案。人工流程与纳入规则见 [审核指南](docs/ground_truth_review.md)；当前没有真实人工 Ground Truth 的正式 Recall。

Aarti Singh 的 37 条记录已整理为 [试点审核包](data/ground_truth/aarti_singh_pilot_v1/README.md)。逐论文审核、版本关联、完整性检查、转换 Ground Truth 和独立核查 Agent 未匹配记录的命令见 [试点评估指南](docs/ground_truth_pilot.md)；人工审核未完成时工具不输出正式 Precision/Recall。

最新 [v2 分组审核页](data/ground_truth/aarti_singh_pilot_v2/review/grouped_review.md) 保留原 37 条并追加两条独立出版来源支持的期刊候选，以出版物为单位展示证据和疑点。按组填写人工决定、转换为现有 Ground Truth 格式的步骤见 [分组审核指南](docs/ground_truth_grouped_review.md)。

## 分层评测集与上线门槛

`paper-benchmark` 对 32 位按 资历 × 领域 × 姓名类型 分层的研究者运行完整检索，逐层报告召回率、精度和全文获取率，并按 [配置](data/benchmark/v1/benchmark.json) 中的门槛（当前每层 verified 召回 ≥ 80%、精度 ≥ 95%）判定能否上线。真值来自研究者本人或实验室维护的书目，人工审核前的分数只是 provisional。用法、指标口径与审核流程见 [docs/benchmark.md](docs/benchmark.md)，数据集见 [data/benchmark/v1](data/benchmark/v1/README.md)。

## 身份解析增强

针对同名与档案拆分：机构限定检索、同来源拆分档案合并、ORCID / 主页 / 已知论文锚点（`--orcid`、`--homepage`、`--known-paper`）、歧义时收集竞争档案供审核（`--ambiguity-policy collect_contenders`，不会产生 verified）、可选的 Claude 裁决（`--llm-adjudication`），以及评测用的诊断与离线校准（`paper-benchmark diagnose-identity`、`calibrate`）。说明见 [docs/identity_resolution.md](docs/identity_resolution.md)。

## 锁定身份与多源检索

输入：姓名、学校必填；主页、发表页 / CV、邮箱、ORCID、Google Scholar 及其他档案链接选填。未提供时自动发现 ORCID（注册表唯一匹配且当前就职或就读于该机构、院系一致）与个人页面（ORCID 个人网站、可选的网页搜索 API），页面上的发表列表可由 LLM 结构化提取。新增来源：DBLP 官方 dump 本地索引（`paper-dblp-index`）、ACL Anthology / PMLR / CVF / OAI-PMH 知识库本地书目库（`paper-catalog-index`）、Europe PMC（含 PubMed）、Crossref 作者 + 机构检索、CORE；本人发表列表中的每一篇缺失论文按配置顺序在各库逐篇反查。说明见 [docs/identity_and_sources.md](docs/identity_and_sources.md)，评测见 [reports/benchmark/phase1-2-evaluation-20261027.md](reports/benchmark/phase1-2-evaluation-20261027.md)。
