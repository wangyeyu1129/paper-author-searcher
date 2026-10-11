# 人工待确认清单

公开书目参考共 15 项，另列出 40 项分层抽样的检索结果供核实误检。全部人工归属仍为 `uncertain`；系统状态与人工判断分开。当前没有人工正确率或正式 80% 验收结果。

核实论文是否属于目标教授、DOI/出版年份/完整署名及版本关系；保留参考页面与人工证据。人工判定使用 `belongs`、`not_belongs` 或 `uncertain`，不要直接复制系统 `verified`。

Andrew Ng 已由用户明确选择 `openalex:A5112456378`。这只确认后续处理身份，不意味着逐篇论文已被人工核实。

可直接复制并标注整合文件：[Hinton，28 项](hinton.ground_truth.pending.json)、[Ng，27 项](andrew_ng.ground_truth.pending.json)。`review_pool` 区分独立公开参考与检索结果审核，原始文件及证据保留。对已核实条目填写 `ownership`、`annotation_status=human_reviewed` 及人工证据；未核实条目继续 uncertain。完成后填写 `metadata.reviewer`、`reviewed_at`、`annotation_state=human_reviewed`，另存为 reviewed 文件。`complete_positive_list` 继续 false，除非已确认声明范围内的正例全集完整。

检索结果审核队列可帮助核查误检，但来自系统输出，不能单独作为发现召回率的代表性真值集合；独立公开参考也只是小样本。不要将系统状态或自动建议复制为人工标签。评估命令与指标分母见 [性能评估说明](../../docs/performance.md)。

## Geoffrey Hinton：公开来源参考

| 人工确认 | 年份 | 标题与公开引用页面 | 可靠标识符 |
| --- | ---: | --- | --- |
| [ ] uncertain | 2008 | [Visualizing Data using t-SNE](https://jmlr.org/papers/v9/vandermaaten08a.html) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2014 | [Dropout: A Simple Way to Prevent Neural Networks from Overfitting](https://jmlr.org/papers/v15/srivastava14a.html) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2015 | [Distilling the Knowledge in a Neural Network](https://arxiv.org/abs/1503.02531) | arxiv:1503.02531 |
| [ ] uncertain | 2022 | [The Forward-Forward Algorithm: Some Preliminary Investigations](https://arxiv.org/abs/2212.13345) | arxiv:2212.13345 |
| [ ] uncertain | 2012 | [Deep Lambertian Networks](https://arxiv.org/abs/1206.6445) | arxiv:1206.6445 |
| [ ] uncertain | 2016 | [Using Fast Weights to Attend to the Recent Past](https://arxiv.org/abs/1610.06258) | arxiv:1610.06258 |
| [ ] uncertain | 2017 | [Dynamic Routing Between Capsules](https://arxiv.org/abs/1710.09829) | arxiv:1710.09829 |
| [ ] uncertain | 2016 | [Attend, Infer, Repeat: Fast Scene Understanding with Generative Models](https://arxiv.org/abs/1603.08575) | arxiv:1603.08575 |

完整作者与原始署名见 [hinton.reference.pending.json](hinton.reference.pending.json)。

### 检索结果抽样：待核实误检及遗漏

| 人工确认 | 系统归属 | 年份 | 论文 / 来源记录 |
| --- | --- | ---: | --- |
| [ ] uncertain | verified | 缺失 | [Using mixtures of deformable models to capture variations in hand printed digits](https://www.semanticscholar.org/paper/5f75f39caf0ad58afd117ec14266658a7e504781) |
| [ ] uncertain | verified | 1988 | [GEMINI: Gradient Estimation Through Matrix Inversion After Noise Injection](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.28.5252) |
| [ ] uncertain | verified | 1993 | [Simulating Brain Damage](https://doi.org/10.1038/scientificamerican1093-76) |
| [ ] uncertain | verified | 1998 | [Fast Neural Network Emulation of Dynamical Systems for Computer Animation](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.888.3947) |
| [ ] uncertain | verified | 2002 | [A Desktop Input Device and Interface for Interactive 3D Character Animation](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.5.8840) |
| [ ] uncertain | verified | 2008 | [Implicit Mixtures of Restricted Boltzmann Machines](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.143.5680) |
| [ ] uncertain | verified | 2011 | [Introduction to the Special Section on Deep Learning for Speech and Language Processing](https://doi.org/10.1109/tasl.2011.2173371) |
| [ ] uncertain | verified | 2013 | [Using an autoencoder with deformable templates to discover features for automated speech recognition](https://doi.org/10.21437/interspeech.2013-432) |
| [ ] uncertain | verified | 2020 | [Imputer: Sequence Modelling via Imputation and Dynamic Programming](http://arxiv.org/abs/2002.08926) |
| [ ] uncertain | verified | 2026 | [AlexNet Source Code](https://doi.org/10.34740/kaggle/dsv/17891075) |
| [ ] uncertain | needs_review | 缺失 | [Intelligent Perception](https://www.semanticscholar.org/paper/0f322dc30aaa0d929ed05e09350bd641e855e688) |
| [ ] uncertain | needs_review | 1986 | [G-maximization: an unsupervised learning procedure for discovering regularities](https://doi.org/10.1063/1.36234) |
| [ ] uncertain | needs_review | 1991 | [Recursive Distributed Representations](https://www.semanticscholar.org/paper/f23b4404ce10cb10047becd296fd504f70957c6d) |
| [ ] uncertain | needs_review | 1997 | [Glove-talk II - a neural-network interface which maps gestures to parallel formant speech synthesizer controls](https://doi.org/10.1109/72.623199) |
| [ ] uncertain | needs_review | 2002 | [Classical and Bayesian Inference in Neuroimaging: Theory](https://doi.org/10.1006/nimg.2002.1090) |
| [ ] uncertain | needs_review | 2010 | [Decision Epoch](https://www.semanticscholar.org/paper/0530c559a13a2bd92d87560ff1b3fe551e6bca57) |
| [ ] uncertain | needs_review | 2010 | [Zinnia peruviana L.](https://www.semanticscholar.org/paper/9ce2d14ddca2581399b297826f3914ff755fbd8f) |
| [ ] uncertain | needs_review | 2014 | [analysis of Neural Network Classifier for the Different Number of Hidden Units](https://www.semanticscholar.org/paper/87394e49da317d4d71f3740820b230230a888826) |
| [ ] uncertain | needs_review | 2019 | [Heme‐Derived Bilins](https://www.semanticscholar.org/paper/4c017ad500ce2170c2ff45b71150c2106fd4695b) |
| [ ] uncertain | needs_review | 2026 | [What if automating AI R&D triggers an intelligence explosion?](https://arxiv.org/abs/2609.36054) |

署名、原始 ID、归属证据和诊断见 [hinton.audit.pending.json](hinton.audit.pending.json)；这些条目不参与临时正例召回率。

## Andrew Ng：公开来源参考

| 人工确认 | 年份 | 标题与公开引用页面 | 可靠标识符 |
| --- | ---: | --- | --- |
| [ ] uncertain | 2011 | [An Analysis of Single-Layer Networks in Unsupervised Feature Learning](https://proceedings.mlr.press/v15/coates11a.html) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2011 | [Learning Word Vectors for Sentiment Analysis](https://aclanthology.org/P11-1015/) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2011 | [Semi-Supervised Recursive Autoencoders for Predicting Sentiment Distributions](https://aclanthology.org/D11-1014/) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2012 | [Semantic Compositionality through Recursive Matrix-Vector Spaces](https://aclanthology.org/D12-1110/) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2011 | [Building high-level features using large scale unsupervised learning](https://arxiv.org/abs/1112.6209) | arxiv:1112.6209 |
| [ ] uncertain | 2003 | [Latent Dirichlet Allocation](https://jmlr.org/papers/v3/blei03a.html) | 待核对；使用完整标题/年份/作者 |
| [ ] uncertain | 2006 | [Learning Factor Graphs in Polynomial Time and Sample Complexity](https://jmlr.org/papers/v7/abbeel06a.html) | 待核对；使用完整标题/年份/作者 |

完整作者与原始署名见 [andrew_ng.reference.pending.json](andrew_ng.reference.pending.json)。

### 检索结果抽样：待核实误检及遗漏

| 人工确认 | 系统归属 | 年份 | 论文 / 来源记录 |
| --- | --- | ---: | --- |
| [ ] uncertain | needs_review | 1982 | [Chemical modification of opiate receptors with ethoxyformic anhydride and photo-oxidation: Evidence for essential histidyl residues](https://doi.org/10.1016/0006-291x(82)91752-1) |
| [ ] uncertain | verified | 2005 | [Robust textual inference via learning and abductive reasoning](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.397.9501) |
| [ ] uncertain | verified | 2007 | [Learning 3-D Scene Structure from a Single Still Image](https://doi.org/10.1109/iccv.2007.4408828) |
| [ ] uncertain | verified | 2009 | [Large-scale deep unsupervised learning using graphics processors](https://doi.org/10.1145/1553374.1553486) |
| [ ] uncertain | verified | 2011 | [Multimodal Deep Learning](http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.186.3756) |
| [ ] uncertain | verified | 2014 | [Grounded Compositional Semantics for Finding and Describing Images with Sentences](https://doi.org/10.1162/tacl_a_00177) |
| [ ] uncertain | needs_review | 2019 | [Neural Text Style Transfer via Denoising and Reranking](https://doi.org/10.18653/v1/w19-2309) |
| [ ] uncertain | needs_review | 2020 | [Penile Ultrasound](https://doi.org/10.1007/978-3-030-52309-1_7) |
| [ ] uncertain | needs_review | 2022 | [Densification Retardation in 3d Printed Transparent Spinel Ceramics: Origin and Mitigation Approach](https://doi.org/10.2139/ssrn.4063460) |
| [ ] uncertain | needs_review | 2026 | [IDEA2: Expert-in-the-loop competency question elicitation for collaborative ontology engineering](http://arxiv.org/abs/2604.01344) |
| [ ] uncertain | needs_review | 2009 | [Guest editorial: Special issue on robot learning, Part A](https://doi.org/10.1007/s10514-009-9122-2) |
| [ ] uncertain | needs_review | 2010 | [Inverse Reinforcement Learning](https://doi.org/10.1007/978-0-387-30164-8_417) |
| [ ] uncertain | needs_review | 2012 | [Proceedings of the Twenty-Fifth Conference on Uncertainty in Artificial Intelligence (2009)](http://arxiv.org/abs/1206.3959) |
| [ ] uncertain | needs_review | 2014 | [Autonomous Helicopter Flight Using Reinforcement Learning](https://doi.org/10.1007/978-1-4899-7502-7_16-1) |
| [ ] uncertain | needs_review | 2016 | [Inverse Reinforcement Learning](https://doi.org/10.1007/978-1-4899-7502-7_142-1) |
| [ ] uncertain | needs_review | 2017 | [Inverse Reinforcement Learning](https://doi.org/10.1007/978-1-4899-7687-1_142) |
| [ ] uncertain | needs_review | 2017 | [Autonomous Helicopter Flight Using Reinforcement Learning](https://doi.org/10.1007/978-1-4899-7687-1_16) |
| [ ] uncertain | needs_review | 2021 | [DLBCL-Morph](https://figshare.com/articles/dataset/DLBCL-Morph/12964772) |
| [ ] uncertain | needs_review | 2026 | [Comment on essd-2026-124](https://doi.org/10.5194/essd-2026-124-cc1) |
| [ ] uncertain | needs_review | 2026 | [Comment on essd-2026-124](https://doi.org/10.5194/essd-2026-124-rc2) |

署名、原始 ID、归属证据和诊断见 [andrew_ng.audit.pending.json](andrew_ng.audit.pending.json)；这些条目不参与临时正例召回率。


Ng 审核样本保留初次系统判定及修正后判定：作者档案 ORCID 不再当作独立逐论文证据，来源档案中未确认的历史机构也不替代目标学校证据。原始记录和原证据均保留；人工标签没有随系统状态变化。
