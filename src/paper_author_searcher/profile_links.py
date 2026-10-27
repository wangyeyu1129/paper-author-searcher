"""Turn caller-supplied profile links into source identifiers.

A link to the person's own profile (ORCID, OpenAlex, Semantic Scholar, DBLP, OpenReview, Google Scholar) names them
exactly, so the namesake problem disappears for that source. Patterns live in ``ProfileLinkConfig``; adding a source
is a configuration change.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote

from .config import DEFAULT_PROFILE_LINKS, ProfileLinkConfig
from .matching import canonical_orcid

ORCID_SOURCE = 'orcid'


@dataclass(frozen=True, order=True)
class ProfileLink:
    source: str
    identifier: str
    url: str


class ProfileLinkParser:
    """Each configured pattern is tried in order; the first that matches a link names its source."""

    def __init__(self, config: ProfileLinkConfig = DEFAULT_PROFILE_LINKS) -> None:
        self.patterns = [(source, re.compile(pattern, re.IGNORECASE)) for source, pattern in config.patterns]
        for source, pattern in self.patterns:
            if 'id' not in pattern.groupindex:
                raise ValueError(f'Profile link pattern for {source!r} needs an (?P<id>...) group')

    def parse(self, url: str) -> ProfileLink | None:
        text = unquote(url.strip())
        for source, pattern in self.patterns:
            match = pattern.search(text)
            if match:
                identifier = match.group('id')
                if source == ORCID_SOURCE:
                    identifier = canonical_orcid(identifier) or identifier
                return ProfileLink(source, identifier, url.strip())
        return None

    def parse_all(self, urls: Iterable[str]) -> list[ProfileLink]:
        return sorted({link for link in map(self.parse, urls) if link is not None})
