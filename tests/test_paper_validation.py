import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from paper_author_searcher.config import OwnershipConfig
from paper_author_searcher.listed_works import RETRIEVED_EVIDENCE
from paper_author_searcher.models import (
    AnchorEvidence,
    PaperCollectionResult,
    PaperRecord,
    PaperSourceResult,
    ProfessorProfile,
)
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.paper_ownership import PaperOwnershipValidator, record_fields
from paper_author_searcher.paper_reanalysis import reanalyze_saved_result
from paper_author_searcher.paper_types import classify_record
from paper_author_searcher.status import AnchorKind, OwnershipStatus


def profile():
    return ProfessorProfile(
        name='Alex Morgan',
        affiliations=['Example University'],
        author_ids={'openalex': 'author-a', 'semantic_scholar': 'author-b'},
        author_id_aliases={'openalex': ['author-a'], 'semantic_scholar': ['author-b']},
        identity_confidence=0.95,
        status='resolved',
        evidence=[],
        orcid='profile-orcid',
    )


def record(
    source='openalex',
    title='An interdisciplinary study',
    doi='10.example/study',
    orcid=None,
    name='Alex Morgan',
    publication_type='article',
):
    author_id = 'author-a' if source == 'openalex' else 'author-b'
    return PaperRecord(
        title=title,
        authors=[name, 'Jane Smith', 'John Green'],
        year=2024,
        doi=doi,
        venue='Example Journal',
        abstract=None,
        source=source,
        source_paper_id=source + ':' + title,
        source_author_id=author_id,
        paper_url=None,
        publication_type=publication_type,
        author_details=[{'author_id': author_id, 'name': name, 'orcid': orcid, 'orcid_scope': 'paper_authorship'}],
    )


def validate(records):
    papers = deduplicate_papers(records)
    return PaperOwnershipValidator().validate(papers, profile())


class Places:
    def __init__(self, known, on_byline):
        self.known = known
        self.on_byline = on_byline
        self.looked_up = []

    def history(self, _ids):
        return set(self.known)

    def on_bylines(self, dois, _is_target):
        self.looked_up = list(dois)
        return {doi: places for doi, places in self.on_byline.items() if doi in dois}


class NamesakeGroupTests(unittest.TestCase):
    def paper(self, title, coauthors, source='dblp', doi=None):
        name = 'Alex Morgan'
        author_id = '123/4433-1' if source == 'dblp' else 'A1'
        return PaperRecord(
            title=title,
            authors=[name, *coauthors],
            year=2024,
            doi=doi,
            venue='Venue',
            abstract=None,
            source=source,
            source_paper_id=title,
            source_author_id=author_id,
            paper_url=None,
            publication_type='article',
            author_details=[{'author_id': author_id, 'name': name, 'orcid': None, 'orcid_scope': 'paper_authorship'}],
        )

    def statuses(self, places, extra=(), **config):
        records = [
            self.paper('Main one', ('Jane Smith', 'Li Wei'), doi='10.1/m1'),
            self.paper('Main two', ('Jane Smith', 'Omar Hadi'), doi='10.1/m2'),
            self.paper('Main three', ('Li Wei', 'Omar Hadi'), doi='10.1/m3'),
            self.paper('Music one', ('Simon Dixon', 'Ann Lee'), doi='10.1/x1'),
            self.paper('Music two', ('Simon Dixon',)),
            *extra,
        ]
        target = replace(
            profile(),
            orcid=None,
            author_ids={'dblp': '123/4433-1', 'openalex': 'A1'},
            author_id_aliases={'dblp': ['123/4433-1'], 'openalex': ['A1']},
            identity_anchors=[AnchorEvidence('dblp', '123/4433-1', 'anchor', 'orcid')],
            curated_author_ids={'dblp': ['123/4433-1']},
        )
        papers = deduplicate_papers(records)
        PaperOwnershipValidator(OwnershipConfig(**config), places=places).validate(papers, target)
        return {paper.title: paper.ownership_status for paper in papers}

    def test_a_separate_group_at_foreign_institutions_is_held_whole(self):
        statuses = self.statuses(Places({'UIUC'}, {'10.1/x1': {'Queen Mary'}}))
        self.assertEqual(statuses['Music one'], OwnershipStatus.NEEDS_REVIEW)
        self.assertEqual(statuses['Music two'], OwnershipStatus.NEEDS_REVIEW)
        self.assertEqual(statuses['Main one'], OwnershipStatus.VERIFIED)

    def test_only_the_groups_outside_the_main_body_are_looked_up(self):
        places = Places({'UIUC'}, {})
        self.statuses(places)
        self.assertEqual(places.looked_up, ['10.1/x1'])

    def test_a_known_institution_keeps_the_group(self):
        statuses = self.statuses(Places({'UIUC'}, {'10.1/x1': {'UIUC', 'Queen Mary'}}))
        self.assertEqual(statuses['Music one'], OwnershipStatus.VERIFIED)

    def test_no_printed_institution_keeps_the_group(self):
        self.assertEqual(self.statuses(Places({'UIUC'}, {}))['Music one'], OwnershipStatus.VERIFIED)

    def test_no_known_history_keeps_the_group(self):
        statuses = self.statuses(Places(set(), {'10.1/x1': {'Queen Mary'}}))
        self.assertEqual(statuses['Music one'], OwnershipStatus.VERIFIED)

    def test_a_group_below_the_minimum_is_kept(self):
        statuses = self.statuses(Places({'UIUC'}, {'10.1/x1': {'Queen Mary'}}), namesake_group_minimum_papers=3)
        self.assertEqual(statuses['Music one'], OwnershipStatus.VERIFIED)

    def test_a_coauthor_linking_the_groups_keeps_them(self):
        bridge = self.paper('Bridge', ('Simon Dixon', 'Jane Smith'))
        statuses = self.statuses(Places({'UIUC'}, {'10.1/x1': {'Queen Mary'}}), extra=[bridge])
        self.assertEqual(statuses['Music one'], OwnershipStatus.VERIFIED)

    def test_a_group_retrieved_from_a_publication_list_is_held(self):
        listed = [
            replace(
                self.paper(title, ('Guillermina Lozano',), source='openalex', doi=doi),
                source_author_id=None,
                author_details=[],
                attribution_evidence=[RETRIEVED_EVIDENCE],
                ownership_evidence=[],
            )
            for title, doi in (('Stem one', '10.1/s1'), ('Stem two', '10.1/s2'))
        ]
        papers = deduplicate_papers(listed)
        validator = PaperOwnershipValidator(places=Places({'UIUC'}, {'10.1/s1': {'NCI'}}))
        for paper in papers:
            for record in paper.records:
                record.ownership_status = OwnershipStatus.VERIFIED
            validator._aggregate(paper)
        main = deduplicate_papers([self.paper('Main', ('Jane Smith', 'Li Wei'))])
        for record in main[0].records:
            record.ownership_status = OwnershipStatus.VERIFIED
        validator._aggregate(main[0])
        main.extend(deduplicate_papers([self.paper('Main again', ('Jane Smith',))]))
        for record in main[1].records:
            record.ownership_status = OwnershipStatus.VERIFIED
        validator._aggregate(main[1])
        target = replace(profile(), orcid=None, author_ids={'openalex': 'A1'}, author_id_aliases={'openalex': ['A1']})
        validator._hold_namesake_groups([*main, *papers], target)
        self.assertEqual(papers[0].ownership_status, OwnershipStatus.NEEDS_REVIEW)
        self.assertEqual(main[0].ownership_status, OwnershipStatus.VERIFIED)

    def test_a_consortium_paper_links_nobody(self):
        crowd = tuple(f'Person{letter} Crowd' for letter in 'abcdefghijklmn')
        bridge = self.paper('Consortium', ('Simon Dixon', 'Jane Smith', *crowd))
        statuses = self.statuses(Places({'UIUC'}, {'10.1/x1': {'Queen Mary'}}), extra=[bridge])
        self.assertEqual(statuses['Music one'], OwnershipStatus.NEEDS_REVIEW)


class CuratedProfileTests(unittest.TestCase):
    def paper(self, title, venue, coauthors=('Jane Smith',), author_id='123/4433', orcid=None):
        name = 'Alex Morgan'
        return PaperRecord(
            title=title,
            authors=[name, *coauthors],
            year=2024,
            doi=None,
            venue=venue,
            abstract=None,
            source='dblp',
            source_paper_id=title,
            source_author_id=author_id,
            paper_url=None,
            publication_type='article',
            author_details=[{'author_id': author_id, 'name': name, 'orcid': orcid, 'orcid_scope': 'paper_authorship'}],
        )

    def target(self, kind=None):
        anchors = [AnchorEvidence('dblp', '123/4433', 'anchor', kind)] if kind else []
        return replace(
            profile(),
            orcid=None,
            author_ids={'dblp': '123/4433'},
            author_id_aliases={'dblp': ['123/4433']},
            identity_anchors=anchors,
            curated_author_ids={'dblp': []},
        )

    def statuses(self, records, target=None, **config):
        papers = deduplicate_papers(records)
        PaperOwnershipValidator(OwnershipConfig(**config)).validate(papers, target or self.target())
        return {paper.title: paper.ownership_status for paper in papers}

    def anchored(self, count):
        return [
            self.paper(f'Proven {index}', 'NeurIPS', (f'Friend {index}', 'Regular Coauthor')) for index in range(count)
        ]

    def test_a_proven_profile_vouches_for_its_papers_in_the_persons_venues(self):
        # Paper-level ORCIDs prove these, not the profile, so the other papers have nothing else to rely on.
        proven = [
            self.paper(f'Proven {index}', 'NeurIPS', (f'Friend {index}', 'Advisor'), orcid='orcid')
            for index in range(4)
        ]
        preprints = [self.paper(f'Proven preprint {index}', 'CoRR', orcid='orcid') for index in range(3)]
        records = [
            *proven,
            *preprints,
            self.paper('New student work', 'NeurIPS', ('New Student', 'Friend 1')),
            self.paper('Namesake in the venue', 'NeurIPS', ('Stranger',)),
            self.paper('Elsewhere', 'Journal of Other Things', ('Friend 1',)),
            self.paper('Preprint', 'CoRR', ('Friend 2',)),
        ]
        statuses = self.statuses(records, replace(self.target(), orcid='orcid'))
        self.assertEqual(statuses['New student work'], 'verified')
        self.assertEqual(statuses['Namesake in the venue'], 'needs_review')
        self.assertEqual(statuses['Elsewhere'], 'needs_review')
        self.assertEqual(statuses['Preprint'], 'needs_review')

    def test_a_mostly_unproven_profile_vouches_for_nothing(self):
        records = [
            self.paper(f'Unproven {index}', 'NeurIPS', (f'Stranger {index}',), author_id='123/4433')
            for index in range(6)
        ]
        statuses = self.statuses(records)
        self.assertEqual(set(statuses.values()), {'needs_review'})

    def test_an_id_that_may_pool_namesakes_vouches_for_nothing(self):
        for shared in ('123/4433-1', 'w/WeiWang'):
            proven = [
                self.paper(f'Proven {index}', 'NeurIPS', (f'Friend {index}',), shared, 'orcid') for index in range(4)
            ]
            records = [*proven, self.paper('New student work', 'NeurIPS', ('Friend 1',), shared)]
            target = replace(
                self.target(), orcid='orcid', author_ids={'dblp': shared}, author_id_aliases={'dblp': [shared]}
            )
            self.assertEqual(self.statuses(records, target)['New student work'], 'needs_review')


class NonPublicationTitleTests(unittest.TestCase):
    def test_corrections_and_data_supplements_are_not_verified_even_with_a_paper_orcid(self):
        for title in (
            'Author Correction: Pan-cancer analysis',
            'Data from Loss of Regulation',
            'Erratum: Moral Hazard',
        ):
            paper = validate([record(title=title, orcid='https://orcid.org/profile-orcid')])[0]
            self.assertEqual(paper.ownership_status, 'needs_review', title)

    def test_a_paper_that_merely_mentions_data_is_not_caught(self):
        paper = validate([record(title='Big data from cities', orcid='https://orcid.org/profile-orcid')])[0]
        self.assertEqual(paper.ownership_status, 'verified')


class RareNameTests(unittest.TestCase):
    """Under a rare name, papers in the person's proven fields go through; another field's stay under review."""

    def fielded(self, title, field, orcid=None, source='openalex'):
        item = record(title=title, doi=None, orcid=orcid, source=source)
        # Alone on the byline: a shared coauthor would verify it by another rule.
        item.authors = item.authors[:1]
        item.raw_metadata = {'primary_topic': {'field': {'display_name': field}}} if field else {}
        return item

    def statuses(self, records, rare=True):
        rarity = SimpleNamespace(rare=lambda name, ids: rare)
        papers = PaperOwnershipValidator(name_rarity=rarity).validate(deduplicate_papers(records), profile())
        return {paper.title: paper.ownership_status for paper in papers}

    def proven(self, count=3, field='Economics'):
        return [
            self.fielded(f'Proven paper number {i}', field, orcid='https://orcid.org/profile-orcid')
            for i in range(count)
        ]

    def test_only_the_proven_field_is_freed(self):
        found = self.statuses(
            [
                *self.proven(),
                self.fielded('Consumption over the life cycle', 'Economics'),
                self.fielded('Training implicit scene representations', 'Computer Science'),
                self.fielded('A paper with no stated field at all', None),
            ]
        )
        self.assertEqual(found['Consumption over the life cycle'], 'verified')
        self.assertEqual(found['Training implicit scene representations'], 'needs_review')
        self.assertEqual(found['A paper with no stated field at all'], 'needs_review')

    def test_a_shared_name_frees_nothing(self):
        found = self.statuses(
            [*self.proven(), self.fielded('Consumption over the life cycle', 'Economics')], rare=False
        )
        self.assertEqual(found['Consumption over the life cycle'], 'needs_review')

    def test_too_few_proven_papers_free_nothing(self):
        found = self.statuses([*self.proven(2), self.fielded('Consumption over the life cycle', 'Economics')])
        self.assertEqual(found['Consumption over the life cycle'], 'needs_review')

    def test_a_record_found_by_a_name_search_has_no_author_id_to_trust(self):
        item = self.fielded('Consumption over the life cycle', 'Economics')
        item.author_details = [{'name': 'Alex Morgan'}]
        found = self.statuses([*self.proven(), item])
        self.assertNotEqual(found['Consumption over the life cycle'], 'verified')

    def test_a_dblp_record_is_in_computer_science(self):
        self.assertEqual(record_fields(self.fielded('A', None, source='dblp')), {'Computer Science'})


class OwnershipTests(unittest.TestCase):
    def test_author_id_and_name_alone_are_not_verified(self):
        paper = validate([record()])[0]
        self.assertEqual(paper.ownership_status, 'needs_review')
        self.assertTrue(paper.ownership_evidence)

    def test_target_orcid_agreement_verifies(self):
        paper = validate([record(orcid='https://orcid.org/profile-orcid')])[0]
        self.assertEqual(paper.ownership_status, 'verified')

    def test_conflicting_orcid_is_rejected_and_retained(self):
        paper = validate([record(orcid='another-orcid')])[0]
        self.assertEqual(paper.ownership_status, 'rejected')
        self.assertEqual(len(paper.records), 1)

    def test_explicit_wrong_target_signature_is_rejected(self):
        self.assertEqual(validate([record(name='Someone Else')])[0].ownership_status, 'rejected')

    def test_byline_is_compared_with_the_requested_name_too(self):
        # A source display name can carry noise ("Jia Liu B"); the caller's own query name still matches the byline.
        corrupted = replace(profile(), name='Jia Liu B', name_aliases=['Jia Liu B'], orcid=None)
        for target, expected in (
            (corrupted, 'needs_review'),
            (replace(corrupted, requested_name='Jia Liu'), 'verified'),
        ):
            papers = deduplicate_papers([record(name='Jia Liu')])
            papers[0].records[0].author_details[0]['institutions'] = [{'display_name': 'Example University'}]
            PaperOwnershipValidator().validate(papers, target)
            self.assertEqual(papers[0].ownership_status, expected)

    def test_past_institution_counts_only_for_an_orcid_anchored_author(self):
        def run(kind, config=None):
            target = replace(
                profile(),
                orcid=None,
                affiliations=['Example University', 'Old University'],
                matched_affiliations=['Example University'],
                identity_anchors=[AnchorEvidence('openalex', 'author-a', 'anchor', kind)],
            )
            papers = deduplicate_papers([record()])
            papers[0].records[0].author_details[0]['institutions'] = [{'display_name': 'Old University'}]
            PaperOwnershipValidator(config or OwnershipConfig()).validate(papers, target)
            return papers[0]

        verified = run(AnchorKind.ORCID)
        self.assertEqual(verified.ownership_status, 'verified')
        self.assertTrue(any('past affiliation' in line for line in verified.ownership_evidence))
        self.assertEqual(run(AnchorKind.PAPER).ownership_status, 'needs_review')
        self.assertEqual(
            run(AnchorKind.ORCID, OwnershipConfig(history_anchor_kinds=())).ownership_status, 'needs_review'
        )
        # Two institutions exceed a cap of one: a merged profile's history vouches for nobody.
        merged_profile = OwnershipConfig(maximum_history_institutions=1)
        self.assertEqual(run(AnchorKind.ORCID, merged_profile).ownership_status, 'needs_review')

    def test_a_curated_profile_the_orcid_anchors_verifies_the_papers_listing_it(self):
        def run(source, kind, config=None):
            author_id = 'author-a' if source == 'openalex' else 'author-b'
            target = replace(
                profile(),
                author_ids={source: author_id},
                author_id_aliases={source: [author_id]},
                identity_anchors=[AnchorEvidence(source, author_id, 'anchor', kind)],
            )
            papers = deduplicate_papers([record(source=source)])
            PaperOwnershipValidator(config or OwnershipConfig()).validate(papers, target)
            return papers[0]

        curated = OwnershipConfig(curated_orcid_sources=('semantic_scholar',))
        verified = run('semantic_scholar', AnchorKind.ORCID, curated)
        self.assertEqual(verified.ownership_status, 'verified')
        self.assertTrue(any('Curated semantic_scholar author profile' in line for line in verified.ownership_evidence))
        # An uncurated source's profile ORCID, a paper anchor, or no curated sources at all: no verification.
        self.assertEqual(run('openalex', AnchorKind.ORCID, curated).ownership_status, 'needs_review')
        self.assertEqual(run('semantic_scholar', AnchorKind.PAPER, curated).ownership_status, 'needs_review')
        self.assertEqual(run('semantic_scholar', AnchorKind.ORCID).ownership_status, 'needs_review')

    def test_a_curated_profile_whose_record_settled_the_identity_verifies_the_papers_listing_it(self):
        def run(source, curated_ids):
            target = replace(
                profile(),
                orcid=None,
                author_ids={source: 'author-b'},
                author_id_aliases={source: ['author-b']},
                curated_author_ids=curated_ids,
            )
            papers = deduplicate_papers([record(source=source)])
            PaperOwnershipValidator(OwnershipConfig(curated_orcid_sources=('semantic_scholar',))).validate(
                papers, target
            )
            return papers[0]

        verified = run('semantic_scholar', {'semantic_scholar': ['author-b']})
        self.assertEqual(verified.ownership_status, 'verified')
        self.assertTrue(any('own affiliation record placed' in line for line in verified.ownership_evidence))
        # Another profile's record, or an uncurated source, does not verify.
        self.assertEqual(run('semantic_scholar', {'semantic_scholar': ['other']}).ownership_status, 'needs_review')
        self.assertEqual(run('openalex', {'openalex': ['author-b']}).ownership_status, 'needs_review')

    def test_papers_listed_on_the_persons_page_seed_recurring_coauthors(self):
        listed = [record(title='Listed one', doi='10.example/one'), record(title='Listed two', doi='10.example/two')]
        candidate = record(title='Candidate', doi='10.example/candidate')
        target = replace(profile(), orcid=None, listed_works=['doi:10.example/one', 'doi:10.example/two'])
        papers = deduplicate_papers([*listed, candidate])
        PaperOwnershipValidator().validate(papers, target)
        self.assertEqual(papers[-1].ownership_status, 'verified')
        papers = deduplicate_papers([*listed, candidate])
        PaperOwnershipValidator().validate(papers, replace(target, listed_works=[]))
        self.assertEqual(papers[-1].ownership_status, 'needs_review')

    def test_one_shared_own_paper_is_enough_for_a_young_authors_coauthors(self):
        own = record(title='Only own paper', doi='10.example/own')
        candidate = record(title='Candidate', doi='10.example/candidate')
        target = replace(profile(), orcid=None, listed_works=['doi:10.example/own'])
        papers = deduplicate_papers([own, candidate])
        PaperOwnershipValidator().validate(papers, target)
        self.assertEqual(papers[-1].ownership_status, 'verified')
        strict = OwnershipConfig(own_paper_seed_papers_per_coauthor=2)
        papers = deduplicate_papers([own, candidate])
        PaperOwnershipValidator(strict).validate(papers, target)
        self.assertEqual(papers[-1].ownership_status, 'needs_review')

    def test_target_names_are_unique_and_include_the_request(self):
        target = replace(profile(), name_aliases=['Alex Morgan', 'A. Morgan'], requested_name='Alex Morgan')
        self.assertEqual(target.target_names(), ['Alex Morgan', 'A. Morgan'])

    def test_cross_source_work_and_signature_and_coauthors_verify(self):
        papers = validate([record(), record('semantic_scholar', publication_type='JournalArticle')])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].ownership_status, 'verified')

    def test_title_only_dedup_is_not_ownership_verification(self):
        papers = validate([record(doi=None), record('semantic_scholar', doi=None)])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].ownership_status, 'needs_review')

    def test_cross_source_conflict_never_auto_verifies_paper(self):
        paper = validate([record(orcid='profile-orcid'), record('semantic_scholar', orcid='conflict')])[0]
        self.assertEqual(paper.ownership_status, 'needs_review')
        self.assertEqual({r.ownership_status for r in paper.records}, {'verified', 'rejected'})

    def test_interdisciplinary_publication_not_rejected_for_topic(self):
        paper = validate([record(title='History of coastal ecosystems', orcid='profile-orcid')])[0]
        self.assertEqual(paper.ownership_status, 'verified')

    def test_single_author_cross_source_with_confirmed_ids_can_verify(self):
        left = replace(record(), authors=['Alex Morgan'])
        right = replace(record('semantic_scholar'), authors=['Alex Morgan'])
        self.assertEqual(validate([left, right])[0].ownership_status, 'verified')

    def test_single_author_legacy_cross_source_still_needs_review(self):
        left = replace(record(), authors=['Alex Morgan'], author_details=[])
        right = replace(record('semantic_scholar'), authors=['Alex Morgan'], author_details=[])
        self.assertEqual(validate([left, right])[0].ownership_status, 'needs_review')

    def test_missing_author_ids_are_reviewed_not_discarded(self):
        item = replace(record(), author_details=[{'name': 'Alex Morgan'}])
        self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_recurring_verified_coauthors_support_single_source_work(self):
        seeds = [
            record(title='Seed one', doi='10.example/one', orcid='profile-orcid'),
            record(title='Seed two', doi='10.example/two', orcid='profile-orcid'),
        ]
        new = record(title='Another field', doi='10.example/new')
        papers = validate([*seeds, new])
        self.assertEqual(papers[-1].ownership_status, 'verified')
        self.assertTrue(any('coauthors' in message for message in papers[-1].ownership_evidence))

    def test_review_papers_do_not_bootstrap_each_other(self):
        records = [record(title='First', doi='10.example/one'), record(title='Second', doi='10.example/two')]
        self.assertEqual({p.ownership_status for p in validate(records)}, {'needs_review'})


def merged_profile(anchors=True):
    """Split identity: two OpenAlex and two Semantic Scholar fragments, one per source merged without an anchor."""
    value = replace(
        profile(),
        orcid='fragment-a-orcid',
        matched_affiliations=['Example University'],
        author_id_aliases={'openalex': ['author-a', 'author-a2'], 'semantic_scholar': ['author-b', 'author-b2']},
    )
    if anchors:
        value.identity_anchors = [
            'openalex author-a2: author of arXiv 2501.00001',
            'semantic_scholar author-b2: author of ARXIV:2501.00001',
        ]
    return value


def fragment_record(
    source,
    author_id,
    title='Fragment study',
    doi='10.example/fragment',
    orcid=None,
    scope='resolved_author',
    institutions=None,
    coauthors=('Jane Smith', 'John Green'),
):
    return PaperRecord(
        title=title,
        authors=['Alex Morgan', *list(coauthors)],
        year=2024,
        doi=doi,
        venue='Example Journal',
        abstract=None,
        source=source,
        source_paper_id=source + ':' + title,
        source_author_id=author_id,
        paper_url=None,
        publication_type='article',
        author_details=[
            {
                'author_id': author_id,
                'name': 'Alex Morgan',
                'orcid': orcid,
                'orcid_scope': scope,
                'institutions': [{'display_name': name} for name in institutions or []],
            }
        ],
    )


class MergedIdentityOwnershipTests(unittest.TestCase):
    def check(self, records, anchors=True):
        return PaperOwnershipValidator().validate(deduplicate_papers(records), merged_profile(anchors))

    def test_author_profile_orcid_of_another_fragment_is_not_paper_evidence(self):
        record = fragment_record(
            'openalex', 'author-a2', orcid='fragment-a2-orcid', institutions=['Example University']
        )
        paper = self.check([record])[0]
        self.assertEqual(paper.ownership_status, 'verified')
        self.assertIn(
            'Author-profile ORCID differs from the identity ORCID; not paper-level evidence', record.ownership_evidence
        )

    def test_author_profile_orcid_mismatch_alone_does_not_verify(self):
        record = fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid')
        self.assertEqual(self.check([record])[0].ownership_status, 'needs_review')

    def test_paper_level_orcid_conflict_still_rejects_merged_fragment(self):
        record = fragment_record(
            'openalex', 'author-a2', orcid='someone-else', scope='paper_authorship', institutions=['Example University']
        )
        self.assertEqual(self.check([record])[0].ownership_status, 'rejected')

    def test_unscoped_orcid_conflict_still_rejects(self):
        record = fragment_record(
            'openalex', 'author-a2', orcid='someone-else', scope=None, institutions=['Example University']
        )
        self.assertEqual(self.check([record])[0].ownership_status, 'rejected')

    def test_anchored_fragments_corroborate_across_sources(self):
        papers = self.check(
            [
                fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                fragment_record('semantic_scholar', 'author-b2', scope='author_profile'),
            ]
        )
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].ownership_status, 'verified')

    def test_unanchored_merged_fragment_cannot_corroborate_when_anchors_exist(self):
        # author-b is the representative Semantic Scholar ID but was merged without an anchor of its own.
        papers = self.check(
            [
                fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                fragment_record('semantic_scholar', 'author-b', scope='author_profile'),
            ]
        )
        self.assertEqual(papers[0].ownership_status, 'needs_review')

    def test_without_anchors_every_selected_fragment_corroborates(self):
        papers = self.check(
            [
                fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                fragment_record('semantic_scholar', 'author-b', scope='author_profile'),
            ],
            anchors=False,
        )
        self.assertEqual(papers[0].ownership_status, 'verified')

    def test_unanchored_fragment_still_verifies_with_paper_level_institution(self):
        record = fragment_record(
            'semantic_scholar', 'author-b', scope='paper_authorship', institutions=['Example University']
        )
        self.assertEqual(self.check([record])[0].ownership_status, 'verified')

    def test_coauthor_seeds_from_one_fragment_verify_another(self):
        seeds = [
            fragment_record(
                'openalex',
                'author-a',
                title='Seed ' + str(index),
                doi='10.example/seed' + str(index),
                orcid='fragment-a-orcid',
                institutions=['Example University'],
            )
            for index in range(2)
        ]
        target = fragment_record(
            'openalex', 'author-a2', title='Older study', doi='10.example/older', orcid='fragment-a2-orcid'
        )
        papers = self.check([*seeds, target])
        older = next(paper for paper in papers if paper.title == 'Older study')
        self.assertEqual(older.ownership_status, 'verified')
        self.assertTrue(
            any('recurring independently verified coauthors' in message for message in target.ownership_evidence)
        )


class DeduplicationTests(unittest.TestCase):
    def test_one_doi_and_one_no_doi_can_merge(self):
        papers = deduplicate_papers([record(), record('semantic_scholar', doi=None, publication_type='JournalArticle')])
        self.assertEqual(len(papers), 1)
        self.assertEqual(len(papers[0].records), 2)

    def test_authors_initials_can_match_with_other_metadata(self):
        right = replace(record('semantic_scholar', doi=None), authors=['A. Morgan', 'J. Smith', 'John Green'])
        self.assertEqual(len(deduplicate_papers([record(), right])), 1)

    def test_conflicting_full_author_names_do_not_merge(self):
        right = replace(record('semantic_scholar', doi=None), authors=['Alan Morgan', 'Jeff Smith', 'John Green'])
        self.assertEqual(len(deduplicate_papers([record(), right])), 2)

    def test_preprint_and_journal_are_linked_but_separate(self):
        left = record(doi='10.example/preprint', publication_type='preprint')
        right = record('semantic_scholar', doi='10.example/journal', publication_type='JournalArticle')
        papers = deduplicate_papers([left, right])
        self.assertEqual(len(papers), 2)
        self.assertTrue(papers[0].related_versions)
        self.assertEqual(papers[0].related_versions[0]['identifiers'][0]['doi'], '10.example/journal')

    def test_conference_and_journal_extension_remain_separate(self):
        left = record(doi=None, publication_type='conference-paper')
        right = record('semantic_scholar', doi=None, publication_type='JournalArticle')
        self.assertEqual(len(deduplicate_papers([left, right])), 2)

    def test_stable_external_paper_id_links_doi_less_records(self):
        left = replace(record(doi=None), source='dblp', source_paper_id='journal/example')
        right = replace(record('semantic_scholar', doi=None), external_ids={'dblp': 'journal/example'})
        self.assertEqual(len(deduplicate_papers([left, right])), 1)


class ClassificationAndStatisticsTests(unittest.TestCase):
    def test_explicit_types_only(self):
        cases = {
            'Review': 'review',
            'preprint': 'preprint',
            'book-chapter': 'book_chapter',
            'dataset': 'dataset',
            'editorial': 'editorial',
            'mystery': 'unknown',
            None: 'unknown',
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                item = record(publication_type=label)
                classify_record(item)
                self.assertEqual(item.paper_type, expected)

    def test_filters_preserve_archive_and_unfiltered_statistics(self):
        records = [
            record(orcid='profile-orcid'),
            record(title='Dataset', doi='10.example/data', publication_type='dataset'),
        ]
        papers = validate(records)
        source = PaperSourceResult(
            source='openalex',
            author_id='author-a',
            records_received=2,
            complete=True,
            pagination_complete=True,
            papers=records,
        )
        result = PaperCollectionResult(
            papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved', 'resolved'
        )
        output = result.to_dict(academic_only=True, ownership_statuses=['verified'])
        self.assertEqual(len(output['papers']), 1)
        self.assertEqual(len(output['all_papers']), 2)
        self.assertEqual(output['statistics']['raw_records_received'], 2)
        self.assertEqual(sum(output['statistics']['ownership_counts'].values()), 2)
        self.assertTrue(output['sources'][0]['pagination_complete'])

    def test_saved_legacy_metadata_can_be_reanalyzed_without_inventing_orcid(self):
        records = [record()]
        for item in records:
            item.author_details = []
        papers = deduplicate_papers(records)
        source = PaperSourceResult('openalex', 'author-a', papers=records, complete=True, records_received=1)
        saved = {
            'name': 'Alex Morgan',
            'status': 'resolved',
            'collection': PaperCollectionResult(
                papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved'
            ).to_dict(),
        }
        result = reanalyze_saved_result(json.loads(json.dumps(saved)))
        self.assertEqual(result.papers[0].ownership_status, 'needs_review')
        self.assertEqual(len(result.papers[0].records), 1)

    def test_raw_parse_failure_is_retained_and_other_records_survive(self):
        from paper_author_searcher.paper_providers import OpenAlexPaperProvider
        from paper_author_searcher.providers import OpenAlexProvider
        from test_paper_collector import Client, openalex_work

        client = Client([{'results': [42, openalex_work('valid')], 'meta': {'next_cursor': None}}])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.rejected_records[0]['raw_metadata'], 42)
        self.assertTrue(result.pagination_complete)
        self.assertFalse(result.complete)

    def test_source_status_is_independent_from_ownership_status(self):
        records = [record()]
        papers = validate(records)
        source = PaperSourceResult(
            'openalex', 'author-a', papers=records, complete=True, pagination_complete=True, records_received=1
        )
        output = PaperCollectionResult(
            papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved', 'resolved'
        ).to_dict()
        self.assertEqual(output['professor_identity_status'], 'resolved')
        self.assertEqual(output['sources'][0]['fetch_status'], 'complete')
        self.assertEqual(output['statistics']['ownership_counts']['needs_review'], 1)

    def test_two_versions_are_not_two_independent_coauthor_seeds(self):
        first = record(
            title='Seed study', doi='10.example/preprint', orcid='profile-orcid', publication_type='preprint'
        )
        second = record(title='Seed study', doi='10.example/journal', orcid='profile-orcid', publication_type='article')
        unverified = record(title='Unverified', doi='10.example/other')
        self.assertEqual(validate([first, second, unverified])[-1].ownership_status, 'needs_review')

    def test_review_label_is_more_specific_than_generic_article(self):
        records = [record(publication_type='article'), record('semantic_scholar', publication_type='Review')]
        paper = deduplicate_papers(records)[0]
        self.assertEqual(paper.paper_type, 'review')
        self.assertEqual(len(paper.type_evidence), 2)

    def test_target_initial_is_not_counted_as_a_coauthor(self):
        item = replace(record(), authors=['A. Morgan', 'Jane Smith'])
        validator = PaperOwnershipValidator()
        self.assertEqual(validator._coauthors(item, profile()), {'jane smith'})
