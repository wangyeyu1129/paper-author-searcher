import unittest

from paper_author_searcher.html_support import decode_html, html_encoding
from paper_author_searcher.pdf_catalogs import catalog_links
from paper_author_searcher.pdf_public_pages import public_page_links


class DecodingTests(unittest.TestCase):
    def test_bom_wins(self):
        page = '<p>Zoë</p>'.encode('utf-16')
        self.assertEqual(html_encoding(page, 'text/html; charset=iso-8859-1'), 'utf-16-le')
        self.assertEqual(decode_html(page), '<p>Zoë</p>')

    def test_header_charset_then_meta_then_utf8(self):
        latin = '<p>Zoë</p>'.encode('latin-1')
        self.assertEqual(decode_html(latin, {'Content-Type': 'text/html; charset=ISO-8859-1'}), '<p>Zoë</p>')
        meta = b'<meta charset="windows-1252"><p>Zo\xeb</p>'
        self.assertTrue(decode_html(meta).endswith('<p>Zoë</p>'))
        self.assertEqual(html_encoding(b'<p>plain</p>'), 'utf-8')

    def test_unknown_declared_encoding_falls_back_without_failing(self):
        self.assertEqual(html_encoding(b'<meta charset="klingon">', 'text/html; charset=nope'), 'utf-8')
        self.assertIn('�', decode_html(b'\xff\xfe\xfd', {'content-type': 'text/html'}) + '�')


class LinkExtractionTests(unittest.TestCase):
    def test_catalog_links_keep_heading_title_and_trailing_byline(self):
        links = catalog_links(
            '<dt>First study<dd><a href="a.html">abs</a> Alex Example, 2020<dt>Next<dd><a>no href</a>'
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['url'], 'a.html')
        self.assertEqual((links[0]['title'], links[0]['heading']), ('abs', 'First study'))
        self.assertIn('Alex Example', links[0]['context'])

    def test_public_page_links_include_link_elements_and_json_ld(self):
        parser = public_page_links()
        parser.feed(
            '<link rel="alternate" href="/paper.pdf"><h2>Papers</h2><a href="/p1.pdf">One</a>'
            '<script type="application/ld+json">{"@type": "ScholarlyArticle"}</script>'
        )
        self.assertEqual(
            [(link['href'], link['heading']) for link in parser.links], [('/paper.pdf', ''), ('/p1.pdf', 'Papers')]
        )
        self.assertEqual(parser.links[1]['text'], 'One')
        self.assertIn('ScholarlyArticle', parser.scripts[0])


if __name__ == '__main__':
    unittest.main()
