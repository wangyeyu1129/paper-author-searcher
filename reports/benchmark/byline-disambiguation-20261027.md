# 同校同名者的署名裁决：评测（2026-10-27）

所有对比都是离线重放，同一份 HTTP 缓存、同一份代码，只切换 `PAPER_SEARCH__DISAMBIGUATION__*`。因此没有 API 限流或数据变化造成的噪声。数据集 `stratified_researchers_v1`，32 人，真值由 AI 审阅（未经人工认证）。

## 最少输入（姓名 + 学校 + 院系），`--replay-from current-minimal-20261027`

| | 对照 `byl-ctl-min-b`（关闭） | 院系署名 `byl-new-min-c`（默认） | 加上学校层 `byl-new-min-b` |
|---|---|---|---|
| 宏平均召回 | 75.9% | **82.5%** | 80.4% |
| 常见中文姓名 | 56.0% | **69.8%** | 65.5% |
| 解析率 | 81.2% | 87.5% | 87.5% |
| 身份精度 | 100% | 100% | 100% |
| 论文精度 | 99.5% | 99.2% | 99.2% |
| 未通过的 stratum 检查 | 14 / 55 | **8 / 55** | — |

逐人变化（默认设置）：

- **Feng Zhang**：0/21 → 20/21。真正的档案 A5100401298 原来不在前两名；在院系署名证据（"Brain and Cognitive Sciences, MIT"）上，它大幅领先其他同名者。另有 2 篇已验证论文不在真值中；他的已验证论文都是本人的 CRISPR 工作，真值只覆盖一段年份，所以这 2 篇更可能是真值缺漏，而非误认。
- **Yi Cui**：0/33 → 32/33。院系署名证据远超同名者 Yi Shun Cui。
- 其他人没有任何变化。

未解决的人：
- **Yang Liu、Daniel Chen**：证据少于 5 篇，保持未解决。
- **Jing Huang**：所有候选都没有院系证据，保持未解决。
- **Jia Liu**：署名从不写 "Bioengineering"。

## 学校层为什么默认关闭

`school_fallback=true` 时（`byl-new-min-b`）：

- **Jing Huang（Stanford CS 博士生）**：6/10 → 0/10。抽样论文里没有任何候选写出 "Computer Science, Stanford"，于是一位免疫学同名者凭 21 篇写 Stanford 的论文胜出。
- **Jia Liu**：没有因此解决。学校证据 33 比 25，另一位 Harvard 医学方向的 Jia Liu 也常写 Harvard。

学校层没有选对任何人，还选错了一人，因此保留为可配置的选项，默认关闭。

## 带线索输入，`--replay-from final-hints-20261027`

开启前后（含学校层）每人每项指标完全相同：宏平均召回 89.7%，身份精度 100%。有锚点的竞争不交给这一步。重放不读取 PDF，所以 Yang Liu 在两边都是 0，4 项检查未通过来自这一点。

## 剩余差距（最少输入）

- **Jia Liu**：需要院系别名，例如把 SEAS 与 Bioengineering 对应起来。
- **Yang Liu**：正确档案不在候选中。
- **Jing Huang、Daniel Chen**：证据太少。
