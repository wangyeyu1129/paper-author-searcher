# PDF URL 小规模真实测试

输入：`hinton_papers.authorship.json`。按归档顺序处理前 5 篇 ownership=verified 的论文，未筛选成功案例。没有重新运行 Professor Resolver 或 Paper Collector；未保存任何 PDF 文件。

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem PYTHONPATH=src python3 -m paper_author_searcher.pdf_cli \
  --input hinton_papers.authorship.json --output hinton_papers.pdf_urls.sample.json \
  --max-papers 5 --timeout 8 --retries 1
```

归档总条目：573；实际处理：5；PDF 验证通过论文：1；不同已验证 PDF URL：1；只有未验证链接：1；未找到有效或待验证候选：3。

归属跳过：232；数量限制未处理：336；存在来源失败的检索：5。CLI 返回 2 并保存报告，表明结果不完整。以上数量不是教授全部论文或全部公开 PDF 的数量。

| 论文 | PDF 状态 |
| --- | --- |
| ImageNet classification with deep convolutional neural networks | not_found |
| Deep learning | verified |
| Visualizing Data using t-SNE | not_found |
| Dropout: a simple way to prevent neural networks from overfitting | not_found |
| Learning representations by back-propagating errors | unverified |

## 真正通过验证的 URL

- Deep learning：https://hal.science/hal-04206682/document
  HTTP 206，Content-Type=application/pdf，实际读取 1024 字节，PDF signature=True；身份依据：Exact DOI agreement with saved paper metadata。版本 unknown，许可证未提供时不作推断。

## 来源结果

| Provider | 查询状态 | 唯一候选贡献 | 已验证贡献 | 错误/配置说明次数 |
| --- | --- | ---: | ---: | ---: |
| existing_metadata | {"ok": 5} | 1 | 0 | 0 |
| openalex | {"ok": 5} | 1 | 0 | 0 |
| semantic_scholar | {"ok": 1, "error": 4} | 1 | 0 | 4 |
| unpaywall | {"disabled": 5} | 0 | 0 | 5 |
| arxiv | {"not_applicable": 5} | 0 | 0 | 0 |
| landing_page | {"partial": 3, "ok": 2} | 3 | 1 | 6 |

Semantic Scholar 出现 HTTP 429；部分网站发生 DNS 错误、HTTP 404/429 或重定向循环。ACM 返回 HTML 中间页，没有计入有效 PDF。Unpaywall 未配置真实邮箱，因此 disabled；这不是已执行的 Unpaywall 查询失败。该样本没有已知 arXiv ID，arXiv 标记 not_applicable。

验证范围：真实轻量 HTTP 请求确认 PDF 文件头，同时公开来源 DOI/论文元数据确认链接对应关系。没有解析 PDF 全文，因此不是对完整文件及内部内容的独立鉴定。未验证或失效链接均保留于 JSON。

[完整 JSON 报告](../hinton_papers.pdf_urls.sample.json)
