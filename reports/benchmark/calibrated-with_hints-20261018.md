# Benchmark stratified_researchers_v1 — run calibrated-with_hints-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 8 assistant-reviewed, 0 provisional, 0 missing
- 15 of 22 stratum checks failed
- 8 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 8 | 75.0% (min 0.0%) | 51.4% (min 0.0%) ✗ | 86.6% (min 60.0%) | 97.8% (min 88.9%) | – (min –) |
| seniority=renowned_senior | 2 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 77.5% (min 67.4%) | – (min –) ✗ | – (min –) |
| seniority=established_faculty | 2 | 100.0% (min 100.0%) | 91.1% (min 88.9%) | 94.4% (min 88.9%) | 100.0% (min 100.0%) | – (min –) |
| seniority=assistant_professor | 2 | 100.0% (min 100.0%) | 50.0% (min 0.0%) ✗ | 100.0% (min 100.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=phd_student | 2 | 100.0% (min 100.0%) | 64.4% (min 40.0%) ✗ | 74.4% (min 60.0%) | 94.4% (min 88.9%) ✗ | – (min –) |
| field=computer_science | 2 | 50.0% (min 0.0%) | 20.0% (min 0.0%) ✗ | 63.7% (min 60.0%) | 100.0% (min 100.0%) | – (min –) |
| field=biomedicine | 2 | 100.0% (min 100.0%) | 44.4% (min 0.0%) ✗ | 94.4% (min 88.9%) | 100.0% (min 100.0%) | – (min –) |
| field=engineering | 2 | 100.0% (min 100.0%) | 96.7% (min 93.3%) | 100.0% (min 100.0%) | 100.0% (min 100.0%) | – (min –) |
| field=social_science | 2 | 50.0% (min 0.0%) | 44.4% (min 0.0%) ✗ | 88.2% (min 87.5%) | 88.9% (min 88.9%) ✗ | – (min –) |
| name_profile=common_chinese | 4 | 75.0% (min 0.0%) | 32.2% (min 0.0%) ✗ | 79.1% (min 60.0%) | 100.0% (min 100.0%) | – (min –) |
| name_profile=other | 4 | 75.0% (min 0.0%) | 70.6% (min 0.0%) ✗ | 94.1% (min 87.5%) | 96.3% (min 88.9%) | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | assistant_reviewed | partial | 43 | 0.0% (0/1) | 0.0% (0/43) | 67.4% (29/43) | – (0/0) | – (0/43) |
| cs_jing_huang | phd_student, computer_science, common_chinese | assistant_reviewed | partial | 10 | 100.0% (1/1) | 40.0% (4/10) | 60.0% (6/10) | 100.0% (4/4) | – (0/10) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | assistant_reviewed | partial | 18 | 100.0% (1/1) | 88.9% (16/18) | 88.9% (16/18) | 100.0% (16/16) | – (0/18) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | assistant_reviewed | partial | 23 | 100.0% (1/1) | 0.0% (0/23) | 100.0% (23/23) | – (0/0) | – (0/23) |
| eng_sindy_tang | established_faculty, engineering, other | assistant_reviewed | partial | 15 | 100.0% (1/1) | 93.3% (14/15) | 100.0% (15/15) | 100.0% (14/14) | – (0/15) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 100.0% (9/9) | 100.0% (9/9) | 100.0% (9/9) | – (0/9) |
| soc_raj_chetty | renowned_senior, social_science, other | assistant_reviewed | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 87.5% (7/8) | – (0/0) | – (0/8) |
| soc_shakked_noy | phd_student, social_science, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 88.9% (8/9) | 88.9% (8/9) | 88.9% (8/9) | – (0/9) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
