"""
Schemas Pydantic para validación/normalización post-extracción.

Cada schema:
- Define las claves canónicas que el LLM debe devolver.
- Acepta aliases comunes que Qwen tiende a inventar
  (ej. "total" → "total_amount", "incoterms" → "incoterm").
- Descarta claves desconocidas (extra="ignore").
- Sirve además como JSON Schema para forzar el output via `format` de Ollama.
"""
from .factura import Factura

SCHEMA_MAP = {
    "FACTURA_COMERCIAL": Factura,
}
