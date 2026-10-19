"""Stage progress callbacks for long-running pipeline steps."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Logs stage changes and every 10% of progress (every step when verbose)."""

    def __init__(self, verbose: bool = False, sink: Optional[logging.Logger] = None) -> None:
        self.verbose = verbose
        self.sink = sink or logger
        self.stage: Optional[str] = None
        self.bucket = -1

    def __call__(self, stage: str, completed: Optional[int] = None, total: Optional[int] = None) -> None:
        new_stage = stage != self.stage
        bucket = int(10 * completed / total) if completed is not None and total else -1
        if new_stage or self.verbose or bucket > self.bucket:
            suffix = f' [{completed}/{total}]' if completed is not None else ''
            self.sink.info('%s%s', stage, suffix)
        self.stage = stage
        self.bucket = bucket
