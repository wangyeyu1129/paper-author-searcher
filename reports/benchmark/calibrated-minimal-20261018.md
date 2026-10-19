# Benchmark stratified_researchers_v1 — run calibrated-minimal-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 32 assistant-reviewed, 0 provisional, 0 missing
- 11 of 22 stratum checks failed
- 32 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 56.2% (min 0.0%) | 46.4% (min 0.0%) ✗ | 74.0% (min 0.0%) | 99.2% (min 88.9%) | – (min –) |
| seniority=renowned_senior | 8 | 37.5% (min 0.0%) | 34.0% (min 0.0%) ✗ | 74.3% (min 0.0%) | 99.1% (min 97.4%) | – (min –) |
| seniority=established_faculty | 8 | 75.0% (min 0.0%) | 66.3% (min 0.0%) ✗ | 81.3% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=assistant_professor | 8 | 62.5% (min 0.0%) | 42.7% (min 0.0%) ✗ | 72.0% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=phd_student | 8 | 50.0% (min 0.0%) | 42.7% (min 0.0%) ✗ | 68.2% (min 0.0%) | 97.2% (min 88.9%) | – (min –) |
| field=computer_science | 8 | 50.0% (min 0.0%) | 40.8% (min 0.0%) ✗ | 60.3% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| field=biomedicine | 8 | 62.5% (min 0.0%) | 55.8% (min 0.0%) ✗ | 70.2% (min 0.0%) | 99.5% (min 97.4%) | – (min –) |
| field=engineering | 8 | 62.5% (min 0.0%) | 46.6% (min 0.0%) ✗ | 90.7% (min 50.0%) | 100.0% (min 100.0%) | – (min –) |
| field=social_science | 8 | 50.0% (min 0.0%) | 42.4% (min 0.0%) ✗ | 74.6% (min 0.0%) | 97.2% (min 88.9%) | – (min –) |
| name_profile=common_chinese | 16 | 25.0% (min 0.0%) | 18.6% (min 0.0%) ✗ | 54.1% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| name_profile=other | 16 | 87.5% (min 0.0%) | 74.2% (min 0.0%) ✗ | 93.8% (min 84.8%) | 99.0% (min 88.9%) | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | assistant_reviewed | partial | 43 | 0.0% (0/1) | 0.0% (0/43) | 67.4% (29/43) | – (0/0) | – (0/43) |
| cs_christopher_manning | renowned_senior, computer_science, other | assistant_reviewed | partial | 35 | 100.0% (1/1) | 91.4% (32/35) | 91.4% (32/35) | 100.0% (32/32) | – (0/35) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | assistant_reviewed | partial | 76 | 0.0% (0/1) | 0.0% (0/76) | 0.0% (0/76) | – (0/0) | – (0/76) |
| cs_graham_neubig | established_faculty, computer_science, other | assistant_reviewed | partial | 97 | 100.0% (1/1) | 88.7% (86/97) | 93.8% (91/97) | 100.0% (86/86) | – (0/97) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | assistant_reviewed | partial | 67 | 0.0% (0/1) | 0.0% (0/67) | 4.5% (3/67) | – (0/0) | – (0/67) |
| cs_kevin_ellis | assistant_professor, computer_science, other | assistant_reviewed | partial | 41 | 100.0% (1/1) | 61.0% (25/41) | 85.4% (35/41) | 100.0% (25/25) | – (0/41) |
| cs_jing_huang | phd_student, computer_science, common_chinese | assistant_reviewed | partial | 10 | 0.0% (0/1) | 0.0% (0/10) | 40.0% (4/10) | – (0/0) | – (0/10) |
| cs_julie_kallini | phd_student, computer_science, other | assistant_reviewed | partial | 7 | 100.0% (1/1) | 85.7% (6/7) | 100.0% (7/7) | 100.0% (6/6) | – (0/7) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | assistant_reviewed | partial | 21 | 0.0% (0/1) | 0.0% (0/21) | 0.0% (0/21) | – (0/0) | – (0/21) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | assistant_reviewed | partial | 46 | 100.0% (1/1) | 80.4% (37/46) | 84.8% (39/46) | 97.4% (37/38) | – (0/46) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | assistant_reviewed | partial | 18 | 100.0% (1/1) | 88.9% (16/18) | 88.9% (16/18) | 100.0% (16/16) | – (0/18) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | assistant_reviewed | partial | 68 | 100.0% (1/1) | 95.6% (65/68) | 100.0% (68/68) | 100.0% (65/65) | – (0/68) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | assistant_reviewed | partial | 23 | 0.0% (0/1) | 0.0% (0/23) | 100.0% (23/23) | – (0/0) | – (0/23) |
| bio_brian_hie | assistant_professor, biomedicine, other | assistant_reviewed | partial | 33 | 100.0% (1/1) | 81.8% (27/33) | 87.9% (29/33) | 100.0% (27/27) | – (0/33) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | assistant_reviewed | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | assistant_reviewed | partial | 21 | 100.0% (1/1) | 100.0% (21/21) | 100.0% (21/21) | 100.0% (21/21) | – (0/21) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | assistant_reviewed | partial | 33 | 0.0% (0/1) | 0.0% (0/33) | 97.0% (32/33) | – (0/0) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | assistant_reviewed | partial | 94 | 100.0% (1/1) | 100.0% (94/94) | 100.0% (94/94) | 100.0% (94/94) | – (0/94) |
| eng_wei_lu | established_faculty, engineering, common_chinese | assistant_reviewed | partial | 16 | 100.0% (1/1) | 87.5% (14/16) | 93.8% (15/16) | 100.0% (14/14) | – (0/16) |
| eng_sindy_tang | established_faculty, engineering, other | assistant_reviewed | partial | 15 | 100.0% (1/1) | 86.7% (13/15) | 86.7% (13/15) | 100.0% (13/13) | – (0/15) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | assistant_reviewed | partial | 66 | 100.0% (1/1) | 54.5% (36/66) | 98.5% (65/66) | 100.0% (36/36) | – (0/66) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 44.4% (4/9) | 100.0% (9/9) | 100.0% (4/4) | – (0/9) |
| eng_min_zhu | phd_student, engineering, common_chinese | assistant_reviewed | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 50.0% (4/8) | – (0/0) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | assistant_reviewed | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 100.0% (1/1) | – (0/0) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | assistant_reviewed | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 87.5% (7/8) | – (0/0) | – (0/8) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | assistant_reviewed | partial | 45 | 0.0% (0/1) | 0.0% (0/45) | 66.7% (30/45) | – (0/0) | – (0/45) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | assistant_reviewed | partial | 14 | 0.0% (0/1) | 0.0% (0/14) | 92.9% (13/14) | – (0/0) | – (0/14) |
| soc_christian_wolf | established_faculty, social_science, other | assistant_reviewed | partial | 18 | 100.0% (1/1) | 83.3% (15/18) | 94.4% (17/18) | 100.0% (15/15) | – (0/18) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | assistant_reviewed | partial | 2 | 0.0% (0/1) | 0.0% (0/2) | 0.0% (0/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | assistant_reviewed | partial | 7 | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 100.0% (7/7) | – (0/7) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | assistant_reviewed | partial | 3 | 100.0% (1/1) | 66.7% (2/3) | 66.7% (2/3) | 100.0% (2/2) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 88.9% (8/9) | 88.9% (8/9) | 88.9% (8/9) | – (0/9) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
