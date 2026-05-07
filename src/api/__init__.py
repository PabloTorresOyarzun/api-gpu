"""
api — Configuración y punto de entrada de la aplicación Litestar.
"""

from litestar import Litestar
from litestar.openapi import OpenAPIConfig
from litestar.openapi.spec import Components, SecurityScheme
from litestar.openapi.plugins import SwaggerRenderPlugin
from litestar.middleware import DefineMiddleware

from ..services.extractor import cargar_modelos
from .middleware import FiltroIPMiddleware
from .endpoints import (
    extraer_endpoint,
    sanitizar_endpoint,
    clasificar_endpoint,
    procesar_endpoint,
    health,
)


async def on_startup() -> None:
    print("Iniciando API. Precargando modelos de Surya...")
    cargar_modelos()
    print("API lista para recibir requests.")


app = Litestar(
    route_handlers=[
        extraer_endpoint,
        sanitizar_endpoint,
        clasificar_endpoint,
        procesar_endpoint,
        health,
    ],
    middleware=[DefineMiddleware(FiltroIPMiddleware)],
    on_startup=[on_startup],
    openapi_config=OpenAPIConfig(
        title="BL Extractor API",
        version="2.0.0",
        description=(
            "API para procesamiento de documentos de comercio exterior.\n\n"
            "**Pipeline completo**: Conversión → Sanitización → OCR (Surya GPU) → Clasificación → Extracción (LLM).\n\n"
            "**Endpoints**:\n"
            "- `/extraer` — Extracción directa de BL desde PDF\n"
            "- `/procesar` — Pipeline completo (cualquier formato de archivo)\n"
            "- `/sanitizar` — Solo sanitización y análisis de calidad\n"
            "- `/clasificar` — Clasificación y segmentación de documentos\n"
        ),
        render_plugins=[SwaggerRenderPlugin()],
        path="/docs",
        components=Components(
            security_schemes={
                "ApiKeyAuth": SecurityScheme(
                    type="apiKey",
                    security_scheme_in="header",
                    name="X-API-Key",
                    description="API key de acceso. Configurada en la variable de entorno API_KEY del servidor.",
                )
            },
        ),
        security=[{"ApiKeyAuth": []}],
    ),
    debug=True,
)
