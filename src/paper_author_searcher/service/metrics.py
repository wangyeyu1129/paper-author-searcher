"""Prometheus text exposition of the service's state (``GET /metrics``).

Job counters come from the jobs database, so every API replica reports the same totals; request counters are per
process. Alert rules over these series are in ``deploy/prometheus/alerts.yml``.
"""

import threading
from collections import Counter
from typing import Any

PREFIX = 'paper_search'


class RequestCounter:
    """Requests answered by this process, by route and status code."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, int]] = Counter()
        self._lock = threading.Lock()

    def add(self, route: str, status: int) -> None:
        with self._lock:
            self._counts[(route, status)] += 1

    def items(self) -> list[tuple[tuple[str, int], int]]:
        with self._lock:
            return sorted(self._counts.items())


def _line(name: str, value: float, labels: dict[str, Any] | None = None) -> str:
    rendered = ','.join(f'{key}="{value}"' for key, value in (labels or {}).items())
    return f'{PREFIX}_{name}{{{rendered}}} {value}' if rendered else f'{PREFIX}_{name} {value}'


def render(snapshot: dict[str, Any], requests: RequestCounter | None = None) -> str:
    lines = [
        f'# HELP {PREFIX}_jobs Searches by current status.',
        f'# TYPE {PREFIX}_jobs gauge',
        *(_line('jobs', count, {'status': status}) for status, count in sorted(snapshot['by_status'].items())),
        f'# HELP {PREFIX}_job_runs_finished_total Runs that ended, by outcome.',
        f'# TYPE {PREFIX}_job_runs_finished_total counter',
        *(
            _line('job_runs_finished_total', entry['count'], {'status': status})
            for status, entry in sorted(snapshot['finished'].items())
        ),
        f'# HELP {PREFIX}_job_run_seconds_total Time spent by runs that ended, by outcome.',
        f'# TYPE {PREFIX}_job_run_seconds_total counter',
        *(
            _line('job_run_seconds_total', round(entry['seconds'], 3), {'status': status})
            for status, entry in sorted(snapshot['finished'].items())
        ),
        f'# HELP {PREFIX}_queue_oldest_seconds Age of the oldest queued search.',
        f'# TYPE {PREFIX}_queue_oldest_seconds gauge',
        _line('queue_oldest_seconds', round(snapshot['oldest_queued_seconds'], 3)),
        f'# HELP {PREFIX}_workers_alive Workers that reported recently.',
        f'# TYPE {PREFIX}_workers_alive gauge',
        _line('workers_alive', snapshot['workers_alive']),
        f'# HELP {PREFIX}_cost_usd_total Estimated metered-API spend of all searches.',
        f'# TYPE {PREFIX}_cost_usd_total counter',
        _line('cost_usd_total', round(snapshot['cost_usd'], 6)),
    ]
    if requests is not None:
        lines += [
            f'# HELP {PREFIX}_http_requests_total Requests answered by this process.',
            f'# TYPE {PREFIX}_http_requests_total counter',
            *(
                _line('http_requests_total', count, {'route': route, 'code': status})
                for (route, status), count in requests.items()
            ),
        ]
    return '\n'.join(lines) + '\n'
