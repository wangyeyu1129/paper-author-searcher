# 部署与运维

## 架构

```
         ┌──────────── HTTP 适配器（api/）────────────┐
 调用方 ─▶│ 路由 · 鉴权 · JSON Schema 校验 · 状态码     │
         └──────────────────┬──────────────────────────┘
                            ▼
              SearchService（门面，service/facade.py）
          提交 · 查询 · 确认同名者 · 取文件 · 幂等 · 就绪 · 指标
                 │                 │                  │
        JobRepository          FileStore         SearchRunner      ← 端口（service/ports.py）
                 │                 │                  │
     SqliteJobRepository   本地目录 / S3 兼容    WorkflowRunner      ← 适配器
                 ▲                 ▲                  │
                 └──── SearchWorker（任务队列的消费者）┘
```

- **内核不依赖任何 web 框架。** HTTP 只是一个适配器；以后加命令行、消息队列或别的框架做前端，都只需要调用 `SearchService`。
- **仓储可以换后端。** 任务和结果存在 `JobRepository` 里（目前是 SQLite）；PDF、zip 和身份检查点存在 `FileStore` 里（本地目录或 S3 兼容存储）。
- **搜索以命令的形式执行。** 提交后，任务以 `queued` 状态写进任务表，由 worker 领取执行。领取是一条带条件的 UPDATE，两个 worker 不会拿到同一个任务。
- **worker 不保留状态。** 文件和检查点都放在 FileStore 里，所以 worker 可以随意增减，也可以跑在不同的机器上。
- **心跳与重新排队。** worker 定时上报心跳；某个 worker 超过 `stale_after_seconds` 没有上报（进程死了），它手上的任务会被别的 worker 重新排队，从头再跑一遍。

## Docker

```bash
cp deploy/.env.example deploy/.env      # 填数据源密钥等，此文件不进 git
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml run --rm api create-key --name cold-email-agent
```

| 服务 | 作用 |
|---|---|
| `api` | HTTP API 和网页（端口 8080），只负责排队和应答，不运行搜索 |
| `worker` | 运行搜索。`--scale worker=3` 同时跑 3 个 |
| `indexer` | 每周检查 DBLP 是否发布了新 dump：有新的才重建索引，然后原子替换；同时更新书目库 |
| `storage`、`bucket` | 可选（`--profile s3`）：S3 兼容存储（Versity S3 Gateway，会校验签名），用来保存 PDF；`bucket` 负责建桶 |
| `prometheus` | 可选（`--profile monitoring`）：采集指标，加载告警规则 |

所有服务共用 `data` 卷，里面有任务库、本地文件、HTTP 缓存和索引。

注意 DBLP 索引的首次构建：
- 首次构建要下载约 1 GB 的 dump，生成约 6 GB 的索引，需要一段时间。
- 索引建好之前，DBLP 来源会退回到在线 API。
- 如果手上已经有索引，可以直接放到卷里的 `/data/indexes/dblp.sqlite`。

## 配置

所有配置都走环境变量。
- 普通配置：`PAPER_SEARCH__<分区>__<字段>`，见 `Settings`。服务相关的在 `service` 分区（`config/service.py`）。
- 密钥只从各自约定的环境变量读取，不会写进配置文件或日志：
  - 数据源密钥：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY` 等；
  - 网页搜索密钥：`TAVILY_API_KEY`、`BRAVE_API_KEY`、`EXA_API_KEY`，配一个即可。没有配置时，只存放在作者主页或其他网站上的 PDF 找不到，结果里的 `notices` 会说明这一点；
  - 对象存储密钥：`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`。

常用项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PAPER_SEARCH__SERVICE__API_KEY_HASHES` | 空 | `id:name:sha256`，多个用逗号分隔；也可以用数据卷里的密钥文件 |
| `PAPER_SEARCH__SERVICE__WORKERS` | 1 | `serve` 进程内的 worker 线程数；compose 里是 0，由 worker 服务执行搜索 |
| `PAPER_SEARCH__SERVICE__MAXIMUM_COST_USD` | 0.5 | 每次搜索的计费 API 花费上限 |
| `PAPER_SEARCH__SERVICE__FILE_STORE` | local | `local` 或 `s3` |
| `PAPER_SEARCH__SERVICE__S3_ENDPOINT` / `S3_PUBLIC_ENDPOINT` / `S3_BUCKET` | | 服务访问存储用的地址、调用方下载用的地址、桶名 |
| `PAPER_SEARCH__SERVICE__STALE_AFTER_SECONDS` | 120 | worker 多久没有心跳算死掉 |
| `PAPER_SEARCH__SERVICE__MAXIMUM_ATTEMPTS` | 3 | 一个任务让 worker 中断这么多次后判为失败，不再排队 |
| `PAPER_SEARCH__SERVICE__MAXIMUM_DAILY_COST_USD` | 1.0 | 每个计费数据源每天（UTC）的花费上限，所有 worker 共用 |
| `PAPER_SEARCH__SERVICE__MAXIMUM_ACTIVE_SEARCHES` | 20 | 每个 API 密钥同时排队和运行的搜索数，超过返回 429；0 不限 |
| `PAPER_SEARCH__SERVICE__MAXIMUM_SEARCHES_PER_HOUR` | 60 | 每个 API 密钥每小时可提交的搜索数，超过返回 429；0 不限 |
| `PAPER_SEARCH__SERVICE__RETENTION_DAYS` | 30 | 已结束的搜索和文件保留多久；0 永久保留 |
| `PAPER_SEARCH__SERVICE__HTTP_CACHE_RETENTION_DAYS` | 90 | HTTP 缓存文件保留多久；0 永久保留 |
| `PAPER_SEARCH__EXTRACTION__MAXIMUM_COST_USD` | 1.0 | 每次搜索 Claude 的花费上限，用完后页面只用规则解析 |
| `PAPER_SEARCH__SERVICE__REQUIRE_INDEXES` | false | 就绪检查是否要求 DBLP 索引和书目库已经存在 |
| `PAPER_SEARCH_LOG_FORMAT` | 镜像内为 json | 日志格式 |

使用 S3 时，调用方下载 PDF 会被 302 跳转到一个短时有效的预签名链接（`presign_seconds`，默认 15 分钟），文件不经过 API 进程。预签名用的是标准库自己实现的 SigV4，已经用 AWS 文档里的示例核对过；AWS S3、MinIO、Cloudflare R2 都可以用。

## 健康检查、日志、指标、告警

- **`GET /v1/health`（存活）：** 进程能应答就是 200。镜像的 `HEALTHCHECK` 用的就是它。
- **`GET /v1/ready`（就绪）：** 逐项检查任务库、文件存储、是否有活着的 worker，以及（可选）索引。任何一项不通过就返回 503，并列出是哪一项。负载均衡应该看这个接口。
- **日志：** 镜像内默认输出 JSON 行，写到 stderr。日志里只有路径和状态码，不记录请求体，也不记录 `Authorization` 头。
- **`GET /metrics`（Prometheus）：**

  | 指标 | 含义 |
  |---|---|
  | `paper_search_jobs{status}` | 各状态的任务数 |
  | `paper_search_job_runs_finished_total{status}` | 已结束的运行次数，按结果分 |
  | `paper_search_job_run_seconds_total{status}` | 已结束运行的总耗时，按结果分 |
  | `paper_search_queue_oldest_seconds` | 最早一个排队任务已经等了多久 |
  | `paper_search_workers_alive` | 最近上报过心跳的 worker 数 |
  | `paper_search_cost_usd_total` | 所有搜索累计的计费 API 花费 |
  | `paper_search_llm_cost_usd_total` | 所有搜索累计的 Claude 花费 |
  | `paper_search_http_requests_total{route,code}` | 本进程应答的请求数，按路由和状态码分 |

  这个接口不需要密钥，请只在内网暴露。

- **告警：** 规则在 `deploy/prometheus/alerts.yml`。

  | 告警 | 触发条件 |
  |---|---|
  | API 不可达 | Prometheus 取不到指标 |
  | 没有 worker | 5 分钟内没有 worker 上报心跳 |
  | 队列卡住 | 有任务排队超过 30 分钟 |
  | 失败率高 | 最近一小时超过 25% 的搜索失败 |
  | 持续 5xx | API 持续返回服务器错误 |
  | 搜索过慢 | 搜索平均耗时超过 1 小时 |
  | 花费接近上限 | 一天的计费 API 花费接近 OpenAlex 每日免费额度 |

  需要发通知的话，在 `prometheus.yml` 里配置一个 Alertmanager。

## 扩容与限制

- **SQLite 的适用范围：** 适合单机，或者多个进程共享同一个卷。如果要跨主机部署多个 API 副本，需要换成服务器型数据库，做法是实现一个新的 `JobRepository`，内核不需要改。
- **worker 数量：** 所有 worker 通过 `limits.sqlite` 共用每个数据源的请求间隔（`service.shared_host_intervals`）、429 退避和每日预算，增加 worker 不会让请求超过这些限制。其他网站（作者主页、出版商页面）仍然只在每次搜索内部限速。建议先从 1–3 个开始，观察数据源返回的 429 再调整。
- **花费：** OpenAlex 等计费接口受每次搜索和每天两层上限约束；Claude 受每次搜索上限约束，每天的总量由调用方限流和 `PaperSearchLlmSpend` 告警控制。指标 `paper_search_llm_cost_usd_total` 单独统计 Claude 的花费。
- **数据清理：** worker 每隔 `housekeeping_hours` 删除过期的搜索和文件。删除后指标里的累计值保持不变。
