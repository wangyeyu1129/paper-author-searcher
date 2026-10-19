# 补全 Wei Wang 和 Raj Chetty 的真值（2026-10-19，AI 审核）

2026-10-18 这两人的真值被标成了"已列全"（`complete_positive_list`），但实际上并不全。召回率诊断显示，身份一旦解析正确，系统多找到的大多是他们自己的论文，却会被当成误检，导致精度虚低。

这次审核做了三件事：

1. 去掉"已列全"标记。两份真值都改为开放式：系统输出的论文如果不在真值里，就不判定，不再算作误检。
2. 补线索。把 `calibrated-with_hints-20261018` 中这两人的 verified 和 needs_review 输出都作为线索导出。非出版物（Dataverse 数据文件等）在导出时已经跳过。
3. 逐条判定线索。规则与 [2026-10-19 审核](../2026-10-19/README.md) 相同，另外加了两条：
   - Wei Wang 是常见名字，机构只写到 UCLA 不够，必须写明计算机系。
   - Wei Wang 整个时间窗内都在 UCLA，所以论文本身标注的机构如果是别的学校（如港科大广州、上海），判为同名者，标 `not_belongs`。

真值行保留原来的决定：导出时用了 `--prefill-from assistant_reviewed` 预填。

| 人 | 真值（之前 → 现在） | 新增线索的决定 |
|---|---|---|
| Wei Wang（UCLA） | 43 → 86 篇 belongs | 43 belongs，21 not_belongs（同名者），7 合并为已有条目，31 uncertain |
| Raj Chetty（Harvard） | 8 → 19 篇 belongs | 11 belongs，7 合并为已有条目，10 uncertain（重复版本、NBER 编著） |

两份真值仍然是 `assistant_reviewed`，门槛不会认证。

## 重新生成

```bash
python3 evidence/fetch_crossref.py <dir>/crossref.json
python3 evidence/analyse.py <dir> data/benchmark/v1/review_sheets/2026-10-19-closed-world   # 写 <dir>/analysis_cw.json
python3 evidence/decide.py <dir> data/benchmark/v1/review_sheets/2026-10-19-closed-world
```

`evidence/crossref.json` 是本次使用的 Crossref/DataCite 记录。
