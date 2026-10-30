"""Turn a workflow run (its report and artifacts) into the API's candidates and result (schema v1 ``search``)."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from paper_author_searcher.jsonio import read_json
from paper_author_searcher.status import (
    WEB_SEARCH_OFF,
    FormatStatus,
    IdentityStatus,
    OwnershipStatus,
    PDFValidationStatus,
    RunStatus,
)

MAXIMUM_EVIDENCE = 5
# Warnings about the whole result that a caller should see, as opposed to one paper's lookup details.
NOTICE_CODES = {WEB_SEARCH_OFF}
MAXIMUM_CANDIDATES = 10
# arXiv DOIs name a preprint edition; another edition of the same work is preferred as the work's main entry.
PREPRINT_DOI_PREFIX = '10.48550/'
FULL_TEXT_RANK = {'file': 0, 'link': 1, 'none': 2}
CONFIDENCE_RANK = {'high': 0, 'medium': 1, 'low': 2}
# Ownership findings are prefixed with the record they came from ("openalex:https://openalex.org/W1: ...").
RECORD_PREFIX = re.compile(r'^[a-z_]+:\S+: ')


def candidates(identity: dict[str, Any], limit: int = MAXIMUM_CANDIDATES) -> list[dict[str, Any]]:
    """The leading profiles a caller can confirm, each with the selections that pick it ("source:author_id")."""
    found = []
    for candidate in identity.get('candidates') or []:
        aliases = candidate.get('author_id_aliases') or {}
        selections = [
            f'{source}:{value}'
            for source, author_id in (candidate.get('author_ids') or {}).items()
            for value in sorted({*aliases.get(source, []), author_id})
        ]
        if not selections:
            continue
        found.append(
            {
                'name': candidate.get('name') or '',
                'selections': selections,
                'affiliations': list(candidate.get('matched_affiliations') or candidate.get('affiliations') or []),
                'orcid': candidate.get('orcid'),
                'match_score': candidate.get('match_score'),
                'evidence': list(candidate.get('evidence') or [])[:MAXIMUM_EVIDENCE],
            }
        )
        if len(found) == limit:
            break
    return found


def selections(found: list[dict[str, Any]]) -> set[str]:
    return {selection for candidate in found for selection in candidate['selections']}


class ResultView:
    """Builds the ``result`` object from a finished run directory.

    The collection, PDF report and download manifest list the same papers in the same order, so they are joined
    by position. Editions of one work (its arXiv preprint, the proceedings version) are separate entries that the
    collection links as related versions; the result returns each work once, with its other editions under
    ``editions``. ``download_link(paper_id)`` gives the API path that serves a paper's checked file.
    """

    def __init__(
        self,
        download_link: Callable[[str], str],
        strong_evidence: tuple[str, ...] = (),
        supporting_evidence: tuple[str, ...] = (),
        maximum_evidence: int = 3,
    ) -> None:
        self.download_link = download_link
        # Findings that make a verified paper 'high' confidence, and the other findings worth showing.
        self.strong_evidence = strong_evidence
        self.supporting_evidence = supporting_evidence
        self.maximum_evidence = maximum_evidence

    def build(self, report: dict[str, Any], include_needs_review: bool = True) -> dict[str, Any]:
        root = Path(report['output_directory'])
        artifacts = report.get('artifacts') or {}
        archive = self._read(root, artifacts.get('papers')) or {}
        collection = archive.get('collection') or archive
        entries = collection.get('all_papers') or collection.get('papers') or []
        links = (self._read(root, artifacts.get('pdf_urls')) or {}).get('papers') or []
        files = (self._read(root, artifacts.get('manifest')) or {}).get('papers') or []
        wanted = {OwnershipStatus.VERIFIED} | ({OwnershipStatus.NEEDS_REVIEW} if include_needs_review else set())
        returned = []
        for index, entry in enumerate(entries):
            if entry.get('ownership_status') not in wanted:
                continue
            link = links[index] if index < len(links) else {}
            file = files[index] if index < len(files) else {}
            returned.append((index, self._paper(str(index), entry, link, file, root)))
        papers = [self._work(group) for group in self._editions(entries, returned)]
        identity = (report.get('identity') or {}).get('profile') or {}
        statuses = [paper['full_text']['status'] for paper in papers]
        return {
            'run_status': RunStatus.COMPLETED if report['run_status'] == RunStatus.COMPLETED else RunStatus.PARTIAL,
            'identity': {
                'name': identity.get('name'),
                'status': identity.get('status'),
                'orcid': identity.get('orcid'),
                'author_ids': dict(identity.get('author_ids') or {}),
                'confidence': identity.get('identity_confidence', identity.get('confidence')),
                'evidence': list(identity.get('evidence') or [])[:MAXIMUM_EVIDENCE],
            },
            'papers': papers,
            'statistics': {
                'papers': len(papers),
                'verified': sum(paper['ownership_status'] == OwnershipStatus.VERIFIED for paper in papers),
                'full_text_files': statuses.count('file'),
                'full_text_links': statuses.count('link'),
            },
            'warnings': [str(item.get('warning') or item) for item in report.get('warnings') or []],
            'notices': [
                {'code': item['code'], 'message': str(item.get('warning'))}
                for item in report.get('warnings') or []
                if item.get('code') in NOTICE_CODES
            ],
        }

    @staticmethod
    def visible(result: dict[str, Any], include_needs_review: bool) -> dict[str, Any]:
        """The result as a caller asked for it: needs-review papers only on request, statistics to match."""
        if include_needs_review:
            return result
        papers = [paper for paper in result['papers'] if paper['ownership_status'] == OwnershipStatus.VERIFIED]
        statuses = [paper['full_text']['status'] for paper in papers]
        statistics = {
            'papers': len(papers),
            'verified': len(papers),
            'full_text_files': statuses.count('file'),
            'full_text_links': statuses.count('link'),
        }
        return {**result, 'papers': papers, 'statistics': statistics}

    @staticmethod
    def _editions(entries: list[dict[str, Any]], returned: list[tuple[int, dict[str, Any]]]) -> list[list[dict]]:
        """Returned papers grouped by work: entries the collection links as related versions share a group."""
        owner: dict[Any, int] = {}
        for index, _ in returned:
            for key in _identifiers(entries[index]):
                owner.setdefault(key, index)
        parent = {index: index for index, _ in returned}

        def root(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for index, _ in returned:
            for version in entries[index].get('related_versions') or []:
                for identifier in version.get('identifiers') or []:
                    for key in _keys(identifier.get('source'), identifier.get('paper_id'), identifier.get('doi')):
                        if key in owner:
                            parent[root(owner[key])] = root(index)
        groups: dict[int, list[dict[str, Any]]] = {}
        for index, paper in returned:
            groups.setdefault(root(index), []).append(paper)
        return sorted(groups.values(), key=lambda group: min(int(paper['id']) for paper in group))

    @staticmethod
    def _work(editions: list[dict[str, Any]]) -> dict[str, Any]:
        """One work: its preferred edition's fields, the best full text of any edition, the others listed."""
        ranked = sorted(
            editions,
            key=lambda paper: (
                FULL_TEXT_RANK[paper['full_text']['status']],
                paper['ownership_status'] != OwnershipStatus.VERIFIED,
                (paper['doi'] or '').lower().startswith(PREPRINT_DOI_PREFIX) or not paper['doi'],
                int(paper['id']),
            ),
        )
        main, others = ranked[0], ranked[1:]
        work = dict(main)
        if any(paper['ownership_status'] == OwnershipStatus.VERIFIED for paper in editions):
            work['ownership_status'] = OwnershipStatus.VERIFIED
        # The work is as certain as its best-attested edition, and carries any edition's abstract.
        best = min(editions, key=lambda paper: CONFIDENCE_RANK[paper['confidence']])
        work['confidence'], work['evidence'] = best['confidence'], best['evidence']
        work['abstract'] = next((paper['abstract'] for paper in ranked if paper['abstract']), None)
        if others:
            work['editions'] = [
                {name: paper[name] for name in ('id', 'title', 'year', 'venue', 'doi', 'full_text')}
                for paper in sorted(others, key=lambda paper: int(paper['id']))
            ]
        return work

    def file_path(self, report: dict[str, Any], paper_id: str) -> Path | None:
        """The checked local PDF of one returned paper, if the run downloaded and confirmed it."""
        root = Path(report['output_directory'])
        files = (self._read(root, (report.get('artifacts') or {}).get('manifest')) or {}).get('papers') or []
        if not paper_id.isdigit() or int(paper_id) >= len(files):
            return None
        return self._checked_file(files[int(paper_id)], root)

    def _paper(
        self, paper_id: str, entry: dict[str, Any], link: dict[str, Any], file: dict[str, Any], root: Path
    ) -> dict[str, Any]:
        checked = self._checked_file(file, root) is not None
        url = file.get('final_url') or file.get('pdf_url') if checked else None
        if not url and link.get('validation_status') == PDFValidationStatus.VERIFIED:
            url = link.get('best_pdf_url')
        confidence, evidence = self._ownership(entry)
        return {
            'id': paper_id,
            'title': entry.get('title') or '',
            'authors': list(entry.get('authors') or []),
            'year': entry.get('year') if isinstance(entry.get('year'), int) else None,
            'venue': entry.get('venue') if isinstance(entry.get('venue'), str) else None,
            'doi': entry.get('doi'),
            'abstract': entry.get('abstract') if isinstance(entry.get('abstract'), str) and entry['abstract'] else None,
            'ownership_status': entry.get('ownership_status'),
            'confidence': confidence,
            'evidence': evidence,
            'full_text': {
                'status': 'file' if checked else 'link' if url else 'none',
                'url': url,
                'version': (file.get('version_type') if checked else link.get('version_type')) if url else None,
                'download': self.download_link(paper_id) if checked else None,
            },
        }

    def _ownership(self, entry: dict[str, Any]) -> tuple[str, list[str]]:
        """How sure the paper is the person's ('high', 'medium', 'low') and the findings that say so."""
        findings = list(
            dict.fromkeys(RECORD_PREFIX.sub('', str(item)) for item in entry.get('ownership_evidence') or [])
        )
        if entry.get('ownership_status') != OwnershipStatus.VERIFIED:
            # Why it was not verified: the last findings are the deciding ones.
            return 'low', findings[-self.maximum_evidence :] if self.maximum_evidence else []
        strong = [item for item in findings if any(item.startswith(phrase) for phrase in self.strong_evidence)]
        supporting = [item for item in findings if any(item.startswith(phrase) for phrase in self.supporting_evidence)]
        return ('high' if strong else 'medium'), [*strong, *supporting][: self.maximum_evidence]

    @staticmethod
    def _checked_file(file: dict[str, Any], root: Path) -> Path | None:
        """Only files whose format, content identity and validation were all confirmed, inside the run's folder."""
        if not (
            file.get('local_filename')
            and file.get('format_status') == FormatStatus.VALID
            and file.get('identity_status') == IdentityStatus.VERIFIED
            and file.get('validation_status') == PDFValidationStatus.VERIFIED
        ):
            return None
        folder = (root / 'papers').resolve()
        path = (folder / file['local_filename']).resolve()
        if folder not in path.parents or not path.is_file():
            return None
        return path

    @staticmethod
    def _read(root: Path, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        path = root / name
        return read_json(path) if path.exists() else None


def _keys(source: Any, paper_id: Any, doi: Any) -> list[tuple[str, str]]:
    keys = []
    if source and paper_id:
        keys.append((str(source), str(paper_id)))
    if doi:
        keys.append(('doi', str(doi).lower()))
    return keys


def _identifiers(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (source, paper ID) and DOI under which the collection knows an entry."""
    keys = _keys(entry.get('source'), entry.get('source_paper_id'), entry.get('doi'))
    for record in entry.get('records') or []:
        keys.extend(_keys(record.get('source'), record.get('source_paper_id'), record.get('doi')))
    return keys
