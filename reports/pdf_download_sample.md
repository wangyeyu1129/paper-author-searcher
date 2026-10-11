# PDF Downloader 小规模真实测试

输入：`hinton_papers.pdf_urls.sample.json`。该归档含 573 个论文条目，其中只有 1 篇具有已验证的 PDF 候选；测试限制最多尝试 1 篇。没有重新运行教授解析、论文检索或 PDF URL 检索。

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.download_cli \
  --input hinton_papers.pdf_urls.sample.json \
  --output-dir output/pdf/hinton_download_sample \
  --max-downloads 1 --timeout 15 --retries 1
```

首次通过公网 HTTP(S) 流式请求保存 1 个完整文件，下载失败 0 个。修复通用 DOI 排版分隔符处理后，从已保存文件重新解析确认，无须再次下载。最终有效 PDF 1 个、论文对应关系 verified 1 个、needs_review 0 个、rejected 0 个。

## 实际文件

- 标题：Deep learning
- 输入 DOI：10.1038/nature14539
- 下载 URL：https://hal.science/hal-04206682/document
- 本地文件：`unknown_year_Deep_learning_fdf1b9bfa3fc0a9a.pdf`
- 大小：695128 字节；读取本地文件重新计算，与 manifest 一致。
- SHA-256：`db8e1ed80a3f076bb2568b0ea1ded69f2354e83139ad6fa2b66d88a662a8ca6e`
- 文件格式：valid；严格解析 10 页，并检查页面内容流及 startxref/EOF。
- 内容身份：verified；下载文件标题与 DOI 一致，证据：Strict PDF parser resolved pages and page content streams; EOF and startxref checked; Expected normalized title occurs in PDF title metadata or the first-page title region; Target DOI found in the downloaded PDF front matter/metadata
- 原有论文归属：verified，没有修改。

PDF 封面及论文首页已做只读渲染和视觉检查，标题、署名和 DOI 可读。生产下载器不依赖渲染工具，也不执行 PDF 动作或脚本。

## 重跑结果

再次运行已安装的 `pdf-download` CLI：新下载 0，网络下载尝试 0，复用 1，内容确认 1，失败 0；其他 572 条保留跳过记录。manifest 的 statistics 是本次运行口径，因此 downloaded_files=0、reused_existing_papers=1 是预期结果，不表示从未下载过文件。

manifest 保留每个输入条目、来源错误、归属、已尝试 URL、完整文件信息、解析证据以及历史结果；重跑对完整文件重新检查 SHA-256 和内容。

## 文件夹结构

```text
output/pdf/hinton_download_sample/
├── manifest.json
└── unknown_year_Deep_learning_fdf1b9bfa3fc0a9a.pdf
```

旧 PDF Resolver JSON 没有 authors/year 字段，因此 manifest 保留 authors=[]、year=null，文件名使用 unknown_year。没有从 PDF 中观察到的年份或署名反填为原输入元数据；新版 URL 报告已添加这些字段的保留能力。

验证范围不等同于人工 Ground Truth：内容判定使用 PDF 元数据和前两页文字；扫描件、文字提取失败和其他版本仍可能需要审核。本次只验证一篇，不能推算全部论文下载成功率。

## 单元测试与兼容性

Python 3.8.0，安装 download 可选依赖后，296 项测试全部通过。新增 75 项测试覆盖公开 DNS/内网目标、TLS 固定连接、流式大小与哈希、重试和期限、异常 HTML/截断文件、身份内容冲突、无 DOI、可解析性、文件名冲突、内容重复、重跑和中断恢复，以及 manifest/CLI。基础 Python 环境未安装 pypdf 时，15 项解析相关测试按可选依赖条件跳过，其余通过；原有功能可继续独立使用。

[Manifest](../output/pdf/hinton_download_sample/manifest.json) · [下载模块说明](../docs/download.md)
