# Benchmark stratified_researchers_v1 — run baseline-metadata-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 32 provisional, 0 missing
- 22 of 22 stratum checks failed
- 32 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|
| overall=all | 32 | 22.4% (min 0.0%) ✗ | 30.7% (min 0.0%) | 57.2% (min 0.0%) ✗ | – (min –) |
| seniority=renowned_senior | 8 | 0.4% (min 0.0%) ✗ | 0.4% (min 0.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=established_faculty | 8 | 26.1% (min 0.0%) ✗ | 27.8% (min 0.0%) | 60.2% (min 40.7%) ✗ | – (min –) |
| seniority=assistant_professor | 8 | 22.3% (min 0.0%) ✗ | 51.9% (min 0.0%) | 51.0% (min 0.0%) ✗ | – (min –) |
| seniority=phd_student | 8 | 40.6% (min 0.0%) ✗ | 42.7% (min 0.0%) | 50.4% (min 7.9%) ✗ | – (min –) |
| field=computer_science | 8 | 17.9% (min 0.0%) ✗ | 23.3% (min 0.0%) | 71.7% (min 60.0%) ✗ | – (min –) |
| field=biomedicine | 8 | 27.5% (min 0.0%) ✗ | 28.9% (min 0.0%) | 54.8% (min 7.9%) ✗ | – (min –) |
| field=engineering | 8 | 14.1% (min 0.0%) ✗ | 32.6% (min 0.0%) | 86.4% (min 82.9%) ✗ | – (min –) |
| field=social_science | 8 | 30.0% (min 0.0%) ✗ | 37.9% (min 0.0%) | 37.8% (min 0.0%) ✗ | – (min –) |
| name_profile=common_chinese | 16 | 13.7% (min 0.0%) ✗ | 19.9% (min 0.0%) | 77.6% (min 50.0%) ✗ | – (min –) |
| name_profile=other | 16 | 31.0% (min 0.0%) ✗ | 41.5% (min 0.0%) | 50.4% (min 0.0%) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | provisional | identity_confirmation_required | 8 | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| cs_christopher_manning | renowned_senior, computer_science, other | provisional | partial | 14 | 0.0% (0/14) | 0.0% (0/14) | – (0/0) | – (0/14) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | provisional | identity_confirmation_required | 69 | 0.0% (0/69) | 0.0% (0/69) | – (0/0) | – (0/69) |
| cs_graham_neubig | established_faculty, computer_science, other | provisional | identity_confirmation_required | 71 | 0.0% (0/71) | 0.0% (0/71) | – (0/0) | – (0/71) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | identity_confirmation_required | 57 | 0.0% (0/57) | 0.0% (0/57) | – (0/0) | – (0/57) |
| cs_kevin_ellis | assistant_professor, computer_science, other | provisional | partial | 30 | 60.0% (18/30) | 86.7% (26/30) | 60.0% (18/30) | – (0/30) |
| cs_jing_huang | phd_student, computer_science, common_chinese | provisional | partial | 8 | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| cs_julie_kallini | phd_student, computer_science, other | provisional | partial | 6 | 83.3% (5/6) | 100.0% (6/6) | 83.3% (5/6) | – (0/6) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | provisional | partial | 12 | 0.0% (0/12) | 0.0% (0/12) | – (0/0) | – (0/12) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | provisional | partial | 29 | 3.4% (1/29) | 3.4% (1/29) | 100.0% (1/1) | – (0/29) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | provisional | partial | 14 | 71.4% (10/14) | 71.4% (10/14) | 50.0% (10/20) | – (0/14) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | provisional | identity_confirmation_required | 59 | 0.0% (0/59) | 0.0% (0/59) | – (0/0) | – (0/59) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | provisional | identity_confirmation_required | 24 | 0.0% (0/24) | 0.0% (0/24) | – (0/0) | – (0/24) |
| bio_brian_hie | assistant_professor, biomedicine, other | provisional | partial | 27 | 70.4% (19/27) | 81.5% (22/27) | 61.3% (19/31) | – (0/27) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | provisional | identity_confirmation_required | 1 | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | provisional | partial | 4 | 75.0% (3/4) | 75.0% (3/4) | 7.9% (3/38) | – (0/4) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | provisional | identity_confirmation_required | 33 | 0.0% (0/33) | 0.0% (0/33) | – (0/0) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | provisional | identity_confirmation_required | 20 | 0.0% (0/20) | 0.0% (0/20) | – (0/0) | – (0/20) |
| eng_wei_lu | established_faculty, engineering, common_chinese | provisional | identity_confirmation_required | 10 | 0.0% (0/10) | 0.0% (0/10) | – (0/0) | – (0/10) |
| eng_sindy_tang | established_faculty, engineering, other | provisional | partial | 14 | 64.3% (9/14) | 64.3% (9/14) | 90.0% (9/10) | – (0/14) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | provisional | partial | 60 | 48.3% (29/60) | 96.7% (58/60) | 82.9% (29/35) | – (0/60) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | provisional | partial | 7 | 0.0% (0/7) | 100.0% (7/7) | – (0/0) | – (0/7) |
| eng_min_zhu | phd_student, engineering, common_chinese | provisional | partial | 8 | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | provisional | partial | 1 | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | provisional | identity_confirmation_required | 8 | 0.0% (0/8) | 0.0% (0/8) | – (0/0) | – (0/8) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | provisional | identity_confirmation_required | 25 | 0.0% (0/25) | 0.0% (0/25) | – (0/0) | – (0/25) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | provisional | identity_confirmation_required | 12 | 0.0% (0/12) | 0.0% (0/12) | – (0/0) | – (0/12) |
| soc_christian_wolf | established_faculty, social_science, other | provisional | partial | 15 | 73.3% (11/15) | 86.7% (13/15) | 40.7% (11/27) | – (0/15) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | provisional | partial | 2 | 0.0% (0/2) | 50.0% (1/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | provisional | partial | 4 | 0.0% (0/4) | 0.0% (0/4) | 0.0% (0/12) | – (0/4) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | provisional | partial | 3 | 100.0% (3/3) | 100.0% (3/3) | 100.0% (3/3) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | provisional | partial | 3 | 66.7% (2/3) | 66.7% (2/3) | 10.5% (2/19) | – (0/3) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
