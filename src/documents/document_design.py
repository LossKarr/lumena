"""Validated visual design layer for Document Studio HTML/PDF templates."""
from __future__ import annotations

from html import escape
import re
from typing import Any, Mapping


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONTS = {
    "inter": "'Trebuchet MS', 'Segoe UI', Arial, sans-serif",
    "modern": "'Aptos', 'Segoe UI', Arial, sans-serif",
    "classic": "Georgia, 'Times New Roman', serif",
    "technical": "'Bahnschrift', 'Arial Narrow', Arial, sans-serif",
}
_DENSITIES = {
    "compact": (10.5, 1.38),
    "standard": (11.5, 1.52),
    "airy": (12.0, 1.68),
}

DEFAULT_DOCUMENT_DESIGN: dict[str, Any] = {
    "accent": "#D97706",
    "text": "#1C2430",
    "muted": "#667085",
    "surface": "#F5F7FA",
    "font": "modern",
    "density": "standard",
    "page_margin_mm": 18,
    "logo_enabled": True,
    "logo_position": "left",
    "logo_width_px": 128,
    "logo_layout": "flow",
    "logo_x_pct": 0,
    "logo_y_mm": 0,
}


def normalize_design(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    result = dict(DEFAULT_DOCUMENT_DESIGN)
    for key in ("accent", "text", "muted", "surface"):
        candidate = str(raw.get(key, result[key])).strip()
        if _HEX.fullmatch(candidate):
            result[key] = candidate.upper()
    if str(raw.get("font", "")) in _FONTS:
        result["font"] = str(raw["font"])
    if str(raw.get("density", "")) in _DENSITIES:
        result["density"] = str(raw["density"])
    if str(raw.get("logo_position", "")) in {"left", "center", "right"}:
        result["logo_position"] = str(raw["logo_position"])
    if str(raw.get("logo_layout", "")) in {"flow", "free"}:
        result["logo_layout"] = str(raw["logo_layout"])
    result["page_margin_mm"] = max(8, min(35, _as_int(raw.get("page_margin_mm"), result["page_margin_mm"])))
    result["logo_width_px"] = max(48, min(220, _as_int(raw.get("logo_width_px"), result["logo_width_px"])))
    result["logo_x_pct"] = max(0, min(100, _as_int(raw.get("logo_x_pct"), result["logo_x_pct"])))
    result["logo_y_mm"] = max(0, min(240, _as_int(raw.get("logo_y_mm"), result["logo_y_mm"])))
    if "logo_enabled" in raw:
        result["logo_enabled"] = bool(raw["logo_enabled"])
    return result


def inject_document_design(html: str, design: Mapping[str, Any] | None, *, logo_data_uri: str = "") -> str:
    normalized = normalize_design(design)
    font_size, line_height = _DENSITIES[normalized["density"]]
    compact_size = max(8.5, font_size - 1.0)
    small_size = max(8.0, font_size - 1.5)
    cell_padding = {"compact": 6, "standard": 9, "airy": 12}[normalized["density"]]
    align = {"left": "flex-start", "center": "center", "right": "flex-end"}[normalized["logo_position"]]
    if normalized["logo_layout"] == "free":
        brand_layout = (
            "position:absolute;z-index:5;pointer-events:none;"
            f"left:{normalized['logo_x_pct']}%;top:{normalized['logo_y_mm']}mm;"
            f"transform:translateX(-{normalized['logo_x_pct']}%);"
            "width:max-content;max-width:100%;display:flex;min-height:0;margin:0;"
        )
        logo_max_width = "100%"
    else:
        brand_layout = f"position:relative;display:flex;justify-content:{align};min-height:28px;margin:0 0 18px;"
        logo_max_width = "32%"
    css = f"""
<style id="lumena-document-design">
@page {{ margin: {normalized['page_margin_mm']}mm; }}
:root {{ --doc-accent:{normalized['accent']}; --doc-text:{normalized['text']}; --doc-muted:{normalized['muted']}; --doc-surface:{normalized['surface']}; }}
html {{ color-scheme:light; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
body {{ position:relative; font-family:{_FONTS[normalized['font']]} !important; color:var(--doc-text) !important; font-size:{font_size}px !important; line-height:{line_height} !important; text-rendering:optimizeLegibility; }}
@media screen {{ body {{ box-sizing:border-box; padding:{normalized['page_margin_mm']}mm !important; }} }}
body *:not(svg):not(path) {{ font-family:{_FONTS[normalized['font']]} !important; }}
p,li,.block,.info-block,.partie,.conditions,.due,.validity {{ font-size:{font_size}px !important; line-height:{line_height} !important; }}
td {{ font-size:{compact_size}px !important; line-height:{line_height} !important; padding:{cell_padding}px !important; }}
th,.label,.footer,.mention,.meta {{ font-size:{small_size}px !important; line-height:{line_height} !important; }}
h1,h2,h3,h4 {{ font-family:{_FONTS[normalized['font']]} !important; color:var(--doc-accent) !important; letter-spacing:0; break-after:avoid; }}
h1 {{ font-weight:750 !important; }}
table {{ border-collapse:collapse; border-spacing:0; }}
th {{ background:var(--doc-accent) !important; color:#fff !important; font-weight:700; letter-spacing:.02em; }}
td {{ border-bottom-color:#E4E7EC !important; }}
.block,.info-block,.partie {{ background:var(--doc-surface) !important; border:1px solid #E4E7EC; border-radius:4px !important; }}
.label,.footer,.mention,.meta {{ color:var(--doc-muted) !important; }}
.lumena-document-brand {{ width:100%;box-sizing:border-box;{brand_layout} }}
.lumena-document-brand img {{ display:block; width:{normalized['logo_width_px']}px; max-width:{logo_max_width}; max-height:70px; object-fit:contain; object-position:{normalized['logo_position']} center; }}
</style>""".strip()
    branded = html
    if "</head>" in branded:
        branded = branded.replace("</head>", css + "\n</head>", 1)
    else:
        branded = css + branded
    if logo_data_uri and normalized["logo_enabled"]:
        logo = f'<div class="lumena-document-brand"><img src="{escape(logo_data_uri, quote=True)}" alt="Logo"></div>'
        if "<body>" in branded:
            branded = branded.replace("<body>", "<body>\n" + logo, 1)
        else:
            branded = logo + branded
    return branded


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
