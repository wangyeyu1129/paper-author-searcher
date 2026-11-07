"""Ownership evidence phrases that other code reads back.

Ownership decisions record their reasons as text. A few of those phrases are later matched, by the validator itself
(a conflict blocks cross-source upgrades) and by the review diagnostics, so each is written once here and both the
writer and the readers use the same constant. Saved reports keep the same wording.
"""

# Written when a paper's target byline carries ORCIDs that disagree with each other or with the identity's.
ORCID_CONFLICT = 'ORCID conflict'
MULTIPLE_ORCIDS = f'Target authorship {ORCID_CONFLICT}: multiple identities reported'
CONFIRMED_ORCID_CONFLICT = 'conflicts with confirmed professor ORCID'
TARGET_ORCID_CONFLICT = f'Target authorship ORCID {CONFIRMED_ORCID_CONFLICT}'
CROSS_SOURCE_ORCID_CONFLICT = f'Cross-source target {ORCID_CONFLICT}'
CROSS_SOURCE_ORCID_BLOCK = f'{CROSS_SOURCE_ORCID_CONFLICT}; automatic verification blocked'

# Written when a source classifies the publication as something other than a standard research paper.
NONSTANDARD = 'Nonstandard'
NONSTANDARD_TYPE = f'{NONSTANDARD} publication type: '
NONSTANDARD_MERGED = f'{NONSTANDARD} source classification in merged publication'
NONSTANDARD_MERGED_BLOCK = f'{NONSTANDARD_MERGED}; automatic verification blocked'

INSUFFICIENT_BYLINE = 'Insufficient compatible target byline'
INSUFFICIENT_BYLINE_EVIDENCE = f'{INSUFFICIENT_BYLINE} evidence'

PROFILE_ORCID_NOT_INDEPENDENT = 'not independent paper authorship evidence'

# Matched case-insensitively: identity-unconfirmed notes are written in several sentence forms.
IDENTITY_UNCONFIRMED = 'identity unconfirmed'
