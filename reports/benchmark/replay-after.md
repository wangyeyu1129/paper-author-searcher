# Benchmark stratified_researchers_v1 — run replay-after

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 1 provisional, 0 missing
- 22 of 22 stratum checks failed
- 1 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|
| overall=all | 1 | 100.0% (min 100.0%) | 15.8% (min 15.8%) ✗ | 42.1% (min 42.1%) | 60.0% (min 60.0%) ✗ | – (min –) |
| seniority=renowned_senior | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| seniority=established_faculty | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| seniority=assistant_professor | 1 | 100.0% (min 100.0%) | 15.8% (min 15.8%) ✗ | 42.1% (min 42.1%) | 60.0% (min 60.0%) ✗ | – (min –) |
| seniority=phd_student | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=computer_science | 1 | 100.0% (min 100.0%) | 15.8% (min 15.8%) ✗ | 42.1% (min 42.1%) | 60.0% (min 60.0%) ✗ | – (min –) |
| field=biomedicine | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=engineering | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=social_science | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| name_profile=common_chinese | 1 | 100.0% (min 100.0%) | 15.8% (min 15.8%) ✗ | 42.1% (min 42.1%) | 60.0% (min 60.0%) ✗ | – (min –) |
| name_profile=other | 0 | – (min –) | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | partial | 57 | 100.0% (1/1) | 15.8% (9/57) | 42.1% (24/57) | 60.0% (9/15) | – (0/57) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
