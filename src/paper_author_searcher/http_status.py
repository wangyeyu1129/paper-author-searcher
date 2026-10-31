"""HTTP status classes the clients branch on."""

from http import HTTPStatus


def is_success(status: int) -> bool:
    return HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES


def is_server_error(status: int) -> bool:
    return HTTPStatus.INTERNAL_SERVER_ERROR <= status < SERVER_ERRORS_END


SERVER_ERRORS_END = 600
