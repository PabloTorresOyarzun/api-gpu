# Pendientes y casos a analizar

## Agrupamiento de páginas UNKNOWN_DOCUMENT

**Fecha:** 2026-05-13
**Pipeline:** `/procesar-hibrido`

### Caso

Documento de 4 páginas (`511944_otro_1_2025-04-17_121237.pdf`):
- Página 1: FACTURA_COMERCIAL
- Página 2: LISTA_EMBALAJE
- Página 3: CERTIFICADO_ORIGEN
- Página 4: reverso del CO ("Overleaf Instruction"), originalmente invertida 180°

### Comportamiento actual

1. Sanitizador detecta y corrige rotación 180° de página 4 ✓
2. OCR híbrido detecta "OVERLEAF INSTRUCTION" en el texto y omite la página ✓
3. Clasificador recibe texto vacío → marca página 4 como `UNKNOWN_DOCUMENT` ✓
4. Qwen NO procesa página 4 (no hay datos que extraer) ✓
5. **Pero**: en la respuesta final, el documento CERTIFICADO_ORIGEN aparece con `"paginas": [3, 4]` en lugar de `[3]`

### Causa probable

La lógica de agrupamiento en `src/api/endpoints.py` probablemente fusiona páginas
consecutivas o absorbe páginas `UNKNOWN_DOCUMENT` dentro del documento anterior.

### Fix propuesto (cuando se aborde)

Excluir páginas `UNKNOWN_DOCUMENT` del grouping de documentos. ~1-2 líneas en
`endpoints.py` antes de construir la lista `documentos`.

### Impacto

Cosmético. La extracción de datos es correcta — solo la metadata de "qué páginas
componen este documento" es imprecisa.
