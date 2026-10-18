import json
import unittest
from dataclasses import replace

from paper_author_searcher.models import PaperRecord, ProfessorProfile, PaperCollectionResult, PaperSourceResult
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.paper_ownership import PaperOwnershipValidator
from paper_author_searcher.paper_types import classify_record
from paper_author_searcher.paper_reanalysis import reanalyze_saved_result


def profile():
    return ProfessorProfile(
        name='Alex Morgan', affiliations=['Example University'],
        author_ids={'openalex': 'author-a', 'semantic_scholar': 'author-b'},
        author_id_aliases={'openalex': ['author-a'], 'semantic_scholar': ['author-b']},
        identity_confidence=0.95, status='resolved', evidence=[], orcid='profile-orcid',
    )


def record(source='openalex', title='An interdisciplinary study', doi='10.example/study',
           orcid=None, name='Alex Morgan', publication_type='article'):
    author_id = 'author-a' if source == 'openalex' else 'author-b'
    return PaperRecord(
        title=title, authors=[name, 'Jane Smith', 'John Green'], year=2024,
        doi=doi, venue='Example Journal', abstract=None, source=source,
        source_paper_id=source + ':' + title, source_author_id=author_id, paper_url=None,
        publication_type=publication_type,
        author_details=[{'author_id': author_id, 'name': name, 'orcid': orcid,
                         'orcid_scope': 'paper_authorship'}],
    )


def validate(records):
    papers = deduplicate_papers(records)
    return PaperOwnershipValidator().validate(papers, profile())


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
        seeds = [record(title='Seed one', doi='10.example/one', orcid='profile-orcid'),
                 record(title='Seed two', doi='10.example/two', orcid='profile-orcid')]
        new = record(title='Another field', doi='10.example/new')
        papers = validate(seeds + [new])
        self.assertEqual(papers[-1].ownership_status, 'verified')
        self.assertTrue(any('coauthors' in message for message in papers[-1].ownership_evidence))

    def test_review_papers_do_not_bootstrap_each_other(self):
        records = [record(title='First', doi='10.example/one'),
                   record(title='Second', doi='10.example/two')]
        self.assertEqual({p.ownership_status for p in validate(records)}, {'needs_review'})


def merged_profile(anchors=True):
    """Split identity: two OpenAlex and two Semantic Scholar fragments, one per source merged without an anchor."""
    value = replace(
        profile(), orcid='fragment-a-orcid', matched_affiliations=['Example University'],
        author_id_aliases={'openalex': ['author-a', 'author-a2'], 'semantic_scholar': ['author-b', 'author-b2']},
    )
    if anchors:
        value.identity_anchors = ['openalex author-a2: author of arXiv 2501.00001',
                                  'semantic_scholar author-b2: author of ARXIV:2501.00001']
    return value


def fragment_record(source, author_id, title='Fragment study', doi='10.example/fragment', orcid=None,
                    scope='resolved_author', institutions=None, coauthors=('Jane Smith', 'John Green')):
    return PaperRecord(
        title=title, authors=['Alex Morgan'] + list(coauthors), year=2024, doi=doi, venue='Example Journal',
        abstract=None, source=source, source_paper_id=source + ':' + title, source_author_id=author_id,
        paper_url=None, publication_type='article',
        author_details=[{'author_id': author_id, 'name': 'Alex Morgan', 'orcid': orcid, 'orcid_scope': scope,
                         'institutions': [{'display_name': name} for name in institutions or []]}],
    )


class MergedIdentityOwnershipTests(unittest.TestCase):
    def check(self, records, anchors=True):
        return PaperOwnershipValidator().validate(deduplicate_papers(records), merged_profile(anchors))

    def test_author_profile_orcid_of_another_fragment_is_not_paper_evidence(self):
        record = fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid',
                                 institutions=['Example University'])
        paper = self.check([record])[0]
        self.assertEqual(paper.ownership_status, 'verified')
        self.assertIn('Author-profile ORCID differs from the identity ORCID; not paper-level evidence',
                      record.ownership_evidence)

    def test_author_profile_orcid_mismatch_alone_does_not_verify(self):
        record = fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid')
        self.assertEqual(self.check([record])[0].ownership_status, 'needs_review')

    def test_paper_level_orcid_conflict_still_rejects_merged_fragment(self):
        record = fragment_record('openalex', 'author-a2', orcid='someone-else', scope='paper_authorship',
                                 institutions=['Example University'])
        self.assertEqual(self.check([record])[0].ownership_status, 'rejected')

    def test_unscoped_orcid_conflict_still_rejects(self):
        record = fragment_record('openalex', 'author-a2', orcid='someone-else', scope=None,
                                 institutions=['Example University'])
        self.assertEqual(self.check([record])[0].ownership_status, 'rejected')

    def test_anchored_fragments_corroborate_across_sources(self):
        papers = self.check([fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                             fragment_record('semantic_scholar', 'author-b2', scope='author_profile')])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].ownership_status, 'verified')

    def test_unanchored_merged_fragment_cannot_corroborate_when_anchors_exist(self):
        # author-b is the representative Semantic Scholar ID but was merged without an anchor of its own.
        papers = self.check([fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                             fragment_record('semantic_scholar', 'author-b', scope='author_profile')])
        self.assertEqual(papers[0].ownership_status, 'needs_review')

    def test_without_anchors_every_selected_fragment_corroborates(self):
        papers = self.check([fragment_record('openalex', 'author-a2', orcid='fragment-a2-orcid'),
                             fragment_record('semantic_scholar', 'author-b', scope='author_profile')],
                            anchors=False)
        self.assertEqual(papers[0].ownership_status, 'verified')

    def test_unanchored_fragment_still_verifies_with_paper_level_institution(self):
        record = fragment_record('semantic_scholar', 'author-b', scope='paper_authorship',
                                 institutions=['Example University'])
        self.assertEqual(self.check([record])[0].ownership_status, 'verified')

    def test_coauthor_seeds_from_one_fragment_verify_another(self):
        seeds = [fragment_record('openalex', 'author-a', title='Seed ' + str(index), doi='10.example/seed' + str(index),
                                 orcid='fragment-a-orcid', institutions=['Example University'])
                 for index in range(2)]
        target = fragment_record('openalex', 'author-a2', title='Older study', doi='10.example/older',
                                 orcid='fragment-a2-orcid')
        papers = self.check(seeds + [target])
        older = next(paper for paper in papers if paper.title == 'Older study')
        self.assertEqual(older.ownership_status, 'verified')
        self.assertTrue(any('recurring independently verified coauthors' in message
                            for message in target.ownership_evidence))


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
        cases = {'Review': 'review', 'preprint': 'preprint', 'book-chapter': 'book_chapter',
                 'dataset': 'dataset', 'editorial': 'editorial', 'mystery': 'unknown', None: 'unknown'}
        for label, expected in cases.items():
            with self.subTest(label=label):
                item = record(publication_type=label)
                classify_record(item)
                self.assertEqual(item.paper_type, expected)

    def test_filters_preserve_archive_and_unfiltered_statistics(self):
        records = [record(orcid='profile-orcid'), record(title='Dataset', doi='10.example/data', publication_type='dataset')]
        papers = validate(records)
        source = PaperSourceResult(source='openalex', author_id='author-a', records_received=2,
                                   complete=True, pagination_complete=True, papers=records)
        result = PaperCollectionResult(papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved', 'resolved')
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
        saved = {'name': 'Alex Morgan', 'status': 'resolved', 'collection':
                 PaperCollectionResult(papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved').to_dict()}
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
        source = PaperSourceResult('openalex', 'author-a', papers=records,
                                   complete=True, pagination_complete=True, records_received=1)
        output = PaperCollectionResult(papers, [source], True, {'openalex': ['author-a']}, 'resolver_resolved', 'resolved').to_dict()
        self.assertEqual(output['professor_identity_status'], 'resolved')
        self.assertEqual(output['sources'][0]['fetch_status'], 'complete')
        self.assertEqual(output['statistics']['ownership_counts']['needs_review'], 1)

    def test_two_versions_are_not_two_independent_coauthor_seeds(self):
        first = record(title='Seed study', doi='10.example/preprint', orcid='profile-orcid', publication_type='preprint')
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
