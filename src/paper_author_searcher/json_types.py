"""Type aliases for the JSON documents (saved reports, checkpoints, API payloads) passed between stages."""

from typing import Any

JSONObject = dict[str, Any]
JSONList = list[JSONObject]
