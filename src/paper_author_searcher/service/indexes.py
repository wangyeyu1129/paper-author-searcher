"""Keep the local indexes current: the DBLP dump index and the venue catalog store.

The DBLP dump (about 1 GB, updated by dblp.org every few days) is fetched with ``If-Modified-Since``, so an
unchanged dump is neither downloaded nor reindexed. A new index is built beside the live one and swapped in with
an atomic rename: searches that already opened the old file keep reading it, new ones get the new file. The
catalog indexer updates its SQLite store in transactions, so it runs in place.
"""

import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.dblp_dump import DblpDumpIndexer
from paper_author_searcher.settings import Settings

logger = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024
USER_AGENT = 'paper-author-searcher index refresh'


@dataclass(frozen=True)
class RefreshReport:
    index: str
    changed: bool
    detail: dict[str, Any]


class IndexRefresher:
    def __init__(
        self,
        settings: Settings,
        downloads: Path,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 600,
    ) -> None:
        self.settings = settings
        self.downloads = Path(downloads)
        self.opener = opener
        self.timeout = timeout

    def refresh(self, which: tuple[str, ...] = ('dblp', 'catalogs')) -> list[RefreshReport]:
        reports = []
        if 'dblp' in which:
            reports.append(self.refresh_dblp())
        if 'catalogs' in which:
            reports.append(self.refresh_catalogs())
        return reports

    def refresh_dblp(self) -> RefreshReport:
        index = self.settings.dblp_dump.index_path
        if not index:
            return RefreshReport('dblp', False, {'skipped': 'dblp_dump.index_path is not set'})
        dump = self.downloads / 'dblp.xml.gz'
        downloaded = self._download(self.settings.service.dblp_dump_url, dump)
        if not downloaded and Path(index).is_file():
            return RefreshReport('dblp', False, {'unchanged': self.settings.service.dblp_dump_url})
        target = Path(index)
        target.parent.mkdir(parents=True, exist_ok=True)
        building = target.with_name(target.name + '.building')
        building.unlink(missing_ok=True)
        started = time.monotonic()
        statistics = DblpDumpIndexer(self.settings.dblp_dump).build(dump, building)
        os.replace(building, target)
        logger.info('DBLP index rebuilt in %.0f s', time.monotonic() - started)
        return RefreshReport('dblp', True, asdict(statistics))

    def refresh_catalogs(self) -> RefreshReport:
        if not self.settings.catalogs.store_path:
            return RefreshReport('catalogs', False, {'skipped': 'catalogs.store_path is not set'})
        report = ServiceContainer(self.settings, RunOptions(mode='metadata')).catalog_indexer().build()
        return RefreshReport('catalogs', True, asdict(report))

    def _download(self, url: str, destination: Path) -> bool:
        """Fetch ``url`` into ``destination`` unless the server says it has not changed; True if fetched."""
        meta_path = destination.with_name(destination.name + '.meta.json')
        meta = json.loads(meta_path.read_text()) if meta_path.exists() and destination.exists() else {}
        headers = {'User-Agent': USER_AGENT}
        if meta.get('last_modified'):
            headers['If-Modified-Since'] = meta['last_modified']
        request = urllib.request.Request(url, headers=headers)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + '.part')
        try:
            with self.opener(request, timeout=self.timeout) as response, partial.open('wb') as handle:
                shutil.copyfileobj(response, handle, CHUNK)
                last_modified = response.headers.get('Last-Modified')
        except urllib.error.HTTPError as error:
            partial.unlink(missing_ok=True)
            if error.code == HTTPStatus.NOT_MODIFIED:
                logger.info('%s unchanged since %s', url, meta.get('last_modified'))
                return False
            raise
        os.replace(partial, destination)
        meta_path.write_text(json.dumps({'last_modified': last_modified, 'url': url}))
        return True


def refresh_forever(refresher: IndexRefresher, every_hours: float, which: tuple[str, ...]) -> None:
    """Refresh now, then every ``every_hours``; a failed refresh is logged and retried at the next turn."""
    while True:
        try:
            for report in refresher.refresh(which):
                logger.warning('Index %s: %s', report.index, 'rebuilt' if report.changed else 'unchanged')
        except Exception:
            logger.exception('Index refresh failed; retrying in %.1f h', every_hours)
        time.sleep(every_hours * 3600)
