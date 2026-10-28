# DBLP 记录格式修正：评测（2026-10-27）

## 问题

逐篇检查最少输入下 4 位缺口最大的人（Yang Liu、Huan Zhang、Yu Xie、Wei Wang），发现一部分"缺失"的论文其实**已经收集到并通过了归属验证**，只是对不上真值。原因是 DBLP 的打印格式：

- 作者名带同名编号："Yang Liu 0005"、"Wenbing Huang 0001"；
- 标题以句号结尾："… Flow-based Direct Preference Optimization."；
- CoRR（arXiv）版本的记录没有带出 arXiv ID。

这些格式也会进入最终输出：cold-email agent 会看到 "Yang Liu 0005"。同时它们让 DBLP 的论文副本对不上其他来源的同一篇。

## 修正

新增 `dblp_format` 模块，集中放 DBLP 的格式规则：

- `base_name`：从原 `dblp_dump` 移来；
- `plain_title`：去掉标题结尾的句号，但保留问号、叹号和省略号；
- `arxiv_ids`：从电子版链接里取出 arXiv ID。

`dblp_record` 用这些规则输出不带编号的姓名、不带句号的标题，并为 CoRR 版本带上 arXiv ID。作者的 DBLP pid 仍保留在 `author_details` 中，按 ID 判断归属的逻辑不受影响。

## 结果（离线重放，同一份缓存）

| | 修正前 `curated-final3-*` | 修正后 `dblpfmt-*` |
|---|---|---|
| 最少输入：宏平均召回 | 88.3% | **89.1%** |
| 最少输入：常见中文姓名，宏平均 / 合并 | 81.8% / 79.8% | **83.4% / 82.1%** |
| 最少输入：门槛 | failed（1 项未过） | **55 项全部通过**（not_certifiable：真值未经人工审阅） |
| 带线索：宏平均召回 | 91.8% | **92.7%** |
| 身份精度 / 论文精度 | 100% / 99.2% | 100% / 99.2% |

逐人：
- Yang Liu：47 → 53
- Wei Wang：71 → 74（带线索 73 → 78）
- Huan Zhang：44 → 45
- Jing Huang：8 → 9
- 没有人下降。

## 仍然缺的（供后续参考）

- **Yu Xie（30/45）**：OpenAlex 把他拆成了两个档案："Yu Xie"（A5049053117，持有 34 篇真值论文）和 "Yu Feng Xie"（A5106181655，持有 10 篇）。两者的署名都写 "Sociology, Princeton"，谁也不占优，所以署名裁决按设计不选。要解决，需要能安全合并同一人被拆开的档案，风险较高，单独处理。
- **Yang Liu、Wei Wang**：剩下的缺口大多不在他们的 DBLP 档案里（非计算机方向的论文，或只出现在 arXiv / PubMed）。
