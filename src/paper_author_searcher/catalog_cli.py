"""Build the venue and repository catalog store: ``paper-catalog-index`` (sources from the ``catalogs`` settings)."""

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, replace

from .composition import RunOptions, ServiceContainer
from .logging_config import add_logging_arguments, configure_from_arguments
from .settings import add_settings_argument, load_settings

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Index ACL Anthology, PMLR, CVF and OAI-PMH repository listings')
    parser.add_argument('--store', help='Store file (default: catalogs.store_path)')
    parser.add_argument('--source', action='append', default=[], help='Index only this source (repeatable)')
    add_settings_argument(parser)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    settings = load_settings(args)
    catalogs = replace(settings.catalogs, store_path=args.store or settings.catalogs.store_path)
    if args.source:
        catalogs = replace(catalogs, sources=tuple(args.source))
    settings = replace(settings, catalogs=catalogs)
    report = ServiceContainer(settings, RunOptions(mode='metadata')).catalog_indexer().build()
    print(json.dumps({'store': settings.catalogs.store_path, **asdict(report)}, indent=2, sort_keys=True))
    return 1 if report.errors else 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
