# Identity calibration

- Recommended parameters: `{'ambiguity_margin': 0.03, 'merge_split_profiles': True, 'split_profile_coauthors': 5, 'split_profile_topics': 2}`
- Baseline parameters: `{'ambiguity_margin': 0.1, 'merge_split_profiles': True, 'split_profile_coauthors': 3, 'split_profile_topics': 2}`
- Purity floor 95% met by some trial: True

| Set | People | Resolved | Resolved coverage | Resolved purity |
|---|---:|---:|---:|---:|
| baseline / tune | 11 | 6 | 53.4% | 100.0% |
| baseline / holdout | 9 | 5 | 55.6% | 100.0% |
| recommended / tune | 11 | 8 | 69.8% | 100.0% |
| recommended / holdout | 9 | 7 | 55.6% | 100.0% |
