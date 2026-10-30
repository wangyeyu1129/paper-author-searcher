"""The cold-email agent's contract: confirmed identities and verified papers only, everything else withheld."""

import json
import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.api.schema import validator
from paper_author_searcher.config import DEFAULT_SERVICE
from paper_author_searcher.service.agent import agent_view
from paper_author_searcher.service.model import Job, JobStatus
from paper_author_searcher.service.results import ResultView
from test_api import Harness
from test_service import REQUEST

NONE = {'status': 'none', 'url': None, 'version': None, 'download': None}
FILE = {
    'status': 'file',
    'url': 'https://arxiv.org/pdf/1',
    'version': 'preprint',
    'download': '/v1/searches/s/papers/0/pdf',
}


def paper(paper_id, status='verified', confidence='high', doi=None, full_text=NONE):
    return {
        'id': paper_id,
        'title': f'Paper {paper_id}',
        'authors': ['Ada'],
        'year': 2024,
        'venue': 'ACL',
        'doi': doi,
        'abstract': 'About things.',
        'ownership_status': status,
        'confidence': confidence,
        'evidence': ['finding'],
        'full_text': full_text,
    }


def result(identity_status='resolved', papers=()):
    return {
        'run_status': 'completed',
        'identity': {
            'name': 'Ada',
            'status': identity_status,
            'orcid': None,
            'author_ids': {'openalex': 'A1'},
            'confidence': 0.95,
            'evidence': ['ORCID'],
        },
        'papers': list(papers),
        'statistics': {},
    }


class ContractTests(unittest.TestCase):
    def check(self, view):
        self.assertEqual(validator('agent-result').errors(view), [])
        return view

    def test_only_verified_papers_of_a_confirmed_person_are_usable(self):
        job = Job(
            's',
            'k',
            REQUEST,
            status=JobStatus.SUCCEEDED,
            result=result(
                papers=[
                    paper('0', doi='10.1/x', full_text=FILE),
                    paper('1', confidence='medium'),
                    paper('2', status='needs_review', confidence='low'),
                ]
            ),
        )
        view = self.check(agent_view(job))
        self.assertEqual((view['status'], view['usable']), ('ready', True))
        self.assertEqual(view['researcher']['identity']['confirmed_by'], 'evidence')
        self.assertEqual([item['id'] for item in view['papers']], ['0', '1'])
        first = view['papers'][0]
        self.assertEqual(
            (first['url'], first['abstract'], first['confidence']), ('https://doi.org/10.1/x', 'About things.', 'high')
        )
        self.assertEqual(first['full_text']['download'], FILE['download'])
        self.assertEqual(
            view['withheld'],
            [{'id': '2', 'title': 'Paper 2', 'year': 2024, 'reason': 'ownership_unverified', 'evidence': ['finding']}],
        )

    def test_an_unconfirmed_identity_withholds_every_paper(self):
        job = Job(
            's',
            'k',
            REQUEST,
            status=JobStatus.SUCCEEDED,
            result=result('ambiguous_contenders', [paper('0'), paper('1')]),
        )
        view = self.check(agent_view(job))
        self.assertEqual((view['status'], view['usable'], view['papers']), ('nothing_usable', False, []))
        self.assertEqual({item['reason'] for item in view['withheld']}, {'identity_unconfirmed'})
        self.assertFalse(view['researcher']['identity']['confirmed'])

    def test_a_person_picked_by_the_caller_counts_as_confirmed(self):
        job = Job(
            's',
            'k',
            REQUEST,
            status=JobStatus.SUCCEEDED,
            selection='openalex:A1',
            result=result('ambiguous', [paper('0')]),
        )
        view = self.check(agent_view(job))
        self.assertEqual((view['researcher']['identity']['confirmed_by'], view['usable']), ('caller', True))

    def test_unfinished_searches_offer_nothing(self):
        for status, expected in (
            (JobStatus.QUEUED, 'pending'),
            (JobStatus.RUNNING, 'pending'),
            (JobStatus.NEEDS_CONFIRMATION, 'needs_confirmation'),
            (JobStatus.FAILED, 'failed'),
        ):
            view = self.check(agent_view(Job('s', 'k', REQUEST, status=status, error={'message': 'down'})))
            self.assertEqual((view['status'], view['usable'], view['researcher']['identity']), (expected, False, None))

    def test_partial_runs_say_so(self):
        job = Job(
            's',
            'k',
            REQUEST,
            status=JobStatus.SUCCEEDED,
            result=dict(result(papers=[paper('0')]), run_status='partial'),
        )
        self.assertIn('Some sources did not answer; the list may be incomplete', agent_view(job)['notes'])


class ConfidenceTests(unittest.TestCase):
    def build(self, entries):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'papers.json').write_text(json.dumps({'collection': {'papers': entries}}))
        report = {'output_directory': str(root), 'run_status': 'completed', 'artifacts': {'papers': 'papers.json'}}
        view = ResultView(str, DEFAULT_SERVICE.strong_ownership_evidence, DEFAULT_SERVICE.supporting_ownership_evidence)
        return view.build(report)

    def test_findings_decide_the_confidence(self):
        own_list = "openalex:https://openalex.org/W1: Listed on the researcher's own publication list: Example"
        byline = (
            'openalex:https://openalex.org/W2: Explicit target Author ID, compatible byline and paper-level '
            'institution agree: Example University'
        )
        entries = [
            {
                'title': 'Listed',
                'ownership_status': 'verified',
                'abstract': 'Text',
                'ownership_evidence': ['openalex:W1: Target signature similarity: 1.000', own_list, byline],
            },
            {'title': 'Byline', 'ownership_status': 'verified', 'ownership_evidence': [byline]},
            {
                'title': 'Unsure',
                'ownership_status': 'needs_review',
                'ownership_evidence': ['dblp:x: Insufficient compatible target byline evidence'],
            },
        ]
        listed, byline_only, unsure = self.build(entries)['papers']
        self.assertEqual(listed['confidence'], 'high')
        self.assertEqual(listed['evidence'][0], "Listed on the researcher's own publication list: Example")
        self.assertEqual(len(listed['evidence']), 2)  # the signature score is not shown
        self.assertEqual((byline_only['confidence'], byline_only['abstract']), ('medium', None))
        self.assertEqual(
            (unsure['confidence'], unsure['evidence']), ('low', ['Insufficient compatible target byline evidence'])
        )
        self.assertEqual(listed['abstract'], 'Text')

    def test_the_search_view_hides_needs_review_unless_asked(self):
        built = self.build(
            [{'title': 'A', 'ownership_status': 'verified'}, {'title': 'B', 'ownership_status': 'needs_review'}]
        )
        self.assertEqual(len(built['papers']), 2)
        hidden = ResultView.visible(built, include_needs_review=False)
        self.assertEqual(([item['title'] for item in hidden['papers']], hidden['statistics']['papers']), (['A'], 1))
        self.assertIs(ResultView.visible(built, include_needs_review=True), built)


class RouteTests(unittest.TestCase):
    def test_the_agent_route_serves_the_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            api = Harness(directory, [])
            api.core.runner.outcomes.append(api.succeed())
            created = api.call('POST', '/v1/searches', REQUEST)
            response = api.call('GET', created.body['links']['self'] + '/agent')
            self.assertEqual((response.status, response.body['status']), (200, 'ready'))
            self.assertEqual(validator('agent-result').errors(response.body), [])
            self.assertEqual(api.call('GET', created.body['links']['self'] + '/agent', secret=api.other).status, 404)


if __name__ == '__main__':
    unittest.main()
