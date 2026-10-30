"""The search service: HTTP front end, job queue and workers, result and file storage, index refresh.

Every field can be set as ``PAPER_SEARCH__SERVICE__<FIELD>``; object-store credentials come from
``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` (settings.Credentials), never from here.
"""

from dataclasses import dataclass

FILE_STORES = ('local', 's3')
S3_ADDRESSING = ('path', 'virtual')


@dataclass(frozen=True)
class ServiceConfig:
    host: str = '127.0.0.1'
    port: int = 8080
    # Jobs database, run directories, the local file store and the keys file live here unless set otherwise.
    data_directory: str = 'output/api'
    # API keys file (default: DATA_DIRECTORY/api_keys.json) and/or keys given as "id:name:sha256" entries,
    # e.g. from a secret store; the SHA-256 is of the key itself (paper-search-api create-key prints both).
    keys_file: str = ''
    api_key_hashes: tuple[str, ...] = ()
    # Worker threads inside `serve`; 0 leaves every search to separate `paper-search-api worker` processes.
    workers: int = 1
    # Searches pace requests per host within one run only: each worker adds proportionally more source traffic.
    maximum_cost_usd: float = 0.5
    maximum_body_bytes: int = 64 * 1024
    idempotency_hours: float = 24.0
    # Where checked PDFs, zips and identity checkpoints are kept: 'local' (DATA_DIRECTORY/files) or 's3'
    # (any S3-compatible store: AWS, MinIO, R2). Downloads from S3 are redirects to short-lived signed URLs.
    file_store: str = 'local'
    s3_endpoint: str = ''
    # Where callers reach the store, if not s3_endpoint (inside compose the service uses http://minio:9000, a
    # browser http://localhost:9000). Download links are signed for this host.
    s3_public_endpoint: str = ''
    s3_bucket: str = ''
    s3_region: str = 'us-east-1'
    s3_prefix: str = 'paper-search/'
    # 'path' (endpoint/bucket/key: MinIO and most compatible stores) or 'virtual' (bucket.endpoint/key: AWS).
    s3_addressing: str = 'path'
    presign_seconds: int = 900
    # HTTP response cache shared by all searches ('' disables it); mode as RunOptions.http_cache_mode.
    http_cache_directory: str = ''
    http_cache_mode: str = 'record'
    # A worker looks for queued jobs this often, reports it is alive this often, and a running job whose worker
    # has not reported for stale_after_seconds is queued again (the worker died; the search restarts).
    worker_poll_seconds: float = 2.0
    heartbeat_seconds: float = 15.0
    stale_after_seconds: float = 120.0
    # Run directories are scratch once their files are stored; keep them only to debug a run.
    keep_run_directories: bool = False
    # Readiness also requires the DBLP index and catalog store (dblp_dump.index_path, catalogs.store_path).
    require_indexes: bool = False
    # Index refresh (paper-search-api refresh-indexes): the dump to download and how often to rebuild.
    dblp_dump_url: str = 'https://dblp.org/xml/dblp.xml.gz'
    refresh_hours: float = 168.0
    # Agent contract (GET /v1/searches/{id}/agent): a verified paper is 'high' confidence when one of these
    # ownership findings backs it (the person's own list, a paper-level ORCID, a curated DBLP profile), otherwise
    # 'medium' (author ID with matching byline and institution, verified coauthors). Needs-review papers are
    # withheld. Each paper carries at most maximum_paper_evidence of its findings.
    strong_ownership_evidence: tuple[str, ...] = (
        "Listed on the researcher's own publication list",
        "Retrieved because the researcher's own publication list names this work",
        'Target authorship ORCID agrees with confirmed professor ORCID',
        "Curated dblp author profile carrying the person's ORCID",
        'Curated dblp author profile whose own affiliation record placed the person',
    )
    supporting_ownership_evidence: tuple[str, ...] = (
        'Explicit target Author ID, compatible byline and paper-level institution agree',
        'Compatible signature plus recurring independently verified coauthors',
        'Cross-source stable work identity and confirmed source-scoped target Author IDs',
    )
    maximum_paper_evidence: int = 3

    def __post_init__(self) -> None:
        if self.file_store not in FILE_STORES:
            raise ValueError(f'file_store must be one of {", ".join(FILE_STORES)}')
        if self.s3_addressing not in S3_ADDRESSING:
            raise ValueError(f's3_addressing must be one of {", ".join(S3_ADDRESSING)}')
        if self.file_store == 's3' and not (self.s3_endpoint and self.s3_bucket):
            raise ValueError('file_store s3 needs s3_endpoint and s3_bucket')
        if self.workers < 0 or not 0 < self.port < 65536:
            raise ValueError('workers must not be negative and port must be a TCP port')
        for name in (
            'maximum_cost_usd',
            'idempotency_hours',
            'worker_poll_seconds',
            'heartbeat_seconds',
            'refresh_hours',
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError('stale_after_seconds must exceed heartbeat_seconds')
        for entry in self.api_key_hashes:
            if entry.count(':') != 2:
                raise ValueError('api_key_hashes entries look like id:name:sha256')


DEFAULT_SERVICE = ServiceConfig()
