import os
import ipaddress
import logging

from litestar import Request
from litestar.exceptions import NotAuthorizedException
from litestar.middleware.base import MiddlewareProtocol
from litestar.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


# ===========================================================================
# Configuración de seguridad
# ===========================================================================

API_KEY = os.getenv("API_KEY", "")
IPS_PERMITIDAS_RAW = os.getenv("IPS_PERMITIDAS", "")
REDES_PERMITIDAS = [
    ipaddress.ip_network(red.strip(), strict=False)
    for red in IPS_PERMITIDAS_RAW.split(",")
    if red.strip()
]


# ===========================================================================
# Filtro de IP
# ===========================================================================

def ip_permitida(ip_str: str) -> bool:
    if not REDES_PERMITIDAS:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in red for red in REDES_PERMITIDAS)
    except ValueError:
        return False


class FiltroIPMiddleware(MiddlewareProtocol):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            cliente = scope.get("client")
            if cliente:
                ip = cliente[0]
                if not ip_permitida(ip):
                    response = {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"application/json")],
                    }
                    await send(response)
                    await send({
                        "type": "http.response.body",
                        "body": b'{"detail": "IP no autorizada"}',
                    })
                    return
        await self.app(scope, receive, send)


# ===========================================================================
# Validación de API Key
# ===========================================================================

def validar_api_key(request: Request) -> None:
    if not API_KEY:
        return
    key_recibida = request.headers.get("X-API-Key")
    if key_recibida != API_KEY:
        raise NotAuthorizedException(detail="API key inválida o faltante")
