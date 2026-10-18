"""Hard spending cap for metered APIs: estimate each request's price before sending, refuse past the cap.

Place it inside the response cache (cache -> budget -> client) so cached replays cost nothing.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlsplit

from .http import SourceError


@dataclass(frozen=True)
class MeteredHost:
    """Per-request prices in USD; defaults follow OpenAlex's published list (verify on its pricing page)."""

    host: str = 'api.openalex.org'
    search: float = 0.001  # any full-text search parameter
    listing: float = 0.0001  # filtered or paged list endpoints
    single: float = 0.0  # one record by ID


@dataclass(frozen=True)
class BudgetConfig:
    maximum_cost_usd: float = 0.5
    hosts: tuple = (MeteredHost(),)

    def __post_init__(self) -> None:
        if self.maximum_cost_usd < 0:
            raise ValueError('maximum_cost_usd must not be negative')


class BudgetExceeded(SourceError):
    def __init__(self, message):
        super().__init__(message, error_type='budget_exceeded')


@dataclass
class CostLedger:
    """Shared by every client in one run so the cap covers the whole run, not each request path."""

    maximum_cost_usd: float
    spent_usd: float = 0.0
    requests: Dict[str, int] = field(default_factory=dict)
    refused: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def charge(self, kind: str, cost: float) -> None:
        with self.lock:
            if self.spent_usd + cost > self.maximum_cost_usd + 1e-12:
                self.refused += 1
                raise BudgetExceeded(
                    f'Run budget of ${self.maximum_cost_usd:.2f} reached (spent '
                    f'${self.spent_usd:.4f}); request not sent'
                )
            self.spent_usd += cost
            self.requests[kind] = self.requests.get(kind, 0) + 1

    def summary(self) -> dict:
        return {
            'maximum_cost_usd': self.maximum_cost_usd,
            'estimated_cost_usd': round(self.spent_usd, 6),
            'metered_requests': dict(self.requests),
            'refused_requests': self.refused,
        }


_LEDGERS: Dict[str, CostLedger] = {}
_LEDGERS_LOCK = threading.Lock()


def shared_ledger(name: str, maximum_cost_usd: float) -> CostLedger:
    with _LEDGERS_LOCK:
        ledger = _LEDGERS.get(name)
        if ledger is None:
            ledger = _LEDGERS[name] = CostLedger(maximum_cost_usd)
        return ledger


def ledger_summary(name: str) -> Optional[dict]:
    ledger = _LEDGERS.get(name)
    return ledger.summary() if ledger else None


def classify(url: str, params: Optional[dict]) -> str:
    """'search', 'listing' or 'single' for a metered request."""
    params = params or {}
    if params.get('search') or '.search:' in str(params.get('filter') or ''):
        return 'search'
    path = urlsplit(url).path.rstrip('/')
    if params.get('filter') or params.get('cursor') or path.endswith(('/works', '/authors', '/institutions')):
        return 'listing'
    return 'single'


class BudgetedHttpClient:
    def __init__(self, client, ledger: CostLedger, config: Optional[BudgetConfig] = None) -> None:
        config = config or BudgetConfig()
        self.client = client
        self.ledger = ledger
        self.hosts = {host.host: host for host in config.hosts}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def get(self, url, params=None, headers=None, xml=False):
        host = self.hosts.get(urlsplit(url).hostname or '')
        if host is not None:
            kind = classify(url, params)
            self.ledger.charge(f'{host.host}:{kind}', getattr(host, kind))
        return self.client.get(url, params, headers, xml)


def budgeted_client(client, maximum_cost_usd: Optional[float], ledger_name: str = 'default'):
    if maximum_cost_usd is None:
        return client
    return BudgetedHttpClient(
        client, shared_ledger(ledger_name, maximum_cost_usd), BudgetConfig(maximum_cost_usd=maximum_cost_usd)
    )
