"""The affiliations an ORCID record states: who employs or educates the person, in which unit, and until when.

The record is kept by the person (or their institution), so it is curated evidence: identity discovery uses it to
confirm a registry match, and byline disambiguation to tell namesakes apart.
"""

from datetime import date
from typing import Any

from .config import DEFAULT_SOURCES, SourceConfig
from .models import CuratedPosition

ORCID_JSON = {'Accept': 'application/json'}


class OrcidAffiliations:
    def __init__(
        self,
        client: Any,
        source_config: SourceConfig = DEFAULT_SOURCES,
        sections: tuple[str, ...] = ('employments', 'educations'),
        current_year: int | None = None,
    ) -> None:
        self.client = client
        self.base_url = source_config.orcid_api_url.rstrip('/')
        self.sections = sections
        self.current_year = current_year or date.today().year

    def summaries(self, orcid: str) -> list[dict]:
        """Every summary of the configured sections (employment-summary, education-summary...)."""
        found: list[dict] = []
        for section in self.sections:
            data = self.client.get(f'{self.base_url}/{orcid}/{section}', None, ORCID_JSON)
            for group in (data or {}).get('affiliation-group') or []:
                for summary in group.get('summaries') or []:
                    found.extend(value for value in summary.values() if isinstance(value, dict))
        return found

    def current(self, summary: dict) -> bool:
        """No end year, or one not yet past."""
        end = ((summary.get('end-date') or {}).get('year') or {}).get('value')
        return not end or int(end) >= self.current_year

    def positions(self, orcid: str) -> list[CuratedPosition]:
        positions = []
        for summary in self.summaries(orcid):
            organization = (summary.get('organization') or {}).get('name')
            if not organization:
                continue
            department = (summary.get('department-name') or '').strip()
            positions.append(
                CuratedPosition(
                    'orcid',
                    (organization,),
                    (department,) if department else (),
                    current=self.current(summary),
                )
            )
        return positions
