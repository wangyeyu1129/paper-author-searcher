# 拆分片段合并的上限（2026-10-27）

运行：`current-minimal-20261027`（最少输入，32 人）。对每个人最终没有被找到的真值论文，按标题在 OpenAlex 检索，找出该论文上与本人姓名一致的作者 ID，再看这个 ID 属于哪一类：

- `fragment`：解析阶段提出的同名片段，片段合并最多能补回的就是这些；
- `selected`：已选中的身份本身（漏在收集或验证环节）；
- `other_id`：OpenAlex 的其他作者 ID，从未作为候选出现；
- `not_in_openalex`：OpenAlex 中没找到。

| 人 | OpenAlex 片段数 | 未找到 | fragment | selected | other_id | not_in_openalex |
|---|---|---|---|---|---|---|
| cs_wei_wang_ucla | 0 | 11 | 0 | 1 | 4 | 6 |
| cs_christopher_manning | 11 | 3 | 0 | 1 | 0 | 2 |
| cs_yang_liu_tsinghua | 0 | 71 | 0 | 0 | 43 | 28 |
| cs_graham_neubig | 11 | 0 | 0 | 0 | 0 | 0 |
| cs_huan_zhang | 8 | 13 | 0 | 0 | 8 | 5 |
| cs_kevin_ellis | 9 | 2 | 0 | 0 | 0 | 2 |
| cs_jing_huang | 2 | 2 | 0 | 1 | 0 | 1 |
| cs_julie_kallini | 0 | 0 | 0 | 0 | 0 | 0 |
| bio_feng_zhang | 1 | 21 | 0 | 0 | 21 | 0 |
| bio_jennifer_doudna | 0 | 6 | 0 | 4 | 0 | 2 |
| bio_le_cong | 4 | 2 | 0 | 0 | 0 | 2 |
| bio_anshul_kundaje | 1 | 0 | 0 | 0 | 0 | 0 |
| bio_jia_liu | 0 | 0 | 0 | 0 | 0 | 0 |
| bio_brian_hie | 0 | 2 | 0 | 2 | 0 | 0 |
| bio_zhiwei_xue | 12 | 1 | 0 | 0 | 0 | 1 |
| bio_marena_trinidad | 0 | 0 | 0 | 0 | 0 | 0 |
| eng_yi_cui | 5 | 1 | 0 | 1 | 0 | 0 |
| eng_daniela_rus | 4 | 0 | 0 | 0 | 0 | 0 |
| eng_wei_lu | 3 | 1 | 0 | 0 | 1 | 0 |
| eng_sindy_tang | 0 | 1 | 0 | 1 | 0 | 0 |
| eng_lu_lu | 0 | 1 | 0 | 0 | 0 | 1 |
| eng_siddharth_krishnan | 6 | 0 | 0 | 0 | 0 | 0 |
| eng_min_zhu | 1 | 1 | 0 | 1 | 0 | 0 |
| eng_jonathan_lee | 2 | 0 | 0 | 0 | 0 | 0 |
| soc_raj_chetty | 3 | 0 | 0 | 0 | 0 | 0 |
| soc_yu_xie | 3 | 13 | 0 | 0 | 12 | 1 |
| soc_xiang_zhou | 2 | 1 | 0 | 0 | 1 | 0 |
| soc_christian_wolf | 12 | 1 | 0 | 0 | 0 | 1 |
| soc_daniel_chen | 11 | 2 | 1 | 0 | 0 | 1 |
| soc_nina_roussille | 0 | 0 | 0 | 0 | 0 | 0 |
| soc_whitney_zhang | 0 | 1 | 0 | 1 | 0 | 0 |
| soc_shakked_noy | 0 | 1 | 0 | 1 | 0 | 0 |
| **合计** | 111 | 158 | **1** | 14 | **90** | 53 |

结论：

- 片段合并最多只能补回 **1** 篇（Daniel Chen）。在所有运行中它也一次都没有接受过片段，因此删除。
- 更大的缺口是 `other_id`（90 篇）：论文挂在从未被提出的 OpenAlex 作者 ID 下，集中在 Yang Liu（43）、Feng Zhang（21）、Yu Xie（12）、Huan Zhang（8）。按作者 ID 合并解决不了，需要按论文聚类（下一步）。
- 按标题检索可能把同名者的同标题论文算进来，所以各类数字是近似值；`fragment` 列为上限估计。

删除后的回归检查：用同一份 HTTP 缓存离线重放最少输入的全量运行（`--replay-from current-minimal-20261027`），删除前（`replay-ctl-20261027`）与删除后（`replay-nofrag-20261027`）的每一人、每一项指标完全相同：宏平均召回 73.3%，解析 81.2%，身份精度 100%，论文精度 99.5%。同时做的在线运行 `nofrag-minimal-20261027` 遇到 OpenAlex 429（当日额度用尽），结果无效，未收录。
