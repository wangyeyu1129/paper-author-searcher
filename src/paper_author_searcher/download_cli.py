"""Download from saved PDF Resolver JSON without author or paper API searches."""

import argparse
import json
import logging
import math
from pathlib import Path
from typing import Optional, Sequence

from .composition import RunOptions, ServiceContainer
from .logging_config import add_logging_arguments, configure_from_arguments
from .settings import add_settings_argument, load_settings

logger = logging.getLogger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Safely download and parse PDFs from a saved URL report')
    add_settings_argument(parser)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument(
        '--max-downloads', type=int, help='Maximum eligible paper attempts in this run; completed files do not count'
    )
    parser.add_argument('--timeout', type=float, help='HTTP timeout in seconds')
    parser.add_argument('--retries', type=int, help='HTTP retry count; interrupted transfers restart safely')
    parser.add_argument('--max-file-mb', type=float, help='Single-file size limit in MiB')
    parser.add_argument(
        '--include-needs-review', action='store_true', help='Allow needs_review ownership; URL must still be verified'
    )
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    if args.input.resolve() == (args.output_dir / 'manifest.json').resolve():
        parser.error('Input must differ from the output manifest')
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('--timeout must be positive')
    if args.retries is not None and args.retries < 0:
        parser.error('--retries must be nonnegative')
    if args.max_file_mb is not None and (not math.isfinite(args.max_file_mb) or args.max_file_mb <= 0):
        parser.error('--max-file-mb must be finite and positive')
    try:
        options = RunOptions(timeout=args.timeout, retries=args.retries, max_file_mb=args.max_file_mb)
        container = ServiceContainer(load_settings(args), options)
        with args.input.open(encoding='utf-8') as handle:
            saved = json.load(handle)
        if not isinstance(saved, dict):
            raise ValueError('Input must be a JSON object')
        downloader = container.downloader
        manifest = downloader.download(saved, args.output_dir, args.include_needs_review, args.max_downloads)
    except KeyboardInterrupt:
        logger.warning('Interrupted; rerun to reuse completed files. Manifest: %s', args.output_dir / 'manifest.json')
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    statistics = manifest['statistics']
    print(
        'Input: {total_input_papers}; attempted: {attempted_papers}; new files: {downloaded_files}; '
        'verified: {verified_papers}; needs review: {needs_review_papers}; failed: {failed_papers}'.format(**statistics)
    )
    print(
        'Reused: {reused_existing_papers}; content duplicates: {duplicate_content_papers}; '
        'skipped: {skipped_papers}'.format(**statistics)
    )
    print('Manifest: {}'.format(args.output_dir / 'manifest.json'))
    if statistics['failed_papers'] or statistics['needs_review_papers']:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
