"""How DBLP prints names, titles and links, and their plain forms.

DBLP disambiguates homonyms with a four-digit suffix ("Yang Liu 0005") and ends every title with a period
("... Direct Preference Optimization."). Neither belongs to the person or the work: kept, they reach users, keep
DBLP's copy of a paper from matching other sources' copies, and make one person's name differ across sources.
"""

import re

from .matching import canonical_arxiv

HOMONYM_SUFFIX = re.compile(r'\s+\d{4}$')
ARXIV_LINK = re.compile(r'arxiv\.org/abs/([^\s?#]+)', re.IGNORECASE)


def base_name(name: str) -> str:
    """The printed name without DBLP's homonym number."""
    return HOMONYM_SUFFIX.sub('', name.strip())


def plain_title(title: str) -> str:
    """The title without DBLP's closing period; a question or exclamation mark is part of the title."""
    title = title.strip()
    return title[:-1].rstrip() if title.endswith('.') and not title.endswith('..') else title


def arxiv_ids(links: list[str]) -> list[str]:
    """Canonical arXiv identifiers among a record's electronic-edition links."""
    found = (canonical_arxiv(match.group(1)) for link in links for match in ARXIV_LINK.finditer(link))
    return list(dict.fromkeys(value for value in found if value))
