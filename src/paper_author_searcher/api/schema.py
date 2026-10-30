"""Versioned JSON Schemas for the API, and a validator for the subset of JSON Schema 2020-12 they use.

The package has no runtime dependencies, so it carries its own validator instead of ``jsonschema``. It supports
exactly the keywords the shipped schemas use and refuses to load a schema with any other, so no keyword is ever
silently ignored. The schemas themselves are standard: any JSON Schema tool can read them.
"""

import json
import re
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

API_VERSION = 'v1'
SCHEMA_NAMES = ('search-request', 'confirmation-request', 'search', 'error', 'agent-result')

# Keywords that only describe; validation never reads them.
ANNOTATIONS = frozenset({'$schema', '$id', '$defs', 'title', 'description', 'default'})
ASSERTIONS = frozenset(
    {
        'type',
        'enum',
        'const',
        'properties',
        'required',
        'additionalProperties',
        'items',
        'maxItems',
        'minLength',
        'maxLength',
        'pattern',
        'minimum',
        'maximum',
        'oneOf',
        '$ref',
    }
)
TYPES = {
    'object': lambda value: isinstance(value, dict),
    'array': lambda value: isinstance(value, list),
    'string': lambda value: isinstance(value, str),
    'integer': lambda value: isinstance(value, int) and not isinstance(value, bool),
    'number': lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    'boolean': lambda value: isinstance(value, bool),
    'null': lambda value: value is None,
}


class SchemaError(ValueError):
    """A schema uses something the validator does not support."""


class Validator:
    """Validates instances against one schema document; ``errors`` lists every violation with its JSON path."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        self.schema = schema
        self._check(schema, '#')

    def errors(self, instance: Any) -> list[str]:
        found: list[str] = []
        self._validate(self.schema, instance, '$', found)
        return found

    def _check(self, node: Any, where: str) -> None:
        if not isinstance(node, Mapping):
            raise SchemaError(f'{where}: a schema must be an object')
        unknown = set(node) - ANNOTATIONS - ASSERTIONS
        if unknown:
            raise SchemaError(f'{where}: unsupported keywords {sorted(unknown)}')
        for name, child in (node.get('$defs') or {}).items():
            self._check(child, f'{where}/$defs/{name}')
        for name, child in (node.get('properties') or {}).items():
            self._check(child, f'{where}/properties/{name}')
        for key in ('items',):
            if key in node:
                self._check(node[key], f'{where}/{key}')
        for index, child in enumerate(node.get('oneOf') or []):
            self._check(child, f'{where}/oneOf/{index}')
        if node.get('additionalProperties') not in (None, False):
            raise SchemaError(f'{where}: additionalProperties must be false when given')
        if '$ref' in node:
            self._resolve(node['$ref'])
        if 'pattern' in node:
            re.compile(node['pattern'])

    def _resolve(self, reference: str) -> Mapping[str, Any]:
        if not reference.startswith('#/$defs/'):
            raise SchemaError(f'only local $defs references are supported: {reference}')
        target = (self.schema.get('$defs') or {}).get(reference.removeprefix('#/$defs/'))
        if target is None:
            raise SchemaError(f'unresolved reference {reference}')
        return target

    def _validate(self, node: Mapping[str, Any], value: Any, path: str, found: list[str]) -> None:
        if '$ref' in node:
            self._validate(self._resolve(node['$ref']), value, path, found)
        if 'type' in node:
            allowed = node['type'] if isinstance(node['type'], list) else [node['type']]
            if not any(TYPES[name](value) for name in allowed):
                found.append(f'{path}: expected {" or ".join(allowed)}')
                return
        if 'const' in node and value != node['const']:
            found.append(f'{path}: must be {node["const"]!r}')
        if 'enum' in node and value not in node['enum']:
            found.append(f'{path}: must be one of {", ".join(map(str, node["enum"]))}')
        if 'oneOf' in node:
            matches = sum(not self._errors_for(option, value, path) for option in node['oneOf'])
            if matches != 1:
                found.append(f'{path}: must match exactly one allowed form')
        if isinstance(value, str):
            self._string(node, value, path, found)
        elif isinstance(value, int | float) and not isinstance(value, bool):
            if 'minimum' in node and value < node['minimum']:
                found.append(f'{path}: must be at least {node["minimum"]}')
            if 'maximum' in node and value > node['maximum']:
                found.append(f'{path}: must be at most {node["maximum"]}')
        elif isinstance(value, list):
            if 'maxItems' in node and len(value) > node['maxItems']:
                found.append(f'{path}: at most {node["maxItems"]} items')
            if 'items' in node:
                for index, item in enumerate(value):
                    self._validate(node['items'], item, f'{path}[{index}]', found)
        elif isinstance(value, dict):
            self._object(node, value, path, found)

    def _errors_for(self, node: Mapping[str, Any], value: Any, path: str) -> list[str]:
        found: list[str] = []
        self._validate(node, value, path, found)
        return found

    @staticmethod
    def _string(node: Mapping[str, Any], value: str, path: str, found: list[str]) -> None:
        if 'minLength' in node and len(value) < node['minLength']:
            found.append(f'{path}: at least {node["minLength"]} characters')
        if 'maxLength' in node and len(value) > node['maxLength']:
            found.append(f'{path}: at most {node["maxLength"]} characters')
        if 'pattern' in node and not re.search(node['pattern'], value):
            found.append(f'{path}: does not match the expected format')

    def _object(self, node: Mapping[str, Any], value: dict, path: str, found: list[str]) -> None:
        properties = node.get('properties') or {}
        for name in node.get('required') or []:
            if name not in value:
                found.append(f'{path}.{name}: required')
        for name, item in value.items():
            if name in properties:
                self._validate(properties[name], item, f'{path}.{name}', found)
            elif node.get('additionalProperties') is False:
                found.append(f'{path}.{name}: unknown field')


@cache
def schema(name: str, version: str = API_VERSION) -> dict[str, Any]:
    """A shipped schema document, by name (``SCHEMA_NAMES``) and API version."""
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    text = (
        resources.files(__package__)
        .joinpath('schemas')
        .joinpath(version)
        .joinpath(f'{name}.json')
        .read_text(encoding='utf-8')
    )
    return json.loads(text)


@cache
def validator(name: str, version: str = API_VERSION) -> Validator:
    return Validator(schema(name, version))
