"""
services — Módulos de procesamiento de documentos.

pipeline/
  - extractor: OCR (Surya GPU) + extracción estructurada (LLM)
  - converter: Validación y conversión de formatos a PDF
  - sanitizer: Análisis de calidad y reparación de PDFs
  - classifier: Clasificación por patrones y segmentación

prompts/
  - transporte: Prompt y esquema JSON para documentos de transporte (B/L)
  - factura: Prompt y esquema JSON para facturas comerciales
  - lista_embalaje: Prompt y esquema JSON para listas de embalaje
  - certificado_origen: Prompt y esquema JSON para certificados de origen
"""
