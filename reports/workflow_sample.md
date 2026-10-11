# End-to-end integration: limited live checks

Executed with Python 3.8.0 and TLS certificate verification enabled. Unit/integration suite: **324 tests passed**. No PDF files were executed; content validation uses the existing isolated parser.

## Live identity check

A fresh query for Geoffrey Hinton / University of Toronto returned an ambiguous identity. The two leading profiles had scores 0.95 and 0.85; the highest profile's final confidence was capped at 0.69 by the existing ambiguity policy. The workflow displayed IDs and stopped before collection. It did not select a competing candidate or modify matching thresholds.

Checkpoint: `output/integration/search_Geoffrey_Hinton_20261011T032316Z_b182fb5eb557/`. DBLP returned an HTML interstitial; its error is retained. This identity check is **not** reported as a successful fresh identity resolution.

## Collection-to-download check using a previously confirmed identity

To test the remaining stages independently of that new ambiguity, the saved confirmed identity from `hinton_papers.authorship.json` was reused. No competing candidate from the live ambiguous result was selected. Collection was limited to one page of five records per paginated source ID, PDF discovery to two eligible entries, and downloads to one paper attempt.

Output: `output/integration/search_Geoffrey_E_Hinton_20261011T032705Z_4f6970361191/`.

| Check | Observed result |
| --- | --- |
| OpenAlex | 5 records, 1 page, intentionally incomplete pagination |
| Semantic Scholar | 5 records, 1 page, intentionally incomplete pagination |
| DBLP | No previously confirmed ID; skipped coverage gap |
| Raw records / deduplicated entries | 10 / 10 |
| Ownership | 3 verified, 7 needs_review, 0 rejected |
| PDF URL lookups | 2 eligible entries |
| Validated PDF paper / distinct validated URL | 1 / 1 |
| Eligible URL lookup left unprocessed | 1 |
| Newly downloaded file | 1 |
| Failed downloads / content review files | 0 / 0 |
| Parsed format / paper content identity | valid / verified |
| Metadata transmission losses | 0 |
| Missing input year / author list | 0 / 0 |

The downloaded file is `papers/2015_Deep_learning_fdf1b9bfa3fc0a9a.pdf`:

- Title: Deep learning; year: 2015.
- Authors: Yann LeCun, Yoshua Bengio, Geoffrey Hinton.
- DOI: `10.1038/nature14539`.
- Source Author ID: OpenAlex `A5108093963`; other source records and author identity evidence remain in `paper_metadata` and `papers.json`.
- Public PDF source: `https://hal.science/hal-04206682/document`.
- Size: 695128 bytes; parser: 10 pages.
- SHA-256: `db8e1ed80a3f076bb2568b0ea1ded69f2354e83139ad6fa2b66d88a662a8ca6e`.
- The parser found the expected title and DOI in front matter/metadata, beyond the URL probe's signature check.

The first run downloaded one new file. A subsequent **offline** invocation through the installed `paper-search` entry point reused one completed file, downloaded zero new files, and revalidated its existing content. The manifest retains the first successful download in history. Saved PDF URLs and collection data were reused; there were no fresh API requests during this continuation.

The execution status remains **partial**, with 13 retained stage errors: original DBLP unavailability, DBLP identity coverage gap, configured page limits, Semantic Scholar PDF metadata rate limits, missing Unpaywall contact email, inaccessible/HTML landing pages, and a Nature redirect response. Available providers still produced a verified file. The counts describe a limited retrieved sample, not all papers published by the professor.

## Existing full archive continuation

A separate test read `hinton_papers.authorship.json` directly, retaining its 890 raw records / 573 deduplicated entries (341 verified, 232 needs_review). Only two eligible entries were queried for PDF URLs; one file was downloaded and content-verified. Output: `output/integration/search_Geoffrey_E_Hinton_20261011T032430Z_cf033da54e36/`.

That archive had seven missing years at input. These stayed missing; no authors/year were invented. The downloaded paper retained 2015 and its three authors. The pipeline reported no downstream field losses. Legacy URL reports without authors/year remain usable conservatively and can still generate `unknown_year`; the field audit identifies this as missing input data.

## Layout

```text
output/integration/search_Geoffrey_E_Hinton_20261011T032705Z_4f6970361191/
  professor.json
  papers.json
  pdf_urls.json
  papers/
    2015_Deep_learning_fdf1b9bfa3fc0a9a.pdf
  manifest.json
  report.json
```

The root manifest's `file_root` is `papers`. Each manifest row retains ownership, source evidence, PDF provenance, format/content checks, errors and history, including skipped or failed rows.

## Commands

A full live search, with limited scope:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python -m paper_author_searcher.cli \
  --mode full --name 'Geoffrey Hinton' --university 'University of Toronto' \
  --output-dir output --max-pages 1 --page-size 5 \
  --max-pdf-papers 2 --max-downloads 1 --timeout 8 --retries 0
```

The certificate setting above is specific to this machine's trust store; the code keeps normal TLS verification and does not hardcode that path. An ambiguous identity stops and shows the explicit selection command.

To reproduce the already-tested downstream run from its confirmed checkpoint:

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/paper-search --mode full \
  --input output/integration/search_Geoffrey_E_Hinton_20261011T032430Z_cf033da54e36/professor.json \
  --output-dir output --max-pages 1 --page-size 5 --max-pdf-papers 2 \
  --max-downloads 1 --timeout 8 --retries 0
```

For offline reuse of the successful limited run:

```bash
.venv/bin/paper-search --mode full \
  --input output/integration/search_Geoffrey_E_Hinton_20261011T032705Z_4f6970361191/report.json \
  --resume-dir output/integration/search_Geoffrey_E_Hinton_20261011T032705Z_4f6970361191 \
  --max-downloads 1
```

All original dataset JSON files remain unchanged. No evaluation ground truth was invented, and ownership/matching thresholds were not adjusted.
