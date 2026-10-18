# Benchmark stratified_researchers_v1 — run keyed-hints-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 32 provisional, 0 missing
- 22 of 22 stratum checks failed
- 32 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 81.2% (min 0.0%) | 51.9% (min 0.0%) ✗ | 76.9% (min 0.0%) | 49.5% (min 0.0%) ✗ | – (min –) |
| seniority=renowned_senior | 8 | 62.5% (min 0.0%) | 57.4% (min 0.0%) ✗ | 92.9% (min 78.6%) | 41.7% (min 20.6%) ✗ | – (min –) |
| seniority=established_faculty | 8 | 87.5% (min 0.0%) | 65.1% (min 0.0%) ✗ | 82.9% (min 43.5%) | 50.4% (min 34.8%) ✗ | – (min –) |
| seniority=assistant_professor | 8 | 100.0% (min 100.0%) | 41.9% (min 0.0%) ✗ | 70.7% (min 0.0%) | 55.0% (min 0.0%) ✗ | – (min –) |
| seniority=phd_student | 8 | 75.0% (min 0.0%) | 43.2% (min 0.0%) ✗ | 60.9% (min 0.0%) | 49.7% (min 4.5%) ✗ | – (min –) |
| field=computer_science | 8 | 75.0% (min 0.0%) | 39.6% (min 0.0%) ✗ | 76.2% (min 43.5%) | 51.1% (min 31.4%) ✗ | – (min –) |
| field=biomedicine | 8 | 87.5% (min 0.0%) | 66.1% (min 0.0%) ✗ | 79.7% (min 0.0%) | 50.5% (min 7.9%) ✗ | – (min –) |
| field=engineering | 8 | 75.0% (min 0.0%) | 60.7% (min 0.0%) ✗ | 93.5% (min 62.5%) | 58.1% (min 20.6%) ✗ | – (min –) |
| field=social_science | 8 | 87.5% (min 0.0%) | 41.3% (min 0.0%) ✗ | 58.1% (min 0.0%) | 38.5% (min 0.0%) ✗ | – (min –) |
| name_profile=common_chinese | 16 | 81.2% (min 0.0%) | 41.6% (min 0.0%) ✗ | 68.4% (min 0.0%) | 53.4% (min 34.8%) ✗ | – (min –) |
| name_profile=other | 16 | 81.2% (min 0.0%) | 62.2% (min 0.0%) ✗ | 85.3% (min 25.0%) | 46.3% (min 0.0%) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 100.0% (8/8) | – (0/0) | – (0/8) |
| cs_christopher_manning | renowned_senior, computer_science, other | provisional | partial | 14 | 100.0% (1/1) | 78.6% (11/14) | 78.6% (11/14) | 31.4% (11/35) | – (0/14) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | provisional | partial | 69 | 100.0% (1/1) | 33.3% (23/69) | 43.5% (30/69) | 41.8% (23/55) | – (0/69) |
| cs_graham_neubig | established_faculty, computer_science, other | provisional | partial | 71 | 0.0% (0/1) | 0.0% (0/71) | 91.5% (65/71) | – (0/0) | – (0/71) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | partial | 57 | 100.0% (1/1) | 26.3% (15/57) | 56.1% (32/57) | 57.7% (15/26) | – (0/57) |
| cs_kevin_ellis | assistant_professor, computer_science, other | provisional | partial | 30 | 100.0% (1/1) | 70.0% (21/30) | 90.0% (27/30) | 52.5% (21/40) | – (0/30) |
| cs_jing_huang | phd_student, computer_science, common_chinese | provisional | partial | 8 | 100.0% (1/1) | 25.0% (2/8) | 50.0% (4/8) | 40.0% (2/5) | – (0/8) |
| cs_julie_kallini | phd_student, computer_science, other | provisional | partial | 6 | 100.0% (1/1) | 83.3% (5/6) | 100.0% (6/6) | 83.3% (5/6) | – (0/6) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | provisional | partial | 12 | 100.0% (1/1) | 100.0% (12/12) | 100.0% (12/12) | 37.5% (12/32) | – (0/12) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | provisional | partial | 29 | 100.0% (1/1) | 96.6% (28/29) | 96.6% (28/29) | 70.0% (28/40) | – (0/29) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | provisional | partial | 14 | 100.0% (1/1) | 71.4% (10/14) | 71.4% (10/14) | 45.5% (10/22) | – (0/14) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | provisional | partial | 59 | 100.0% (1/1) | 96.6% (57/59) | 98.3% (58/59) | 77.0% (57/74) | – (0/59) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | provisional | partial | 24 | 100.0% (1/1) | 0.0% (0/24) | 100.0% (24/24) | – (0/0) | – (0/24) |
| bio_brian_hie | assistant_professor, biomedicine, other | provisional | partial | 27 | 100.0% (1/1) | 88.9% (24/27) | 96.3% (26/27) | 64.9% (24/37) | – (0/27) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | provisional | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | provisional | partial | 4 | 100.0% (1/1) | 75.0% (3/4) | 75.0% (3/4) | 7.9% (3/38) | – (0/4) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | provisional | partial | 33 | 0.0% (0/1) | 0.0% (0/33) | 97.0% (32/33) | – (0/0) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | provisional | partial | 20 | 100.0% (1/1) | 100.0% (20/20) | 100.0% (20/20) | 20.6% (20/97) | – (0/20) |
| eng_wei_lu | established_faculty, engineering, common_chinese | provisional | partial | 10 | 100.0% (1/1) | 80.0% (8/10) | 90.0% (9/10) | 42.1% (8/19) | – (0/10) |
| eng_sindy_tang | established_faculty, engineering, other | provisional | partial | 14 | 100.0% (1/1) | 92.9% (13/14) | 100.0% (14/14) | 68.4% (13/19) | – (0/14) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | provisional | partial | 60 | 100.0% (1/1) | 50.0% (30/60) | 98.3% (59/60) | 76.9% (30/39) | – (0/60) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | provisional | partial | 7 | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 77.8% (7/9) | – (0/7) |
| eng_min_zhu | phd_student, engineering, common_chinese | provisional | partial | 8 | 100.0% (1/1) | 62.5% (5/8) | 62.5% (5/8) | 62.5% (5/8) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | provisional | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 100.0% (1/1) | – (0/0) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 87.5% (7/8) | – (0/0) | – (0/8) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | provisional | partial | 25 | 100.0% (1/1) | 84.0% (21/25) | 84.0% (21/25) | 48.8% (21/43) | – (0/25) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | provisional | partial | 12 | 100.0% (1/1) | 66.7% (8/12) | 75.0% (9/12) | 34.8% (8/23) | – (0/12) |
| soc_christian_wolf | established_faculty, social_science, other | provisional | partial | 15 | 100.0% (1/1) | 80.0% (12/15) | 93.3% (14/15) | 42.9% (12/28) | – (0/15) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | provisional | partial | 2 | 100.0% (1/1) | 0.0% (0/2) | 0.0% (0/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | provisional | partial | 4 | 100.0% (1/1) | 0.0% (0/4) | 25.0% (1/4) | 0.0% (0/17) | – (0/4) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | provisional | partial | 3 | 100.0% (1/1) | 66.7% (2/3) | 66.7% (2/3) | 100.0% (2/2) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | provisional | partial | 3 | 100.0% (1/1) | 33.3% (1/3) | 33.3% (1/3) | 4.5% (1/22) | – (0/3) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
