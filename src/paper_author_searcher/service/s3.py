"""Presigned URLs for S3-compatible stores (AWS Signature Version 4, query-string form), stdlib only.

Every object operation the file store needs (PUT, GET, HEAD) goes through a presigned URL, so the request itself
carries no secret and downloads can be handed to callers as short-lived links. Signing follows
https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html; ``tests/test_service`` checks it
against the example in that page.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

ALGORITHM = 'AWS4-HMAC-SHA256'
UNSIGNED_PAYLOAD = 'UNSIGNED-PAYLOAD'
MAXIMUM_EXPIRY = 7 * 24 * 3600


@dataclass(frozen=True)
class S3Location:
    endpoint: str
    bucket: str
    region: str
    # 'path': endpoint/bucket/key (MinIO and most compatible stores); 'virtual': bucket.endpoint/key (AWS).
    addressing: str = 'path'


def _encode(text: str, keep_slash: bool) -> str:
    return quote(text, safe='/-_.~' if keep_slash else '-_.~')


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode('utf-8'), hashlib.sha256).digest()


def presign(
    location: S3Location,
    method: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    expires: int,
    moment: datetime | None = None,
    query: dict[str, str] | None = None,
) -> str:
    """A URL that performs ``method`` on ``key`` (empty: the bucket itself) until ``expires`` seconds pass."""
    if not 0 < expires <= MAXIMUM_EXPIRY:
        raise ValueError('S3 presigned URLs expire after 1 second to 7 days')
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp, day = moment.strftime('%Y%m%dT%H%M%SZ'), moment.strftime('%Y%m%d')
    endpoint = urlsplit(location.endpoint)
    if location.addressing == 'virtual':
        host = f'{location.bucket}.{endpoint.netloc}'
        path = '/' + _encode(key, keep_slash=True)
    else:
        host = endpoint.netloc
        path = f'/{location.bucket}' + ('/' + _encode(key, keep_slash=True) if key else '')
    scope = f'{day}/{location.region}/s3/aws4_request'
    parameters = {
        **(query or {}),
        'X-Amz-Algorithm': ALGORITHM,
        'X-Amz-Credential': f'{access_key_id}/{scope}',
        'X-Amz-Date': stamp,
        'X-Amz-Expires': str(expires),
        'X-Amz-SignedHeaders': 'host',
    }
    canonical_query = '&'.join(
        f'{_encode(name, False)}={_encode(value, False)}' for name, value in sorted(parameters.items())
    )
    canonical_request = '\n'.join([method, path, canonical_query, f'host:{host}', '', 'host', UNSIGNED_PAYLOAD])
    to_sign = '\n'.join([ALGORITHM, stamp, scope, hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()])
    signing_key = _sign(
        _sign(_sign(_sign(('AWS4' + secret_access_key).encode('utf-8'), day), location.region), 's3'), 'aws4_request'
    )
    signature = hmac.new(signing_key, to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{endpoint.scheme}://{host}{path}?{canonical_query}&X-Amz-Signature={signature}'
