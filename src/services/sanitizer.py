"""
Sanitización y análisis de calidad de PDFs.
- Análisis de orientación (OSD, vectorial, EXIF)
- Detección de skew (inclinación)
- Detección de contraste
- Reparación de CropBox corrupto
- Corrección automática de rotación
"""
import io
import math
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field

import cv2
import fitz
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


# ===========================================================================
# Configuracion
# ===========================================================================

@dataclass
class AnalysisConfig:
    """Umbrales para el analisis de orientacion."""

    skew_tolerance: float = 1.0
    zone_skew_diff_tolerance: float = 1.5
    zone_outlier_threshold: float = 2.0
    zone_min_lines: int = 10
    ambiguous_angle_range: float = 10.0
    contrast_dark_ratio: float = 0.003
    contrast_light_ratio: float = 0.10

    render_dpi: int = 150
    max_workers: int = 4

    stamp_min_aspect_ratio: float = 0.7
    stamp_min_area_ratio: float = 0.005
    stamp_max_area_ratio: float = 0.15
    cluster_radius: float = 5.0

    # Umbrales escaneado vs digital
    scanned_image_ratio: float = 0.7
    scanned_max_text_len: int = 200


# ===========================================================================
# Modelo de resultado
# ===========================================================================

@dataclass
class PageAnalysis:
    page: int
    is_scanned: bool
    declared_rotation: int
    has_text: bool
    has_images: bool
    has_drawings: bool
    is_empty: bool
    has_visible_text: bool = False
    low_contrast: bool = False
    detected_rotation: int = 0
    rotation_score: float = 0.0
    opencv_skew_angle: float | None = None
    skew_label: str = "NORMAL"
    zone_skew_angles: list[float] = field(default_factory=list)
    zone_skew_uniform: bool = True
    vector_rotation_detected: float | None = None
    exif_orientation: int | None = None
    dominant_edge_angle: float | None = None
    is_straight: bool = True
    issues: list[str] = field(default_factory=list)


# ===========================================================================
# Clasificacion y Orientacion
# ===========================================================================

def _is_scanned_page(page: fitz.Page, cfg: AnalysisConfig) -> bool:
    area_pagina = abs(page.rect.width * page.rect.height)
    imagenes = page.get_images(full=True)
    texto = page.get_text().strip()

    if not imagenes:
        return False

    if len(texto) > cfg.scanned_max_text_len:
        return False

    area_imagenes = 0
    for img in imagenes:
        bbox = page.get_image_bbox(img)
        if bbox:
            area_imagenes += abs(bbox.width * bbox.height)

    ocupacion = area_imagenes / area_pagina if area_pagina > 0 else 0
    return ocupacion > cfg.scanned_image_ratio and len(texto) < cfg.scanned_max_text_len


def _detectar_orientacion_digital(pagina: fitz.Page, cfg: AnalysisConfig = None) -> tuple[int, str]:
    """Detecta orientación de una página digital.
    
    Retorna (angulo_rotacion, etiqueta):
    - angulo_rotacion: 0, 90, 180, 270
    - etiqueta: "NORMAL", "ROTADA_90", "ROTADA_180", "ROTADA_270", "SIN TEXTO"
    
    Estrategia:
    1. Analizar spans para detectar 90°/270° (texto vertical vs horizontal)
    2. Analizar vectores de dirección de líneas para detectar 180° rápido
    3. Solo si hay ambigüedad, usar Tesseract OSD como fallback
    """
    text_dict = pagina.get_text("dict")
    directions = {"horizontal": 0, "vertical": 0}
    
    # Contadores de dirección vectorial para detección rápida de 180°
    dir_normal = 0      # dx ≈ 1 (texto izquierda→derecha)
    dir_invertido = 0   # dx ≈ -1 (texto derecha→izquierda = 180°)
    total_lines = 0

    for block in text_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                # Análisis de dirección vectorial
                dx, dy = line.get("dir", (1, 0))
                total_lines += 1
                if dx < -0.5:
                    dir_invertido += 1
                elif dx > 0.5:
                    dir_normal += 1
                
                for span in line.get("spans", []):
                    bbox = span["bbox"]
                    width = bbox[2] - bbox[0]
                    height = bbox[3] - bbox[1]

                    if height > width * 1.5:
                        directions["vertical"] += 1
                    elif width > height * 1.5:
                        directions["horizontal"] += 1

    total = sum(directions.values())
    if total == 0:
        return 0, "SIN TEXTO"

    vertical_ratio = directions["vertical"] / total

    # 90° o 270°: texto vertical detectado por spans
    if vertical_ratio > 0.6:
        # Usar Tesseract para distinguir entre 90° y 270°
        if cfg is None:
            cfg = AnalysisConfig()
        img = _render_page(pagina, cfg.render_dpi)
        rot, conf, _ = _tesseract_osd_rotation(img)
        if rot in (90, 270) and conf >= 1.0:
            return rot, f"ROTADA_{rot}"
        return 270, "ROTADA_270"

    # Detección rápida de 180° por vectores de dirección
    # En un PDF digital rotado 180°, PyMuPDF reporta dir=(-1, 0)
    if total_lines >= 3:
        invertido_ratio = dir_invertido / total_lines
        if invertido_ratio > 0.5:
            return 180, "ROTADA_180"
        # Si la mayoría de líneas son normales, no está invertido
        if dir_normal / total_lines > 0.7:
            return 0, "NORMAL"

    # Caso ambiguo: pocas líneas o dirección mezclada → Tesseract OSD fallback
    if cfg is None:
        cfg = AnalysisConfig()
    img = _render_page(pagina, cfg.render_dpi)
    rot, conf = _validate_scanned_rotation(img)
    if rot == 180:
        return 180, "ROTADA_180"

    return 0, "NORMAL"


def _tesseract_osd_rotation(img_rgb: np.ndarray) -> tuple[int, float, float]:
    """
    Usa Tesseract exclusivamente para detectar la orientacion cardinal (0, 90, 180, 270).
    Retorna (rotacion_corregida, orientation_conf, script_conf).
    Tesseract OSD reporta la orientación actual del texto en grados Anti-Horarios.
    Para corregirlo en PyMuPDF (que rota Horario), invertimos el ángulo: (360 - OSD) % 360.
    """
    try:
        pil_img = Image.fromarray(img_rgb)
        osd = pytesseract.image_to_osd(pil_img, output_type=pytesseract.Output.DICT)
        orientacion_osd = osd.get("orientation", 0)
        
        # Invertir para PyMuPDF
        rotacion_corregida = (360 - orientacion_osd) % 360 if orientacion_osd != 0 else 0
        
        return rotacion_corregida, osd.get("orientation_conf", 0.0), osd.get("script_conf", 0.0)
    except Exception as e:
        logger.warning(f"Error en Tesseract OSD: {e}")
        return 0, 0.0, 0.0


def _horizontal_text_ratio(gray: np.ndarray) -> float:
    """Proporcion de blobs de texto que son horizontales (0.0 a 1.0)."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_lines = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    )
    v_lines = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    )
    text_only = cv2.subtract(cv2.subtract(binary, h_lines), v_lines)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 3))
    word_blobs = cv2.morphologyEx(text_only, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(
        word_blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    horizontal_count = 0
    total_count = 0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < 100 or w < 8 or h < 4:
            continue
        total_count += 1
        if w > h * 1.5:
            horizontal_count += 1
    return (horizontal_count / total_count) if total_count > 0 else 0.0

def _validate_scanned_rotation(img_rgb: np.ndarray, page_text: str = "") -> tuple[int, float]:
    """
    Validacion robusta de orientacion para paginas escaneadas.
    Integra analisis OSD y validacion empirica de dual-pass.
    """
    MIN_CONF_90_270 = 1.0
    MIN_CONF_180 = 2.0
    MIN_SCRIPT_CONF_180 = 5.0  # Safeguard contra páginas numéricas/vacías
    AMBIGUITY_MARGIN = 1.5

    rot, conf, script_conf = _tesseract_osd_rotation(img_rgb)

    if rot == 0:
        return 0, conf

    # 90 o 270: validacion simple
    if rot in (90, 270):
        if conf >= MIN_CONF_90_270:
            return rot, conf
        return 0, conf

    # 180: validacion estricta con dual-pass y script_conf
    if rot == 180:
        if conf < MIN_CONF_180 or script_conf < MIN_SCRIPT_CONF_180:
            return 0, conf

        img_flipped = cv2.rotate(img_rgb, cv2.ROTATE_180)
        rot_inv, conf_inv, _ = _tesseract_osd_rotation(img_flipped)

        if rot_inv == 0:
            return 180, conf

        if rot_inv == 180:
            if conf_inv >= (conf - AMBIGUITY_MARGIN):
                return 0, conf
            return 180, conf

        if conf >= MIN_CONF_180 * 2 and script_conf >= MIN_SCRIPT_CONF_180:
            return 180, conf
        return 0, conf

    return 0, conf

def _get_skew_label(angle: float) -> str:
    abs_angle = abs(angle)
    if abs_angle < 0.5:
        return "Normal"
    elif abs_angle < 2.0:
        return "Ligeramente inclinada"
    elif abs_angle < 10.0:
        return "Inclinada"
    else:
        return "Muy inclinada"

def _get_rotation_label(angle: int) -> str:
    """Traduce el angulo de rotacion a un diagnostico de estado."""
    if angle == 90:
        return "Rotación de 90° (Sentido horario)"
    elif angle == 180:
        return "Rotación de 180° (Invertida)"
    elif angle == 270:
        return "Rotación de 270° (Sentido antihorario)"
    return f"Rotación anómala ({angle}°)"


# ===========================================================================
# Preprocesamiento general
# ===========================================================================

def _find_content_bbox(gray: np.ndarray) -> tuple[int, int, int, int]:
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    coords = cv2.findNonZero(binary)
    if coords is None:
        h, w = gray.shape
        return 0, 0, w, h
    return cv2.boundingRect(coords)

def _is_low_contrast(gray: np.ndarray, cfg: AnalysisConfig) -> bool:
    x, y, w, h = _find_content_bbox(gray)
    content = gray[y:y + h, x:x + w]
    if content.size == 0:
        return True
    total = content.size
    dark_ratio = np.sum(content < 80) / total
    light_ratio = np.sum(content > 180) / total
    return not (dark_ratio > cfg.contrast_dark_ratio and light_ratio > cfg.contrast_light_ratio)

def _enhance_contrast(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)

def _has_visible_text_morph(gray: np.ndarray, min_word_blobs: int = 5) -> bool:
    """Intenta contar parches de texto."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1)))
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40)))
    text_only = cv2.subtract(cv2.subtract(binary, h_lines), v_lines)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 3))
    word_blobs = cv2.morphologyEx(text_only, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(word_blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    horizontal_count = 0
    total_count = 0
    
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < 100 or w < 8 or h < 4:
            continue
        total_count += 1
        if w > h * 1.5:
            horizontal_count += 1
            
    return (horizontal_count / total_count) > 0.0 if total_count > 0 else False

def _preprocess(img_rgb: np.ndarray, cfg: AnalysisConfig) -> tuple[np.ndarray, np.ndarray, bool]:
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    low = _is_low_contrast(gray, cfg)
    gray_analysis = _enhance_contrast(gray) if low else gray
    return gray, gray_analysis, low

def _page_has_content(page: fitz.Page) -> tuple[bool, bool, bool]:
    has_text = bool(page.get_text("text").strip())
    has_images = len(page.get_images(full=True)) > 0
    has_drawings = len(page.get_drawings()) > 0
    return has_text, has_images, has_drawings

def _render_page(page: fitz.Page, dpi: int = 200) -> np.ndarray:
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return img

# ===========================================================================
# Mascara y Skew
# ===========================================================================

def _mask_graphic_regions(gray: np.ndarray, cfg: AnalysisConfig) -> np.ndarray:
    """Busca y enmascara regiones graficas como logos y sellos grandes."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    h, w = gray.shape
    page_area = w * h
    result = gray.copy()

    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if max(cw, ch) == 0:
            continue
            
        aspect = min(cw, ch) / max(cw, ch)
        area_ratio = (cw * ch) / page_area

        if (aspect > cfg.stamp_min_aspect_ratio 
            and cfg.stamp_min_area_ratio < area_ratio < cfg.stamp_max_area_ratio):
            cv2.rectangle(result, (x, y), (x + cw, y + ch), 255, -1)

    return result

def _opencv_skew(gray: np.ndarray, cfg: AnalysisConfig, mask_graphics: bool = True) -> float | None:
    try:
        work = _mask_graphic_regions(gray, cfg) if mask_graphics else gray
        edges = cv2.Canny(work, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 100, minLineLength=100, maxLineGap=10)
        if lines is None or len(lines) == 0:
            return None

        angles, weights = [], []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            a = angle % 90
            a = a if a <= 45 else a - 90
            if abs(a) >= (45 - cfg.ambiguous_angle_range):
                continue
            angles.append(a)
            weights.append(length)

        if not angles:
            return None

        arr = np.array(angles)
        w = np.array(weights)

        sorted_idx = np.argsort(arr)
        sorted_arr = arr[sorted_idx]
        sorted_w = w[sorted_idx]
        cumw = np.cumsum(sorted_w)
        if cumw[-1] == 0:
            return None
        median_idx = np.searchsorted(cumw, cumw[-1] / 2)
        center = sorted_arr[min(median_idx, len(sorted_arr) - 1)]

        mask = np.abs(arr - center) < cfg.cluster_radius
        if not np.any(mask):
            return None

        return float(np.average(arr[mask], weights=w[mask]))
    except Exception:
        return None

def _find_content_bands(gray: np.ndarray, min_band_height: int = 50) -> list[tuple[int, int]]:
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    projection = np.sum(binary, axis=1)
    threshold = np.max(projection) * 0.1 if np.max(projection) > 0 else 0

    bands = []
    in_band = False
    start = 0

    for i, val in enumerate(projection):
        if val > threshold and not in_band:
            start = i
            in_band = True
        elif val <= threshold and in_band:
            if i - start > min_band_height:
                bands.append((start, i))
            in_band = False

    if in_band and len(projection) - start > min_band_height:
        bands.append((start, len(projection)))

    return bands

def _zone_skew_analysis(gray: np.ndarray, cfg: AnalysisConfig, global_angle: float | None = None) -> tuple[list[float], bool]:
    bands = _find_content_bands(gray)

    if len(bands) < 2:
        x, y, w, h = _find_content_bbox(gray)
        zone_h = h // 3
        if zone_h < 50:
            return [], True
        bands = [(y + i * zone_h, y + (i + 1) * zone_h) for i in range(3)]

    zone_angles = []
    for start, end in bands:
        zone = gray[start:end, :]
        edges = cv2.Canny(zone, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 80, minLineLength=80, maxLineGap=10)
        if lines is None or len(lines) < cfg.zone_min_lines:
            continue
        angle = _opencv_skew(zone, cfg, mask_graphics=True)
        if angle is not None:
            zone_angles.append(round(angle, 3))

    if len(zone_angles) < 2:
        return zone_angles, True

    if len(zone_angles) >= 3:
        ref = float(np.median(zone_angles))
    elif global_angle is not None:
        ref = global_angle
    else:
        ref = np.mean(zone_angles)

    filtered = [a for a in zone_angles if abs(a - ref) < cfg.zone_outlier_threshold]

    if len(filtered) < 2:
        return zone_angles, True

    max_adj_diff = max(abs(filtered[i] - filtered[i + 1]) for i in range(len(filtered) - 1))
    return zone_angles, max_adj_diff < cfg.zone_skew_diff_tolerance

def _dominant_edge_angle(gray: np.ndarray, cfg: AnalysisConfig) -> float | None:
    try:
        edges = cv2.Canny(gray, 30, 100, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 50, minLineLength=50, maxLineGap=20)
        if lines is None or len(lines) < 5:
            return None

        angles, weights = [], []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            a = angle % 90
            a = a if a <= 45 else a - 90
            if abs(a) >= (45 - cfg.ambiguous_angle_range):
                continue
            angles.append(a)
            weights.append(length)

        if not angles:
            return None

        return round(float(np.average(np.array(angles), weights=np.array(weights))), 3)
    except Exception:
        return None

def _detect_vector_rotation(page: fitz.Page) -> float | None:
    try:
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        rotation_angles = []
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                dx, dy = line.get("dir", (1, 0))
                angle = math.degrees(math.atan2(dy, dx))
                if abs(angle) > 0.5:
                    rotation_angles.append(round(angle, 3))
        if not rotation_angles:
            return None
        median_angle = float(np.median(rotation_angles))
        return round(median_angle, 3) if abs(median_angle) >= 0.5 else None
    except Exception:
        return None

EXIF_DESCRIPTIONS = {
    2: "Espejado horizontal",
    3: "Rotado 180 grados",
    4: "Espejado vertical",
    5: "Espejado horizontal + rotado 270 grados",
    6: "Rotado 90 grados (CW)",
    7: "Espejado horizontal + rotado 90 grados",
    8: "Rotado 270 grados (CW)",
}

def _check_exif_orientation(page: fitz.Page, doc: fitz.Document) -> int | None:
    try:
        images = page.get_images(full=True)
        if not images:
            return None
        for img_info in images:
            xref = img_info[0]
            img_bytes = doc.extract_image(xref)
            if not img_bytes:
                continue
            try:
                pil_img = Image.open(io.BytesIO(img_bytes["image"]))
                exif = pil_img.getexif()
                if exif:
                    orient = exif.get(274, None)
                    if orient is not None and orient != 1:
                        return orient
            except Exception:
                continue
        return None
    except Exception:
        return None

# ===========================================================================
# Analisis principal por pagina
# ===========================================================================

def _analyze_page_from_bytes(pdf_bytes: bytes, page_idx: int, cfg: AnalysisConfig) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_idx]
    result = _analyze_page(page, page_idx + 1, doc, cfg)
    doc.close()
    return result

def _analyze_page(page: fitz.Page, page_num: int, doc: fitz.Document, cfg: AnalysisConfig) -> dict:
    has_text, has_images, has_drawings = _page_has_content(page)
    is_empty = not has_text and not has_images and not has_drawings
    is_scanned = _is_scanned_page(page, cfg)

    analysis = PageAnalysis(
        page=page_num,
        is_scanned=is_scanned,
        declared_rotation=page.rotation,
        has_text=has_text,
        has_images=has_images,
        has_drawings=has_drawings,
        is_empty=is_empty,
    )

    if is_scanned:
        analysis.issues.append("Documento: Página escaneada")

    if has_text or has_drawings:
        vec_rot = _detect_vector_rotation(page)
        if vec_rot is not None:
            analysis.vector_rotation_detected = vec_rot
            analysis.is_straight = False
            analysis.issues.append(f"Orientación: Rotación vectorial detectada ({vec_rot}°)")

    if has_images:
        exif_orient = _check_exif_orientation(page, doc)
        if exif_orient is not None:
            analysis.exif_orientation = exif_orient
            desc = EXIF_DESCRIPTIONS.get(exif_orient, f"Valor {exif_orient}")
            analysis.issues.append(f"Metadatos: EXIF de imagen anormal ({desc})")

    if is_empty:
        analysis.issues.append("Contenido: Página vacía")
        return _sanitize(analysis.__dict__)

    img = _render_page(page, cfg.render_dpi)
    gray_original, gray_analysis, low_contrast = _preprocess(img, cfg)
    
    analysis.low_contrast = low_contrast
    if low_contrast:
        analysis.issues.append("Visual: Bajo contraste")

    analysis.has_visible_text = _has_visible_text_morph(gray_original)

    if is_scanned:
        # Solo confiar en la rotación si hay texto visible suficiente
        # (OSD falla mucho con páginas numéricas vacías)
        if analysis.has_visible_text:
            best_rot, score = _validate_scanned_rotation(img)
            analysis.detected_rotation = best_rot
            analysis.rotation_score = round(score, 3)
            if best_rot != 0:
                analysis.is_straight = False
                estado_rotacion = _get_rotation_label(best_rot)
                analysis.issues.append(f"Orientación: {estado_rotacion}")
        else:
            analysis.detected_rotation = 0
            analysis.rotation_score = 0.0
    else:
        rot_angle, orient_label = _detectar_orientacion_digital(page, cfg)
        if rot_angle != 0:
            analysis.detected_rotation = rot_angle
            analysis.is_straight = False
            estado_rotacion = _get_rotation_label(rot_angle)
            analysis.issues.append(f"Orientación: {estado_rotacion}")

    # Skew OpenCV
    ocv = _opencv_skew(gray_analysis, cfg, mask_graphics=True)
    if ocv is not None:
        analysis.opencv_skew_angle = round(ocv, 3)
        analysis.skew_label = _get_skew_label(ocv)
        if abs(ocv) >= cfg.skew_tolerance:
            analysis.is_straight = False
            analysis.issues.append(f"Inclinación: {analysis.skew_label} ({analysis.opencv_skew_angle}°)")

    zone_angles, zone_uniform = _zone_skew_analysis(gray_analysis, cfg, global_angle=analysis.opencv_skew_angle)
    analysis.zone_skew_angles = zone_angles
    analysis.zone_skew_uniform = zone_uniform
    if not zone_uniform:
        analysis.is_straight = False
        analysis.issues.append("Inclinación: Deformación de escaneo (No uniforme)")

    if not analysis.has_visible_text and not has_text:
        edge_angle = _dominant_edge_angle(gray_analysis, cfg)
        analysis.dominant_edge_angle = edge_angle
        if edge_angle is not None and abs(edge_angle) >= cfg.skew_tolerance:
            analysis.is_straight = False
            analysis.skew_label = _get_skew_label(edge_angle)
            analysis.issues.append(f"Inclinación: Bordes inclinados ({edge_angle}°)")

    return _sanitize(analysis.__dict__)

def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


# ===========================================================================
# API publica
# ===========================================================================

def analyze_pdf_bytes(pdf_bytes: bytes, filename: str, cfg: AnalysisConfig | None = None, detailed: bool = False) -> dict:
    if cfg is None:
        cfg = AnalysisConfig()

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    doc.close()

    # Reducir DPI para documentos grandes para acelerar renderizado
    if total_pages > 15 and cfg.render_dpi > 100:
        cfg.render_dpi = 100

    if total_pages > 2 and cfg.max_workers > 1:
        # Para documentos grandes, limitar workers para evitar saturación
        effective_workers = min(cfg.max_workers, total_pages)
        if total_pages > 15:
            effective_workers = min(2, effective_workers)
            logger.info(f"Documento grande ({total_pages} páginas): limitando workers a {effective_workers}, DPI reducido")
        results = [None] * total_pages
        with ProcessPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(_analyze_page_from_bytes, pdf_bytes, i, cfg): i
                for i in range(total_pages)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.warning(f"Error analizando página {idx + 1}: {e}")
                    results[idx] = {
                        "page": idx + 1,
                        "is_straight": True,
                        "issues": [f"Error en análisis: {str(e)}"]
                    }
    else:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        results = [_analyze_page(doc[i], i + 1, doc, cfg) for i in range(total_pages)]
        doc.close()

    straight_count = sum(1 for r in results if r.get("is_straight", True))

    # Filtramos los resultados si no se requieren detalles
    if not detailed:
        results = [{"page": r["page"], "issues": r["issues"]} for r in results]

    return {
        "filename": filename,
        "total_pages": total_pages,
        "straight_pages": straight_count,
        "problematic_pages": total_pages - straight_count,
        "pages": results,
    }


# ===========================================================================
# Reparación y Sanitización
# ===========================================================================

def reparar_pdf(pdf_doc: fitz.Document) -> bool:
    """
    Repara errores de geometría en PDFs (CropBox fuera de MediaBox).
    Usa acceso de bajo nivel (xref) para evitar ValueError al leer page.cropbox.
    Retorna True si hubo cambios.
    """
    cambios = False
    try:
        for page in pdf_doc:
            try:
                xref = page.xref
                cropbox_type, cropbox_val = pdf_doc.xref_get_key(xref, "CropBox")

                if cropbox_type == "null" or cropbox_val == "null":
                    continue

                if cropbox_type == "array":
                    try:
                        _ = page.rect
                    except ValueError:
                        logger.warning(f"Página {page.number + 1}: CropBox corrupto, eliminando para usar MediaBox.")
                        pdf_doc.xref_set_key(xref, "CropBox", "null")
                        cambios = True
            except Exception as e:
                logger.warning(f"Error reparando página {page.number + 1}: {e}")
                try:
                    pdf_doc.xref_set_key(page.xref, "CropBox", "null")
                    cambios = True
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error general reparando PDF: {e}")

    return cambios


def sanitizar_pdf(pdf_bytes: bytes, cfg: AnalysisConfig | None = None) -> tuple[bytes, list[dict]]:
    """
    Pipeline completo de sanitización:
    1. Repara geometría (CropBox corrupto)
    2. Analiza calidad (orientación, skew, contraste)
    3. Corrige rotaciones detectadas

    Retorna (pdf_bytes_corregido, lista_alertas_por_pagina).
    """
    if cfg is None:
        cfg = AnalysisConfig()

    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Paso 1: Reparar geometría
    reparado = reparar_pdf(pdf_doc)
    if reparado:
        pdf_bytes = pdf_doc.tobytes()

    # Paso 2: Analizar calidad
    analisis = analyze_pdf_bytes(pdf_bytes, "doc.pdf", cfg, detailed=True)

    # Paso 3: Corregir rotaciones
    paginas_corregidas = 0
    for resultado_pagina in analisis.get("pages", []):
        if not resultado_pagina.get("is_straight", True):
            idx = resultado_pagina["page"] - 1
            pagina = pdf_doc[idx]

            rot_detectada = resultado_pagina.get("detected_rotation", 0)
            if rot_detectada != 0:
                rot_actual = pagina.rotation
                rot_compensada = (rot_actual + rot_detectada) % 360
                pagina.set_rotation(rot_compensada)
                paginas_corregidas += 1

    if paginas_corregidas > 0 or reparado:
        pdf_bytes = pdf_doc.tobytes()

    pdf_doc.close()
    return pdf_bytes, analisis.get("pages", [])
