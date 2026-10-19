"""AI review decisions (Claude, 2026-10-18, at the user's request) for the 2026-10-18 sheets.

Evidence: Crossref author lists and affiliations for each DOI, the researchers' own pages, and ORCID. Row numbers
follow each CSV's order. Leads marked belongs carry the evidence in their note; same_as merges another version of
an existing publication; uncertain covers duplicates and non-publications (registry entries, abstracts, corrections).
"""
import csv
import sys
from pathlib import Path

B, U, N = 'belongs', 'uncertain', 'not_belongs'
CROSSREF = 'Crossref author list names the researcher'

DECISIONS = {
    'cs_wei_wang_ucla': {'default': B, 4: (U, 'Duplicate of row 3 (same paper from ORCID)')},
    'soc_raj_chetty': {'default': B},
    'bio_jia_liu': {'default': B, 13: (U, 'Author correction notice, not a publication')},
    'eng_siddharth_krishnan': {'default': B,
        7: (B, CROSSREF + '; affiliation David H. Koch Institute, MIT (his postdoc lab)'),
        8: (B, CROSSREF + '; affiliation Querrey Simpson Institute, Northwestern (his PhD group)')},
    'cs_jing_huang': {'default': B,
        8: (B, CROSSREF + ' with Diyi Yang (Stanford NLP), EMNLP 2025 proceedings'),
        9: (B, CROSSREF + ' with Stanford NLP coauthors (Wu, Geiger, Arora, Potts), NAACL 2024 demo'),
        10: (U, 'arXiv version of row 9')},
    'eng_sindy_tang': {'default': B,
        6: (U, 'bioRxiv version of row 5'),
        14: (B, CROSSREF + ' (Sindy K.Y. Tang) with her basophil-test coauthors'),
        15: (U, 'Journal supplement abstract, not a full publication'),
        16: (B, CROSSREF + ' (Sindy K. Y. Tang), microdissection line of work with Cordts/Arif'),
        17: ('same_as', 11), 18: (U, 'MicroTAS book-of-abstracts entry')},
    'bio_le_cong': {'default': B,
        2: (U, 'Preprint version of row 8 (CRISPR-GPT)'), 9: (U, 'Version of row 8 (CRISPR-GPT)'),
        14: (B, CROSSREF + '; affiliation Stanford University'),
        15: (B, CROSSREF + ' with his group (Jin, Qu, Mengdi Wang)'),
        16: ('same_as', 5), 17: ('same_as', 11), 18: (U, 'Publisher correction notice'),
        19: (B, CROSSREF + ' with Yin, Qu, Mengdi Wang'),
        20: (U, 'Likely the arXiv version of row 19 (same authors and topic)'),
        21: (B, CROSSREF + '; affiliation Stanford University; NeurIPS 2025'),
        22: (B, CROSSREF + '; Stanford study (Le Guen et al.)'),
        23: (B, CROSSREF + ' with his group (Zhang, Jin)'),
        24: (U, 'Author correction notice'), 25: (U, 'Conference abstract (Cancer Research supplement)')},
    'soc_shakked_noy': {'default': B,
        3: (B, CROSSREF + '; working paper with Aakaash Rao, also on his MIT job-market profile'),
        **{row: (U, 'AEA RCT registry entry, not a publication') for row in range(4, 13)},
        13: (B, CROSSREF + '; 2024 working paper with Graeber and Roth, earlier version of the QJE paper on his profile'),
        14: (U, 'Second SSRN version of row 13'),
        15: (N, 'DOI 10.1016/s1470-2045(07)70048-7 is a 2007 Lancet Oncology article by other authors'),
        16: (B, CROSSREF + ' with Jäger and Schoefer (same coauthors as his JEP 2022 paper)'),
        17: (U, 'Primer version of the JEP 2022 paper (row 1)'),
        18: (B, CROSSREF + ' with Jäger and Schoefer, ILR Review'),
        19: (B, CROSSREF + ' with Feess and Feld (New Zealand-based coauthors, matching his NZ work)'),
        20: (U, 'NBER version of row 18'),
        21: (B, CROSSREF + ' with Isabelle Sin (coauthor of row 0), Motu working paper'),
        22: (U, 'SSRN version of row 18'), 23: ('same_as', 0)},
}
COMPLETE = {'cs_wei_wang_ucla', 'soc_raj_chetty'}


def apply(path: Path, decisions: dict) -> None:
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows):
        choice = decisions.get(index, decisions['default'])
        decision, note = (choice, '') if isinstance(choice, str) else choice
        if decision == 'same_as':
            decision, note = 'same_as:' + rows[note]['evaluation_id'], 'Another version of row {}'.format(note)
        row['decision'] = decision
        if note:
            row['reviewer_note'] = note
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    for person_id, decisions in DECISIONS.items():
        apply(folder / (person_id + '.csv'), decisions)
        print(person_id, 'complete' if person_id in COMPLETE else 'not asserted complete')
