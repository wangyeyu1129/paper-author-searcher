# End-to-end workflow

Install the existing download extra for content parsing:

```bash
python3 -m pip install -e '.[download]'
```

A live search uses the original resolver, paginated providers, deduplicator, ownership validator, PDF URL resolver and downloader. Each search creates a separate directory. The original stage CLIs remain available.

```bash
python3 -m paper_author_searcher.cli \
  --mode full \
  --name 'Geoffrey Hinton' \
  --university 'University of Toronto' \
  --output-dir output \
  --max-downloads 3
```

The equivalent entry point is `paper-search`. Its default mode is `full`. `--department`, repeated `--research`, repeated `--university-alias`, `--timeout`, `--retries`, `--max-file-mb`, and `--unpaywall-email` are optional. Unpaywall requires a real contact email or the existing `UNPAYWALL_EMAIL` environment variable. Existing API key environment variables still apply. TLS verification and public-network download restrictions remain enabled.

## Modes and request limits

- `--mode metadata`: collect, deduplicate and evaluate ownership; no PDF URL queries or downloads.
- `--mode pdf-urls`: additionally discover and lightly validate PDF URLs; no PDF files.
- `--mode full`: additionally stream files, parse PDFs and check content identity.

`--max-pages 1 --page-size 5` limits collection to one page of five records per selected paginated source Author ID. This is a coverage limit, not a claim about the professor's total output. Resolver candidate enrichment and provider identity checks remain necessary independent requests. DBLP does not have equivalent pagination settings.

`--max-pdf-papers 2` processes two eligible paper entries for URL discovery; others remain `not_processed`. `--max-downloads 1` permits one paper download attempt. Multiple candidate URLs can be tried for that paper. Completed files reused from a manifest do not consume this limit. A download limit alone does not limit metadata or PDF URL queries.

Only `ownership_status=verified` entries are automatically processed. `--include-needs-review` explicitly permits review entries without changing their ownership labels. `rejected` entries are always skipped. PDF URLs still require successful URL and association checks; valid downloaded PDF structure and content identity are recorded separately.

## Identity confirmation

An ambiguous or unconfirmed identity stops before collection. The terminal shows candidate IDs, affiliations, raw scores, confidence and evidence. Full identity data are saved in `professor.json` even without `--verbose`. No competing identity is selected automatically.

Continue with an explicitly chosen candidate:

```bash
python3 -m paper_author_searcher.cli --mode full \
  --input output/SEARCH_DIRECTORY/professor.json \
  --select-author SOURCE:AUTHOR_ID \
  --output-dir output --max-downloads 3
```

Manual confirmation authorizes exactly that source ID. It does not implicitly authorize other candidates or additional aliases. Selection is checked against saved resolver candidates. Source errors, ORCID conflicts and ownership evidence remain intact. To inspect all diagnostics, use `--verbose` or open the saved JSON.

## Checkpoints and continuation

Inputs can be saved Resolver JSON, Collector JSON (including the existing CLI wrapper), PDF URL JSON, or a workflow `report.json`. Completed upstream stages are reused. An existing Collector archive is not silently reclassified or refreshed; its labels and provenance remain the input decisions.

```bash
# Existing collection -> PDF URLs without collecting again
python3 -m paper_author_searcher.cli --mode pdf-urls \
  --input hinton_papers.authorship.json \
  --output-dir output --max-pdf-papers 2

# Resume the same workflow folder; reuse and revalidate completed files
python3 -m paper_author_searcher.cli --mode full \
  --input output/SEARCH_DIRECTORY/report.json \
  --resume-dir output/SEARCH_DIRECTORY --max-downloads 3
```

Without `--resume-dir`, continuation creates a new isolated output directory. `--resume-dir` requires a checkpoint from that same workflow directory. It reuses the saved URL report without refreshing incomplete URL lookups. To repeat URL discovery, explicitly use that folder's `papers.json` as input. To refresh metadata, use the existing collector refresh command before continuing. A report's artifact references must stay within its directory; symlinks and traversal are refused.

## Output and interpretation

```text
output/
  search_PROFESSOR_TIMESTAMP_UNIQUE_ID/
    professor.json
    papers.json
    pdf_urls.json          # PDF modes
    papers/
      YEAR_TITLE_STABLE_ID.pdf
      needs_review/        # parseable file, uncertain content identity
      quarantine/          # PDF identifies a different paper
    manifest.json          # full mode; file_root="papers"
    report.json
```

`local_filename` in the manifest is relative to `file_root`, so the file path is `workflow_directory / file_root / local_filename`. Existing standalone downloads keep `file_root="."`. Filename sanitation, stable hashes, content hashes, atomic manifests and file revalidation are shared with the original downloader. Temporary downloads are never counted as successful files.

`papers.json` keeps the full collected archive, including source records and identity evidence. Every new PDF result and manifest entry also retains `paper_metadata`, source Paper/Author IDs, year, authors, DOI and ownership evidence. `report.json` records stage status, source fetch/pagination completeness, errors, limits, ownership counts, URL counts and download/content-validation counts. `metadata_audit` distinguishes absent input fields from fields changed or lost downstream. Old URL-only reports may genuinely lack authors or year; these are reported as missing and never invented. A missing year can still produce `unknown_year` for such inputs.

Statistics describe retrieved records, deduplicated entries and observed PDF availability, never the professor's complete publication count. A valid PDF URL is a bounded network/metadata check; the full mode's content validation provides a separate result. Source errors do not prevent remaining sources or papers from running. Failed downloads are retained in the manifest. Fatal stage failures still preserve earlier checkpoints. Interrupted downloads can be resumed through the saved report and manifest.

Exit codes: `0` completed execution, `2` identity confirmation required / partial or failed stages, `130` interrupted. Configured limits are recorded explicitly; completion does not imply exhaustive coverage. Review ownership entries remain review entries even if their PDF content matches.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Integration tests use synthetic author, paper and PDF providers with the real resolver, collector, deduplicator, ownership validator and downloader. They cover cross-source duplicates, ambiguous/manual identities, stage failures, partial pagination, review/rejected ownership, old archives, metadata preservation, three modes, request limits, safe folders and offline continuation. With the download extra installed, one full integration test also uses the actual PDF parser and front-matter identity check.
