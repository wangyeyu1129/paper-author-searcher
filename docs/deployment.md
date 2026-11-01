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

| 服务 | 启动方式 | 作用 |
|---|---|---|
| `api` | 默认 | HTTP API 和网页，只负责排队和应答，不运行搜索。只监听 `127.0.0.1:8080` |
| `worker` | 默认 | 运行搜索。`--scale worker=3` 同时跑 3 个 |
| `indexer` | 默认 | 启动时构建索引，之后每周检查 DBLP 是否发布了新 dump，有新的才重建并原子替换；同时更新书目库 |
| `backup` | 默认 | 定时生成备份，写到 `BACKUP_DIRECTORY` |
| `caddy` | `--profile public` | HTTPS 和反向代理，唯一对公网开放的服务（80、443），自动申请证书，`/metrics` 返回 404 |
| `prometheus`、`alertmanager` | `--profile monitoring` | 采集指标、判断告警、发送通知。只监听本机 |
| `storage`、`bucket` | `--profile s3` | 可选：自带的 S3 兼容存储，用来保存 PDF |

所有服务共用 `data` 卷，里面有任务库、本地文件、HTTP 缓存和索引。

上线步骤见[部署手册](#部署手册)。

## 配置

所有配置都走环境变量。
- 普通配置：`PAPER_SEARCH__<分区>__<字段>`，见 `Settings`。服务相关的在 `service` 分区（`config/service.py`）。
- 密钥只从各自约定的环境变量读取，不会写进配置文件或日志：
  - 数据源密钥：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY` 等；
  - 网页搜索密钥：`TAVILY_API_KEY`、`BRAVE_API_KEY`、`EXA_API_KEY`，配一个即可。没有配置时，只存放在作者主页或其他网站上的 PDF 找不到，结果里的 `notices` 会说明这一点；
  - 对象存储密钥：`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`。
- 密钥文件优先于环境变量。`PAPER_SEARCH_SECRETS_DIRECTORY` 指向的目录里，以变量名命名的文件就是它的值。compose 把 `SECRETS_DIRECTORY` 挂载到这里。生产环境请用这种方式，不要把密钥写进 `.env`。

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
  | `paper_search_last_backup_timestamp_seconds` | 最近一次成功备份的时间 |

  这个接口不需要密钥。经过 Caddy 访问时返回 404，只有本机和 compose 内部网络能读到。

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
  | 备份过期 | 超过 36 小时没有成功备份 |
  | 没有备份 | 服务运行 2 小时后仍然没有任何备份 |

  通知由 Alertmanager 发送，配置文件是 `deploy/alertmanager/alertmanager.yml`，从 `alertmanager.example.yml` 复制后修改（这个文件不进 git）。

## 扩容与限制

- **SQLite 的适用范围：** 适合单机，或者多个进程共享同一个卷。如果要跨主机部署多个 API 副本，需要换成服务器型数据库，做法是实现一个新的 `JobRepository`，内核不需要改。
- **worker 数量：** 所有 worker 通过 `limits.sqlite` 共用每个数据源的请求间隔（`service.shared_host_intervals`）、429 退避和每日预算，增加 worker 不会让请求超过这些限制。其他网站（作者主页、出版商页面）仍然只在每次搜索内部限速。建议先从 1–3 个开始，观察数据源返回的 429 再调整。
- **花费：** OpenAlex 等计费接口受每次搜索和每天两层上限约束；Claude 受每次搜索上限约束，每天的总量由调用方限流和 `PaperSearchLlmSpend` 告警控制。指标 `paper_search_llm_cost_usd_total` 单独统计 Claude 的花费。
- **数据清理：** worker 每隔 `housekeeping_hours` 删除过期的搜索和文件。删除后指标里的累计值保持不变。

## 部署手册

一台 Linux 虚拟机加 Docker Compose。SQLite 要求所有进程在同一台机器上，所以不要用 Kubernetes 或多台机器。

不想花钱的话，看[零成本方案](#零成本方案)。

### 1. 准备服务器和域名

- 系统选 Ubuntu 24.04，配置见[容量](#容量)。
- 把域名（例如 `search.example.com`）的 A 记录指向服务器 IP。
- 云防火墙只放行 22、80、443。

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
git clone https://github.com/OWNER/paper-author-searcher.git
cd paper-author-searcher
```

重新登录一次，让 docker 用户组生效。

### 2. 配置和密钥

```bash
cp deploy/.env.example deploy/.env
mkdir -p deploy/secrets deploy/backups
```

在 `deploy/.env` 里设置 `SITE_ADDRESS`、`UNPAYWALL_EMAIL`、`CROSSREF_MAILTO`、`PAPER_SEARCH_IMAGE`，并把合规决定对应的 `PAPER_SEARCH__SERVICE__HTTP_CACHE_RETENTION_DAYS` 写上（见 [合规要点](compliance.md)）。

每个密钥写成一个文件，文件名就是变量名，比如 `deploy/secrets/OPENALEX_API_KEY`。写好后设置权限：

```bash
sudo chown -R 10001:10001 deploy/secrets deploy/backups
sudo chmod 500 deploy/secrets
sudo chmod 400 deploy/secrets/*
```

需要的文件：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`、`TAVILY_API_KEY`（或 `BRAVE_API_KEY`、`EXA_API_KEY`），以及接收告警用的 `ALERT_SMTP_PASSWORD`。

### 3. 配置告警通知

```bash
cp deploy/alertmanager/alertmanager.example.yml deploy/alertmanager/alertmanager.yml
```

填入收件人和 SMTP 服务器，或者改用文件里注释掉的 Slack、webhook。

### 4. 启动

```bash
docker compose -f deploy/compose.yaml --profile public --profile monitoring up -d --scale worker=3
docker compose -f deploy/compose.yaml run --rm api create-key --name cold-email-agent
```

第二条命令只显示一次 API 密钥，请马上保存。

### 5. 等索引建好

`indexer` 启动后会自动构建索引。实测约 14 分钟，内存峰值 252 MiB，下载 1.1 GB，索引 6.1 GB。

```bash
docker compose -f deploy/compose.yaml logs -f indexer
```

建好后，在 `deploy/.env` 里设置 `PAPER_SEARCH__SERVICE__REQUIRE_INDEXES=true`，再执行一次第 4 步的 `up -d`。

### 6. 验证

把 `search.example.com` 换成你的域名，`$KEY` 换成上面的 API 密钥。

```bash
curl -s https://search.example.com/v1/ready
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://search.example.com/metrics
```

第一条应该全部通过，第二条应该是 404。再提交一次真实搜索：

```bash
curl -s https://search.example.com/v1/searches -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"name":"Kevin Ellis","university":"Cornell University","mode":"pdf-urls"}'
```

最后试一次告警：`docker compose -f deploy/compose.yaml stop api`，约 2 分钟后应该收到通知，`start api` 后收到恢复通知。

### 零成本方案

用 Oracle Cloud Always Free 的 Arm 服务器，每月 0 美元。这个方案还没有在 Oracle 上实际部署过；所有容器（包括 Caddy、Prometheus、Alertmanager）在 arm64 的 Mac 上跑通过。

下面只列和前面步骤不同的地方，其余照做。

| 项目 | 用什么 | 额度 |
|---|---|---|
| 服务器 | Oracle Always Free，`VM.Standard.A1.Flex` | 2 OCPU / 12 GB 内存 / 200 GB 磁盘 / 每月 10 TB 出站流量 |
| 域名 | DuckDNS 子域名（`xxx.duckdns.org`） | 免费 |
| 异地备份 | rclone 加密后传到 Google Drive 或 Cloudflare R2 | R2 免费 10 GB；Drive 免费额度请到官网确认 |
| 告警邮件 | Gmail SMTP，用应用专用密码 | 免费 |
| OpenAlex | 免费额度，服务默认每天 1 美元就停 | 约 80 个作者/天（按每人约 80 篇论文估算） |
| Tavily | 免费额度，不要绑卡 | 每月 1,000 credits，够 36–330 次搜索 |

**第 1 步改成：**

1. 注册 Oracle Cloud。需要绑信用卡验证。主区域选美国，注册后不能改，免费的服务器和磁盘都必须建在主区域。
2. 创建实例：机型选 `VM.Standard.A1.Flex`（2 OCPU、12 GB），系统选 Ubuntu 24.04 的 aarch64 版本，启动盘设为 100 GB。如果提示 `out of host capacity`，换一个可用区或晚点重试。
3. 在实例所在子网的安全列表里放行 TCP 80 和 443。
4. 在 duckdns.org 上建一个子域名，指向实例的公网 IP。

如果放行后外部仍然连不上 80 和 443，再放行系统防火墙。Oracle 的 Ubuntu 镜像通常自带限制性的 iptables 规则：

```bash
sudo iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT
```

```bash
sudo netfilter-persistent save
```

**第 2 步：** `SITE_ADDRESS` 填 DuckDNS 的子域名。

**第 3 步：** `alertmanager.yml` 里的 `smtp_smarthost` 填 `smtp.gmail.com:587`，把 Gmail 应用专用密码写进 `deploy/secrets/ALERT_SMTP_PASSWORD`。

**第 4 步：** 在服务器上直接构建镜像，不设置 `PAPER_SEARCH_IMAGE`。`release.yml` 目前只构建 x86 镜像，不能在 Arm 上运行。

```bash
docker compose -f deploy/compose.yaml --profile public --profile monitoring up -d --build --scale worker=3
```

升级时先 `git pull`，再执行同一条命令。回滚就是 `git checkout` 到上一个版本，再执行一次。

**不要做这两件事，否则会开始计费：** 把 Oracle 账号升级成按量付费；给 Tavily 绑卡。

**这个方案的风险：**

- **服务器可能被回收。** 连续 7 天内，如果 CPU（95 分位）、网络、内存使用率都低于 20%，Oracle 可以回收实例。没有搜索时，这个服务很可能符合这个条件。
- **规则可能再变。** 据报道，Oracle 在 2026 年把 Arm 免费额度减半，而且没有公开宣布。免费服务也没有可用性保证。
- **异地备份必须配好**，并且不要放在 Oracle 账号里。服务器没了就新建一台，照上面的步骤部署，再按[备份和恢复](#备份和恢复)恢复。索引约 14 分钟重建完。
- **额度用完后结果会变差。** Tavily 用完后，靠网页搜索找到的主页和全文会缺失（实测全文覆盖率 61.5% 对 56.4%）。OpenAlex 用完后，当天的请求会被拒绝，结果里会说明。

要节省 Tavily 额度，调低 `PAPER_SEARCH__PDF__WEB_SEARCH_MAXIMUM_PAPERS`（默认 25）。

### 备份和恢复

`backup` 服务每天生成一份归档到 `deploy/backups`，保留 14 份。内容是任务库、限流库、API 密钥哈希和本地文件，并带 SHA-256 校验。索引和 HTTP 缓存不备份，它们可以重新生成。

**备份留在同一台机器上不算备份。** 用 rclone 加密后同步到另一家的对象存储（例如 Cloudflare R2）。先用 `rclone config` 配一个 `crypt` 类型的远端，这里叫 `offsite`，再加一条 cron：

```bash
0 5 * * * sudo rclone sync /home/ubuntu/paper-author-searcher/deploy/backups offsite:paper-search --config /root/.config/rclone/rclone.conf
```

恢复时，先停掉写入数据的服务，再用最近的归档恢复：

```bash
docker compose -f deploy/compose.yaml stop api worker indexer backup
```

```bash
docker compose -f deploy/compose.yaml run --rm --no-deps backup restore /backups/ARCHIVE.tar.gz --force
```

```bash
docker compose -f deploy/compose.yaml --profile public --profile monitoring up -d --scale worker=3
```

恢复前会先校验整个归档，校验不通过时不会改动现有数据。在新机器上恢复时，索引会由 `indexer` 重新构建。

可以用 `scripts/ops/backup_drill.py` 重新演练：它在隔离的 compose 项目里跑真实搜索，备份，删除所有容器和卷，恢复，再比较结果和文件是否一致。

### 发布、升级和回滚

推送 `v1.2.3` 这样的 tag，`.github/workflows/release.yml` 就会把镜像发布到 `ghcr.io/OWNER/paper-author-searcher:v1.2.3`。tag 发布后不要移动。

升级时，修改 `deploy/.env` 里的 `PAPER_SEARCH_IMAGE`，然后执行：

```bash
docker compose -f deploy/compose.yaml --profile public --profile monitoring pull
```

```bash
docker compose -f deploy/compose.yaml --profile public --profile monitoring up -d --scale worker=3
```

回滚就是把这一行改回上一个 tag，再执行同样的两条命令。如果新版本改过数据库结构，先恢复升级前的备份。仓库是私有的话，服务器上要先 `docker login ghcr.io`。

### 负载测试

`scripts/loadtest/run.py` 用真实镜像和假数据源，在隔离网络里验证所有 worker 共用的限流。

```bash
python scripts/loadtest/run.py --workers 1 4 --searches 16
```

### 容量

下面是实测数字，运行环境是一台 Mac 上的 Docker，用真实数据源，HTTP 缓存为空。那台机器的网络不稳定，第二次运行还断过约 15 分钟，所以耗时只能当上限参考，上线后请用 `paper_search_job_run_seconds_total` 重新核对。

| 项目 | 实测 |
|---|---|
| 约 80 篇论文的作者 | 30–45 分钟，`pdf-urls` 和 `full` 差不多，下载 PDF 不是主要耗时 |
| 4 篇论文的作者 | 不到 3 分钟 |
| 高产作者（Christopher Manning） | **两次都没跑完**，单个 worker 分别跑了约 87 分钟和 90 分钟后被测试中止 |
| 每次搜索的 OpenAlex 花费 | 约 80 篇论文的作者 0.010–0.013 美元 |
| 内存峰值 | 每个 worker 694 MiB，API 42 MiB，备份 27 MiB |
| PDF | `full` 模式 76 个文件的备份约 413 MiB，平均每个约 5 MiB |
| 索引 | 6.1 GB，加下载的 dump 1.1 GB；重建时新旧索引会同时存在 |

搜索期间 CPU 几乎空闲，时间花在等网络和按数据源限速上（例如 arXiv 每 3 秒一次），所以增加 CPU 不会变快。

- **worker 数量：** 从 3 个开始。一个高产作者可能占用一个 worker 几个小时，只有 1 个 worker 时其他搜索都要排队。
- **没有单次搜索的时间上限：** 高产作者会触发"搜索过慢"或"队列卡住"告警，这时先确认是不是这种情况。
- **调用方：** 请按小时而不是分钟等待结果，用轮询，不要同步等待。

| 用法 | 推荐配置（DigitalOcean Basic） | 月费 |
|---|---|---|
| `mode=pdf-urls`，3 个 worker | 2 vCPU / 4 GiB / 80 GiB | 24 美元 |
| `mode=full`，保存 PDF | 4 vCPU / 8 GiB / 160 GiB，或者 PDF 放 Cloudflare R2 | 48 美元 |

### 数据源额度

| 数据源 | 额度 | 够不够 |
|---|---|---|
| OpenAlex | 每天 1 美元免费 | 约 80 篇论文的搜索每次约 0.012 美元，每天大约 80 次。超过后当天拒绝请求，结果会说明有来源没响应。需要更多时先充值，再调高 `MAXIMUM_DAILY_COST_USD` |
| Tavily | 每月 1,000 credits 免费，超出按每个 0.008 美元计 | 每次搜索 3 个查询找主页，再为最多 25 篇找不到全文的论文各查一次，也就是 3–28 个 credits。免费额度够一个月 36–330 次，超出后每次搜索 0.024–0.22 美元。实测 Kevin Ellis 加上没跑完的 Manning 一共不到 16 个。要控制用量，调低 `PAPER_SEARCH__PDF__WEB_SEARCH_MAXIMUM_PAPERS` |
| Semantic Scholar | 带密钥时限速 1 次/秒 | 实测仍有不少 429，服务会退避重试，这是变慢的原因之一 |
