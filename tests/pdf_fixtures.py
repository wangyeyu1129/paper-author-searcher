"""Synthetic PDF data for tests, independent of any professor or real paper."""
import io
import importlib.util


HAS_PYPDF = importlib.util.find_spec('pypdf') is not None


def make_pdf(title='Reusable methods for synthetic research', doi='10.1234/example',
             authors='Alex Example; Taylor Researcher', pages=1, javascript=None):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    writer.add_metadata({'/Title': title, '/Author': authors})
    lines = [title, authors]
    if doi:
        lines.append('DOI: ' + doi)
    for index in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
            NameObject('/F1'): writer._add_object(font),
        })})
        commands = ['BT /F1 12 Tf 50 750 Td']
        for line in lines:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append('({}) Tj 0 -20 Td'.format(escaped))
        commands.append('ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(commands).encode('latin-1'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if javascript:
        writer.add_js(javascript)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
