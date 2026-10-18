"""Optional LLM adjudication for the few identity contests that structured evidence leaves open.

The adjudicator only sees evidence the resolver already gathered (names, affiliations, topics, coauthors, ORCIDs,
known DOIs). Its verdict can merge split profiles or break a tie between profiles; it never verifies a paper.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence

from .logging_config import log_isolated_failure

logger = logging.getLogger(__name__)

ADJUDICATION_SCHEMA = {
    'type': 'object',
    'properties': {
        'same_person': {'type': 'string', 'enum': ['yes', 'no', 'unsure']},
        'target': {'type': 'string', 'enum': ['first', 'second', 'both', 'neither', 'unsure']},
        'confidence': {'type': 'number'},
        'rationale': {'type': 'string'},
    },
    'required': ['same_person', 'target', 'confidence', 'rationale'],
    'additionalProperties': False,
}

SYSTEM_PROMPT = (
    'You compare scholarly author profiles from bibliographic databases. Databases often split one researcher '
    'into several profiles, and different researchers often share a name. Decide from the evidence given whether '
    'two profiles are the same person, and which of them is the requested researcher. Weigh shared coauthors, '
    "institutions, research topics and identifiers; a shared name alone is not evidence. Answer 'unsure' when the "
    "evidence does not settle it - a wrong merge attributes someone else's papers to the researcher. Confidence is "
    "your probability (0-1) that your 'target' answer is correct."
)


@dataclass
class AdjudicationVerdict:
    same_person: str
    target: str
    confidence: float
    rationale: str
    model: str = ''
    errors: List[str] = field(default_factory=list)

    @classmethod
    def unsure(cls, reason: str) -> 'AdjudicationVerdict':
        return cls('unsure', 'unsure', 0.0, reason, errors=[reason])


class IdentityAdjudicator(Protocol):
    def judge(self, query, first: Sequence, second: Sequence) -> AdjudicationVerdict: ...


def describe(records: Sequence, limit: int) -> dict:
    """Compact, deterministic evidence summary of one candidate group (a list of AuthorCandidate)."""

    def take(values):
        return sorted({value for value in values if value})[:limit]

    return {
        'ids': sorted(f'{record.source}:{record.author_id}' for record in records),
        'names': take(name for record in records for name in [record.name, *list(record.aliases)]),
        'affiliations': take(item for record in records for item in record.affiliations + record.current_affiliations),
        'orcids': take(record.orcid for record in records),
        'topics': take(item for record in records for item in record.topics),
        'coauthors': take(item for record in records for item in record.coauthors),
        'dois': take(item for record in records for item in record.paper_ids),
    }


def prompt(query, first: Sequence, second: Sequence, limit: int) -> str:
    request = {
        'name': query.name,
        'university': query.university,
        'department': query.department,
        'research_interests': list(query.research_interests),
        'orcid': query.orcid,
    }
    sections = (
        ('Requested researcher', request),
        ('Profile "first"', describe(first, limit)),
        ('Profile "second"', describe(second, limit)),
    )
    return '\n\n'.join(f'{title}:\n{json.dumps(body, ensure_ascii=False, sort_keys=True)}' for title, body in sections)


class ClaudeIdentityAdjudicator:
    """Claude via the Anthropic SDK (optional extra ``llm``; needs Python >= 3.10 and Anthropic credentials)."""

    def __init__(
        self,
        client=None,
        model: str = 'claude-opus-5-5',
        effort: str = 'medium',
        evidence_limit: int = 40,
        max_tokens: int = 16000,
    ) -> None:
        if client is None:
            import anthropic  # deferred: the core package has no third-party dependencies

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.evidence_limit = evidence_limit
        self.max_tokens = max_tokens

    def judge(self, query, first, second) -> AdjudicationVerdict:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': prompt(query, first, second, self.evidence_limit)}],
                output_config={'effort': self.effort, 'format': {'type': 'json_schema', 'schema': ADJUDICATION_SCHEMA}},
                # Route policy declines to a fallback model instead of failing the call.
                betas=['server-side-fallback-2026-07-01'],
                fallbacks='default',
            )
        except Exception as exc:  # network, auth or API errors leave the contest unresolved
            log_isolated_failure(logger, 'Identity adjudication request failed: %s', type(exc).__name__)
            return AdjudicationVerdict.unsure(f'adjudication request failed: {type(exc).__name__}')
        if response.stop_reason in ('refusal', 'max_tokens'):
            return AdjudicationVerdict.unsure(f'adjudication stopped: {response.stop_reason}')
        text = next((block.text for block in response.content if block.type == 'text'), '')
        try:
            data = json.loads(text)
        except ValueError:
            return AdjudicationVerdict.unsure('adjudication returned invalid JSON')
        return AdjudicationVerdict(
            data['same_person'],
            data['target'],
            float(data['confidence']),
            data['rationale'],
            model=getattr(response, 'model', self.model),
        )


def accepted(verdict: Optional[AdjudicationVerdict], minimum_confidence: float) -> bool:
    return verdict is not None and not verdict.errors and verdict.confidence >= minimum_confidence
