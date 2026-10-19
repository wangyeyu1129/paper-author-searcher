"""DNS-checked, pinned public connections with normal TLS hostname verification."""

import ipaddress
import socket
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler, build_opener

# urllib's sentinel meaning "use the socket module's global default timeout".
DEFAULT_SOCKET_TIMEOUT = getattr(socket, '_GLOBAL_DEFAULT_TIMEOUT', None)


def is_public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    mapped = getattr(address, 'ipv4_mapped', None)
    if mapped is not None:
        return is_public_address(str(mapped))
    translated = getattr(address, 'sixtofour', None)
    if translated is not None and not is_public_address(str(translated)):
        return False
    teredo = getattr(address, 'teredo', None)
    return not (teredo is not None and not all(is_public_address(str(endpoint)) for endpoint in teredo))


def public_addresses(url, allowed_ports):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in allowed_ports:
        raise ValueError('URL port is outside the configured public download policy')
    if not parsed.hostname or '%' in parsed.hostname:
        raise ValueError('Invalid or scoped destination hostname')
    answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise ValueError('Destination DNS includes a nonpublic address')
    return addresses


def pinned_connection(connection_type, addresses):
    def create_connection(host, **kwargs):
        connection = connection_type(host, **kwargs)

        def connect_address(destination, timeout=DEFAULT_SOCKET_TIMEOUT, source_address=None):
            last_error = None
            for address in addresses:
                try:
                    return socket.create_connection((address, destination[1]), timeout, source_address)
                except OSError as exc:
                    last_error = exc
            raise last_error or OSError('No public address available for ' + str(destination[0]))

        # HTTPConnection still sends the original Host; HTTPSConnection retains its SNI/TLS checks.
        connection._create_connection = connect_address
        return connection

    return create_connection


class PublicHTTPHandler(HTTPHandler):
    def __init__(self, allowed_ports):
        super().__init__()
        self.allowed_ports = allowed_ports

    def http_open(self, request):
        addresses = public_addresses(request.full_url, self.allowed_ports)
        return self.do_open(pinned_connection(HTTPConnection, addresses), request)


class PublicHTTPSHandler(HTTPSHandler):
    def __init__(self, allowed_ports):
        super().__init__()
        self.allowed_ports = allowed_ports

    def https_open(self, request):
        addresses = public_addresses(request.full_url, self.allowed_ports)
        return self.do_open(
            pinned_connection(HTTPSConnection, addresses),
            request,
            context=getattr(self, '_context', None),
            check_hostname=getattr(self, '_check_hostname', None),
        )


def secure_download_opener(redirect_handler, allowed_ports):
    # Environmental proxies would resolve/tunnel destinations outside the checked connection path.
    return build_opener(
        ProxyHandler({}), PublicHTTPHandler(allowed_ports), PublicHTTPSHandler(allowed_ports), redirect_handler
    )
