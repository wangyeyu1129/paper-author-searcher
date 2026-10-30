"""Asynchronous search service: submit a search, poll it, confirm an ambiguous identity, fetch the result.

Routes and job flow are in ``app``; ``server`` runs it over HTTP. Request and response bodies follow the versioned
JSON Schemas in ``schemas/`` (served at ``/v1/schemas/{name}``).
"""
