"""JSON object files: atomic, fsync'd writes and type-checked reads. No pipeline knowledge."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

PathLike = Union[str, Path]


def write_json(path: PathLike, data: Any) -> None:
    """Replace ``path`` atomically so a crash never leaves a truncated checkpoint behind."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Output JSON must not be a symlink')
    descriptor, temporary = tempfile.mkstemp(prefix='.checkpoint-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: PathLike) -> Dict[str, Any]:
    """Read a JSON file whose top level must be an object."""
    with Path(path).open(encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Input must be a JSON object')
    return data
