"""Opt-in live smoke check driven by user input, without fixed author IDs."""
import argparse
import json

from paper_author_searcher import ProfessorQuery, ProfessorResolver
from paper_author_searcher.cli import summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check live resolver output invariants")
    parser.add_argument('--name', required=True)
    parser.add_argument('--university', required=True)
    parser.add_argument('--department')
    parser.add_argument('--research', action='append', default=[])
    parser.add_argument('--university-alias', action='append', default=[])
    args = parser.parse_args(argv)
    query = ProfessorQuery(
        name=args.name,
        university=args.university,
        department=args.department,
        research_interests=args.research,
        university_aliases=args.university_alias,
    )
    result = ProfessorResolver().resolve(query)
    print(json.dumps(summary(result), ensure_ascii=False, indent=2))
    if result.profile is None:
        raise RuntimeError('No live candidate returned; inspect source_errors')
    for candidate in result.candidates:
        assert 0 <= candidate.identity_confidence <= 1
        assert candidate.author_ids
        if candidate.university_status != 'matched':
            assert candidate.status == 'needs_review'
        if candidate.status == 'resolved':
            assert candidate.university_status == 'matched'
    # These checks validate behavior, not the ground truth of a person's identity.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
