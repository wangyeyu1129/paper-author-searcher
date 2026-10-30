# The paper-search service: API, workers and index refresh all run from this one image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# The "download" extra reads PDFs (pypdf), "llm" calls Claude; the service itself needs only the standard library.
RUN pip install ".[download,llm]" && useradd --create-home --uid 10001 search && mkdir /data && chown search /data

# Every setting is an environment variable (PAPER_SEARCH__<SECTION>__<FIELD>); these are the container defaults.
ENV PAPER_SEARCH__SERVICE__HOST=0.0.0.0 \
    PAPER_SEARCH__SERVICE__PORT=8080 \
    PAPER_SEARCH__SERVICE__DATA_DIRECTORY=/data \
    PAPER_SEARCH__SERVICE__HTTP_CACHE_DIRECTORY=/data/http_cache \
    PAPER_SEARCH__DBLP_DUMP__INDEX_PATH=/data/indexes/dblp.sqlite \
    PAPER_SEARCH__CATALOGS__STORE_PATH=/data/indexes/catalogs.sqlite \
    PAPER_SEARCH_LOG_FORMAT=json \
    PAPER_SEARCH_LOG_LEVEL=INFO

USER search
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4)"
ENTRYPOINT ["paper-search-api"]
CMD ["serve"]
