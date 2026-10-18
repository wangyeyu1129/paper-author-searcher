# Benchmark stratified_researchers_v1 — run smoke

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: verified_paper_recall ≥ 80%, author_match_precision ≥ 95%
- Truth: 0 human-reviewed, 1 provisional, 0 missing
- 22 of 22 stratum checks failed
- 1 people are scored against provisional (not human-reviewed) truth

## Strata

| Stratum | People | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|
| overall=all | 1 | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| seniority=renowned_senior | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| seniority=established_faculty | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| seniority=assistant_professor | 1 | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| seniority=phd_student | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=computer_science | 1 | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| field=biomedicine | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=engineering | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| field=social_science | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |
| name_profile=common_chinese | 1 | 0.0% (min 0.0%) ✗ | 0.0% (min 0.0%) | – (min –) ✗ | – (min –) |
| name_profile=other | 0 | – (min –) ✗ | – (min –) | – (min –) ✗ | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | provisional | identity_confirmation_required | 57 | 0.0% (0/57) | 0.0% (0/57) | – (0/0) | – (0/57) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
