"""
Servidor Flask que envuelve el SDK glmocr[selfhosted].

Expone POST /parse → recibe imagen (multipart o base64), corre layout analysis +
GLM-OCR vía el SDK oficial, devuelve markdown estructurado.

Vive en su propio contenedor porque las deps del SDK (paddlepaddle, pillow>=12)
chocan con las del API principal (surya-ocr exige pillow<11).
"""
import os
import base64
import logging
import tempfile
from urllib.parse import urlparse

import yaml
from flask import Flask, request, jsonify
from glmocr import GlmOcr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

URL_OLLAMA = os.getenv("URL_OLLAMA", "http://host.docker.internal:11434/api/generate")
MODELO_OCR_VL = os.getenv("MODELO_OCR_VL", "glm-ocr:bf16")
CONFIG_PATH = "/tmp/glmocr_config.yaml"


def _build_config() -> str:
    parsed = urlparse(URL_OLLAMA)
    config = {
        "pipeline": {
            "maas": {"enabled": False},
            "ocr_api": {
                "api_host": parsed.hostname or "localhost",
                "api_port": parsed.port or 11434,
                "api_path": parsed.path or "/api/generate",
                "model": MODELO_OCR_VL,
                "api_mode": "ollama_generate",
            },
        },
    }
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f)
    logger.info(f"Config generado: host={parsed.hostname} port={parsed.port} model={MODELO_OCR_VL}")
    return CONFIG_PATH


_build_config()
_parser_ctx = GlmOcr(config_path=CONFIG_PATH)
parser = _parser_ctx.__enter__()

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/parse", methods=["POST"])
def parse():
    tmp_path = None
    try:
        if "file" in request.files:
            file = request.files["file"]
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name
        else:
            data = request.get_json(force=True)
            img_bytes = base64.b64decode(data["image_base64"])
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name

        result = parser.parse(tmp_path)
        return jsonify({
            "markdown": result.markdown_result or "",
        })
    except Exception as e:
        logger.exception("Error parseando imagen")
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
