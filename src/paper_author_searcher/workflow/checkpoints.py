"""Read saved checkpoints and choose the identity a run collects for."""

from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

from paper_author_searcher.config import AmbiguityPolicy
from paper_author_searcher.json_types import JSONObject
from paper_author_searcher.jsonio import read_json
from paper_author_searcher.models import ProfessorProfile, ResolutionResult
from paper_author_searcher.paper_collector import IdentityConfirmationRequired
from paper_author_searcher.status import ResolutionStatus, UniversityStatus


def load_checkpoint(path: str | Path, intern_strings: bool = False) -> JSONObject:
    """Report references are local artifacts, never arbitrary paths or URLs."""
    path = Path(path).resolve()
    data = read_json(path, intern_strings)
    if data.get('report_type') != 'paper_search_workflow':
        return data
    loaded = {}
    for key in ('professor', 'papers', 'pdf_urls'):
        filename = data.get('artifacts', {}).get(key)
        if not filename:
            continue
        artifact = path.parent / filename
        if artifact.is_symlink() or artifact.resolve().parent != path.parent:
            raise ValueError('Report artifact must be a file in the report directory')
        loaded[key] = read_json(artifact, intern_strings)
    if 'pdf_urls' in loaded:
        saved = loaded['pdf_urls']
        if 'papers' in loaded:
            saved['collection_archive'] = loaded['papers']
        if 'professor' in loaded:
            saved['identity_archive'] = loaded['professor']
        return saved
    if 'papers' in loaded:
        return loaded['papers']
    if 'professor' in loaded:
        return loaded['professor']
    raise ValueError('Report contains no resumable checkpoint')


def _profile(values: JSONObject) -> ProfessorProfile:
    values = dict(values)
    values.setdefault('affiliations', values.get('matched_affiliations') or [])
    values.setdefault('identity_confidence', values.get('confidence', 0))
    values.setdefault('evidence', [])
    allowed = {entry.name for entry in fields(ProfessorProfile)}
    return ProfessorProfile(**{key: value for key, value in values.items() if key in allowed})


def _resolution(saved: JSONObject) -> ResolutionResult:
    if 'candidates' not in saved or 'profile' not in saved:
        if not saved.get('name') or not isinstance(saved.get('author_ids'), dict) or 'status' not in saved:
            raise ValueError('Expected Resolver, Collector or PDF Resolver JSON')
        single = _profile(saved)
        return ResolutionResult(single, [single], saved.get('source_errors') or {}, saved.get('ambiguity'))
    profile = _profile(saved['profile']) if saved['profile'] else None
    return ResolutionResult(
        profile,
        [_profile(item) for item in saved['candidates']],
        saved.get('source_errors') or {},
        saved.get('ambiguity'),
    )


def contender_profile(result: ResolutionResult, maximum_contenders: int, margin: float) -> ProfessorProfile:
    """Merge the leading, school-compatible candidates into one unconfirmed profile for collection."""
    best = result.profile
    if best is None:
        raise IdentityConfirmationRequired('No identity candidate available')
    contenders = [
        candidate
        for candidate in result.candidates
        if candidate.university_status != UniversityStatus.MISMATCH
        and candidate.match_score >= best.match_score - margin
    ][:maximum_contenders]
    merged = deepcopy(best)
    merged.status = ResolutionStatus.AMBIGUOUS_CONTENDERS
    aliases: dict[str, Any] = {}
    for candidate in contenders:
        for source, author_id in candidate.author_ids.items():
            aliases.setdefault(source, [])
            for value in (candidate.author_id_aliases.get(source) or []) + [author_id]:
                if value not in aliases[source]:
                    aliases[source].append(value)
    merged.author_id_aliases = aliases
    merged.author_ids = {source: values[0] for source, values in aliases.items()}
    merged.contender_ids = [
        {
            source: list(dict.fromkeys([*(candidate.author_id_aliases.get(source) or []), author_id]))
            for source, author_id in candidate.author_ids.items()
        }
        for candidate in contenders
    ]
    merged.evidence = [
        *list(best.evidence),
        f'Collected {len(contenders)} competing profiles without confirming identity',
    ]
    return merged


def _select_identity(
    result: ResolutionResult,
    selection: str | None,
    policy: str = 'stop',
    maximum_contenders: int = 3,
    margin: float = 0.1,
) -> tuple[ProfessorProfile, tuple[str, str] | None]:
    if selection:
        if ':' not in selection:
            raise IdentityConfirmationRequired('Selection must use SOURCE:AUTHOR_ID')
        source, author_id = selection.split(':', 1)
        for candidate in result.candidates:
            ids = candidate.author_id_aliases.get(source) or []
            if candidate.author_ids.get(source) != author_id and author_id not in ids:
                continue
            confirmed = deepcopy(candidate)
            confirmed.author_ids = {source: author_id}
            confirmed.author_id_aliases = {source: [author_id]}
            return confirmed, (source, author_id)
        raise IdentityConfirmationRequired('Selected Author ID is absent from the candidates')
    if result.profile is None:
        raise IdentityConfirmationRequired('No identity candidate available')
    if result.profile.status != ResolutionStatus.RESOLVED:
        if (
            policy == AmbiguityPolicy.COLLECT_CONTENDERS
            and result.profile.university_status != UniversityStatus.MISMATCH
        ):
            return contender_profile(result, maximum_contenders, margin), None
        raise IdentityConfirmationRequired('Explicit Author ID selection is required for this identity')
    return result.profile, None


def _is_pdf_report(saved: JSONObject) -> bool:
    return 'validation_scope' in saved and isinstance(saved.get('papers'), list)


def _paper_entries(saved: JSONObject) -> list[JSONObject]:
    collection = saved.get('collection', saved)
    papers = collection.get('all_papers', collection.get('papers'))
    if not isinstance(papers, list) or not all(isinstance(item, dict) for item in papers):
        raise ValueError('Expected a paper list')
    return papers


def metadata_audit(
    original: list[JSONObject], pdf: JSONObject | None = None, manifest: JSONObject | None = None
) -> JSONObject:
    """Missing source data is separate from evidence lost between stages."""
    audited_fields = (
        'title',
        'authors',
        'year',
        'doi',
        'source',
        'source_paper_id',
        'source_author_id',
        'ownership_status',
        'ownership_evidence',
    )
    missing = {}
    losses = []
    for field in audited_fields:
        missing[field] = sum(item.get(field) in (None, '', []) for item in original)
    for stage, downstream in (('pdf_urls', pdf), ('download', manifest)):
        if downstream is None:
            continue
        for index, (before, after) in enumerate(zip(original, downstream.get('papers', []), strict=False)):
            for field in audited_fields:
                value = before.get(field)
                if value not in (None, '', []) and after.get(field) != value:
                    losses.append({'stage': stage, 'entry_index': index, 'field': field})
        if len(downstream.get('papers', [])) < len(original):
            losses.append({'stage': stage, 'field': 'paper_entries', 'error': 'Missing entries'})
    return {'missing_input_fields': missing, 'transmission_losses': losses}
