# 搜索服务 API（v1）

异步任务式 HTTP API：提交一次搜索，得到任务 ID，再轮询状态和结果。一次搜索通常要几分钟到几十分钟，所以不阻塞等待。

服务只用 Python 标准库（`http.server`、`sqlite3`），不引入运行时依赖。

## 网页：输入姓名和学校，拿到 PDF

```bash
paper-search-api serve --open
```

浏览器会自动打开 `http://127.0.0.1:8080/`，不用自己填密钥：
- 服务启动时为这个浏览器生成一个本次会话专用的密钥，只存在内存里。
- 密钥放在网址的 `#` 片段中交给页面。浏览器不会把片段发给服务器，所以它不会出现在任何日志里。
- 页面把密钥存在本浏览器，并从地址栏抹掉。
- 服务停止后，这个密钥就失效了。

页面的用法：
- 填姓名、学校（院系可选，填了同名者更少），点"搜索"。页面会显示进度，可以关掉页面，稍后再回来看。
- 遇到分不清的同名者，页面列出候选人，点"就是这位"后继续。
- 完成后列出论文，每篇可以单独下载 PDF；也可以点"下载全部 PDF（zip）"，一次下载全部。

## 启动（给程序调用）

```bash
paper-search-api create-key --name cold-email-agent
```

这条命令会打印一次密钥，之后不再显示。服务端只保存密钥的 SHA-256。

```bash
paper-search-api serve --port 8080 --workers 1 --max-cost-usd 0.5
```

- `--data-dir`（默认 `output/api`）保存三样东西：任务数据库 `jobs.sqlite`、每次运行的目录 `runs/`、密钥文件 `api_keys.json`（权限 600）。
- 数据源的密钥和其他配置，与命令行工具一样从环境变量和 `--settings` 读取（见 README）。
- `--max-cost-usd` 是每次搜索的计费 API 花费上限，每个任务单独计账。
- `--workers` 是同时运行的搜索数。每次搜索只在自身内部按网站限速，所以多开几个，对数据源的请求量也会成倍增加。
- 服务只监听本机（默认 `127.0.0.1`）。对外提供时请放在 TLS 反向代理后面，因为密钥在请求头里传输。
- 重启后，未完成的任务会自动重新排队，并从头运行。

撤销密钥：`paper-search-api revoke-key KEY_ID`，然后重启服务。

## 鉴权与幂等

- 除 `/`、`/v1/health` 和 `/v1/schemas/*` 外，所有请求都要带 `Authorization: Bearer <key>`。缺少或无效时返回 401。
- 每个密钥只能看到自己创建的任务；别人的任务一律返回 404。
- 两个 POST 接口都支持 `Idempotency-Key` 请求头（1–255 个字符），在 24 小时内有效：
  - 用同一个键重试同样的请求，返回第一次的响应，并带上 `Idempotent-Replayed: true`，不会创建第二个任务；
  - 同一个键用于不同的请求体，返回 422 `idempotency_key_reused`；
  - 第一次请求还没处理完，返回 409 `idempotency_in_progress`；
  - 请求被拒绝（参数错误、状态不对）时不占用这个键，改正后可以用同一个键重试。

## 接口

| 方法与路径 | 作用 | 成功 |
|---|---|---|
| `POST /v1/searches` | 提交搜索（请求体见 `search-request`） | 202，返回任务，`Location` 指向它 |
| `GET /v1/searches/{id}` | 任务状态、进度、候选名单、结果 | 200 |
| `POST /v1/searches/{id}/confirmation` | 在 `needs_confirmation` 时选定一个候选 | 202，任务重新排队 |
| `GET /v1/searches/{id}/papers/{pid}/pdf` | 下载结果中某篇论文已核对的 PDF | 200，`application/pdf` |
| `GET /v1/searches/{id}/pdfs.zip` | 结果中所有已核对 PDF 的 zip | 200，`application/zip` |
| `GET /` | 网页（加载页面不需要密钥） | 200 |
| `GET /v1/schemas/{name}` | JSON Schema：`search-request`、`confirmation-request`、`search`、`error` | 200 |
| `GET /v1/health` | 存活检查 | 200 |

请求体上限 64 KB，超出返回 413。错误统一用 `error` schema：`{"api_version": "v1", "error": {"code", "message", "details"}}`。

## 任务状态

```
queued → running → succeeded
                 → failed
                 → needs_confirmation --(confirmation)--> queued → running → …
```

- `running` 时，`progress` 给出当前阶段，以及已完成数和总数（论文级计数）。
- `succeeded`：返回 `result`，包含身份、论文列表和统计。`run_status` 为 `partial`，表示部分来源出错，或有文件待审。
- `failed`：返回 `error`，其中 `code` 为以下之一：
  - `identity_not_found`：没有任何匹配的人；
  - `selection_rejected`；
  - `search_failed`；
  - `internal_error`。

## 同名者确认

`on_ambiguity` 默认为 `confirm`。分不清同名者时，任务停在 `needs_confirmation`，并返回 `candidates`。每个候选包含姓名、单位、ORCID、匹配分、证据和 `selections`。

调用方从某个候选的 `selections` 里选一个值提交：

```json
POST /v1/searches/srch_…/confirmation
{"selection": "openalex:A5086000078"}
```

任务从第一次运行解析出的身份继续，不会重新解析。如果设为 `"on_ambiguity": "collect_contenders"`，就不停下来，而是收集几个接近候选的论文，全部标为 `needs_review`。

## 结果中的论文

默认只返回归属已验证（`verified`）的论文。`include_needs_review: true` 时，也返回待审论文。

同一工作的不同版本（arXiv 预印本、会议版、期刊版）在收集结果中是几条互相关联的记录，结果中只返回一条工作，其他版本列在 `editions` 里。主条目的选择顺序：先选有已核对文件的版本，再选已出版的版本（非 arXiv DOI）。`statistics.papers` 按工作计数。没有被收集结果关联起来的同名条目不合并。

`full_text.status` 有三种取值：
- `file`：已下载，且确认是这篇论文，可从 `download` 路径取回；
- `link`：有一个验证过的开放获取链接；
- `none`：两者都没有。

## Schema 版本

请求和响应都按 `/v1` 的 JSON Schema（draft 2020-12）定义，文件在 `src/paper_author_searcher/api/schemas/v1/`，所有响应都带 `api_version`。

- 向后兼容的增补（例如新的可选字段）留在 v1。
- 不兼容的改动发布为 v2 的新路径和新 schema，v1 继续保留。

服务端用内置校验器检查请求。校验器只支持这些 schema 用到的关键字，遇到其他关键字会直接报错，不会悄悄忽略。

## 示例

```bash
curl -s -X POST localhost:8080/v1/searches \
  -H "Authorization: Bearer $KEY" -H 'Idempotency-Key: lead-4711' \
  -d '{"name": "Julie Kallini", "university": "Stanford University", "department": "Computer Science"}'
curl -s localhost:8080/v1/searches/srch_… -H "Authorization: Bearer $KEY"
```
