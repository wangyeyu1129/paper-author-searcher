"""How much of each source's raw API payload a collection keeps (``collection.raw_metadata``).

``full`` keeps everything for audit and reanalysis. ``minimal`` keeps only the fields later stages read (PDF
locations and identifiers), so saved PDF sources need no new requests. ``none`` keeps nothing; the PDF stage then
queries the sources again.
"""

from typing import Any, Dict, Iterable, Mapping, Tuple

from .config import RawMetadataPolicy

# Fields of the raw payload that downstream stages read, per source.
RETAINED_FIELDS: Mapping[str, Tuple[str, ...]] = {
    'openalex': (
        'id',
        'doi',
        'title',
        'publication_year',
        'locations',
        'primary_location',
        'best_oa_location',
        'open_access',
    ),
    'semantic_scholar': ('paperId', 'externalIds', 'openAccessPdf', 'title', 'year', 'authors'),
}


def retained(source: str, raw: Mapping[str, Any], policy: RawMetadataPolicy) -> Dict[str, Any]:
    if policy is RawMetadataPolicy.FULL:
        return dict(raw)
    if policy is RawMetadataPolicy.NONE:
        return {}
    return {name: raw[name] for name in RETAINED_FIELDS.get(source, ()) if name in raw}


def apply_policy(records: Iterable[Any], policy: RawMetadataPolicy) -> None:
    """Trim ``raw_metadata`` in place on papers and their source records."""
    if policy is RawMetadataPolicy.FULL:
        return
    for record in records:
        if isinstance(record.raw_metadata, Mapping):
            record.raw_metadata = retained(record.source, record.raw_metadata, policy)
        apply_policy(getattr(record, 'records', ()), policy)
