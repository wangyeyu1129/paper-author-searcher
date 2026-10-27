"""Build the DBLP dump index: ``paper-dblp-index --dump dblp.xml.gz`` (download from https://dblp.org/xml/)."""

import argparse
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from .dblp_dump import DblpDumpIndexer
from .logging_config import add_logging_arguments, configure_from_arguments
from .settings import add_settings_argument, load_settings

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Index the DBLP XML dump for the DBLP sources')
    parser.add_argument('--dump', type=Path, required=True, help='dblp.xml or dblp.xml.gz')
    parser.add_argument('--index', type=Path, required=True, help='Index file to write (then set dblp_dump.index_path)')
    add_settings_argument(parser)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    settings = load_settings(args)
    index = args.index
    started = time.monotonic()
    statistics = DblpDumpIndexer(settings.dblp_dump).build(args.dump, index)
    logger.info('Indexed %s in %.0f s', args.dump, time.monotonic() - started)
    print(json.dumps({'index': str(index), **asdict(statistics)}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
