"""Type aliases for the JSON documents (saved reports, checkpoints, API payloads) passed between stages."""

from typing import Any, Dict, List

JSONObject = Dict[str, Any]
JSONList = List[JSONObject]
