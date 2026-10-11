"""Interpret explicit source type labels; never infer types from titles/topics."""
from .matching import normalize

TYPE_LABELS = {
    'article': 'research_paper', 'journalarticle': 'research_paper',
    'conference': 'research_paper', 'conference paper': 'research_paper',
    'inproceedings': 'research_paper', 'review': 'review',
    'preprint': 'preprint', 'book chapter': 'book_chapter', 'incollection': 'book_chapter',
    'dataset': 'dataset', 'editorial': 'editorial', 'book': 'book',
    'book review': 'book_review', 'dissertation': 'dissertation', 'phdthesis': 'dissertation',
    'mastersthesis': 'dissertation', 'report': 'report', 'proceedings': 'proceedings',
    'reference entry': 'reference_entry', 'conference abstract': 'conference_abstract',
    'supplementary materials': 'supplementary_materials', 'paratext': 'paratext',
    'peer review': 'peer_review',
}
ACADEMIC_TYPES = frozenset({'research_paper', 'review', 'preprint', 'book_chapter'})
SPECIFIC_TYPES = ('editorial', 'review', 'preprint', 'book_chapter', 'dataset', 'book_review')


def classify_record(record):
    labels = []
    if record.publication_type:
        labels = record.publication_type.split(',')
    categories = set()
    for label in labels:
        category = TYPE_LABELS.get(normalize(label))
        if category:
            categories.add(category)
    record.type_evidence = ['Source type: ' + (record.publication_type or 'missing')]
    specifics = categories.intersection(SPECIFIC_TYPES)
    if len(specifics) == 1:
        record.paper_type = next(iter(specifics))
    elif len(categories) == 1:
        record.paper_type = next(iter(categories))
    else:
        record.paper_type = 'unknown'
        record.type_evidence.append('Missing, unsupported or conflicting explicit type labels')


def publication_form(record):
    labels = set()
    if record.publication_type:
        labels = set(map(normalize, record.publication_type.split(',')))
    if 'preprint' in labels:
        return 'preprint'
    conference = bool(labels & {'conference', 'conference paper', 'inproceedings'})
    journal = bool(labels & {'article', 'journalarticle'})
    if conference and journal:
        return 'unknown'
    if conference:
        return 'conference'
    if journal:
        return 'journal'
    if record.paper_type in {'book_chapter', 'dataset', 'book', 'editorial', 'review'}:
        return record.paper_type
    return 'unknown'
