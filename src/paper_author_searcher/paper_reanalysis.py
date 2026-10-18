"""Re-evaluate saved metadata without API calls or modifying the source file."""
from dataclasses import fields

from .models import PaperRecord, PaperSourceResult, PaperCollectionResult, ProfessorProfile
from .paper_deduplication import deduplicate_papers
from .paper_ownership import PaperOwnershipValidator


def _contains_selected_ids(candidate, selected):
    for source, author_ids in selected.items():
        known = set((candidate.get('author_id_aliases') or {}).get(source) or [])
        representative = (candidate.get('author_ids') or {}).get(source)
        if representative:
            known.add(representative)
        if not set(author_ids).issubset(known):
            return False
    return True


def _saved_identity(data, selected):
    candidates = data.get('candidates') or [data.get('profile') or data]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        has_author_ids = candidate.get('author_ids') or candidate.get('author_id_aliases')
        if not data.get('candidates') and not has_author_ids:
            # Older archives stored the confirmed IDs only on the collection.
            return candidate
        if _contains_selected_ids(candidate, selected):
            return candidate
    return data.get('collection_identity') or {}


def saved_collection_profile(data):
    collection = data.get('collection')
    if collection is None:
        raise ValueError('Expected a saved CLI result containing collection and professor identity')
    selected = collection.get('selected_author_ids') or {}
    collection_identity = data.get('collection_identity') or {}
    identity = _saved_identity(data, selected)
    name = collection_identity.get('name') or identity.get('name')
    if not name or not selected:
        raise ValueError('Saved result lacks target identity or selected Author IDs')
    return ProfessorProfile(
        name=name, affiliations=identity.get('affiliations') or identity.get('matched_affiliations') or [],
        author_ids={source: ids[0] for source, ids in selected.items() if ids},
        identity_confidence=identity.get('identity_confidence', identity.get('confidence', 0)),
        status=collection_identity.get('status', identity.get('status', 'unknown')), evidence=identity.get('evidence') or [],
        orcid=collection_identity.get('orcid', identity.get('orcid')), author_id_aliases=selected,
        name_aliases=collection_identity.get('name_aliases', identity.get('name_aliases')) or [],
        university=identity.get('university'), university_status=identity.get('university_status', 'unknown'),
        requested_university=identity.get('requested_university'),
        matched_affiliations=identity.get('matched_affiliations') or [],
        match_score=identity.get('match_score', 0), research_interests=identity.get('research_interests') or [],
        identity_anchors=identity.get('identity_anchors') or [],
    )


def reanalyze_saved_result(data):
    profile = saved_collection_profile(data)
    collection = data['collection']
    selected = collection['selected_author_ids']
    record_fields = {field.name for field in fields(PaperRecord)}
    records = []
    stored_papers = collection.get('all_papers', collection.get('papers', []))
    for paper in stored_papers:
        for entry in paper.get('records') or [paper]:
            values = {key: value for key, value in entry.items() if key in record_fields}
            # Recompute decisions from evidence; do not treat previous labels as proof.
            values['ownership_status'] = 'needs_review'
            values['ownership_evidence'] = []
            records.append(PaperRecord(**values))
    sources = []
    for entry in collection.get('sources', []):
        source = PaperSourceResult(
            source=entry['source'], author_id=entry.get('author_id'),
            complete=entry.get('complete', False),
            pagination_complete=entry.get('pagination_complete', entry.get('complete', False)),
            pages_fetched=entry.get('pages_fetched', 0), records_received=entry.get('records_received', 0),
            rejected_records=entry.get('rejected_records') or [], error=entry.get('error'),
            metadata_errors=entry.get('metadata_errors') or [],
        )
        source.papers = [record for record in records
                         if record.source == source.source and record.source_author_id == source.author_id]
        sources.append(source)
    papers = deduplicate_papers(records)
    PaperOwnershipValidator().validate(papers, profile)
    return PaperCollectionResult(
        papers=papers, sources=sources, complete=collection.get('complete', False),
        selected_author_ids=selected,
        identity_confirmation=collection.get('identity_confirmation', 'unknown'),
        professor_identity_status=profile.status,
    )
