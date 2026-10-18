# Benchmark stratified_researchers_v1 — run calibrated-minimal-20261018

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 8 assistant-reviewed, 0 provisional, 0 missing
- 17 of 22 stratum checks failed
- 8 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 8 | 50.0% (min 0.0%) | 38.6% (min 0.0%) ✗ | 82.4% (min 40.0%) | 97.2% (min 88.9%) | – (min –) |
| seniority=renowned_senior | 2 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 77.5% (min 67.4%) | – (min –) ✗ | – (min –) |
| seniority=established_faculty | 2 | 100.0% (min 100.0%) | 87.8% (min 86.7%) | 87.8% (min 86.7%) | 100.0% (min 100.0%) | – (min –) |
| seniority=assistant_professor | 2 | 50.0% (min 0.0%) | 22.2% (min 0.0%) ✗ | 100.0% (min 100.0%) | 100.0% (min 100.0%) | – (min –) |
| seniority=phd_student | 2 | 50.0% (min 0.0%) | 44.4% (min 0.0%) ✗ | 64.4% (min 40.0%) | 88.9% (min 88.9%) ✗ | – (min –) |
| field=computer_science | 2 | 0.0% (min 0.0%) | 0.0% (min 0.0%) ✗ | 53.7% (min 40.0%) | – (min –) ✗ | – (min –) |
| field=biomedicine | 2 | 50.0% (min 0.0%) | 44.4% (min 0.0%) ✗ | 94.4% (min 88.9%) | 100.0% (min 100.0%) | – (min –) |
| field=engineering | 2 | 100.0% (min 100.0%) | 65.6% (min 44.4%) ✗ | 93.3% (min 86.7%) | 100.0% (min 100.0%) | – (min –) |
| field=social_science | 2 | 50.0% (min 0.0%) | 44.4% (min 0.0%) ✗ | 88.2% (min 87.5%) | 88.9% (min 88.9%) ✗ | – (min –) |
| name_profile=common_chinese | 4 | 25.0% (min 0.0%) | 22.2% (min 0.0%) ✗ | 74.1% (min 40.0%) | 100.0% (min 100.0%) | – (min –) |
| name_profile=other | 4 | 75.0% (min 0.0%) | 55.0% (min 0.0%) ✗ | 90.8% (min 86.7%) | 96.3% (min 88.9%) | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | assistant_reviewed | partial | 43 | 0.0% (0/1) | 0.0% (0/43) | 67.4% (29/43) | – (0/0) | – (0/43) |
| cs_jing_huang | phd_student, computer_science, common_chinese | assistant_reviewed | partial | 10 | 0.0% (0/1) | 0.0% (0/10) | 40.0% (4/10) | – (0/0) | – (0/10) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | assistant_reviewed | partial | 18 | 100.0% (1/1) | 88.9% (16/18) | 88.9% (16/18) | 100.0% (16/16) | – (0/18) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | assistant_reviewed | partial | 23 | 0.0% (0/1) | 0.0% (0/23) | 100.0% (23/23) | – (0/0) | – (0/23) |
| eng_sindy_tang | established_faculty, engineering, other | assistant_reviewed | partial | 15 | 100.0% (1/1) | 86.7% (13/15) | 86.7% (13/15) | 100.0% (13/13) | – (0/15) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 44.4% (4/9) | 100.0% (9/9) | 100.0% (4/4) | – (0/9) |
| soc_raj_chetty | renowned_senior, social_science, other | assistant_reviewed | partial | 8 | 0.0% (0/1) | 0.0% (0/8) | 87.5% (7/8) | – (0/0) | – (0/8) |
| soc_shakked_noy | phd_student, social_science, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 88.9% (8/9) | 88.9% (8/9) | 88.9% (8/9) | – (0/9) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
