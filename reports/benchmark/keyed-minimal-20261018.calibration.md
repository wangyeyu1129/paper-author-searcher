# Identity calibration

- Recommended parameters: `{'ambiguity_margin': 0.03, 'merge_split_profiles': True, 'split_profile_coauthors': 5, 'split_profile_topics': 2}`
- Baseline parameters: `{'ambiguity_margin': 0.1, 'merge_split_profiles': True, 'split_profile_coauthors': 3, 'split_profile_topics': 2}`
- Purity floor 95% met by some trial: True

| Set | People | Resolved | Resolved coverage | Resolved purity |
|---|---:|---:|---:|---:|
| baseline / tune | 16 | 8 | 52.5% | 100.0% |
| baseline / holdout | 16 | 6 | 33.1% | 100.0% |
| recommended / tune | 16 | 10 | 63.2% | 100.0% |
| recommended / holdout | 16 | 8 | 44.9% | 100.0% |
