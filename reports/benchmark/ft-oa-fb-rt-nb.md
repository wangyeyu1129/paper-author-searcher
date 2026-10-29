# Benchmark stratified_researchers_v1 — run ft-oa-fb-rt-nb

- Gate: **not_certifiable** (exit code 3)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: identity_precision (macro) ≥ 100%, verified_paper_recall (macro) ≥ 80%, verified_paper_recall (micro) ≥ 80%, author_match_precision (macro) ≥ 95%, author_match_precision (micro) ≥ 95%
- Truth: 0 human-reviewed, 32 assistant-reviewed, 0 provisional, 0 missing
- Truth floor (people below it are left out of that metric): verified_paper_recall ≥ 3 publications, paper_discovery_recall ≥ 3 publications, actual_download_coverage ≥ 3 publications
- 32 people are scored against truth that is not human-reviewed

## Strata

| Stratum | People | identity_resolution_rate | identity_precision | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 93.8% (min 0.0%) | 100.0% (min 100.0%) | 89.1% (min 66.7%) · micro 88.9% | 92.1% (min 66.7%) | 99.2% (min 88.9%) · micro 99.6% | 57.0% (min 13.0%) |
| seniority=renowned_senior | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 89.5% (min 66.7%) · micro 89.4% | 91.7% (min 71.1%) | 98.6% (min 90.9%) · micro 99.1% | 57.1% (min 18.2%) |
| seniority=established_faculty | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 88.9% (min 69.7%) · micro 89.1% | 91.8% (min 71.1%) | 100.0% (min 100.0%) · micro 100.0% | 57.4% (min 25.0%) |
| seniority=assistant_professor | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 90.9% (min 67.2%) · micro 87.0% | 95.7% (min 82.1%) | 100.0% (min 100.0%) · micro 100.0% | 51.7% (min 13.0%) |
| seniority=phd_student | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 86.8% (min 66.7%) · micro 91.4% | 88.8% (min 66.7%) | 98.4% (min 88.9%) · micro 98.2% | 62.6% (min 33.3%) |
| field=computer_science | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 87.1% (min 67.2%) · micro 84.7% | 90.2% (min 71.1%) | 100.0% (min 100.0%) · micro 100.0% | 76.8% (min 64.5%) |
| field=biomedicine | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 91.8% (min 84.8%) · micro 91.7% | 95.0% (min 87.0%) | 98.3% (min 90.9%) · micro 98.6% | 59.4% (min 13.0%) |
| field=engineering | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 93.0% (min 75.0%) · micro 97.1% | 95.7% (min 87.5%) | 100.0% (min 100.0%) · micro 100.0% | 47.5% (min 18.2%) |
| field=social_science | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 84.7% (min 66.7%) · micro 80.9% | 87.7% (min 66.7%) | 98.4% (min 88.9%) · micro 98.9% | 41.5% (min 24.4%) |
| name_profile=common_chinese | 16 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 83.4% (min 66.7%) · micro 82.1% | 87.6% (min 66.7%) | 99.4% (min 90.9%) · micro 99.5% | 51.1% (min 13.0%) |
| name_profile=other | 16 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 94.4% (min 83.3%) · micro 95.2% | 96.3% (min 87.0%) | 99.1% (min 88.9%) · micro 99.6% | 62.6% (min 28.6%) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | identity_precision | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | assistant_reviewed | partial | 86 | 100.0% (1/1) | 100.0% (1/1) | 86.0% (74/86) | 91.9% (79/86) | 100.0% (74/74) | 65.1% (56/86) |
| cs_christopher_manning | renowned_senior, computer_science, other | assistant_reviewed | partial | 35 | 100.0% (1/1) | 100.0% (1/1) | 91.4% (32/35) | 91.4% (32/35) | 100.0% (32/32) | 82.9% (29/35) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | assistant_reviewed | partial | 76 | 100.0% (1/1) | 100.0% (1/1) | 69.7% (53/76) | 71.1% (54/76) | 100.0% (53/53) | 64.5% (49/76) |
| cs_graham_neubig | established_faculty, computer_science, other | assistant_reviewed | completed | 97 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (97/97) | 100.0% (97/97) | 100.0% (97/97) | 92.8% (90/97) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | assistant_reviewed | partial | 67 | 100.0% (1/1) | 100.0% (1/1) | 67.2% (45/67) | 82.1% (55/67) | 100.0% (45/45) | 65.7% (44/67) |
| cs_kevin_ellis | assistant_professor, computer_science, other | assistant_reviewed | partial | 41 | 100.0% (1/1) | 100.0% (1/1) | 92.7% (38/41) | 95.1% (39/41) | 100.0% (38/38) | 78.0% (32/41) |
| cs_jing_huang | phd_student, computer_science, common_chinese | assistant_reviewed | partial | 10 | 100.0% (1/1) | 100.0% (1/1) | 90.0% (9/10) | 90.0% (9/10) | 100.0% (9/9) | 80.0% (8/10) |
| cs_julie_kallini | phd_student, computer_science, other | assistant_reviewed | completed | 7 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 100.0% (7/7) | 85.7% (6/7) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | assistant_reviewed | partial | 21 | 100.0% (1/1) | 100.0% (1/1) | 95.2% (20/21) | 95.2% (20/21) | 90.9% (20/22) | 66.7% (14/21) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | assistant_reviewed | partial | 46 | 100.0% (1/1) | 100.0% (1/1) | 84.8% (39/46) | 87.0% (40/46) | 97.5% (39/40) | 71.7% (33/46) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | assistant_reviewed | partial | 18 | 100.0% (1/1) | 100.0% (1/1) | 88.9% (16/18) | 88.9% (16/18) | 100.0% (16/16) | 83.3% (15/18) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | assistant_reviewed | partial | 68 | 100.0% (1/1) | 100.0% (1/1) | 95.6% (65/68) | 100.0% (68/68) | 100.0% (65/65) | 45.6% (31/68) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | assistant_reviewed | partial | 23 | 100.0% (1/1) | 100.0% (1/1) | 87.0% (20/23) | 100.0% (23/23) | 100.0% (20/20) | 13.0% (3/23) |
| bio_brian_hie | assistant_professor, biomedicine, other | assistant_reviewed | completed | 33 | 100.0% (1/1) | 100.0% (1/1) | 90.9% (30/33) | 93.9% (31/33) | 100.0% (30/30) | 54.5% (18/33) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | assistant_reviewed | partial | 1 | 0.0% (0/1) | – (0/0) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | 0.0% (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | assistant_reviewed | partial | 21 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (21/21) | 100.0% (21/21) | 100.0% (21/21) | 81.0% (17/21) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | assistant_reviewed | partial | 33 | 100.0% (1/1) | 100.0% (1/1) | 97.0% (32/33) | 97.0% (32/33) | 100.0% (32/32) | 18.2% (6/33) |
| eng_daniela_rus | renowned_senior, engineering, other | assistant_reviewed | completed | 94 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (94/94) | 100.0% (94/94) | 100.0% (94/94) | 64.9% (61/94) |
| eng_wei_lu | established_faculty, engineering, common_chinese | assistant_reviewed | partial | 16 | 100.0% (1/1) | 100.0% (1/1) | 87.5% (14/16) | 93.8% (15/16) | 100.0% (14/14) | 25.0% (4/16) |
| eng_sindy_tang | established_faculty, engineering, other | assistant_reviewed | partial | 15 | 100.0% (1/1) | 100.0% (1/1) | 93.3% (14/15) | 93.3% (14/15) | 100.0% (14/14) | 40.0% (6/15) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | assistant_reviewed | partial | 66 | 100.0% (1/1) | 100.0% (1/1) | 98.5% (65/66) | 98.5% (65/66) | 100.0% (65/65) | 77.3% (51/66) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (9/9) | 100.0% (9/9) | 100.0% (9/9) | 44.4% (4/9) |
| eng_min_zhu | phd_student, engineering, common_chinese | assistant_reviewed | partial | 8 | 100.0% (1/1) | 100.0% (1/1) | 75.0% (6/8) | 87.5% (7/8) | 100.0% (6/6) | 62.5% (5/8) |
| eng_jonathan_lee | phd_student, engineering, other | assistant_reviewed | partial | 1 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) |
| soc_raj_chetty | renowned_senior, social_science, other | assistant_reviewed | partial | 19 | 100.0% (1/1) | 100.0% (1/1) | 94.7% (18/19) | 100.0% (19/19) | 100.0% (18/18) | 63.2% (12/19) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | assistant_reviewed | partial | 45 | 100.0% (1/1) | 100.0% (1/1) | 66.7% (30/45) | 71.1% (32/45) | 100.0% (30/30) | 24.4% (11/45) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | assistant_reviewed | partial | 14 | 100.0% (1/1) | 100.0% (1/1) | 92.9% (13/14) | 92.9% (13/14) | 100.0% (13/13) | 35.7% (5/14) |
| soc_christian_wolf | established_faculty, social_science, other | assistant_reviewed | partial | 18 | 100.0% (1/1) | 100.0% (1/1) | 83.3% (15/18) | 94.4% (17/18) | 100.0% (15/15) | 72.2% (13/18) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | assistant_reviewed | partial | 2 | 0.0% (0/1) | – (0/0) | 0.0% (0/2) | 0.0% (0/2) | – (0/0) | 0.0% (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | assistant_reviewed | completed | 7 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 100.0% (7/7) | 28.6% (2/7) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | assistant_reviewed | partial | 3 | 100.0% (1/1) | 100.0% (1/1) | 66.7% (2/3) | 66.7% (2/3) | 100.0% (2/2) | 33.3% (1/3) |
| soc_shakked_noy | phd_student, social_science, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 100.0% (1/1) | 88.9% (8/9) | 88.9% (8/9) | 88.9% (8/9) | 33.3% (3/9) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
