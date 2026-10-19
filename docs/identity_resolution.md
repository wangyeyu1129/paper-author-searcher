# 身份解析：诊断、合并、锚点与校准

基线评测（`baseline-metadata-20261018`）中 13/32 人停在 `identity_confirmation_required`，召回直接为 0。本文件说明针对这一问题新增的机制，全部可通过配置开关和调参。

## 1. 诊断（仅评测用）

```bash
paper-benchmark diagnose-identity --run-id <run-id>
```

对每个人重新运行候选检索（经 HTTP 缓存记录），列出每个候选档案在时间窗内的论文，并与真值比对，给出失败类型：

| 诊断 | 含义 | 对应解法 |
|---|---|---|
| `stuck_split_profiles` | 多个候选都持有本人论文：同一人被拆成多个档案 | 档案合并（§2）、锚点（§3） |
| `stuck_true_competitor` | 只有一个候选是本人，但同名者分数接近 | 锚点（§3）、LLM 裁决（§6） |
| `target_not_retrieved` | 没有任何候选持有本人论文：本人没进入候选列表 | 机构限定检索（§2）、锚点补取（§3） |
| `resolved_fragment` | 已解析，但只拿到部分档案 | 档案合并 |
| `resolved_wrong_person` | 已解析到错误的人 | 锚点、校准 |
| `resolved_complete` | 已解析且覆盖全部持有本人论文的档案 | — |
| `inconclusive_source_errors` | 检索或列表请求失败（限流/401），无法判断 | 配置 API key 后重跑 |

诊断读取真值，只用于评测，永远不进入检索决策。报告：`reports/benchmark/<run-id>.identity.{json,md}`。

## 2. 检索与档案合并

- **机构限定检索**（`SourceConfig.institution_scoped_search`）：OpenAlex 仅按姓名检索只返回最有名的 25 个同名者，常见中文名的本人常常不在其中。新增按"姓名 + 曾任职机构"的检索，并优先对这些候选做补充元数据。
- **拆分档案合并**（`MatchingConfig.merge_split_profiles`）：同一来源的不同 ID，只有在姓名兼容、同一学校、共同合作者 ≥ `split_profile_coauthors`、共同主题 ≥ `split_profile_topics` 时才视为同一人。ORCID 冲突或姓名不兼容会阻止合并。合并允许链式（A~B、A~C），但链上任一成员存在硬冲突时拒绝。

## 3. 外部锚点

调用方提供的任何一项都会转成"作者 ID 锚点"（`identity_anchors.py`）：

- `orcid`：OpenAlex 按 ORCID 查作者；ORCID 与之冲突的候选被扣分（`orcid_conflict_penalty`）。
- `known_papers`：DOI / arXiv ID / 精确标题 → 查出实际写了这些论文、且署名与姓名兼容的作者 ID（支持 "M. Lin" 这类缩写署名）。
- `homepage`：读取页面上的 DOI 与 arXiv 链接作为 known papers。

带锚点的档案加分（`anchor_bonus`），压过分数接近的同名者；多个带锚点的档案合并为一个身份；检索结果中没有、但锚点指出的档案会按 ID 补取；锚点身份即使机构信息缺失或已过时也可确认。

## 4. 输入扩展

```bash
paper-search --name 'Mei Lin' --university 'Example University' \
  --orcid 0000-0000-0000-0001 --homepage https://example.edu/~mlin \
  --known-paper 10.1000/xyz --known-paper 2401.01234 --email mlin@example.edu
```

全部可选。`email` 目前只随查询记录，尚未用于判定。

## 5. 歧义时不再"全有或全无"

`--ambiguity-policy collect_contenders`（工作流 CLI 和评测的默认值）：身份未确认时，收集分数在 `ambiguity_margin` 内、学校不冲突的前 `maximum_contenders` 个候选的论文，**全部标为 needs_review，绝不 verified**，并在报告中给出警告。这样能提升"检索到"的召回，不损失 verified 的精度。`--ambiguity-policy stop` 保留原来的停止行为；库默认仍是 `stop`。

## 6. LLM 裁决（可选，默认关闭）

`--llm-adjudication` 会让 Claude（`claude-opus-5-5`，结构化 JSON 输出，启用服务端拒答回退）只对"前两名分数接近且都没有锚点"的情况判断：两个档案是否同一人、哪个是目标。置信度低于 `adjudication_minimum_confidence` 或回答 `unsure` 时不采纳。裁决只影响身份选择，不会让任何论文直接 verified。需要安装 `.[llm]`（Anthropic SDK 1.x 需要 Python ≥ 3.10）和 Anthropic 凭据。

## 7. 校准

```bash
paper-benchmark calibrate --diagnosis-run <run-id>
```

在**记录下来的 API 响应上离线重放**（`output/benchmark/http_cache`），对 `benchmark.json` 中 `calibration.grid` 的每组参数，只在 `split=dev` 的 16 人上评估：

- resolved coverage：选中 ID 覆盖了候选中多少真值论文（未解析计 0）；
- resolved purity：选中且被诊断过的 ID 中，属于本人的比例。

在 purity ≥ `minimum_purity` 的参数中选 coverage 最高者（同分取改动最少的），再在 `split=test` 的 16 人上报告，避免只对评测集调参。结果只给建议，不自动修改默认值。2026-10-18 用有 key 的完整数据校准后，推荐值在 test 组上可以泛化，已采用为默认值：`ambiguity_margin=0.03`、`split_profile_coauthors=5`。

## 8. 指标

评测报告新增 `identity_resolution_rate`：系统自行确认身份的人所占比例。只收集竞争档案（contenders）不计为已解析。

## 当前限制

- 运行需要设置 `OPENALEX_API_KEY` 和 `SEMANTIC_SCHOLAR_API_KEY`；没有 key 时免费额度很快耗尽（401/429），结果会被截断。每次运行都有花费上限（`--max-cost-usd`，评测里的 `run.max_cost_usd`），缓存命中不计费。
- 校准依赖诊断标签；诊断不完整时，校准结论只适用于可诊断的人。
