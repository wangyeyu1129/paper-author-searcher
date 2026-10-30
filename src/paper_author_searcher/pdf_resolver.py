"""Resolve URLs from saved collections while preserving ownership judgments."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from urllib.parse import urldefrag, urlsplit

from .config import DEFAULT_PDF
from .http import HttpClient
from .http_decorators import unwrap
from .logging_config import log_isolated_failure
from .matching import normalize
from .models import PaperPDFResult, PDFProviderResult, PDFResolutionResult
from .paper_views import bibliographic_view
from .pdf_resource_requests import PublicResourceRequests
from .pdf_search_status import SEARCH_COMPLETION_DEFINITION, paper_search_complete
from .performance_diagnostics import pdf_failure_reasons
from .status import AccessStatus, IdentityStatus, OwnershipStatus, PDFValidationStatus, ProviderStatus

logger = logging.getLogger(__name__)

# Probe failures that say nothing about the URL itself; another probe later may succeed.
TRANSIENT_ERRORS = frozenset({'source_deferred', 'timeout', 'network_error', 'server_error', 'rate_limited'})


class PDFResolver:
    """Run every PDF source for each eligible paper and keep only validated candidates.

    ``requests`` is the shared public-resource scheduler the providers and validator use; when given, its
    probe statistics are reported. Build the full source set with ``composition.ServiceContainer``.
    ``fallbacks`` are costly sources asked only for verified papers no other source found a PDF for (``_ask_fallbacks``).
    """

    def __init__(
        self,
        providers,
        validator,
        requests: PublicResourceRequests | None = None,
        config=DEFAULT_PDF,
        sleep=time.sleep,
        fallbacks=(),
    ):
        self.providers = list(providers)
        self.fallbacks = list(fallbacks)
        self.validator = validator
        self.resource_requests = requests
        self.config = config
        self.sleep = sleep
        self.preparation_errors: dict[str, Any] = {}
        # Candidates as the sources returned them, before probing, so a retry starts from a clean record.
        self._unprobed: dict[int, list[Any]] = {}
        self._unprobed_lock = threading.Lock()
        self.retry_statistics: dict[str, Any] = {}

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
        self._prepare_providers(selected, saved_result.get('profile'))
        jobs: list[tuple[dict[str, Any], PaperPDFResult]] = []
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
        self._retry_transient(jobs)
        self._ask_fallbacks(jobs)
        for result in results:
            result.failure_reasons = pdf_failure_reasons(asdict(result))
        return PDFResolutionResult(results, self._statistics(results))

    def _run_lookups(self, jobs, work=None):
        """Resolve papers concurrently (sources keep their own per-host pacing); yield the running count."""
        work = work or self._resolve_isolated
        workers = min(self.config.lookup_workers, len(jobs))
        if workers <= 1:
            for processed, job in enumerate(jobs, 1):
                work(*job)
                yield processed
            return
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pdf-lookup') as pool:
            futures = [pool.submit(work, *job) for job in jobs]
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

    def _retry_transient(self, jobs):
        """Probe again, once host cooldowns expire, the identity-bound links that failed only transiently.

        Only papers still without a verified link take part; a definitive answer (403, 404, HTML) is final.
        """
        stats = {'rounds': 0, 'waited_seconds': 0.0, 'retried_links': 0, 'recovered_papers': 0}
        self.retry_statistics = stats
        pending = self._transient_jobs(jobs)
        for _ in range(self.config.transient_retry_rounds):
            if not pending:
                break
            hosts = {
                self._failed_host(result.pdf_candidates[index]) for _, result, indexes in pending for index in indexes
            }
            wait = self.resource_requests.cooldown_remaining(hosts) if self.resource_requests else 0.0
            wait = min(wait, self.config.transient_retry_maximum_wait)
            if wait > 0:
                self.sleep(wait)
            stats['rounds'] += 1
            stats['waited_seconds'] += wait
            stats['retried_links'] += sum(len(indexes) for _, _, indexes in pending)
            for _ in self._run_lookups(pending, self._retry_isolated):
                pass
            stats['recovered_papers'] += sum(
                result.validation_status == PDFValidationStatus.VERIFIED for _, result, _ in pending
            )
            pending = self._transient_jobs([(paper, result) for paper, result, _ in pending])
        self._unprobed.clear()

    def _transient_jobs(self, jobs):
        pending = []
        for paper, result in jobs:
            if result.validation_status == PDFValidationStatus.VERIFIED or id(result) not in self._unprobed:
                continue
            indexes = [
                index
                for index, candidate in enumerate(result.pdf_candidates)
                if candidate.identity_status == IdentityStatus.VERIFIED
                and candidate.query_error_type in TRANSIENT_ERRORS
            ]
            if indexes:
                pending.append((paper, result, indexes))
        return pending

    @staticmethod
    def _failed_host(candidate):
        return (candidate.request_context or {}).get('endpoint_host') or urlsplit(candidate.url).hostname or ''

    def _retry_isolated(self, paper, result, indexes):
        try:
            for index in indexes:
                stale = result.pdf_candidates[index]
                self._forget(result, stale.url)
                fresh = deepcopy(self._unprobed[id(result)][index])
                result.pdf_candidates[index] = fresh
                self._validate(fresh, paper, result)
            self._settle(result)
        except Exception as exc:
            log_isolated_failure(
                logger, 'PDF link retry failed for %s: %s', paper.get('doi') or paper.get('title'), exc
            )

    @staticmethod
    def _forget(result, url):
        """Drop the probe outcome recorded for this URL; the retry records its own."""
        result.warnings = [
            w for w in result.warnings if not (w.get('code') == 'source_deferred' and w.get('url') == url)
        ]
        result.error_details = [e for e in result.error_details if not ('error_type' in e and e.get('url') == url)]
        result.candidate_decisions = [d for d in result.candidate_decisions if d.get('url') != url]

    def _ask_fallbacks(self, jobs):
        """Ask each fallback once per work still without a PDF, newest first, up to the configured number of works.

        Entries sharing a title are editions of one work, so the answer serves them all, and a work with a PDF under
        any of them is not asked about.
        """
        if not self.fallbacks:
            return
        covered = {
            normalize(paper.get('title') or '')
            for paper, result in jobs
            if result.validation_status == PDFValidationStatus.VERIFIED
        }
        works: dict[str, list[Any]] = {}
        for paper, result in jobs:
            title = normalize(paper.get('title') or '')
            if title and title not in covered and result.ownership_status == OwnershipStatus.VERIFIED:
                works.setdefault(title, []).append((paper, result))
        newest = sorted(works.values(), key=lambda work: max(paper.get('year') or 0 for paper, _ in work), reverse=True)
        selected = [(work,) for work in newest[: self.config.web_search_maximum_papers]]
        for _ in self._run_lookups(selected, self._ask_about_work):
            pass

    def _ask_about_work(self, work):
        for provider in self.fallbacks:
            try:
                found = provider.find(work[0][0])
            except Exception as exc:
                log_isolated_failure(logger, 'PDF fallback %s failed: %s', provider.name, exc)
                found = PDFProviderResult(provider.name, status=ProviderStatus.ERROR, errors=[str(exc)])
            for paper, result in work:
                self._record_provider(result, found)
                known = {urldefrag(candidate.url)[0] for candidate in result.pdf_candidates}
                fresh = [item for item in self._merge_urls(found.candidates) if urldefrag(item.url)[0] not in known]
                result.pdf_candidates.extend(fresh)
                for candidate in fresh:
                    self._validate(candidate, paper, result)
                self._settle(result)

    def _prepare_providers(self, papers, profile=None):
        """Each provider that prepares sees the person's resolved profile (prepare_profile), then the papers."""
        self.preparation_errors = {}
        for provider in self.providers:
            try:
                if callable(getattr(type(provider), 'prepare_profile', None)):
                    provider.prepare_profile(profile or {})
                if callable(getattr(type(provider), 'prepare', None)):
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
            self._record_provider(result, found)
            candidates.extend(found.candidates)
            lookup_paper['_pdf_landing_pages'].extend(found.landing_pages)
        result.pdf_candidates = self._merge_urls(candidates)
        with self._unprobed_lock:
            self._unprobed[id(result)] = deepcopy(result.pdf_candidates)
        for candidate in result.pdf_candidates:
            self._validate(candidate, paper, result)
        self._settle(result)

    @staticmethod
    def _record_provider(result, found):
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

    def _validate(self, candidate, paper, result):
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

    def _settle(self, result):
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
        versions: dict[str, Any] = {}
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
        counts: dict[str, Any] = {
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
        sources: dict[str, Any] = {}
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
        counts['transient_retry'] = dict(self.retry_statistics)
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
