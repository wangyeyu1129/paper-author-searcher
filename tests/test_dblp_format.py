import unittest
import xml.etree.ElementTree as ET

from paper_author_searcher.dblp_format import arxiv_ids, base_name, plain_title
from paper_author_searcher.paper_providers import dblp_record


class DblpFormatTests(unittest.TestCase):
    def test_homonym_numbers_are_not_part_of_the_name(self):
        self.assertEqual(base_name('Yang Liu 0005'), 'Yang Liu')
        self.assertEqual(base_name('Yang Liu'), 'Yang Liu')

    def test_the_closing_period_is_not_part_of_the_title(self):
        self.assertEqual(
            plain_title('Flow-based Direct Preference Optimization.'), 'Flow-based Direct Preference Optimization'
        )
        self.assertEqual(plain_title('Can Models Think?'), 'Can Models Think?')
        self.assertEqual(plain_title('To be continued...'), 'To be continued...')

    def test_arxiv_links_become_canonical_identifiers(self):
        links = ['https://doi.org/10.48550/arXiv.2311.15596', 'https://arxiv.org/abs/2311.15596v2']
        self.assertEqual(arxiv_ids(links), ['2311.15596'])


class DblpRecordTests(unittest.TestCase):
    def test_a_record_reads_as_the_work_and_people_it_names(self):
        element = ET.fromstring(
            '<article key="journals/corr/abs-2311-15596">'
            '<author pid="51/3710-5">Yang Liu 0005</author><author pid="12/1">Peng Li 0030</author>'
            '<title>Can Vision-Language Models Think from a First-Person Perspective?</title>'
            '<year>2023</year><journal>CoRR</journal><ee>https://arxiv.org/abs/2311.15596</ee></article>'
        )
        record = dblp_record(element, '51/3710-5', 'https://dblp.org')
        self.assertEqual(record.authors, ['Yang Liu', 'Peng Li'])
        self.assertEqual(record.author_details[0], {'name': 'Yang Liu', 'author_id': '51/3710-5', 'role': 'author'})
        self.assertEqual(record.title, 'Can Vision-Language Models Think from a First-Person Perspective?')
        self.assertEqual(record.external_ids, {'dblp': 'journals/corr/abs-2311-15596', 'arxiv': '2311.15596'})


if __name__ == '__main__':
    unittest.main()
