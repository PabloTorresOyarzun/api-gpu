"""
main — Punto de entrada de la aplicación Litestar.
"""

from litestar import Litestar
from litestar.openapi import OpenAPIConfig
from litestar.openapi.spec import Components, SecurityScheme
from litestar.openapi.plugins import SwaggerRenderPlugin
from litestar.middleware import DefineMiddleware

from .services.pipeline.extractor import cargar_modelos
from .api.middleware import FiltroIPMiddleware
from .api.endpoints import procesar_endpoint, procesar_endpoint_vl, health


async def on_startup() -> None:
    print("Iniciando API. Precargando modelos de Surya...")
    cargar_modelos()
    print("API lista para recibir requests.")


app = Litestar(
    route_handlers=[procesar_endpoint, procesar_endpoint_vl, health],
    middleware=[DefineMiddleware(FiltroIPMiddleware)],
    on_startup=[on_startup],
    openapi_config=OpenAPIConfig(
        title="BL Extractor API",
        version="2.0.0",
        description=(
            "API para procesamiento de documentos de comercio exterior.\n\n"
            "**Pipeline OCR** (`/procesar`): Conversión → Sanitización → "
            "OCR (Surya GPU) → Clasificación → Extracción (LLM texto).\n\n"
            "**Pipeline VL** (`/procesar-vl`): Conversión → Sanitización → "
            "Imágenes → Clasificación + Extracción (Qwen3-VL multimodal)."
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
