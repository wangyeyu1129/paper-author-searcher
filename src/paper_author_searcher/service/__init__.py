"""The search service core (hexagonal): the ``SearchService`` facade, its ports, and their adapters.

    facade.SearchService     the one interface front ends use (submit, job, confirm, files, readiness)
    ports                    JobRepository, FileStore, SearchRunner
    sqlite / files / runner  adapters: SQLite jobs, local or S3 files, the paper-search workflow
    worker.SearchWorker      carries out queued searches (threads in the API process, or separate processes)
    assembly.assemble        builds all of it from Settings

The core imports no web framework; ``paper_author_searcher.api`` is the HTTP adapter.
"""
