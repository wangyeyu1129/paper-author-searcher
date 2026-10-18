# Benchmark stratified_researchers_v1 — run keyed-minimal-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 32 provisional, 0 missing
- 22 of 22 stratum checks failed
- 32 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 43.8% (min 0.0%) | 27.3% (min 0.0%) ✗ | 67.2% (min 0.0%) | 52.6% (min 0.0%) ✗ | – (min –) |
| seniority=renowned_senior | 8 | 25.0% (min 0.0%) | 20.6% (min 0.0%) ✗ | 76.6% (min 0.0%) | 54.8% (min 31.4%) ✗ | – (min –) |
| seniority=established_faculty | 8 | 37.5% (min 0.0%) | 27.9% (min 0.0%) ✗ | 74.1% (min 0.0%) | 53.0% (min 42.3%) ✗ | – (min –) |
| seniority=assistant_professor | 8 | 62.5% (min 0.0%) | 28.4% (min 0.0%) ✗ | 61.7% (min 0.0%) | 54.5% (min 0.0%) ✗ | – (min –) |
| seniority=phd_student | 8 | 50.0% (min 0.0%) | 32.3% (min 0.0%) ✗ | 56.2% (min 0.0%) | 48.9% (min 4.5%) ✗ | – (min –) |
| field=computer_science | 8 | 37.5% (min 0.0%) | 27.7% (min 0.0%) ✗ | 60.4% (min 0.0%) | 58.3% (min 31.4%) ✗ | – (min –) |
| field=biomedicine | 8 | 50.0% (min 0.0%) | 38.3% (min 0.0%) ✗ | 64.5% (min 0.0%) | 47.5% (min 7.9%) ✗ | – (min –) |
| field=engineering | 8 | 37.5% (min 0.0%) | 21.4% (min 0.0%) ✗ | 90.5% (min 50.0%) | 75.1% (min 73.3%) ✗ | – (min –) |
| field=social_science | 8 | 50.0% (min 0.0%) | 21.7% (min 0.0%) ✗ | 53.2% (min 0.0%) | 36.7% (min 0.0%) ✗ | – (min –) |
| name_profile=common_chinese | 16 | 18.8% (min 0.0%) | 11.8% (min 0.0%) ✗ | 52.3% (min 0.0%) | 73.5% (min 43.5%) ✗ | – (min –) |
| name_profile=other | 16 | 68.8% (min 0.0%) | 42.8% (min 0.0%) ✗ | 82.0% (min 25.0%) | 47.0% (min 0.0%) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 100.0% (8/8) | – (0/0) | – (0/8) |
| cs_christopher_manning | renowned_senior, computer_science, other | provisional | partial | 14 | 100.0% (1/1) | 78.6% (11/14) | 78.6% (11/14) | 31.4% (11/35) | – (0/14) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | provisional | partial | 69 | 0.0% (0/1) | 0.0% (0/69) | 0.0% (0/69) | – (0/0) | – (0/69) |
| cs_graham_neubig | established_faculty, computer_science, other | provisional | partial | 71 | 0.0% (0/1) | 0.0% (0/71) | 91.5% (65/71) | – (0/0) | – (0/71) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | partial | 57 | 0.0% (0/1) | 0.0% (0/57) | 1.8% (1/57) | – (0/0) | – (0/57) |
| cs_kevin_ellis | assistant_professor, computer_science, other | provisional | partial | 30 | 100.0% (1/1) | 60.0% (18/30) | 86.7% (26/30) | 60.0% (18/30) | – (0/30) |
| cs_jing_huang | phd_student, computer_science, common_chinese | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 25.0% (2/8) | – (0/0) | – (0/8) |
| cs_julie_kallini | phd_student, computer_science, other | provisional | partial | 6 | 100.0% (1/1) | 83.3% (5/6) | 100.0% (6/6) | 83.3% (5/6) | – (0/6) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | provisional | partial | 12 | 0.0% (0/1) | 0.0% (0/12) | 0.0% (0/12) | – (0/0) | – (0/12) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | provisional | partial | 29 | 100.0% (1/1) | 86.2% (25/29) | 89.7% (26/29) | 78.1% (25/32) | – (0/29) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | provisional | partial | 14 | 100.0% (1/1) | 71.4% (10/14) | 71.4% (10/14) | 43.5% (10/23) | – (0/14) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | provisional | partial | 59 | 0.0% (0/1) | 0.0% (0/59) | 98.3% (58/59) | – (0/0) | – (0/59) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | provisional | partial | 24 | 0.0% (0/1) | 0.0% (0/24) | 100.0% (24/24) | – (0/0) | – (0/24) |
| bio_brian_hie | assistant_professor, biomedicine, other | provisional | partial | 27 | 100.0% (1/1) | 74.1% (20/27) | 81.5% (22/27) | 60.6% (20/33) | – (0/27) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | provisional | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | provisional | partial | 4 | 100.0% (1/1) | 75.0% (3/4) | 75.0% (3/4) | 7.9% (3/38) | – (0/4) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | provisional | partial | 33 | 0.0% (0/1) | 0.0% (0/33) | 97.0% (32/33) | – (0/0) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | provisional | partial | 20 | 0.0% (0/1) | 0.0% (0/20) | 100.0% (20/20) | – (0/0) | – (0/20) |
| eng_wei_lu | established_faculty, engineering, common_chinese | provisional | partial | 10 | 0.0% (0/1) | 0.0% (0/10) | 100.0% (10/10) | – (0/0) | – (0/10) |
| eng_sindy_tang | established_faculty, engineering, other | provisional | partial | 14 | 100.0% (1/1) | 78.6% (11/14) | 78.6% (11/14) | 73.3% (11/15) | – (0/14) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | provisional | partial | 60 | 100.0% (1/1) | 50.0% (30/60) | 98.3% (59/60) | 76.9% (30/39) | – (0/60) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | provisional | partial | 7 | 100.0% (1/1) | 42.9% (3/7) | 100.0% (7/7) | 75.0% (3/4) | – (0/7) |
| eng_min_zhu | phd_student, engineering, common_chinese | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 50.0% (4/8) | – (0/0) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | provisional | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | 100.0% (1/1) | – (0/0) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | provisional | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 87.5% (7/8) | – (0/0) | – (0/8) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | provisional | partial | 25 | 0.0% (0/1) | 0.0% (0/25) | 60.0% (15/25) | – (0/0) | – (0/25) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | provisional | partial | 12 | 0.0% (0/1) | 0.0% (0/12) | 66.7% (8/12) | – (0/0) | – (0/12) |
| soc_christian_wolf | established_faculty, social_science, other | provisional | partial | 15 | 100.0% (1/1) | 73.3% (11/15) | 86.7% (13/15) | 42.3% (11/26) | – (0/15) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | provisional | partial | 2 | 0.0% (0/1) | 0.0% (0/2) | 0.0% (0/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | provisional | partial | 4 | 100.0% (1/1) | 0.0% (0/4) | 25.0% (1/4) | 0.0% (0/17) | – (0/4) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | provisional | partial | 3 | 100.0% (1/1) | 66.7% (2/3) | 66.7% (2/3) | 100.0% (2/2) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | provisional | partial | 3 | 100.0% (1/1) | 33.3% (1/3) | 33.3% (1/3) | 4.5% (1/22) | – (0/3) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
