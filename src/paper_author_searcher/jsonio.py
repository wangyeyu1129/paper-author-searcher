"""JSON object files: atomic, fsync'd writes and type-checked reads. No pipeline knowledge."""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

PathLike = Union[str, Path]


COMPACT_SEPARATORS = (',', ':')


def write_json(path: PathLike, data: Any, compact: bool = False) -> None:
    """Replace ``path`` atomically so a crash never leaves a truncated checkpoint behind.

    ``compact`` drops indentation and spaces: for machine-read artifacts (about half the size, several times
    faster to write). Files people read or diff keep the indented form.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Output JSON must not be a symlink')
    descriptor, temporary = tempfile.mkstemp(prefix='.checkpoint-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            if compact:
                json.dump(data, handle, ensure_ascii=False, separators=COMPACT_SEPARATORS)
            else:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# Short strings repeat heavily in archives (author names, sources, statuses, affiliations); long ones rarely do.
INTERN_MAXIMUM_LENGTH = 200


def _interning_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    return {key: _intern(value) for key, value in pairs}


def _intern(value: Any) -> Any:
    if isinstance(value, str):
        return sys.intern(value) if len(value) <= INTERN_MAXIMUM_LENGTH else value
    if isinstance(value, list):
        return [_intern(item) for item in value]
    return value


def read_json(path: PathLike, intern_strings: bool = False) -> Dict[str, Any]:
    """Read a JSON file whose top level must be an object.

    ``intern_strings`` shares one copy of each repeated short string: about a third less memory for a large
    archive, at roughly 1.6x the load time (storage.intern_strings).
    """
    with Path(path).open(encoding='utf-8') as handle:
        data = json.load(handle, object_pairs_hook=_interning_object) if intern_strings else json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Input must be a JSON object')
    return data
