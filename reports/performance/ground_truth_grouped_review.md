# Aarti Singh 分组 Ground Truth 审核准备

本轮完成了以独立正式出版物为中心的候选审核页和下一版候选集。没有调整检索算法、身份阈值或 PDF 模块，也没有运行新的论文检索或 PDF 下载流程。所有自动结果保持 candidate，真实人工确认数仍为 0，不发布正式 Precision/Recall。

主要入口：[分组审核页](../../data/ground_truth/aarti_singh_pilot_v2/review/grouped_review.md)、[v2 候选集说明](../../data/ground_truth/aarti_singh_pilot_v2/README.md)、[按组审核及转换命令](../../docs/ground_truth_grouped_review.md)。

## 审核单位与证据

原 37 条候选逐条保留，原 22 对关系保持相等；追加两条独立出版来源支持的期刊候选，下一版共 39 条。审核页按 23 个非预印本出版物建议组排列，其中 18 个会议/期刊类组优先，5 个 workshop/未知/其他组在后；10 条预印本附在相关组下并单独审核一次。18 个正式类建议组包括 10 conference、7 journal、1 review。这是建议分组和类型，不能当作已确认的正式论文数量。

每组集中呈现优先论文级链接、教授书目公共入口、来源完整署名、正式年份、DOI、类型、原始成员、预印本及具体疑点。不同正式版本保留独立组；共享预印本只建立导航，不传递合并决定。正式日程和 DBLP 目录与归档出版元数据区分；缺失 DOI、只提供缩写姓名、访问失败和类型证据不足均照实提示。

本轮直接读取了 9 个公开出版/Crossref 元数据地址，并补充官方网页和文稿首页事实。AAAI 的直接 HTTP 响应没有 citation 标签，通过官方网页读取的事实另列，不把响应成功当作元数据验证。Project Euclid、部分 DOI/论坛页面未提供可读正文，保留该限制。未使用 Agent 收集/PDF 输出或人工 Ground Truth 作为检索答案。

## 两项遗漏的处理

- [JMLR 2020 的 Regression with Comparisons](https://jmlr.org/papers/v21/19-505.html)：官方正式四作者与教授书目 ICML 2018 条目的五作者不同，题名也变化。按期刊官方题名、四位作者和 2020 年创建候选，不继承旧会议署名。
- [Mathematical Programming 的 Near-optimal discrete optimization](https://link.springer.com/article/10.1007/s10107-019-01464-2)：保留在线 2020-01-10 和卷期 2021-03；主年份按卷期取 2021，同一 DOI 只对应一个期刊候选，旧 ICML 2017 另作版本背景。

两条新增记录都保存官方元数据响应 SHA-256、独立教授书目补充引用、来源 URL 和需要裁决的版本问题。完整性表指向新增候选但仍 pending。工具阻止将这两项已获独立支持的范围内遗漏标为 out_of_scope，或用无关正例冒充覆盖；真正的身份反证需要明确的人工负例裁决。没有用“范围外”隐藏遗漏。

## 人工决定与后续 Ground Truth

`group_annotations.template.json` 同时展示建议元数据和独立空白人工字段。机器没有填 confirmed、审核人、审核时间、canonical group 或检查标志。人实际完成整组审核后，可以显式接受建议元数据并将决定展开到原始成员；预印本单独处理。若分组有误，仍可使用原 CSV 逐条裁决。

展开工具校验成员和分组证据哈希，拒绝陈旧标注；确认缺少人名、时间、真实来源或核查标志时不能通过。更正 title/authors/year/DOI/type/venue 与原始事实分开，支持旧 CSV。人工审核、关系裁决和来源完整性完成后，继续使用现有 export_truth 与独立 Agent 结果核查流程生成正式 Ground Truth。当前 `complete_positive_list=false`、`formal_metrics=null`。

## 验证

Python **3.8.0** 下全量 **546 项测试通过**，新增 21 项分组/版本修订测试。验证了共享预印本不合并正式版本、冲突 DOI/作者不合并、原始记录保留、人工字段为空、按组元数据更正、独立新记录支持、遗漏不得隐藏、陈旧表拒绝和离线转换。`git diff --check` 通过。

原 187 条集合 manifest 内 10 个文件 SHA-256 均一致；原 Aarti 37 条及 22 对关系精确保留。v2 39 条 JSON/CSV 和按组转换均保持 candidate，没有真实人工判断被伪造。机器校验汇总见 [ground_truth_grouped_review.json](ground_truth_grouped_review.json)。
