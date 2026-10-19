"""Resolve URLs from saved collections while preserving ownership judgments."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urldefrag

from .config import DEFAULT_PDF
from .http import HttpClient
from .http_decorators import unwrap
from .logging_config import log_isolated_failure
from .models import PaperPDFResult, PDFProviderResult, PDFResolutionResult
from .paper_views import bibliographic_view
from .pdf_resource_requests import PublicResourceRequests
from .pdf_search_status import SEARCH_COMPLETION_DEFINITION, paper_search_complete
from .performance_diagnostics import pdf_failure_reasons
from .status import AccessStatus, IdentityStatus, OwnershipStatus, PDFValidationStatus, ProviderStatus

logger = logging.getLogger(__name__)


class PDFResolver:
    """Run every PDF source for each eligible paper and keep only validated candidates.

    ``requests`` is the shared public-resource scheduler the providers and validator use; when given, its
    probe statistics are reported. Build the full source set with ``composition.ServiceContainer``.
    """

    def __init__(self, providers, validator, requests: Optional[PublicResourceRequests] = None, config=DEFAULT_PDF):
        self.providers = list(providers)
        self.validator = validator
        self.resource_requests = requests
        self.config = config
        self.preparation_errors: Dict[str, Any] = {}

    def resolve(self, saved_result, include_needs_review=False, maximum_papers=None, progress=None):
        if maximum_papers is not None and maximum_papers < 1:
            raise ValueError('maximum_papers must be positive')
        collection = saved_result.get('collection', saved_result)
        if not isinstance(collection, dict):
            raise ValueError('Collection must be an object')
        papers = collection.get('all_papers', collection.get('papers'))
        if not isinstance(papers, list) or not all(isinstance(paper, dict) for paper in papers):
            raise ValueError('Collection must contain a list of paper objects')
        results = []
        eligible_statuses = {OwnershipStatus.VERIFIED}
        if include_needs_review:
            eligible_statuses.add(OwnershipStatus.NEEDS_REVIEW)
        eligible_count = sum(paper.get('ownership_status') in eligible_statuses for paper in papers)
        progress_total = min(eligible_count, maximum_papers) if maximum_papers else eligible_count
        selected = [paper for paper in papers if paper.get('ownership_status') in eligible_statuses]
        if maximum_papers is not None:
            selected = selected[:maximum_papers]
        self._prepare_providers(selected)
        jobs: List[Tuple[Dict[str, Any], PaperPDFResult]] = []
        for index, paper in enumerate(papers):
            result = PaperPDFResult(
                index,
                paper.get('title', ''),
                paper.get('doi'),
                paper.get('ownership_status', OwnershipStatus.NEEDS_REVIEW),
                deepcopy(paper.get('ownership_evidence') or []),
                authors=deepcopy(paper.get('authors') or []),
                year=paper.get('year'),
                source=paper.get('source'),
                source_paper_id=paper.get('source_paper_id'),
                external_ids=deepcopy(paper.get('external_ids') or {}),
                source_author_id=paper.get('source_author_id'),
                paper_metadata=deepcopy(paper) if self.config.embed_full_metadata else bibliographic_view(paper),
            )
            results.append(result)
            if result.ownership_status not in eligible_statuses:
                result.validation_status = PDFValidationStatus.SKIPPED_OWNERSHIP
            elif maximum_papers is None or len(jobs) < maximum_papers:
                jobs.append((paper, result))
        for processed in self._run_lookups(jobs):
            if progress:
                progress('Finding PDF URLs', processed, progress_total)
        for result in results:
            result.failure_reasons = pdf_failure_reasons(asdict(result))
        return PDFResolutionResult(results, self._statistics(results))

    def _run_lookups(self, jobs):
        """Resolve papers concurrently (sources keep their own per-host pacing); yield the running count."""
        workers = min(self.config.lookup_workers, len(jobs))
        if workers <= 1:
            for processed, job in enumerate(jobs, 1):
                self._resolve_isolated(*job)
                yield processed
            return
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pdf-lookup') as pool:
            futures = [pool.submit(self._resolve_isolated, *job) for job in jobs]
            for processed, future in enumerate(as_completed(futures), 1):
                future.result()
                yield processed

    def _resolve_isolated(self, paper, result):
        try:
            self._resolve_paper(paper, result)
        except Exception as exc:
            log_isolated_failure(
                logger, 'PDF resolution failed for %s: %s', paper.get('doi') or paper.get('title'), exc
            )
            result.validation_status = PDFValidationStatus.UNVERIFIED
            result.error_details.append({'source': 'pdf_validation', 'error': str(exc)})

    def _prepare_providers(self, papers):
        self.preparation_errors = {}
        for provider in self.providers:
            if not callable(getattr(type(provider), 'prepare', None)):
                continue
            try:
                provider.prepare(papers)
            except Exception as exc:
                log_isolated_failure(logger, 'PDF provider %s preparation failed: %s', provider.name, exc)
                self.preparation_errors[provider.name] = str(exc)

    def _resolve_paper(self, paper, result):
        candidates = []
        lookup_paper = dict(paper, _pdf_landing_pages=[])
        for provider in self.providers:
            try:
                found = provider.find(lookup_paper)
            except Exception as exc:
                log_isolated_failure(logger, 'PDF provider %s lookup failed: %s', provider.name, exc)
                found = PDFProviderResult(provider.name, status=ProviderStatus.ERROR, errors=[str(exc)])
            if provider.name in self.preparation_errors:
                found.errors.append('Provider preparation: ' + self.preparation_errors[provider.name])
                found.status = ProviderStatus.PARTIAL if found.candidates else ProviderStatus.ERROR
            result.providers.append(
                {
                    'source': found.source,
                    'status': found.status,
                    'errors': found.errors,
                    'diagnostics': found.diagnostics,
                    'warnings': found.warnings,
                }
            )
            for error in found.errors:
                result.error_details.append({'source': found.source, 'error': error})
            result.warnings.extend(dict(warning, source=found.source) for warning in found.warnings)
            candidates.extend(found.candidates)
            lookup_paper['_pdf_landing_pages'].extend(found.landing_pages)
        result.pdf_candidates = self._merge_urls(candidates)
        for candidate in result.pdf_candidates:
            self.validator.validate(candidate, paper)
            for error in candidate.error_details:
                entry = {'source': candidate.source, 'url': candidate.url, 'error': error}
                if candidate.query_error_type == 'source_deferred':
                    result.warnings.append(
                        dict(entry, code='source_deferred', incomplete=True, request_context=candidate.request_context)
                    )
                elif candidate.access_status == AccessStatus.UNREACHABLE:
                    entry.update(error_type=candidate.query_error_type, request_context=candidate.request_context)
                    result.error_details.append(entry)
                else:
                    category = (
                        'pdf_identity_conflict'
                        if candidate.identity_status == IdentityStatus.CONFLICTING
                        else 'candidate_filtered'
                    )
                    result.candidate_decisions.append(
                        dict(
                            entry,
                            category=category,
                            identity_status=candidate.identity_status,
                            validation_status=candidate.validation_status,
                        )
                    )
        valid = [
            candidate
            for candidate in result.pdf_candidates
            if candidate.validation_status == PDFValidationStatus.VERIFIED
        ]
        if valid:
            best = min(valid, key=self._version_priority)
            result.best_pdf_url = best.final_url or best.url
            result.pdf_source = best.source
            result.version_type = best.version_type
            result.access_status = best.access_status
            result.validation_status = PDFValidationStatus.VERIFIED
        elif any(candidate.validation_status == PDFValidationStatus.UNVERIFIED for candidate in result.pdf_candidates):
            result.validation_status = PDFValidationStatus.UNVERIFIED
            result.access_status = AccessStatus.UNKNOWN
        else:
            result.validation_status = PDFValidationStatus.NOT_FOUND
            result.access_status = AccessStatus.UNKNOWN

    def _merge_urls(self, candidates):
        merged = {}
        versions: Dict[str, Any] = {}
        for candidate in candidates:
            key = urldefrag(candidate.url)[0]
            versions.setdefault(key, set())
            if candidate.version_type != 'unknown':
                versions[key].add(candidate.version_type)
            if key not in merged:
                merged[key] = deepcopy(candidate)
                continue
            existing = merged[key]
            existing.provenance.extend(candidate.provenance)
            existing.identity_evidence = list(dict.fromkeys(existing.identity_evidence + candidate.identity_evidence))
            if 'conflicting' in (existing.identity_status, candidate.identity_status):
                existing.identity_status = IdentityStatus.CONFLICTING
            elif candidate.identity_status == IdentityStatus.VERIFIED:
                existing.identity_status = IdentityStatus.VERIFIED
        for key, candidate in merged.items():
            if len(versions[key]) == 1:
                candidate.version_type = next(iter(versions[key]))
            elif len(versions[key]) > 1:
                candidate.version_type = 'unknown'
                candidate.identity_evidence.append('Sources disagree about the version type')
        return list(merged.values())

    def _version_priority(self, candidate):
        try:
            return self.config.version_preference.index(candidate.version_type)
        except ValueError:
            return len(self.config.version_preference)

    def _statistics(self, papers):
        counts: Dict[str, Any] = {
            'total_papers': len(papers),
            'processed_papers': 0,
            'valid_pdf_papers': 0,
            'unverified_only_papers': 0,
            'not_found_papers': 0,
            'skipped_ownership_papers': 0,
            'not_processed_papers': 0,
            'incomplete_search_papers': 0,
        }
        status_metrics = {
            PDFValidationStatus.VERIFIED: 'valid_pdf_papers',
            PDFValidationStatus.UNVERIFIED: 'unverified_only_papers',
            PDFValidationStatus.NOT_FOUND: 'not_found_papers',
            PDFValidationStatus.SKIPPED_OWNERSHIP: 'skipped_ownership_papers',
            PDFValidationStatus.NOT_PROCESSED: 'not_processed_papers',
        }
        sources: Dict[str, Any] = {}
        verified_urls = set()
        for paper in papers:
            counts[status_metrics[paper.validation_status]] += 1
            if paper.validation_status in (PDFValidationStatus.NOT_PROCESSED, PDFValidationStatus.SKIPPED_OWNERSHIP):
                continue
            counts['processed_papers'] += 1
            incomplete = not paper_search_complete(paper.providers)
            for provider in paper.providers:
                contribution = sources.setdefault(provider['source'], self._source_counts())
                contribution['errors'] += len(provider['errors'])
                query_status_counts = contribution['query_status_counts']
                query_status_counts[provider['status']] = query_status_counts.get(provider['status'], 0) + 1
            counts['incomplete_search_papers'] += incomplete
            valid_sources = set()
            for candidate in paper.pdf_candidates:
                candidate_sources = {candidate.source}
                candidate_sources.update(
                    record['provider'] for record in candidate.provenance if record.get('provider')
                )
                for source in candidate_sources:
                    sources.setdefault(source, self._source_counts())
                    sources[source]['candidate_urls'] += 1
                    if candidate.validation_status == PDFValidationStatus.VERIFIED:
                        sources[source]['verified_urls'] += 1
                        valid_sources.add(source)
                if candidate.validation_status == PDFValidationStatus.VERIFIED:
                    verified_urls.add(candidate.final_url or candidate.url)
            for source in valid_sources:
                sources[source]['papers_with_valid_pdf'] += 1
        counts['distinct_verified_pdf_urls'] = len(verified_urls)
        counts['source_contributions'] = sources
        counts['search_complete_for_processed_papers'] = counts['incomplete_search_papers'] == 0
        counts['coverage_note'] = (
            'Counts describe processed saved entries and public-source observations, not all available PDFs.'
        )
        counts['provider_request_statistics'] = {}
        if self.resource_requests is not None:
            counts['provider_request_statistics']['public_resources'] = dict(
                self.resource_requests.statistics,
                events=list(self.resource_requests.events),
                deferrals=list(self.resource_requests.deferrals),
                scope='Logical probe calls and terminal request failures; HTTP retries and redirects are separate',
            )
        counts['complete_search_papers'] = counts['processed_papers'] - counts['incomplete_search_papers']
        counts['search_completion_scope'] = (
            'Configured bounded source queries; not all legal PDFs or versions on the Internet'
        )
        counts['search_completion_definition'] = SEARCH_COMPLETION_DEFINITION
        http_client = unwrap(self.resource_requests)
        if isinstance(http_client, HttpClient):
            counts['http_configuration'] = {'timeout': http_client.timeout, 'retries': http_client.retries}
            counts['http_transport_statistics'] = deepcopy(http_client.transport_statistics)
        for provider in self.providers:
            if callable(getattr(type(provider), 'request_statistics', None)):
                counts['provider_request_statistics'][provider.name] = provider.request_statistics()
        return counts

    def _source_counts(self):
        return {
            'candidate_urls': 0,
            'verified_urls': 0,
            'papers_with_valid_pdf': 0,
            'errors': 0,
            'query_status_counts': {},
        }
