"""Run the search system once per benchmark person and keep a resumable run index."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher
from paper_author_searcher.identity_anchors import AnchorFinder, RecordedAnchorFinder
from paper_author_searcher.jsonio import read_json, write_json
from paper_author_searcher.settings import load_settings
from paper_author_searcher.status import RunStatus
from paper_author_searcher.workflow import PaperSearchWorkflow

from .roster import BenchmarkPerson
from .scoring import load_truth
from .settings import BenchmarkSettings, RunSettings

logger = logging.getLogger(__name__)

FULL_MODE = 'full'
METADATA_MODE = 'metadata'

INDEX = 'index.json'
TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.IDENTITY_CONFIRMATION_REQUIRED, RunStatus.FAILED}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SearchSystem(Protocol):
    """Anything that turns a production-style input into a workflow report directory."""

    name: str

    def search(self, person: BenchmarkPerson, output_dir: Path) -> dict: ...


def window_archive(archive: dict, window: tuple[int, int]) -> dict:
    """Keep collected papers whose year falls in the window (or is unknown); decisions are untouched."""
    start, end = window
    # Copy only the containers that change; paper dicts are shared (nothing downstream mutates them).
    restricted = dict(archive)
    collection = restricted
    if isinstance(restricted.get('collection'), dict):
        collection = restricted['collection'] = dict(restricted['collection'])
    papers = collection.get('all_papers', collection.get('papers')) or []
    kept = [paper for paper in papers if paper.get('year') is None or start <= paper['year'] <= end]
    collection['all_papers'] = kept
    collection['papers'] = kept
    collection['benchmark_pdf_scope'] = {
        'year_start': start,
        'year_end': end,
        'kept': len(kept),
        'collected': len(papers),
    }
    return restricted


def truth_archive(archive: dict, truth: dict) -> dict:
    """Keep collected papers that match a truth entry (any label); decisions are untouched."""
    matcher = GroundTruthMatcher(GroundTruth.from_dict(dict(truth, complete_positive_list=False)))
    restricted = dict(archive)
    collection = restricted
    if isinstance(restricted.get('collection'), dict):
        collection = restricted['collection'] = dict(restricted['collection'])
    papers = collection.get('all_papers', collection.get('papers')) or []
    kept = [paper for paper in papers if matcher.match(paper).candidates]
    collection['all_papers'] = kept
    collection['papers'] = kept
    scope = dict(collection.get('benchmark_pdf_scope') or {})
    scope.update(truth_matched=len(kept))
    collection['benchmark_pdf_scope'] = scope
    return restricted


AnchorSource = Callable[[BenchmarkPerson], AnchorFinder | None]
WorkflowFactory = Callable[[RunSettings, AnchorFinder | None], PaperSearchWorkflow]


def default_workflow_factory(run: RunSettings, anchor_finder: AnchorFinder | None = None) -> PaperSearchWorkflow:
    """Production wiring: the service's own composition root, configured by the run settings."""
    return ServiceContainer(load_settings(run), RunOptions.from_namespace(run), anchor_finder=anchor_finder).workflow()


class RecordedRunAnchors:
    """An AnchorSource for offline replay: each person gets the anchors a saved run found for them."""

    def __init__(self, settings: BenchmarkSettings, run_id: str) -> None:
        self.root = settings.paths.runs / run_id
        self.index = load_index(settings, run_id)

    def __call__(self, person: BenchmarkPerson) -> AnchorFinder:
        entry = self.index['people'].get(person.person_id) or {}
        identity = self.root / entry['report'] / 'professor.json' if entry.get('report') else None
        if identity is None or not identity.exists():
            return RecordedAnchorFinder([])
        return RecordedAnchorFinder.from_identity(read_json(identity))


class WorkflowSearchSystem:
    """Adapter over the existing end-to-end workflow; it only ever sees the search input.

    In full mode the PDF stages run on a second pass restricted to the person's window (widened by the version
    tolerance), chosen from the system's own collection without looking at truth, so a senior researcher's whole
    career is not downloaded to measure two years of full-text coverage.
    """

    name = 'paper_search_workflow'

    def __init__(
        self,
        run: RunSettings,
        settings: BenchmarkSettings | None = None,
        workflow_factory: WorkflowFactory | None = None,
        anchor_source: AnchorSource | None = None,
    ) -> None:
        self.run_settings = run
        self.settings = settings
        self.workflow_factory = workflow_factory or default_workflow_factory
        self.anchor_source = anchor_source

    def search(self, person: BenchmarkPerson, output_dir: Path) -> dict:
        settings = self.settings
        windowed = self.run_settings.mode == FULL_MODE and settings is not None
        first_mode = METADATA_MODE if windowed else self.run_settings.mode
        with_hints = self.run_settings.input_profile == 'with_hints'
        anchor_finder = self.anchor_source(person) if self.anchor_source else None
        report = self._run(
            replace(self.run_settings, mode=first_mode),
            output_dir,
            anchor_finder,
            query=person.search_input.to_query(with_hints),
        )
        if settings is None or not windowed or not report.get('artifacts', {}).get('papers'):
            return report
        start, end = person.window(settings)
        tolerance = settings.version_grouping_year_tolerance
        archive = read_json(Path(report['output_directory']) / report['artifacts']['papers'])
        if report.get('identity'):
            archive.setdefault('identity_archive', report['identity'])
        scoped = window_archive(archive, (start - tolerance, end + tolerance))
        if self.run_settings.fulltext_scope == 'truth':
            truth = load_truth(settings, person.person_id)
            scoped = truth_archive(scoped, truth) if truth is not None else scoped
        fulltext = self._run(self.run_settings, Path(output_dir) / 'fulltext', anchor_finder, saved=scoped)
        report['fulltext_report'] = fulltext['output_directory']
        report['fulltext_status'] = fulltext.get('run_status')
        return report

    def _run(self, run: RunSettings, output_dir: Path, anchor_finder: AnchorFinder | None, **source: Any) -> dict:
        workflow = self.workflow_factory(run, anchor_finder)
        return workflow.run(
            output_dir=output_dir,
            mode=run.mode,
            include_needs_review=run.include_needs_review,
            maximum_pdf_papers=run.max_pdf_papers,
            maximum_downloads=run.max_downloads,
            **source,
        )


class BenchmarkRunner:
    def __init__(
        self, settings: BenchmarkSettings, system: SearchSystem, progress: Callable[[str], None] | None = None
    ) -> None:
        self.settings = settings
        self.system = system
        self.progress = progress or (lambda message: None)

    def run(
        self,
        people: Iterable[BenchmarkPerson],
        run_id: str,
        metadata: dict[str, object] | None = None,
        rerun: bool = False,
    ) -> dict:
        root = self.settings.paths.runs / run_id
        root.mkdir(parents=True, exist_ok=True)
        index_path = root / INDEX
        index = (
            read_json(index_path)
            if index_path.exists()
            else {
                'run_id': run_id,
                'dataset_id': self.settings.dataset_id,
                'system': self.system.name,
                'created_at': _now(),
                'run_settings': _public_settings(self.settings.run),
                'metadata': metadata or {},
                'people': {},
            }
        )
        people = list(people)
        for position, person in enumerate(people, 1):
            previous = index['people'].get(person.person_id) or {}
            if previous.get('status') in TERMINAL_STATUSES and not rerun:
                self.progress(
                    '[{}/{}] {} reused ({})'.format(position, len(people), person.person_id, previous['status'])
                )
                continue
            self.progress(f'[{position}/{len(people)}] {person.person_id} searching')
            entry: dict[str, Any] = {'started_at': _now(), 'status': RunStatus.RUNNING}
            index['people'][person.person_id] = entry
            write_json(index_path, index)
            try:
                report = self.system.search(person, root / person.person_id)
                entry['status'] = report.get('run_status', RunStatus.FAILED)
                entry['report'] = str(Path(report['output_directory']).resolve().relative_to(root.resolve()))
                if report.get('fulltext_report'):
                    entry['fulltext_report'] = str(
                        Path(report['fulltext_report']).resolve().relative_to(root.resolve())
                    )
                    entry['fulltext_status'] = report.get('fulltext_status')
                entry['error_count'] = len(report.get('errors') or [])
            except KeyboardInterrupt:
                entry['status'] = RunStatus.INTERRUPTED
                write_json(index_path, index)
                raise
            except Exception as exc:  # one person's failure must not stop the benchmark
                logger.exception('Benchmark search failed for %s', person.person_id)
                entry['status'] = RunStatus.FAILED
                entry['error'] = f'{type(exc).__name__}: {exc}'
            entry['finished_at'] = _now()
            write_json(index_path, index)
            self.progress('[{}/{}] {} {}'.format(position, len(people), person.person_id, entry['status']))
        index['updated_at'] = _now()
        index['api_budget'] = self.budget_summary()
        write_json(index_path, index)
        return index

    def budget_summary(self) -> dict | None:
        from paper_author_searcher.http_budget import ledger_summary

        return ledger_summary(self.settings.run.cost_ledger)


def _public_settings(run: RunSettings) -> dict:
    values = asdict(run)
    # The contact email is personal data; record only whether it was configured.
    values['unpaywall_email'] = bool(values.get('unpaywall_email'))
    return values


def load_index(settings: BenchmarkSettings, run_id: str) -> dict:
    return read_json(settings.paths.runs / run_id / INDEX)
