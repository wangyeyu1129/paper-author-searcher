"""The contract with the cold-email agent: what it may use, and what it must not (schema v1 ``agent-result``).

The agent writes to a person about their work, so a wrong paper or a wrong person is worse than a missing one.
The view therefore splits a search's result in two:

- ``papers``: works the agent may cite. The identity is confirmed (resolved by evidence, or picked by the
  caller) and the paper's ownership is verified. Each carries title, year, venue, abstract, DOI link, full text
  (link or file), a confidence ('high': the person's own list, a paper-level ORCID or a curated profile backs it;
  'medium': author ID, byline and institution agree) and the findings behind it.
- ``withheld``: everything else, each with the reason (``identity_unconfirmed``, ``ownership_unverified``), so the
  agent never cites it but an operator can review it.

``usable`` is true only when there is something to cite; ``status`` says why not otherwise.
"""

from typing import Any

from paper_author_searcher.status import OwnershipStatus, ResolutionStatus

from .model import Job, JobStatus

CONTRACT_VERSION = 'v1'
DOI_RESOLVER = 'https://doi.org/'


def agent_view(job: Job) -> dict[str, Any]:
    request = job.request
    view: dict[str, Any] = {
        'api_version': CONTRACT_VERSION,
        'search_id': job.id,
        'status': _status(job),
        'usable': False,
        'researcher': {
            'requested': {key: request.get(key) for key in ('name', 'university', 'department')},
            'identity': None,
        },
        'papers': [],
        'withheld': [],
        'notes': [],
    }
    if job.status != JobStatus.SUCCEEDED or not job.result:
        if job.status == JobStatus.NEEDS_CONFIRMATION:
            view['notes'].append('Namesakes could not be told apart; confirm the person before using any paper')
        if job.status == JobStatus.FAILED and job.error:
            view['notes'].append(job.error.get('message', 'The search failed'))
        return view
    result = job.result
    identity = result.get('identity') or {}
    confirmed_by = (
        'caller' if job.selection else 'evidence' if identity.get('status') == ResolutionStatus.RESOLVED else None
    )
    view['researcher']['identity'] = {
        'confirmed': confirmed_by is not None,
        'confirmed_by': confirmed_by,
        'name': identity.get('name'),
        'orcid': identity.get('orcid'),
        'author_ids': identity.get('author_ids') or {},
        'confidence': identity.get('confidence'),
        'evidence': identity.get('evidence') or [],
    }
    for paper in result.get('papers') or []:
        if confirmed_by is None:
            view['withheld'].append(_withheld(paper, 'identity_unconfirmed'))
        elif paper['ownership_status'] != OwnershipStatus.VERIFIED:
            view['withheld'].append(_withheld(paper, 'ownership_unverified'))
        else:
            view['papers'].append(_paper(paper))
    if confirmed_by is None:
        view['notes'].append('The identity was not confirmed; every paper is withheld until a person is confirmed')
    if result.get('run_status') != 'completed':
        view['notes'].append('Some sources did not answer; the list may be incomplete')
    view['usable'] = bool(view['papers'])
    if not view['usable'] and view['status'] == 'ready':
        view['status'] = 'nothing_usable'
    return view


STATUS_OF: dict[str, str] = {
    JobStatus.QUEUED: 'pending',
    JobStatus.RUNNING: 'pending',
    JobStatus.NEEDS_CONFIRMATION: 'needs_confirmation',
    JobStatus.FAILED: 'failed',
    JobStatus.SUCCEEDED: 'ready',
}


def _status(job: Job) -> str:
    return STATUS_OF.get(job.status, 'pending')


def _paper(paper: dict[str, Any]) -> dict[str, Any]:
    full_text = paper['full_text']
    return {
        'id': paper['id'],
        'title': paper['title'],
        'year': paper.get('year'),
        'venue': paper.get('venue'),
        'authors': paper.get('authors') or [],
        'abstract': paper.get('abstract'),
        'doi': paper.get('doi'),
        'url': DOI_RESOLVER + paper['doi'] if paper.get('doi') else None,
        'full_text': {
            'status': full_text['status'],
            'url': full_text.get('url'),
            'download': full_text.get('download'),
        },
        'confidence': paper.get('confidence', 'medium'),
        'evidence': paper.get('evidence') or [],
    }


def _withheld(paper: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        'id': paper['id'],
        'title': paper['title'],
        'year': paper.get('year'),
        'reason': reason,
        'evidence': paper.get('evidence') or [],
    }
