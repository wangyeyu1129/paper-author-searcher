# 与 cold-email agent 的输出契约（v1）

agent 会根据研究者的论文给本人写信。引错一篇论文，或者把同名者的论文当成本人的，比少引一篇糟糕得多。所以契约的原则是：**只把确定的内容交给 agent 用；不确定的明确标出来，并说明原因。**

## 接口

```
GET /v1/searches/{id}/agent      Authorization: Bearer <key>
```

响应格式按 JSON Schema `agent-result`（`/v1/schemas/agent-result`）。搜索未完成时也可以调用，此时 `status` 表示处于哪一步。

## 字段

| 字段 | 含义 |
|---|---|
| `status` | `ready` 可以使用；`nothing_usable` 已完成但没有可用的论文；`pending` 还在跑；`needs_confirmation` 需要人工从同名者中选定一位；`failed` 失败 |
| `usable` | 只有 `papers` 不为空时才是 `true`。agent 只在它为 `true` 时写信 |
| `researcher.requested` | 调用方请求的姓名、学校、院系 |
| `researcher.identity` | 系统确认的身份：`confirmed`、`confirmed_by`（`evidence` 表示按证据确认，`caller` 表示由人选定）、姓名、ORCID、各来源的作者 ID、置信度、证据 |
| `papers[]` | **agent 可以引用的论文**（字段见下表） |
| `withheld[]` | **不可引用的论文**，每篇附 `reason` 和证据，供人工复核 |
| `notes[]` | 需要注意的情况，例如部分来源没有响应，列表可能不完整 |

`papers[]` 中每篇论文的字段：

| 字段 | 含义 |
|---|---|
| `title`、`year`、`venue`、`authors` | 标题、年份、期刊或会议、作者 |
| `abstract` | 摘要；来源里没有时为 null |
| `doi`、`url` | DOI 和 DOI 链接 |
| `full_text` | `status` 为 `file` 时可用 `download` 下载已核对的 PDF；为 `link` 时 `url` 是验证过的开放获取链接；为 `none` 时没有原文 |
| `confidence` | `high` 或 `medium`，含义见下 |
| `evidence` | 支撑这个判断的发现，最多 3 条（`service.maximum_paper_evidence`） |

## 什么能交给 agent

一篇论文进入 `papers`，要同时满足两个条件：

1. **身份已确认。** 要么系统按证据确认了身份（`resolved`），要么调用方从候选人里选定了一位。
   - 没有填院系又分不清同校同名者时，身份不算确认，**所有论文都会被扣下**，`reason` 为 `identity_unconfirmed`。
2. **论文归属已验证（verified）。**
   - 没有通过验证的（待审）论文会被扣下，`reason` 为 `ownership_unverified`。例如证据不足，或者不是研究论文。

`confidence` 根据归属证据来定。用哪些证据短语来判断，可以在 `service.strong_ownership_evidence` 和 `service.supporting_ownership_evidence` 里配置：

| 置信度 | 依据 |
|---|---|
| `high` | 本人主页或 CV 列出了这篇论文；论文级 ORCID 与本人一致；DBLP 人工维护的档案（带本人 ORCID，或其任职记录与查询吻合） |
| `medium` | 作者 ID、署名和论文机构一致；与经过验证的固定合作者一起署名；跨来源确认是同一篇作品 |

同一工作的不同版本（预印本、会议版、期刊版）只返回一条：置信度取证据最强的那个版本，摘要取任一版本中有的那份。

## 示例

```json
{
  "api_version": "v1",
  "search_id": "srch_…",
  "status": "ready",
  "usable": true,
  "researcher": {
    "requested": {"name": "Julie Kallini", "university": "Stanford University", "department": "Computer Science"},
    "identity": {"confirmed": true, "confirmed_by": "evidence", "name": "Julie Kallini", "orcid": "0000-0003-0835-6485",
                 "author_ids": {"openalex": "A5019317146"}, "confidence": 0.95, "evidence": ["…"]}
  },
  "papers": [
    {"id": "0", "title": "Mission: Impossible Language Models", "year": 2024, "venue": "ACL (1)",
     "abstract": "…", "doi": "10.18653/v1/2024.acl-long.787", "url": "https://doi.org/10.18653/v1/2024.acl-long.787",
     "full_text": {"status": "file", "url": "https://arxiv.org/pdf/…", "download": "/v1/searches/srch_…/papers/0/pdf"},
     "confidence": "medium", "evidence": ["Explicit target Author ID, compatible byline and paper-level institution agree: Stanford University"]}
  ],
  "withheld": [{"id": "16", "title": "…", "year": 2026, "reason": "ownership_unverified", "evidence": ["…"]}],
  "notes": []
}
```

## agent 侧的建议

- `usable` 为 `false` 时不写信。`needs_confirmation` 的情况交给人处理：在网页或通过 `/confirmation` 接口选定一位后，搜索会继续。
- 信里优先引用 `confidence: high` 的论文，以及近几年的论文。
- 绝不引用 `withheld` 里的论文。
