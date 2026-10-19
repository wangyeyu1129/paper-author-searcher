# Benchmark stratified_researchers_v1 — run hints-smoke

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 32 provisional, 0 missing
- 22 of 22 stratum checks failed
- 32 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 3.1% (min 0.0%) | 0.2% (min 0.0%) ✗ | 1.3% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=renowned_senior | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| seniority=established_faculty | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| seniority=assistant_professor | 8 | 12.5% (min 0.0%) | 0.7% (min 0.0%) ✗ | 5.3% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=phd_student | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| field=computer_science | 8 | 12.5% (min 0.0%) | 0.7% (min 0.0%) ✗ | 5.3% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| field=biomedicine | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| field=engineering | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| field=social_science | 8 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| name_profile=common_chinese | 16 | 6.2% (min 0.0%) | 0.3% (min 0.0%) ✗ | 2.6% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| name_profile=other | 16 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | provisional | not_run | 8 | 0.0% (0/1) | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| cs_christopher_manning | renowned_senior, computer_science, other | provisional | not_run | 14 | 0.0% (0/1) | 0.0% (0/14) | 0.0% (0/14) | – (0/0) | – (0/14) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | provisional | not_run | 69 | 0.0% (0/1) | 0.0% (0/69) | 0.0% (0/69) | – (0/0) | – (0/69) |
| cs_graham_neubig | established_faculty, computer_science, other | provisional | not_run | 71 | 0.0% (0/1) | 0.0% (0/71) | 0.0% (0/71) | – (0/0) | – (0/71) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | partial | 57 | 100.0% (1/1) | 5.3% (3/57) | 42.1% (24/57) | 100.0% (3/3) | – (0/57) |
| cs_kevin_ellis | assistant_professor, computer_science, other | provisional | not_run | 30 | 0.0% (0/1) | 0.0% (0/30) | 0.0% (0/30) | – (0/0) | – (0/30) |
| cs_jing_huang | phd_student, computer_science, common_chinese | provisional | not_run | 8 | 0.0% (0/1) | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| cs_julie_kallini | phd_student, computer_science, other | provisional | not_run | 6 | 0.0% (0/1) | 0.0% (0/6) | 0.0% (0/6) | – (0/0) | – (0/6) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | provisional | not_run | 12 | 0.0% (0/1) | 0.0% (0/12) | 0.0% (0/12) | – (0/0) | – (0/12) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | provisional | not_run | 29 | 0.0% (0/1) | 0.0% (0/29) | 0.0% (0/29) | – (0/0) | – (0/29) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | provisional | not_run | 14 | 0.0% (0/1) | 0.0% (0/14) | 0.0% (0/14) | – (0/0) | – (0/14) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | provisional | not_run | 59 | 0.0% (0/1) | 0.0% (0/59) | 0.0% (0/59) | – (0/0) | – (0/59) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | provisional | not_run | 24 | 0.0% (0/1) | 0.0% (0/24) | 0.0% (0/24) | – (0/0) | – (0/24) |
| bio_brian_hie | assistant_professor, biomedicine, other | provisional | not_run | 27 | 0.0% (0/1) | 0.0% (0/27) | 0.0% (0/27) | – (0/0) | – (0/27) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | provisional | not_run | 1 | 0.0% (0/1) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | provisional | not_run | 4 | 0.0% (0/1) | 0.0% (0/4) | 0.0% (0/4) | – (0/0) | – (0/4) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | provisional | not_run | 33 | 0.0% (0/1) | 0.0% (0/33) | 0.0% (0/33) | – (0/0) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | provisional | not_run | 20 | 0.0% (0/1) | 0.0% (0/20) | 0.0% (0/20) | – (0/0) | – (0/20) |
| eng_wei_lu | established_faculty, engineering, common_chinese | provisional | not_run | 10 | 0.0% (0/1) | 0.0% (0/10) | 0.0% (0/10) | – (0/0) | – (0/10) |
| eng_sindy_tang | established_faculty, engineering, other | provisional | not_run | 14 | 0.0% (0/1) | 0.0% (0/14) | 0.0% (0/14) | – (0/0) | – (0/14) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | provisional | not_run | 60 | 0.0% (0/1) | 0.0% (0/60) | 0.0% (0/60) | – (0/0) | – (0/60) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | provisional | not_run | 7 | 0.0% (0/1) | 0.0% (0/7) | 0.0% (0/7) | – (0/0) | – (0/7) |
| eng_min_zhu | phd_student, engineering, common_chinese | provisional | not_run | 8 | 0.0% (0/1) | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | provisional | not_run | 1 | 0.0% (0/1) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | provisional | not_run | 8 | 0.0% (0/1) | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | provisional | not_run | 25 | 0.0% (0/1) | 0.0% (0/25) | 0.0% (0/25) | – (0/0) | – (0/25) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | provisional | not_run | 12 | 0.0% (0/1) | 0.0% (0/12) | 0.0% (0/12) | – (0/0) | – (0/12) |
| soc_christian_wolf | established_faculty, social_science, other | provisional | not_run | 15 | 0.0% (0/1) | 0.0% (0/15) | 0.0% (0/15) | – (0/0) | – (0/15) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | provisional | not_run | 2 | 0.0% (0/1) | 0.0% (0/2) | 0.0% (0/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | provisional | not_run | 4 | 0.0% (0/1) | 0.0% (0/4) | 0.0% (0/4) | – (0/0) | – (0/4) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | provisional | not_run | 3 | 0.0% (0/1) | 0.0% (0/3) | 0.0% (0/3) | – (0/0) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | provisional | not_run | 3 | 0.0% (0/1) | 0.0% (0/3) | 0.0% (0/3) | – (0/0) | – (0/3) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
