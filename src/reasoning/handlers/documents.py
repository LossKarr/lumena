"""
documents.py - Handlers documents fragmentés depuis react.py.

Handlers: create_pdf, create_invoice_pdf, create_docx, create_xlsx, create_pptx,
          read_document, generate_chart, create_meeting_report.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult, SubToolResult
from .registry_v2 import HandlerDef


# ─── Handlers ──────────────────────────────────────────────────────────────

def normalize_pdf_content(content: Any) -> str:
    """Accept text or a small structured block format without changing text input."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "content" in content:
            return normalize_pdf_content(content["content"])
        if "blocks" in content:
            return normalize_pdf_content(content["blocks"])
        content = [content]
    if not isinstance(content, list):
        raise ValueError("content doit etre du texte ou une liste de blocs documentaires")

    lines: List[str] = []
    for index, block in enumerate(content, start=1):
        if isinstance(block, str):
            lines.append(block)
            continue
        if not isinstance(block, dict):
            raise ValueError(f"bloc content #{index} invalide: texte ou objet attendu")
        block_type = str(block.get("type") or "paragraph").strip().lower()
        text = str(block.get("text") or block.get("content") or "").strip()
        if block_type in {"heading", "title"}:
            level = max(1, min(6, int(block.get("level") or 1)))
            lines.append(f"{'#' * level} {text}".rstrip())
        elif block_type in {"paragraph", "text"}:
            lines.append(text)
        elif block_type in {"bullet", "list_item", "item"}:
            lines.append(f"- {text}".rstrip())
        elif block_type == "list":
            items = block.get("items")
            if not isinstance(items, list):
                raise ValueError(f"bloc content #{index}: items doit etre une liste")
            lines.extend(f"- {str(item)}" for item in items)
        else:
            raise ValueError(
                f"bloc content #{index}: type '{block_type}' non supporte "
                "(heading, paragraph, text, bullet ou list)"
            )
    return "\n\n".join(line for line in lines if line)


async def create_pdf_handler(
    ctx: HandlerContext,
    filename: str,
    title: str,
    content: Any,
    font_size: int = 11,
    images: str = "",
    header_footer: str = "",
    page_size: str = "A4",
    orientation: str = "portrait",
    columns: int = 1,
    numbering: str = "arabic",
    toc: bool = False,
    charts: str = "",
    theme: str = "",
) -> HandlerResult:
    """Génère un fichier PDF depuis du contenu texte/markdown."""
    try:
        hub = ctx.get_document_hub()
        normalized_content = normalize_pdf_content(content)
        kw: Dict[str, Any] = dict(
            filename=filename, title=title, content=normalized_content, font_size=int(font_size),
        )
        if images:
            kw["images"] = json.loads(images) if isinstance(images, str) else images
        if header_footer:
            kw["header_footer"] = json.loads(header_footer) if isinstance(header_footer, str) else header_footer
        if page_size != "A4":
            kw["page_size"] = page_size
        if orientation != "portrait":
            kw["orientation"] = orientation
        if columns != 1:
            kw["columns"] = int(columns)
        if numbering != "arabic":
            kw["numbering"] = numbering
        if toc:
            kw["toc"] = True
        if charts:
            kw["charts"] = json.loads(charts) if isinstance(charts, str) else charts
        if theme:
            kw["theme"] = theme
        result = hub.create_pdf(**kw)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ create_pdf: {result.get('error', 'erreur inconnue')}",
                handler_name="create_pdf",
            )
        return HandlerResult.ok(
            f"✅ PDF créé avec succès\n"
            f"- fichier: {result['filename']}\n"
            f"- chemin: {result['path']}",
            handler_name="create_pdf",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_pdf: {e}", handler_name="create_pdf"
        )


async def create_invoice_pdf_handler(
    ctx: HandlerContext,
    filename: str,
    issuer: str,
    client: str,
    items: str,
    invoice_meta: str = "{}",
    accent_color: str = "#1a1a2e",
    currency: str = "€",
    page_size: str = "A4",
    orientation: str = "portrait",
    logo_path: str = "",
    watermark: str = "",
    document_type: str = "facture",
) -> HandlerResult:
    """Génère un document commercial PDF (facture, devis, bon de commande, avoir, proforma, note de frais)."""
    try:
        hub = ctx.get_document_hub()
        kw: Dict[str, Any] = dict(
            filename=filename, issuer=issuer, client=client, items=items,
            invoice_meta=invoice_meta, accent_color=accent_color, currency=currency,
            document_type=document_type,
        )
        if page_size != "A4":
            kw["page_size"] = page_size
        if orientation != "portrait":
            kw["orientation"] = orientation
        if logo_path:
            kw["logo_path"] = logo_path
        if watermark:
            kw["watermark"] = watermark
        result = hub.create_invoice_pdf(**kw)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ create_invoice_pdf: {result.get('error', 'erreur inconnue')}",
                handler_name="create_invoice_pdf",
            )
        meta = result.get("invoice_meta", {})
        num = meta.get("number", "")
        total = result.get("total_ttc", "")
        dtype = document_type.replace("_", " ").capitalize()
        return HandlerResult.ok(
            f"✅ {dtype} PDF créé(e) avec succès"
            + (f" (n°{num})" if num else "")
            + (f" — Total TTC : {total}" if total else "")
            + f"\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="create_invoice_pdf",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_invoice_pdf: {e}", handler_name="create_invoice_pdf"
        )


async def create_docx_handler(
    ctx: HandlerContext,
    filename: str,
    title: str,
    content: str,
    images: str = "",
    header_footer: str = "",
    page_size: str = "A4",
    orientation: str = "portrait",
    toc: bool = False,
    charts: str = "",
    theme: str = "",
) -> HandlerResult:
    """Génère un fichier Word .docx."""
    try:
        hub = ctx.get_document_hub()
        kw: Dict[str, Any] = dict(filename=filename, title=title, content=content)
        if images:
            kw["images"] = json.loads(images) if isinstance(images, str) else images
        if header_footer:
            kw["header_footer"] = json.loads(header_footer) if isinstance(header_footer, str) else header_footer
        if page_size != "A4":
            kw["page_size"] = page_size
        if orientation != "portrait":
            kw["orientation"] = orientation
        if toc:
            kw["toc"] = True
        if charts:
            kw["charts"] = json.loads(charts) if isinstance(charts, str) else charts
        if theme:
            kw["theme"] = theme
        result = hub.create_docx(**kw)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ create_docx: {result.get('error', 'erreur inconnue')}",
                handler_name="create_docx",
            )
        return HandlerResult.ok(
            f"✅ Document Word créé avec succès\n"
            f"- fichier: {result['filename']}\n"
            f"- chemin: {result['path']}",
            handler_name="create_docx",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_docx: {e}", handler_name="create_docx"
        )


async def create_xlsx_handler(
    ctx: HandlerContext,
    filename: str,
    sheets: str,
) -> HandlerResult:
    """Génère un fichier Excel .xlsx."""
    try:
        hub = ctx.get_document_hub()
        result = hub.create_xlsx(filename=filename, sheets=sheets)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ create_xlsx: {result.get('error', 'erreur inconnue')}",
                handler_name="create_xlsx",
            )
        return HandlerResult.ok(
            f"✅ Fichier Excel créé avec succès\n"
            f"- fichier: {result['filename']}\n"
            f"- chemin: {result['path']}",
            handler_name="create_xlsx",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_xlsx: {e}", handler_name="create_xlsx"
        )


async def create_pptx_handler(
    ctx: HandlerContext,
    filename: str,
    title: str,
    slides: str,
    theme_color: str = "1a1a2e",
    images: str = "",
) -> HandlerResult:
    """Génère une présentation PowerPoint .pptx."""
    try:
        hub = ctx.get_document_hub()
        kw: Dict[str, Any] = dict(filename=filename, title=title, slides=slides, theme_color=theme_color)
        if images:
            kw["images"] = json.loads(images) if isinstance(images, str) else images
        result = hub.create_pptx(**kw)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ create_pptx: {result.get('error', 'erreur inconnue')}",
                handler_name="create_pptx",
            )
        return HandlerResult.ok(
            f"✅ Présentation PowerPoint créée avec succès\n"
            f"- fichier: {result['filename']}\n"
            f"- chemin: {result['path']}",
            handler_name="create_pptx",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_pptx: {e}", handler_name="create_pptx"
        )


async def read_document_handler(
    ctx: HandlerContext, path: str
) -> HandlerResult:
    """Lit le contenu textuel d'un document existant (.docx, .xlsx, .pptx, .pdf)."""
    try:
        hub = ctx.get_document_hub()
        result = hub.read_document(path)
        if not result.get("success"):
            return HandlerResult.fail(
                f"❌ read_document: {result.get('error', 'erreur inconnue')}",
                handler_name="read_document",
            )
        content = result.get("content", "")
        doc_type = result.get("type", "?")
        doc_path = result.get("path", path)
        preview = content[:8000] + (
            "\n[... contenu tronqué ...]" if len(content) > 8000 else ""
        )
        return HandlerResult.ok(
            f"📄 Document lu ({doc_type.upper()}) : {doc_path}\n"
            f"─────────────────────────────\n"
            f"{preview}",
            handler_name="read_document",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur read_document: {e}", handler_name="read_document"
        )


# ─── generate_chart ────────────────────────────────────────────────────────

def _make_chart(chart_type: str, data: dict, title: str, output_path: str,
                xlabel: str, ylabel: str, width: int, height: int,
                color_palette: list) -> None:
    """Génère un graphique PNG avec matplotlib (appelé dans un thread)."""
    import matplotlib
    matplotlib.use("Agg")  # backend sans GUI
    import matplotlib.pyplot as plt
    import numpy as np

    dpi = 150
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")
    ax.tick_params(colors="#cccccc")
    ax.xaxis.label.set_color("#cccccc")
    ax.yaxis.label.set_color("#cccccc")
    ax.title.set_color("#ffffff")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444466")

    labels = data.get("labels", [])
    datasets = data.get("datasets", [])
    palette = color_palette or ["#6366f1", "#22c55e", "#f59e0b", "#ef4444",
                                 "#06b6d4", "#a855f7", "#ec4899", "#14b8a6"]

    if chart_type == "bar":
        x = np.arange(len(labels))
        n = max(len(datasets), 1)
        w = 0.8 / n
        for i, ds in enumerate(datasets):
            col = palette[i % len(palette)]
            ax.bar(x + i * w - w * (n - 1) / 2,
                   ds.get("values", []), width=w,
                   label=ds.get("label", f"Série {i+1}"),
                   color=col, alpha=0.85)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right", color="#cccccc")
        ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc", framealpha=0.7)

    elif chart_type == "line":
        for i, ds in enumerate(datasets):
            col = palette[i % len(palette)]
            ax.plot(labels, ds.get("values", []),
                    label=ds.get("label", f"Série {i+1}"),
                    color=col, linewidth=2, marker="o", markersize=5)
        ax.tick_params(axis="x", rotation=30)
        for tick in ax.get_xticklabels():
            tick.set_color("#cccccc")
        ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc", framealpha=0.7)

    elif chart_type == "pie":
        values = datasets[0].get("values", []) if datasets else []
        wedge_colors = [palette[i % len(palette)] for i in range(len(labels))]
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, colors=wedge_colors,
            autopct="%1.1f%%", startangle=140,
            textprops={"color": "#cccccc"},
        )
        for at in autotexts:
            at.set_color("#ffffff")
            at.set_fontsize(9)
        ax.axis("equal")

    elif chart_type == "scatter":
        for i, ds in enumerate(datasets):
            col = palette[i % len(palette)]
            xs = ds.get("x", [])
            ys = ds.get("y", ds.get("values", []))
            ax.scatter(xs, ys, label=ds.get("label", f"Série {i+1}"),
                       color=col, alpha=0.8, s=60)
        ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc", framealpha=0.7)

    elif chart_type == "area":
        for i, ds in enumerate(datasets):
            col = palette[i % len(palette)]
            ax.fill_between(range(len(labels)), ds.get("values", []),
                            alpha=0.5, color=col,
                            label=ds.get("label", f"Série {i+1}"))
            ax.plot(range(len(labels)), ds.get("values", []), color=col, linewidth=2)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", color="#cccccc")
        ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc", framealpha=0.7)

    elif chart_type == "horizontal_bar":
        y = np.arange(len(labels))
        n = max(len(datasets), 1)
        h = 0.8 / n
        for i, ds in enumerate(datasets):
            col = palette[i % len(palette)]
            ax.barh(y + i * h - h * (n - 1) / 2,
                    ds.get("values", []), height=h,
                    label=ds.get("label", f"Série {i+1}"),
                    color=col, alpha=0.85)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, color="#cccccc")
        ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc", framealpha=0.7)

    if title:
        ax.set_title(title, color="#ffffff", fontsize=13, pad=12)
    if xlabel and chart_type not in ("pie",):
        ax.set_xlabel(xlabel, color="#aaaacc")
    if ylabel and chart_type not in ("pie",):
        ax.set_ylabel(ylabel, color="#aaaacc")

    ax.grid(axis="y", color="#333355", linestyle="--", alpha=0.5)
    plt.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)


async def generate_chart_handler(
    ctx: HandlerContext,
    chart_type: str,
    data: str,
    filename: str,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
    width: int = 900,
    height: int = 500,
    color_palette: str = "",
) -> HandlerResult:
    """
    Génère un graphique PNG (bar, line, pie, scatter, area, horizontal_bar).
    Retourne le chemin absolu du fichier PNG créé — utilisable directement
    dans create_meeting_report ou create_pdf via le paramètre charts.
    """
    try:
        if isinstance(data, str):
            data = data.strip()
            if not data:
                return HandlerResult.fail(
                    "❌ generate_chart: le paramètre 'data' est vide. "
                    "Attendu: JSON {\"labels\":[...], \"datasets\":[{\"label\":...,\"values\":[...]}]}",
                    handler_name="generate_chart",
                )
            # Try to extract JSON from possible markdown fences
            if data.startswith("```"):
                data = data.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                data_dict = json.loads(data)
            except json.JSONDecodeError as je:
                return HandlerResult.fail(
                    f"❌ generate_chart: 'data' n'est pas du JSON valide ({je}). "
                    f"Reçu: {data[:200]}... "
                    "Attendu: {\"labels\":[...], \"datasets\":[{\"label\":...,\"values\":[...]}]}",
                    handler_name="generate_chart",
                )
        else:
            data_dict = data
        try:
            palette = json.loads(color_palette) if color_palette else []
        except (json.JSONDecodeError, TypeError):
            # LLM may pass "blue, red, green" instead of JSON array
            palette = [c.strip() for c in color_palette.split(",") if c.strip()] if color_palette else []

        from ...utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_dir = workspace / date_str
        out_dir.mkdir(parents=True, exist_ok=True)

        fn = filename if filename.endswith(".png") else filename + ".png"
        out_path = str(out_dir / fn)

        await asyncio.to_thread(_make_chart, chart_type, data_dict, title,
                                out_path, xlabel, ylabel, width, height, palette)

        return HandlerResult.ok(
            f"✅ Graphique '{chart_type}' créé : {out_path}\n"
            f"- type: {chart_type}\n"
            f"- taille: {width}×{height}px\n"
            f"- fichier: {fn}",
            handler_name="generate_chart",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur generate_chart: {e}", handler_name="generate_chart"
        )


# ─── create_meeting_report ─────────────────────────────────────────────────

def _build_meeting_report_html(
    title: str,
    date: str,
    location: str,
    participants: list,
    agenda: list,
    decisions: list,
    action_items: list,
    summary: str,
    charts: list,   # list of dicts: {path, caption, position}
    next_meeting: str,
    tags: list,
    accent_color: str,
) -> str:
    """Construit le HTML complet du rapport de réunion."""
    acc = accent_color or "#6366f1"
    acc_light = acc + "22"

    def _rows_participants():
        rows = []
        for p in participants:
            name = p.get("name", p) if isinstance(p, dict) else str(p)
            role = p.get("role", "") if isinstance(p, dict) else ""
            present = p.get("present", True) if isinstance(p, dict) else True
            badge_col = "#22c55e" if present else "#94a3b8"
            badge = "Présent" if present else "Absent"
            rows.append(
                f"<tr><td>{name}</td><td>{role}</td>"
                f"<td><span style='background:{badge_col}22;color:{badge_col};"
                f"border-radius:4px;padding:2px 8px;font-size:11px'>{badge}</span></td></tr>"
            )
        return "\n".join(rows)

    def _rows_actions():
        rows = []
        for i, a in enumerate(action_items, 1):
            who = a.get("who", "—") if isinstance(a, dict) else "—"
            what = a.get("what", str(a)) if isinstance(a, dict) else str(a)
            deadline = a.get("deadline", "—") if isinstance(a, dict) else "—"
            priority = a.get("priority", "normal") if isinstance(a, dict) else "normal"
            pri_colors = {"haute": "#ef4444", "high": "#ef4444",
                          "normale": "#6366f1", "normal": "#6366f1",
                          "basse": "#22c55e", "low": "#22c55e"}
            pri_col = pri_colors.get(priority.lower(), "#6366f1")
            rows.append(
                f"<tr><td style='color:#94a3b8;font-size:12px'>{i}</td>"
                f"<td>{what}</td><td><strong>{who}</strong></td>"
                f"<td style='color:#94a3b8'>{deadline}</td>"
                f"<td><span style='background:{pri_col}22;color:{pri_col};"
                f"border-radius:4px;padding:2px 8px;font-size:11px'>"
                f"{priority.capitalize()}</span></td></tr>"
            )
        return "\n".join(rows)

    def _agenda_items():
        items = []
        for i, a in enumerate(agenda, 1):
            text = a.get("item", str(a)) if isinstance(a, dict) else str(a)
            dur = a.get("duration", "") if isinstance(a, dict) else ""
            dur_tag = f"<span style='color:#94a3b8;font-size:12px;margin-left:8px'>({dur})</span>" if dur else ""
            items.append(f"<li><strong>{i}.</strong> {text}{dur_tag}</li>")
        return "\n".join(items)

    def _decisions_items():
        items = []
        for d in decisions:
            text = d.get("text", str(d)) if isinstance(d, dict) else str(d)
            items.append(f"<li style='margin-bottom:8px'>✓ {text}</li>")
        return "\n".join(items)

    def _chart_tags(position: str):
        """Génère les balises <img> pour les graphiques à la position donnée."""
        imgs = []
        for c in charts:
            pos = c.get("position", "body") if isinstance(c, dict) else "body"
            if pos != position:
                continue
            path = c.get("path", "") if isinstance(c, dict) else str(c)
            caption = c.get("caption", "") if isinstance(c, dict) else ""
            imgs.append(
                f"<figure style='text-align:center;margin:20px 0'>"
                f"<img src='{path}' style='max-width:100%;border-radius:8px;"
                f"border:1px solid #30304a' alt='{caption}'>"
                f"{'<figcaption style=\"color:#94a3b8;font-size:12px;margin-top:6px\">' + caption + '</figcaption>' if caption else ''}"
                f"</figure>"
            )
        return "\n".join(imgs)

    tags_html = "".join(
        f"<span style='background:{acc}22;color:{acc};border:1px solid {acc}44;"
        f"border-radius:4px;padding:2px 10px;font-size:11px;margin-right:4px'>{t}</span>"
        for t in tags
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  body {{ font-family: 'Inter', Arial, sans-serif; background: #0f0f1a; color: #e2e2f0;
          margin: 0; padding: 0; font-size: 13px; line-height: 1.6; }}
  .page {{ max-width: 900px; margin: 0 auto; padding: 48px 40px; }}
  .header {{ background: linear-gradient(135deg, {acc}, {acc}99);
             border-radius: 12px; padding: 32px 36px; margin-bottom: 32px;
             color: white; }}
  .header h1 {{ margin: 0 0 8px; font-size: 26px; font-weight: 700; }}
  .header .meta {{ opacity: 0.85; font-size: 13px; }}
  .header .tags {{ margin-top: 12px; }}
  .section {{ background: #1a1a2e; border-radius: 10px; padding: 24px 28px;
              margin-bottom: 20px; border: 1px solid #30304a; }}
  .section h2 {{ margin: 0 0 16px; font-size: 15px; font-weight: 700;
                 color: {acc}; border-bottom: 1px solid {acc}33;
                 padding-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 10px 12px; font-size: 11px; font-weight: 600;
        color: #94a3b8; background: #16213e; border-bottom: 1px solid #30304a; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #30304a; }}
  tr:last-child td {{ border-bottom: none; }}
  .summary-box {{ background: {acc_light}; border-left: 4px solid {acc};
                  border-radius: 0 8px 8px 0; padding: 16px 20px;
                  font-size: 13px; line-height: 1.7; margin: 0; }}
  .decisions-list {{ list-style: none; padding: 0; margin: 0; }}
  .decisions-list li {{ padding: 8px 12px; background: #22c55e11;
                        border-left: 3px solid #22c55e; border-radius: 0 6px 6px 0;
                        margin-bottom: 8px; }}
  .agenda-list {{ list-style: none; padding: 0 0 0 4px; margin: 0; }}
  .agenda-list li {{ padding: 6px 0; border-bottom: 1px dashed #30304a; }}
  .agenda-list li:last-child {{ border-bottom: none; }}
  .next-meeting {{ background: {acc}11; border: 1px solid {acc}33;
                   border-radius: 8px; padding: 14px 18px; font-weight: 600;
                   color: {acc}; }}
  .footer {{ text-align: center; color: #555577; font-size: 11px;
             margin-top: 32px; padding-top: 16px; border-top: 1px solid #30304a; }}
</style>
</head>
<body>
<div class="page">

  <!-- En-tête -->
  <div class="header">
    <h1>📋 {title}</h1>
    <div class="meta">
      📅 {date} &nbsp;|&nbsp; 📍 {location or 'Non précisé'} &nbsp;|&nbsp;
      👥 {len(participants)} participant{"s" if len(participants) != 1 else ""}
    </div>
    {f'<div class="tags" style="margin-top:12px">{tags_html}</div>' if tags else ''}
  </div>

  <!-- Résumé exécutif -->
  {f"""<div class="section">
    <h2>🎯 Résumé exécutif</h2>
    <p class="summary-box">{summary}</p>
  </div>""" if summary else ''}

  {_chart_tags("before_sections")}

  <!-- Ordre du jour -->
  {f"""<div class="section">
    <h2>📌 Ordre du jour</h2>
    <ul class="agenda-list">{_agenda_items()}</ul>
  </div>""" if agenda else ''}

  <!-- Participants -->
  {f"""<div class="section">
    <h2>👥 Participants</h2>
    <table><thead><tr><th>Nom</th><th>Rôle</th><th>Statut</th></tr></thead>
    <tbody>{_rows_participants()}</tbody></table>
  </div>""" if participants else ''}

  {_chart_tags("body")}

  <!-- Décisions -->
  {f"""<div class="section">
    <h2>✅ Décisions prises</h2>
    <ul class="decisions-list">{_decisions_items()}</ul>
  </div>""" if decisions else ''}

  <!-- Actions -->
  {f"""<div class="section">
    <h2>⚡ Plan d'action</h2>
    <table>
      <thead><tr><th>#</th><th>Action</th><th>Responsable</th><th>Échéance</th><th>Priorité</th></tr></thead>
      <tbody>{_rows_actions()}</tbody>
    </table>
  </div>""" if action_items else ''}

  {_chart_tags("after_actions")}

  <!-- Prochaine réunion -->
  {f"""<div class="section">
    <h2>🔜 Prochaine réunion</h2>
    <p class="next-meeting">📅 {next_meeting}</p>
  </div>""" if next_meeting else ''}

  <div class="footer">
    Rapport généré par Lumena • {datetime.now().strftime("%d/%m/%Y à %H:%M")}
  </div>
</div>
</body>
</html>"""


async def create_meeting_report_handler(
    ctx: HandlerContext,
    filename: str,
    title: str,
    date: str = "",
    location: str = "",
    participants: str = "[]",
    agenda: str = "[]",
    decisions: str = "[]",
    action_items: str = "[]",
    summary: str = "",
    charts: str = "[]",
    next_meeting: str = "",
    tags: str = "[]",
    accent_color: str = "#6366f1",
    output_format: str = "pdf",
) -> HandlerResult:
    """
    Génère un rapport de réunion professionnel complet en PDF ou HTML.
    Inclut : résumé exécutif, ordre du jour, participants avec statut présence,
    décisions prises, plan d'action (qui/quoi/deadline/priorité), graphiques embarqués.
    Les graphiques doivent être créés AVANT avec generate_chart et leurs chemins passés ici.
    """
    try:
        plist = json.loads(participants) if isinstance(participants, str) else participants
        alist = json.loads(agenda) if isinstance(agenda, str) else agenda
        dlist = json.loads(decisions) if isinstance(decisions, str) else decisions
        ailist = json.loads(action_items) if isinstance(action_items, str) else action_items
        clist = json.loads(charts) if isinstance(charts, str) else charts
        tlist = json.loads(tags) if isinstance(tags, str) else tags

        date_str = date or datetime.now().strftime("%d/%m/%Y")

        html = _build_meeting_report_html(
            title=title,
            date=date_str,
            location=location,
            participants=plist,
            agenda=alist,
            decisions=dlist,
            action_items=ailist,
            summary=summary,
            charts=clist,
            next_meeting=next_meeting,
            tags=tlist,
            accent_color=accent_color,
        )

        from ...utils.paths import WORKSPACE_DIR
        workspace = WORKSPACE_DIR
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = workspace / today
        out_dir.mkdir(parents=True, exist_ok=True)

        fmt = output_format.lower()
        base = filename.rsplit(".", 1)[0] if "." in filename else filename

        if fmt == "html":
            out_path = out_dir / f"{base}.html"
            out_path.write_text(html, encoding="utf-8")
            return HandlerResult.ok(
                f"✅ Rapport de réunion HTML créé : {out_path}\n"
                f"- participants: {len(plist)}\n"
                f"- décisions: {len(dlist)}\n"
                f"- actions: {len(ailist)}\n"
                f"- graphiques: {len(clist)}",
                handler_name="create_meeting_report",
            )

        # PDF via weasyprint → Playwright fallback
        def _render_pdf():
            out = str(out_dir / f"{base}.pdf")
            # Try WeasyPrint first
            try:
                from weasyprint import HTML as WH, CSS
                WH(string=html).write_pdf(
                    out,
                    stylesheets=[CSS(string="@page { size: A4; margin: 0; }")],
                )
                return out
            except Exception as exc:
                logger.debug(f"[PDF] WeasyPrint failed, trying Playwright: {exc}")
            # Fallback: Playwright Chromium
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(html, wait_until="networkidle")
                page.pdf(path=out, format="A4", print_background=True)
                browser.close()
            return out

        out_path = await asyncio.to_thread(_render_pdf)
        return HandlerResult.ok(
            f"✅ Rapport de réunion PDF créé : {out_path}\n"
            f"- participants: {len(plist)}\n"
            f"- décisions: {len(dlist)}\n"
            f"- actions: {len(ailist)}\n"
            f"- graphiques embarqués: {len(clist)}",
            handler_name="create_meeting_report",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur create_meeting_report: {e}", handler_name="create_meeting_report"
        )


# ─── Nouveaux handlers P3 — Intelligence documentaire ──────────────────────

async def add_watermark_handler(
    ctx: HandlerContext, input_path: str, text: str = "CONFIDENTIEL",
    opacity: str = "0.15", angle: str = "45", output_path: str = ""
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.add_watermark(input_path=input_path, text=text, opacity=float(opacity), angle=float(angle), output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ add_watermark: {result.get('error')}", handler_name="add_watermark")
        return HandlerResult.ok(f"✅ Watermark '{text}' ajouté\n- chemin: {result['path']}", handler_name="add_watermark")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur add_watermark: {e}", handler_name="add_watermark")


async def sign_document_handler(
    ctx: HandlerContext, input_path: str, signature_image_path: str,
    page: str = "-1", position: str = "bottom-right", date_text: str = "auto", output_path: str = ""
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.add_signature(input_path=input_path, signature_image_path=signature_image_path, page=int(page), position=position, date_text=date_text, output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ sign_document: {result.get('error')}", handler_name="sign_document")
        return HandlerResult.ok(f"✅ Signature ajoutée\n- chemin: {result['path']}", handler_name="sign_document")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur sign_document: {e}", handler_name="sign_document")


async def fill_pdf_form_handler(
    ctx: HandlerContext, input_path: str, fields: str, output_filename: str = ""
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.fill_pdf_form(input_path=input_path, fields=fields, output_filename=output_filename or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ fill_pdf_form: {result.get('error')}", handler_name="fill_pdf_form")
        return HandlerResult.ok(f"✅ Formulaire PDF rempli ({result.get('fields_filled', 0)} champs)\n- chemin: {result['path']}", handler_name="fill_pdf_form")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur fill_pdf_form: {e}", handler_name="fill_pdf_form")


async def list_pdf_fields_handler(ctx: HandlerContext, input_path: str) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.list_pdf_fields(input_path=input_path)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ list_pdf_fields: {result.get('error')}", handler_name="list_pdf_fields")
        fields = result.get("fields", {})
        lines = [f"📄 {len(fields)} champs trouvés :"]
        for name, info in fields.items():
            lines.append(f"  - {name} (type: {info.get('type', '?')}, valeur: {info.get('value', '')})")
        return HandlerResult.ok("\n".join(lines), handler_name="list_pdf_fields")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur list_pdf_fields: {e}", handler_name="list_pdf_fields")


async def analyze_document_handler(ctx: HandlerContext, path: str) -> HandlerResult:
    """Analyse structurée d'un document via lecture + extraction LLM."""
    try:
        hub = ctx.get_document_hub()
        result = hub.read_document(path)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ analyze_document: {result.get('error')}", handler_name="analyze_document")
        content = result.get("content", "")[:8000]
        doc_type = result.get("type", "?")
        analysis = (
            f"📄 Document analysé ({doc_type.upper()}) : {result.get('path', path)}\n"
            f"─────────────────────────────\n"
            f"Longueur : {len(result.get('content', ''))} caractères\n"
            f"Type détecté : {doc_type}\n"
            f"─────────────────────────────\n"
            f"Contenu extrait :\n{content}"
        )
        return HandlerResult.ok(analysis, handler_name="analyze_document")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur analyze_document: {e}", handler_name="analyze_document")


async def compare_documents_handler(ctx: HandlerContext, path_a: str, path_b: str) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.compare_documents(path_a=path_a, path_b=path_b)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ compare_documents: {result.get('error')}", handler_name="compare_documents")
        return HandlerResult.ok(
            f"✅ Comparaison terminée\n- ajouts: {result.get('additions', 0)} lignes\n- suppressions: {result.get('deletions', 0)} lignes\n- rapport: {result['path']}\n\n{result.get('diff_preview', '')[:3000]}",
            handler_name="compare_documents",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur compare_documents: {e}", handler_name="compare_documents")


async def protect_pdf_handler(
    ctx: HandlerContext, input_path: str, password: str,
    output_path: str = "", permissions: str = "read-only"
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.protect_pdf(input_path=input_path, password=password, output_path=output_path or None, permissions=permissions)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ protect_pdf: {result.get('error')}", handler_name="protect_pdf")
        return HandlerResult.ok(f"🔒 PDF protégé ({result.get('permissions')})\n- chemin: {result['path']}", handler_name="protect_pdf")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur protect_pdf: {e}", handler_name="protect_pdf")


async def image_to_document_handler(
    ctx: HandlerContext, image_path: str, output_format: str = "docx", language: str = "fra"
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.image_to_document(image_path=image_path, output_format=output_format, language=language)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ image_to_document: {result.get('error')}", handler_name="image_to_document")
        return HandlerResult.ok(f"✅ Image → document créé\n- fichier: {result.get('filename', '')}\n- chemin: {result.get('path', '')}", handler_name="image_to_document")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur image_to_document: {e}", handler_name="image_to_document")


# ─── Nouveaux handlers P2 — Templates ──────────────────────────────────────

async def create_from_template_handler(
    ctx: HandlerContext, template_name: str, variables: str,
    output_filename: str, output_format: str = "pdf"
) -> HandlerResult:
    """Génère un document à partir d'un template Jinja2."""
    try:
        hub = ctx.get_document_hub()
        result = hub.create_from_template(
            template_name=template_name, variables=variables,
            output_filename=output_filename, output_format=output_format,
        )
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_from_template: {result.get('error')}", handler_name="create_from_template")
        return HandlerResult.ok(
            f"✅ Document généré depuis template '{result.get('template')}'\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="create_from_template",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_from_template: {e}", handler_name="create_from_template")


async def list_templates_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste tous les templates disponibles."""
    try:
        hub = ctx.get_document_hub()
        result = hub.list_templates()
        if not result.get("success"):
            return HandlerResult.fail(f"❌ list_templates: {result.get('error')}", handler_name="list_templates")
        templates = result.get("templates", [])
        lines = [f"📄 {len(templates)} templates disponibles :"]
        for t in templates:
            vars_list = t.get("variables", [])
            vars_hint = f" — variables: {', '.join(vars_list)}" if vars_list else ""
            lines.append(f"  - {t['name']} ({t['type']}){vars_hint}")
        return HandlerResult.ok("\n".join(lines), handler_name="list_templates")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur list_templates: {e}", handler_name="list_templates")


async def save_template_handler(
    ctx: HandlerContext, template_name: str, html_content: str
) -> HandlerResult:
    """Sauvegarde un template HTML Jinja2 custom."""
    try:
        hub = ctx.get_document_hub()
        result = hub.save_template(template_name=template_name, html_content=html_content)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ save_template: {result.get('error')}", handler_name="save_template")
        return HandlerResult.ok(
            f"✅ Template custom sauvé : {result.get('name')}\n- chemin: {result['path']}",
            handler_name="save_template",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur save_template: {e}", handler_name="save_template")


# ─── Nouveaux handlers P1 + P1B ────────────────────────────────────────────

async def html_to_pdf_handler(
    ctx: HandlerContext, filename: str, html_content: str = "", css: str = "", content: str = ""
) -> HandlerResult:
    """Convertit du HTML brut en PDF via weasyprint."""
    # Accept both 'html_content' and 'content' (LLM alias)
    html_content = html_content or content
    if not html_content:
        return HandlerResult.fail("❌ html_to_pdf: paramètre 'html_content' (ou 'content') requis.", handler_name="html_to_pdf")
    try:
        hub = ctx.get_document_hub()
        result = hub.create_html_to_pdf(filename=filename, html_content=html_content, css=css)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ html_to_pdf: {result.get('error')}", handler_name="html_to_pdf")
        return HandlerResult.ok(
            f"✅ HTML→PDF créé\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="html_to_pdf",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur html_to_pdf: {e}", handler_name="html_to_pdf")


async def merge_pdfs_handler(
    ctx: HandlerContext, output_filename: str, input_paths: str
) -> HandlerResult:
    """Fusionne plusieurs PDFs en un seul."""
    try:
        hub = ctx.get_document_hub()
        result = hub.merge_pdfs(output_filename=output_filename, input_paths=input_paths)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ merge_pdfs: {result.get('error')}", handler_name="merge_pdfs")
        return HandlerResult.ok(
            f"✅ PDFs fusionnés ({result.get('pages', '?')} pages)\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="merge_pdfs",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur merge_pdfs: {e}", handler_name="merge_pdfs")


async def split_pdf_handler(
    ctx: HandlerContext, input_path: str, pages: str
) -> HandlerResult:
    """Découpe un PDF selon les pages spécifiées."""
    try:
        hub = ctx.get_document_hub()
        result = hub.split_pdf(input_path=input_path, pages=pages)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ split_pdf: {result.get('error')}", handler_name="split_pdf")
        return HandlerResult.ok(
            f"✅ PDF découpé ({result.get('pages_extracted', '?')} pages extraites)\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="split_pdf",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur split_pdf: {e}", handler_name="split_pdf")


async def create_csv_handler(
    ctx: HandlerContext, filename: str, headers: str, rows: str,
    delimiter: str = ",", encoding: str = "utf-8"
) -> HandlerResult:
    """Crée un fichier CSV."""
    try:
        hub = ctx.get_document_hub()
        result = hub.create_csv(filename=filename, headers=headers, rows=rows, delimiter=delimiter, encoding=encoding)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_csv: {result.get('error')}", handler_name="create_csv")
        return HandlerResult.ok(
            f"✅ CSV créé\n- fichier: {result['filename']}\n- chemin: {result['path']}",
            handler_name="create_csv",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_csv: {e}", handler_name="create_csv")


async def convert_document_handler(
    ctx: HandlerContext, input_path: str, output_format: str
) -> HandlerResult:
    """Convertit un document d'un format à un autre."""
    try:
        hub = ctx.get_document_hub()
        result = hub.convert_document(input_path=input_path, output_format=output_format)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ convert_document: {result.get('error')}", handler_name="convert_document")
        return HandlerResult.ok(
            f"✅ Document converti\n- fichier: {result.get('filename', '')}\n- chemin: {result.get('path', '')}",
            handler_name="convert_document",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur convert_document: {e}", handler_name="convert_document")


async def edit_docx_handler(
    ctx: HandlerContext, input_path: str, operations: str, output_path: str = ""
) -> HandlerResult:
    """Édite un fichier DOCX existant via une liste d'opérations."""
    try:
        hub = ctx.get_document_hub()
        result = hub.edit_docx(input_path=input_path, operations=operations, output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ edit_docx: {result.get('error')}", handler_name="edit_docx")
        return HandlerResult.ok(
            f"✅ DOCX édité ({result.get('operations_applied', 0)} opérations)\n- chemin: {result['path']}",
            handler_name="edit_docx",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_docx: {e}", handler_name="edit_docx")


async def edit_xlsx_handler(
    ctx: HandlerContext, input_path: str, operations: str, output_path: str = ""
) -> HandlerResult:
    """Édite un fichier XLSX existant via une liste d'opérations."""
    try:
        hub = ctx.get_document_hub()
        result = hub.edit_xlsx(input_path=input_path, operations=operations, output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ edit_xlsx: {result.get('error')}", handler_name="edit_xlsx")
        return HandlerResult.ok(
            f"✅ XLSX édité ({result.get('operations_applied', 0)} opérations)\n- chemin: {result['path']}",
            handler_name="edit_xlsx",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_xlsx: {e}", handler_name="edit_xlsx")


async def edit_pptx_handler(
    ctx: HandlerContext, input_path: str, operations: str, output_path: str = ""
) -> HandlerResult:
    """Édite un fichier PPTX existant via une liste d'opérations."""
    try:
        hub = ctx.get_document_hub()
        result = hub.edit_pptx(input_path=input_path, operations=operations, output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ edit_pptx: {result.get('error')}", handler_name="edit_pptx")
        return HandlerResult.ok(
            f"✅ PPTX édité ({result.get('operations_applied', 0)} opérations)\n- chemin: {result['path']}",
            handler_name="edit_pptx",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_pptx: {e}", handler_name="edit_pptx")


async def annotate_pdf_handler(
    ctx: HandlerContext, input_path: str, annotations: str, output_path: str = ""
) -> HandlerResult:
    """Annote un PDF existant (texte, surlignage, tampon)."""
    try:
        hub = ctx.get_document_hub()
        result = hub.annotate_pdf(input_path=input_path, annotations=annotations, output_path=output_path or None)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ annotate_pdf: {result.get('error')}", handler_name="annotate_pdf")
        return HandlerResult.ok(
            f"✅ PDF annoté ({result.get('annotations_count', 0)} annotations)\n- chemin: {result['path']}",
            handler_name="annotate_pdf",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur annotate_pdf: {e}", handler_name="annotate_pdf")


# ─── Nouveaux handlers P5 — Formats supplémentaires ────────────────────────

async def create_markdown_handler(
    ctx: HandlerContext, filename: str, title: str, content: str
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_markdown(filename=filename, title=title, content=content)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_markdown: {result.get('error')}", handler_name="create_markdown")
        return HandlerResult.ok(f"✅ Markdown créé\n- fichier: {result['filename']}\n- chemin: {result['path']}", handler_name="create_markdown")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_markdown: {e}", handler_name="create_markdown")


async def create_html_handler(
    ctx: HandlerContext, filename: str, title: str, content: str,
    css: str = "", template: str = "default"
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_html(filename=filename, title=title, content=content, css=css, template=template)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_html: {result.get('error')}", handler_name="create_html")
        return HandlerResult.ok(f"✅ HTML créé\n- fichier: {result['filename']}\n- chemin: {result['path']}", handler_name="create_html")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_html: {e}", handler_name="create_html")


async def create_email_html_handler(
    ctx: HandlerContext, filename: str, subject: str, body: str,
    sender_name: str = "Lumena", sender_logo_url: str = ""
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_email_html(
            filename=filename, subject=subject, body=body,
            sender_name=sender_name, sender_logo_url=sender_logo_url,
        )
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_email_html: {result.get('error')}", handler_name="create_email_html")
        return HandlerResult.ok(f"✅ Email HTML créé\n- fichier: {result['filename']}\n- chemin: {result['path']}", handler_name="create_email_html")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_email_html: {e}", handler_name="create_email_html")


async def create_ics_handler(
    ctx: HandlerContext, filename: str, events: str
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_ics(filename=filename, events=events)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_ics: {result.get('error')}", handler_name="create_ics")
        return HandlerResult.ok(f"✅ ICS créé\n- fichier: {result['filename']}\n- chemin: {result['path']}", handler_name="create_ics")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_ics: {e}", handler_name="create_ics")


async def create_vcard_handler(
    ctx: HandlerContext, filename: str, contacts: str
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_vcard(filename=filename, contacts=contacts)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ create_vcard: {result.get('error')}", handler_name="create_vcard")
        return HandlerResult.ok(f"✅ vCard créé\n- fichier: {result['filename']}\n- chemin: {result['path']}", handler_name="create_vcard")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_vcard: {e}", handler_name="create_vcard")


# ─── Nouveaux handlers P6 — Batch et automatisation ────────────────────────

async def batch_documents_handler(
    ctx: HandlerContext, template_name: str, data_source: str, output_format: str = "pdf"
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.create_batch_documents(template_name=template_name, data_source=data_source, output_format=output_format)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ batch_documents: {result.get('error')}", handler_name="batch_documents")
        return HandlerResult.ok(
            f"✅ Batch terminé: {result.get('succeeded')}/{result.get('total')} documents générés\n- fichiers: {len(result.get('paths', []))}",
            handler_name="batch_documents",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur batch_documents: {e}", handler_name="batch_documents")


async def zip_documents_handler(
    ctx: HandlerContext, output_filename: str, paths: str
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.zip_documents(output_filename=output_filename, paths=paths)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ zip_documents: {result.get('error')}", handler_name="zip_documents")
        return HandlerResult.ok(
            f"✅ ZIP créé ({result.get('entries', 0)} fichiers)\n- chemin: {result['path']}",
            handler_name="zip_documents",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur zip_documents: {e}", handler_name="zip_documents")


async def assemble_document_handler(
    ctx: HandlerContext, output_filename: str, parts: str
) -> HandlerResult:
    try:
        hub = ctx.get_document_hub()
        result = hub.assemble_document(output_filename=output_filename, parts=parts)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ assemble_document: {result.get('error')}", handler_name="assemble_document")
        return HandlerResult.ok(
            f"✅ Document composite créé ({result.get('parts_count', 0)} parties)\n- chemin: {result['path']}",
            handler_name="assemble_document",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur assemble_document: {e}", handler_name="assemble_document")


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def _targeted_studio_sample(
    studio, kind: str, *, output_format: str = "pdf", template_id: str = "",
) -> dict:
    """Return the sample of the exact template selected for generation."""
    if studio is None or not kind:
        return {}
    try:
        resolver = getattr(studio, "resolve_template", None)
        if callable(resolver):
            record = resolver(
                template_id=template_id, kind=kind, output_format=output_format,
            )
        elif template_id:
            record = studio.catalog.get(template_id)
        else:
            record = studio.catalog.get_default(kind, output_format)
    except (AttributeError, KeyError, ValueError):
        return {}
    if record is None:
        return {}
    if not getattr(record, "valid", True):
        return {}
    return studio.catalog.read_sample_data(record)


_MODEL_COUNT_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
}


def _model_listing_filters(
    ctx: HandlerContext | None, origin: str = "", limit: int = 0, sort: str = "",
) -> tuple[str, int, str]:
    """Resolve explicit model filters, with a narrow natural-language fallback."""
    normalized_origin = str(origin or "").strip().lower()
    normalized_sort = str(sort or "").strip().lower()
    try:
        normalized_limit = max(0, min(int(limit or 0), 100))
    except (TypeError, ValueError):
        normalized_limit = 0

    query = str(getattr(ctx, "original_user_query", "") or "").lower()
    if normalized_origin not in {"", "builtin", "custom"}:
        normalized_origin = ""
    if normalized_sort not in {"", "recent", "name"}:
        normalized_sort = ""

    from src.documents.document_intent import document_model_selection

    selection = document_model_selection(query)
    if not normalized_origin and selection.active:
        normalized_origin = selection.origin
    if not normalized_limit and selection.active:
        normalized_limit = selection.limit
    if not normalized_sort and selection.active:
        normalized_sort = selection.sort

    if not normalized_origin and any(
        token in query
        for token in ("personnalise", "personnalisé", "mes modeles", "mes modèles")
    ):
        normalized_origin = "custom"
    if not normalized_sort and normalized_origin == "custom" and any(
        token in query
        for token in ("dernier", "derniere", "dernière", "recent", "récent")
    ):
        normalized_sort = "recent"
    if not normalized_limit and normalized_origin == "custom":
        match = re.search(
            r"\b(\d{1,2}|" + "|".join(_MODEL_COUNT_WORDS)
            + r")\s+(?:derniers?\s+|dernieres?\s+|dernières?\s+)?mod[èe]les?\b",
            query,
        )
        if match:
            token = match.group(1)
            normalized_limit = int(token) if token.isdigit() else _MODEL_COUNT_WORDS[token]

    return normalized_origin, normalized_limit, normalized_sort


async def list_document_models_handler(
    ctx: HandlerContext, kind: str = "", origin: str = "", limit: int = 0,
    sort: str = "",
) -> HandlerResult:
    try:
        from src.documents.document_intent import normalize_document_kind
        from src.documents.studio import get_document_studio
        studio = get_document_studio()
        raw_kind = str(kind or "").strip()
        kind_tokens = [
            token.strip()
            for token in re.split(r"[,;|]", raw_kind)
            if token.strip()
        ]
        wanted_kinds = tuple(dict.fromkeys(
            normalized
            for token in kind_tokens
            if (normalized := normalize_document_kind(token))
        ))
        wanted = wanted_kinds[0] if len(wanted_kinds) == 1 else ""
        wanted_origin, wanted_limit, wanted_sort = _model_listing_filters(
            ctx, origin=origin, limit=limit, sort=sort,
        )
        all_records = [
            record for record in studio.catalog.list_templates()
            if record.valid
            and (not wanted_origin or record.manifest.origin == wanted_origin)
        ]
        available_kinds = {
            record.manifest.kind for record in all_records
        }
        missing_kinds = tuple(
            item for item in wanted_kinds if item not in available_kinds
        )
        if missing_kinds:
            available = sorted({
                record.manifest.kind
                for record in studio.catalog.list_templates()
                if record.valid
            })
            available_text = ", ".join(available) or "aucun type disponible"
            return HandlerResult.fail(
                "Document Studio: aucun modele pour "
                + ", ".join(f"kind='{item}'" for item in missing_kinds)
                + f". Types canoniques disponibles: {available_text}. "
                "Corrige la liste puis relance une seule fois.",
                handler_name="list_document_models",
            )

        if len(wanted_kinds) > 1:
            records = []
            for requested_kind in wanted_kinds:
                candidates = [
                    record for record in all_records
                    if record.manifest.kind == requested_kind
                ]
                default = studio.catalog.get_default(requested_kind, "pdf")
                selected = (
                    default
                    if default in candidates
                    else candidates[0]
                )
                records.append(selected)
        else:
            records = [
                record for record in all_records
                if not wanted or record.manifest.kind == wanted
            ]
        if wanted_sort == "recent":
            records.sort(
                key=lambda record: (
                    (record.directory / "manifest.json").stat().st_mtime
                    if (record.directory / "manifest.json").is_file() else 0.0
                ),
                reverse=True,
            )
        elif wanted_sort == "name":
            records.sort(key=lambda record: record.manifest.name.casefold())
        if wanted_limit:
            records = records[:wanted_limit]
        if wanted_kinds and not records:
            available = sorted({
                record.manifest.kind
                for record in studio.catalog.list_templates()
                if record.valid
            })
            available_text = ", ".join(available) or "aucun type disponible"
            return HandlerResult.fail(
                f"Document Studio: aucun modele pour kind='{kind}'. "
                f"Types canoniques disponibles: {available_text}. "
                "Utilise le type canonique le plus proche puis relance une seule fois.",
                handler_name="list_document_models",
            )

        rows = []
        for record in records:
            row = {
                "id": record.manifest.id,
                "name": record.manifest.name,
                "kind": record.manifest.kind,
                "format": record.manifest.format,
                "origin": record.manifest.origin,
                "is_default": bool(
                    (default := studio.catalog.get_default(record.manifest.kind, record.manifest.format))
                    and default.manifest.id == record.manifest.id
                ),
            }
            if wanted_kinds:
                row["version"] = record.manifest.version
                row["description"] = record.manifest.description
                row["sample_data"] = studio.catalog.read_sample_data(record)
            rows.append(row)
        payload = {"models": rows}
        if not wanted_kinds:
            payload["hint"] = (
                "Catalogue compact. Rappelle list_document_models avec kind=<type> "
                "pour obtenir uniquement les donnees d'exemple du document a generer."
            )
        return HandlerResult.ok(json.dumps(payload, ensure_ascii=False), handler_name="list_document_models")
    except Exception as exc:
        return HandlerResult.fail(f"Document Studio: {exc}", handler_name="list_document_models")


async def generate_studio_document_handler(
    ctx: HandlerContext, kind: str, data: str, output_format: str = "pdf",
    template_id: str = "", filename: str = "",
) -> HandlerResult:
    studio = None
    try:
        from src.documents.document_intent import normalize_document_kind
        from src.documents.studio import get_document_studio
        studio = get_document_studio()
        canonical_kind = normalize_document_kind(kind)
        raw_data = studio.parse_json_object(data, field="data")
        resolver = getattr(studio, "resolve_template", None)
        record = (
            resolver(
                template_id=template_id,
                kind=canonical_kind,
                output_format=output_format,
            )
            if callable(resolver)
            else None
        )
        sample = _targeted_studio_sample(
            studio, canonical_kind, output_format=output_format,
            template_id=template_id,
        )
        if record is not None:
            _validate_builtin_studio_data(studio, record, sample, raw_data)
        result = await studio.generate(
            template_id=template_id, kind=canonical_kind, output_format=output_format,
            data=_merge_studio_data(sample, raw_data), filename=filename,
        )
        from src.documents.delivery_manifest import compact_generation_payload

        payload = compact_generation_payload(result, kind=canonical_kind)
        _add_mission_publish_hint(payload, ctx)
        return HandlerResult.ok(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            handler_name="generate_studio_document",
        )
    except KeyError:
        available = sorted({
            record.manifest.kind
            for record in studio.catalog.list_templates()
            if record.valid
        }) if studio is not None else []
        requested = str(template_id or kind or "").strip() or "(vide)"
        available_text = ", ".join(available) or "appelle list_document_models"
        return HandlerResult.fail(
            "Document Studio: type ou modèle inconnu "
            f"'{requested}'. Types disponibles: {available_text}. "
            "Appelle list_document_models avec le type français avant de réessayer.",
            handler_name="generate_studio_document",
        )
    except Exception as exc:
        canonical_kind = normalize_document_kind(kind)
        sample = _targeted_studio_sample(
            studio, canonical_kind, output_format=output_format,
            template_id=template_id,
        )
        guidance = ""
        if sample:
            guidance = (
                f" Reessaie uniquement kind='{canonical_kind}' en adaptant cet exemple cible: "
                + json.dumps(sample, ensure_ascii=False)
            )
        return HandlerResult.fail(
            f"Document Studio: {exc}.{guidance}",
            handler_name="generate_studio_document",
        )


def _merge_studio_data(base: Any, patch: Any) -> Any:
    """Recursively merge user overrides over catalog sample data."""
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = _merge_studio_data(merged.get(key), value)
        return merged
    if isinstance(base, list) and isinstance(patch, list) and patch:
        # Structured rows are commonly supplied as partial objects by the LLM.
        # Keep template-required fields, while scalar lists and explicit empty
        # lists retain their historical replacement semantics.
        merged_items: list[Any] = []
        # Lists of business rows are homogeneous even when the catalog sample
        # shows several examples. Reuse the last row schema for user rows that
        # extend beyond those examples, just as a single sample row already
        # acts as a prototype.
        prototype = next(
            (item for item in reversed(base) if isinstance(item, dict)),
            None,
        )
        for index, value in enumerate(patch):
            if not isinstance(value, dict):
                merged_items.append(value)
                continue
            positional = base[index] if index < len(base) and isinstance(base[index], dict) else None
            seed = positional if positional is not None else prototype
            merged_items.append(_merge_studio_data(seed, value) if seed is not None else value)
        return merged_items
    return patch


def _validate_builtin_studio_data(
    studio: Any, record: Any, sample: dict[str, Any], raw_data: dict[str, Any],
) -> None:
    """Reject root fields that an integrated template cannot render."""
    manifest = getattr(record, "manifest", None)
    if str(getattr(manifest, "origin", "") or "") != "builtin" or not sample:
        return
    allowed = {str(key) for key in sample}
    if str(getattr(manifest, "renderer", "") or "") == "html-jinja":
        try:
            from jinja2 import Environment, meta

            environment = Environment()
            parsed = environment.parse(studio.catalog.read_source(record))
            allowed.update(
                str(key)
                for key in meta.find_undeclared_variables(parsed)
                if key not in environment.globals
            )
        except Exception:
            # The catalog sample remains the fail-safe schema if source
            # inspection is unavailable.
            pass
    unknown = tuple(sorted(str(key) for key in raw_data if key not in allowed))
    if not unknown:
        _validate_studio_container_shapes(sample, raw_data)
        return
    allowed_text = ", ".join(sorted(allowed))
    raise ValueError(
        "champ(s) ignore(s) par ce modele integre: "
        + ", ".join(unknown)
        + ". Utilise uniquement les champs rendus: "
        + allowed_text
    )


def _validate_studio_container_shapes(
    expected: Any, provided: Any, *, path: str = "",
) -> None:
    """Validate only JSON container shapes, leaving scalar coercion unchanged."""
    label = path or "data"
    if isinstance(expected, dict):
        if not isinstance(provided, dict):
            example = json.dumps(
                expected, ensure_ascii=False, separators=(",", ":"),
            )
            raise ValueError(f"{label} doit etre un objet JSON. Exemple: {example}")
        for key, value in provided.items():
            if key in expected:
                child = f"{path}.{key}" if path else str(key)
                _validate_studio_container_shapes(
                    expected[key], value, path=child,
                )
        return
    if isinstance(expected, list):
        if not isinstance(provided, list):
            example = json.dumps(
                expected, ensure_ascii=False, separators=(",", ":"),
            )
            raise ValueError(f"{label} doit etre une liste JSON. Exemple: {example}")
        prototype = next(
            (item for item in reversed(expected) if isinstance(item, (dict, list))),
            None,
        )
        if prototype is not None:
            for index, value in enumerate(provided):
                _validate_studio_container_shapes(
                    prototype, value, path=f"{label}[{index}]",
                )


def _studio_batch_retry_guidance(item: dict[str, Any]) -> str:
    """Build bounded, exact retry guidance for one failed prepared request."""
    sample = item.get("sample")
    if not isinstance(sample, dict) or not sample:
        return ""
    sample_json = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
    if len(sample_json) > 2400:
        sample_json = sample_json[:2400] + "..."
    return (
        f" Reessaie uniquement kind='{item.get('kind', '')}' avec ce data cible "
        f"(adapte les valeurs sans supprimer les cles imbriquees): {sample_json}"
    )


def _compact_studio_retry_data(value: Any) -> Any:
    """Keep the exact catalog shape while bounding repeated sample rows."""
    if isinstance(value, dict):
        return {
            str(key): _compact_studio_retry_data(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        if not value:
            return []
        return [_compact_studio_retry_data(value[0])]
    return value


def _prepare_studio_batch(studio: Any, items: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve and validate the complete batch before the first filesystem mutation."""
    from src.documents.document_intent import normalize_document_kind

    prepared: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    targets: set[str] = set()
    for index, item in enumerate(items, start=1):
        requested_kind = ""
        template_id = ""
        manifest_id = ""
        actual_kind = ""
        sample: dict[str, Any] = {}
        try:
            if not isinstance(item, dict):
                raise ValueError("objet attendu")
            requested_kind = normalize_document_kind(str(item.get("kind") or ""))
            template_id = str(item.get("template_id") or "").strip()
            if not requested_kind and not template_id:
                raise ValueError("kind ou template_id requis")

            raw_format = str(item.get("output_format") or "").strip().lower().lstrip(".")
            record = studio.resolve_template(
                template_id=template_id,
                kind=requested_kind,
                output_format=raw_format or "pdf",
            )
            manifest = record.manifest
            manifest_id = str(getattr(manifest, "id", "") or template_id or requested_kind)
            manifest_format = str(getattr(manifest, "format", "") or raw_format or "pdf")
            renderer = str(getattr(manifest, "renderer", "") or "html-jinja")
            actual_kind = normalize_document_kind(
                str(getattr(manifest, "kind", "") or requested_kind)
            )
            if requested_kind and actual_kind != requested_kind and getattr(manifest, "id", ""):
                raise ValueError(
                    f"template_id '{template_id}' appartient au type '{actual_kind}', "
                    f"pas a '{requested_kind}'"
                )
            output_format = raw_format or manifest_format.lower().lstrip(".")
            if renderer != "html-jinja" and output_format != manifest_format:
                raise ValueError(
                    f"le modele natif {manifest_id} produit uniquement {manifest_format}"
                )

            raw_data = item.get("data", {})
            if isinstance(raw_data, str):
                raw_data = studio.parse_json_object(
                    raw_data,
                    field=f"requests[{index}].data",
                )
            if not isinstance(raw_data, dict):
                raise ValueError(f"requests[{index}].data doit etre un objet JSON")
            sample = studio.catalog.read_sample_data(record)
            _validate_builtin_studio_data(studio, record, sample, raw_data)
            data = _merge_studio_data(sample, raw_data)

            filename = str(item.get("filename") or "").strip()
            stem_source = filename or manifest_id
            safe_stem = (
                studio._safe_stem(stem_source)
                if hasattr(studio, "_safe_stem")
                else Path(stem_source).stem
            )
            target_key = f"{safe_stem}.{output_format}".casefold()
            if target_key in targets:
                raise ValueError(f"nom de sortie duplique: {target_key}")
            targets.add(target_key)
            prepared.append({
                "kind": actual_kind,
                "template_id": manifest_id,
                "output_format": output_format,
                "filename": filename or safe_stem,
                "data": data,
                "sample": sample,
            })
        except Exception as exc:
            error = {
                "index": index,
                "kind": actual_kind or requested_kind,
                "template_id": manifest_id or template_id,
                "error": str(exc),
            }
            if sample:
                error["retry_request"] = {
                    "kind": actual_kind or requested_kind,
                    "template_id": manifest_id or template_id,
                    "data": _compact_studio_retry_data(sample),
                }
            errors.append(error)
    return prepared, errors


async def generate_studio_documents_handler(
    ctx: HandlerContext,
    requests: Any,
) -> HandlerResult:
    """Generate a bounded ordered Studio batch without concurrent catalog writes."""
    from src.documents.delivery_manifest import compact_generation_payload
    from src.documents.document_settings import get_document_settings
    from src.documents.document_intent import normalize_document_kind
    from src.documents.studio import get_document_studio

    try:
        items = json.loads(requests) if isinstance(requests, str) else requests
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return HandlerResult.fail(
            f"Document Studio batch: requests doit etre une liste JSON valide ({exc})",
            handler_name="generate_studio_documents",
        )
    if not isinstance(items, list) or not items:
        return HandlerResult.fail(
            "Document Studio batch: requests doit etre une liste non vide",
            handler_name="generate_studio_documents",
        )
    batch_size = get_document_settings().batch_size
    if len(items) > batch_size:
        return HandlerResult.fail(
            f"Document Studio batch: {batch_size} documents maximum par appel",
            handler_name="generate_studio_documents",
        )

    try:
        studio = get_document_studio()
    except Exception as exc:
        return HandlerResult.fail(
            f"Document Studio batch: service indisponible ({exc})",
            handler_name="generate_studio_documents",
        )
    prepared, preflight_errors = _prepare_studio_batch(studio, items)
    if preflight_errors:
        payload = {
            "phase": "preflight",
            "requested": len(items),
            "generated": 0,
            "failed": len(preflight_errors),
            "errors": preflight_errors,
        }
        return HandlerResult(
            success=False,
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            error="Document Studio batch: preflight refuse, aucun document genere",
            handler_name="generate_studio_documents",
            status_code="invalid_request",
            sub_results=(),
        )

    sub_results: list[SubToolResult] = []
    compact_rows: list[dict[str, Any]] = []
    generated = 0
    for index, item in enumerate(prepared, start=1):
        kind = item["kind"]
        output_format = item["output_format"]
        template_id = item["template_id"]
        filename = item["filename"]
        args = {
            "kind": kind,
            "template_id": template_id,
            "filename": filename,
            "output_format": output_format,
        }
        try:
            result = await studio.generate(
                template_id=template_id,
                kind=kind,
                output_format=output_format,
                data=item["data"],
                filename=filename,
            )
            payload = compact_generation_payload(result, kind=kind)
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            compact_rows.append(payload)
            generated += 1
            sub_results.append(SubToolResult(
                tool_name="generate_studio_document", success=True,
                content=content, status_code="success", args=args,
            ))
        except Exception as exc:
            guidance = _studio_batch_retry_guidance(item)
            sub_results.append(SubToolResult(
                tool_name="generate_studio_document", success=False,
                content=f"Document Studio [{kind or index}]: {exc}.{guidance}",
                status_code="failed", args=args,
            ))

    failed = len(items) - generated
    receipt_id = ""
    if compact_rows:
        try:
            from src.documents.delivery_manifest import parse_generation_proof
            from src.documents.delivery_receipt import save_delivery_receipt

            proofs = [
                proof
                for row in compact_rows
                if (proof := parse_generation_proof(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    fallback_kind=str(row.get("kind") or ""),
                )) is not None
            ]
            if len(proofs) != len(compact_rows):
                raise ValueError("preuve de generation incomplete")
            receipt = save_delivery_receipt(
                studio.root / "delivery_receipts",
                proofs,
                requested_count=len(items),
            )
            receipt_id = str(receipt.get("id") or "")
        except Exception as exc:
            logger.warning("[DOCUMENT BATCH RECEIPT] persistence impossible: {}", exc)
    summary = {
        "requested": len(items),
        "generated": generated,
        "failed": failed,
        "receipt_id": receipt_id,
        "documents": compact_rows,
        "errors": [
            {
                "index": index,
                "template_id": str((sub.args or {}).get("template_id") or ""),
                "kind": str((sub.args or {}).get("kind") or ""),
                "error": sub.content,
            }
            for index, sub in enumerate(sub_results, start=1)
            if not sub.success
        ],
    }
    if generated:
        _add_mission_publish_hint(summary, ctx)
    return HandlerResult(
        success=generated > 0,
        output=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        error=None if generated > 0 else "Aucun document Studio genere",
        handler_name="generate_studio_documents",
        status_code="success" if failed == 0 else ("partial" if generated else "error"),
        sub_results=tuple(sub_results),
    )


_MISSION_STUDIO_PUBLISH_HINT = (
    "MISSION: ces documents sont deja enregistres comme artefacts de la mission. "
    "Appelle directement publish_mission_workspace: il les inclura automatiquement "
    "dans le livrable publie. Ne les copie, ne les deplace et ne les reecris pas "
    "avec run_command, copy, Copy-Item, move ou un outil fichier."
)


def _add_mission_publish_hint(payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Expose the existing Studio-to-publisher handoff only inside missions."""
    if (
        ctx is not None
        and bool(getattr(ctx, "is_mission_run", False))
        and bool(str(getattr(ctx, "runtime_task_id", "") or "").strip())
    ):
        payload["mission_publish_hint"] = _MISSION_STUDIO_PUBLISH_HINT
    return payload


def _file_sha256(path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def open_document_delivery_handler(
    ctx: HandlerContext, receipt_id: str,
) -> HandlerResult:
    """Open exact files recorded by a verified receipt or receipt bundle."""
    from pathlib import Path

    from src.documents.document_delivery_bundle import load_delivery_reference
    from src.documents.studio import get_document_studio
    from src.reasoning.handlers.files import open_file_handler

    try:
        studio = get_document_studio()
        receipt = load_delivery_reference(studio.root, receipt_id)
        output_root = studio.output_root.resolve()
    except Exception as exc:
        return HandlerResult.fail(
            f"Lot documentaire introuvable ou invalide: {exc}",
            handler_name="open_document_delivery",
        )

    opened: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    sub_results: list[SubToolResult] = []
    for row in receipt["documents"]:
        filename = str(row.get("filename") or "")
        raw_path = str(row.get("path") or "")
        try:
            resolved = Path(raw_path).resolve(strict=True)
            if not resolved.is_relative_to(output_root):
                raise ValueError("chemin hors du dossier Document Studio")
            expected_hash = str(row.get("sha256") or "")
            if expected_hash and _file_sha256(resolved) != expected_hash:
                raise ValueError("le fichier a change depuis sa livraison")
            result = await open_file_handler(ctx, path=str(resolved))
            if not result.success:
                raise ValueError(result.error or result.output)
            try:
                page_count = max(0, int(row.get("page_count") or 0))
            except (TypeError, ValueError):
                page_count = 0
            item = {
                "filename": filename or resolved.name,
                "path": str(resolved),
                "page_count": page_count,
                "document_id": str(row.get("document_id") or ""),
                "kind": str(row.get("kind") or ""),
                "template_id": str(row.get("template_id") or ""),
            }
            opened.append(item)
            sub_results.append(SubToolResult(
                tool_name="open_file", success=True, content=result.output,
                status_code="success", args={"path": str(resolved)},
            ))
        except Exception as exc:
            item = {"filename": filename or Path(raw_path).name, "path": raw_path, "error": str(exc)}
            failed.append(item)
            sub_results.append(SubToolResult(
                tool_name="open_file", success=False, content=str(exc),
                status_code="failed", args={"path": raw_path},
            ))

    payload = {
        "receipt_id": receipt["id"],
        "delivery_id": receipt["id"],
        "requested": len(receipt["documents"]),
        "opened": len(opened),
        "failed": len(failed),
        "files": opened,
        "failures": failed,
    }
    return HandlerResult(
        success=bool(opened),
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        error=None if opened else "Aucun document du lot n'a pu etre ouvert",
        handler_name="open_document_delivery",
        status_code="success" if not failed else ("partial" if opened else "error"),
        sub_results=tuple(sub_results),
    )


async def import_document_handler(ctx: HandlerContext, path: str) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio
        resolved = ctx.resolve_path(path)
        record, duplicate = get_document_studio().importer.import_file(
            resolved, source_kind="agent_import", source_uri=str(resolved)
        )
        payload = {"record": record.to_dict(include_content=False), "duplicate": duplicate}
        return HandlerResult.ok(json.dumps(payload, ensure_ascii=False), handler_name="import_document")
    except Exception as exc:
        return HandlerResult.fail(f"Import document: {exc}", handler_name="import_document")


async def search_document_library_handler(
    ctx: HandlerContext, query: str, formats: Any = "", source: str = "",
    date_from: str = "", date_to: str = "", template_id: str = "",
    mission_id: str = "", limit: int = 20,
) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio
        if isinstance(formats, (list, tuple, set)):
            normalized_formats = [
                str(item).strip().lower().lstrip(".")
                for item in formats
                if str(item).strip() and str(item).strip().lower() != "none"
            ]
        else:
            raw_formats = "" if formats is None or str(formats).strip().lower() == "none" else str(formats)
            normalized_formats = [
                item.strip().lower().lstrip(".")
                for item in raw_formats.split(",")
                if item.strip()
            ]
        records = get_document_studio().library.search(
            query,
            formats=normalized_formats,
            source=source, date_from=date_from, date_to=date_to,
            template_id=template_id, mission_id=mission_id, limit=limit,
        )
        payload = [record.to_dict(include_content=False) for record in records]
        return HandlerResult.ok(json.dumps({"documents": payload}, ensure_ascii=False), handler_name="search_document_library")
    except Exception as exc:
        return HandlerResult.fail(f"Recherche documentaire: {exc}", handler_name="search_document_library")


def _resolve_document_reference(
    studio: Any,
    reference: str,
    *,
    allow_search: bool = True,
):
    record = studio.library.resolve_reference(reference, allow_search=allow_search)
    if record is None:
        qualifier = " exact" if not allow_search else ""
        raise KeyError(
            f"document{qualifier} introuvable: {reference}. "
            "Recherche-le d'abord puis demande confirmation si la reference differe."
        )
    return record


async def get_document_record_handler(ctx: HandlerContext, document_id: str) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio
        record = get_document_studio().library.resolve_reference(document_id)
        if record is None:
            return HandlerResult.fail("Document introuvable", handler_name="get_document_record")
        return HandlerResult.ok(json.dumps(record.to_dict(), ensure_ascii=False), handler_name="get_document_record")
    except Exception as exc:
        return HandlerResult.fail(f"Document Studio: {exc}", handler_name="get_document_record")


def _parse_document_operations(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("operations doit être une liste JSON valide") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("operations doit être une liste JSON d'objets")
    return [dict(item) for item in parsed]


async def preview_document_edit_handler(
    ctx: HandlerContext, document_id: str, operations: str
) -> HandlerResult:
    """Validate a transactional Office edit without mutating the source."""
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = _resolve_document_reference(studio, document_id, allow_search=False)
        preview = await asyncio.to_thread(
            studio.edits.preview,
            record.id,
            _parse_document_operations(operations),
        )
        return HandlerResult.ok(
            json.dumps(preview.to_dict(), ensure_ascii=False),
            handler_name="preview_document_edit",
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Prévisualisation édition documentaire: {exc}",
            handler_name="preview_document_edit",
        )


async def apply_document_edit_handler(
    ctx: HandlerContext, document_id: str, operations: str
) -> HandlerResult:
    """Create a child version of an indexed DOCX/XLSX/PPTX document."""
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = _resolve_document_reference(studio, document_id, allow_search=False)
        result = await asyncio.to_thread(
            studio.edits.apply,
            record.id,
            _parse_document_operations(operations),
        )
        return HandlerResult.ok(
            json.dumps(result, ensure_ascii=False), handler_name="apply_document_edit"
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Édition documentaire: {exc}", handler_name="apply_document_edit"
        )


async def revise_studio_document_handler(
    ctx: HandlerContext,
    document_id: str,
    data: str,
    replace_data: bool = False,
    output_format: str = "",
    filename: str = "",
) -> HandlerResult:
    """Regenerate a Studio PDF/HTML from its exact recorded recipe."""
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = _resolve_document_reference(studio, document_id, allow_search=False)
        patch = studio.parse_json_object(data, field="data")
        from src.documents.generation_recipe import StudioGenerationRecipe

        recipe = StudioGenerationRecipe.from_metadata(record.metadata)
        editable = tuple(sorted(str(key) for key in recipe.data))
        unknown = tuple(sorted(str(key) for key in patch if key not in recipe.data))
        if unknown and not bool(replace_data):
            return HandlerResult.fail(
                "Revision refusee: champ(s) non editable(s) pour ce modele: "
                + ", ".join(unknown)
                + ". Champs editables: "
                + (", ".join(editable) if editable else "aucun"),
                handler_name="revise_studio_document",
            )
        if bool(replace_data) and set(patch) != set(recipe.data):
            missing = tuple(sorted(str(key) for key in recipe.data if key not in patch))
            extra = tuple(sorted(str(key) for key in patch if key not in recipe.data))
            details = []
            if missing:
                details.append("champs manquants: " + ", ".join(missing))
            if extra:
                details.append("champs inconnus: " + ", ".join(extra))
            return HandlerResult.fail(
                "Revision refusee: replace_data=true exige la recette complete ("
                + "; ".join(details)
                + "). Pour modifier seulement quelques champs, utilise replace_data=false.",
                handler_name="revise_studio_document",
            )
        result = await studio.revise(
            record.id,
            data=patch,
            replace_data=bool(replace_data),
            output_format=output_format,
            filename=filename,
        )
        from src.documents.generation_recipe import changed_document_data

        revised_data = dict((result.get("recipe") or {}).get("data") or {})
        result["changed_fields"] = changed_document_data(recipe.data, revised_data)
        return HandlerResult.ok(
            json.dumps(result, ensure_ascii=False), handler_name="revise_studio_document"
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Révision Document Studio: {exc}", handler_name="revise_studio_document"
        )


async def get_document_history_handler(
    ctx: HandlerContext, document_id: str
) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = studio.library.resolve_reference(document_id)
        if record is None:
            return HandlerResult.fail("Document introuvable", handler_name="get_document_history")
        payload = {
            "document": record.to_dict(include_content=False),
            "transformations": studio.library.list_transformations(record.id),
        }
        return HandlerResult.ok(
            json.dumps(payload, ensure_ascii=False), handler_name="get_document_history"
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Historique documentaire: {exc}", handler_name="get_document_history"
        )


async def convert_library_document_handler(
    ctx: HandlerContext, document_id: str, output_format: str
) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = _resolve_document_reference(studio, document_id)
        try:
            result = await asyncio.to_thread(
                studio.conversions.convert, record.id, output_format
            )
        except ValueError:
            target_format = str(output_format or "").lower().lstrip(".")
            if not (
                record.format == "pdf"
                and target_format == "html"
                and record.source_kind == "generated"
            ):
                raise
            # Studio-generated PDFs retain their exact generation recipe. Use
            # that authoritative source to render the HTML child instead of
            # pretending that a lossy PDF parser can reconstruct the layout.
            result = await studio.revise(
                record.id,
                data={},
                output_format="html",
                filename=f"{Path(record.filename).stem}.html",
            )
            result["conversion_mode"] = "studio_recipe"
        return HandlerResult.ok(
            json.dumps(result, ensure_ascii=False), handler_name="convert_library_document"
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Conversion documentaire: {exc}", handler_name="convert_library_document"
        )


async def export_library_document_handler(
    ctx: HandlerContext, document_id: str, filename: str = ""
) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio

        studio = get_document_studio()
        record = _resolve_document_reference(studio, document_id)
        result = await asyncio.to_thread(
            studio.delivery.export_local, record.id, filename
        )
        if ctx is not None and bool(getattr(ctx, "is_mission_run", False)):
            result["mission_next_step"] = (
                "L'export local est une preuve Document Studio dans le dossier géré "
                "des exports ; le paramètre filename n'est pas un chemin de publication. "
                "Ne répète pas export_library_document pour tenter de le déplacer. "
                "Le lead doit appeler publish_mission_workspace une seule fois : il "
                "copiera le dossier de mission et les artefacts documentaires persistés."
            )
        return HandlerResult.ok(
            json.dumps(result, ensure_ascii=False), handler_name="export_library_document"
        )
    except Exception as exc:
        return HandlerResult.fail(
            f"Export documentaire: {exc}", handler_name="export_library_document"
        )


async def search_documents_web_handler(
    ctx: HandlerContext, query: str, formats: str = "pdf,docx,xlsx,pptx,csv", count: int = 12
) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio
        result = await get_document_studio().web_search.search(
            query, formats=[item.strip() for item in formats.split(",") if item.strip()], count=count
        )
        return HandlerResult.ok(json.dumps(result, ensure_ascii=False), handler_name="search_documents_web")
    except Exception as exc:
        return HandlerResult.fail(f"Recherche web documentaire: {exc}", handler_name="search_documents_web")


async def inspect_document_source_handler(ctx: HandlerContext, url: str) -> HandlerResult:
    try:
        from dataclasses import asdict
        from src.documents.studio import get_document_studio
        info = await get_document_studio().downloader.inspect(url)
        return HandlerResult.ok(json.dumps(asdict(info), ensure_ascii=False), handler_name="inspect_document_source")
    except Exception as exc:
        return HandlerResult.fail(f"Inspection document distant: {exc}", handler_name="inspect_document_source")


async def download_document_handler(ctx: HandlerContext, url: str) -> HandlerResult:
    try:
        from src.documents.studio import get_document_studio
        record, duplicate = await get_document_studio().downloader.download(url)
        payload = {"record": record.to_dict(include_content=False), "duplicate": duplicate}
        return HandlerResult.ok(json.dumps(payload, ensure_ascii=False), handler_name="download_document")
    except Exception as exc:
        return HandlerResult.fail(f"Telechargement document: {exc}", handler_name="download_document")


def get_documents_handler_defs() -> List[HandlerDef]:
    """Return additive Document Studio tools followed by the historical tools unchanged."""
    return _get_document_studio_handler_defs() + _get_legacy_documents_handler_defs()


def _get_document_studio_handler_defs() -> List[HandlerDef]:
    return [
        HandlerDef(
            name="list_document_models",
            description=(
                "Liste les modeles Document Studio. Sans kind, retourne un catalogue compact; avec kind, "
                "retourne les donnees d'exemple ciblees pretes a adapter. Plusieurs kinds canoniques "
                "peuvent etre fournis dans une seule chaine separee par des virgules; leur ordre est conserve. "
                "Appelle-le avec le kind exact "
                "avant generate_studio_document pour facture, devis, bon de commande, contrat, "
                "NDA, attestation, bulletin de paie, fiche de poste et autres modèles structurés."
            ),
            parameters={"properties": {
                "origin": {"type": "string", "default": "", "description": "Filtre d'origine: builtin ou custom."},
                "limit": {"type": "integer", "default": 0, "description": "Nombre maximal de modeles a retourner; 0 ne limite pas."},
                "sort": {"type": "string", "default": "", "description": "Tri optionnel: recent ou name."},
                "kind": {"type": "string", "default": "", "description": "Un ou plusieurs types documentaires canoniques, par exemple bon_commande ou devis,facture,proces_verbal."},
            }, "required": []},
            handler=list_document_models_handler, category="documents", source_module="handlers.documents",
        ),
        HandlerDef(
            name="generate_studio_document", description="Genere un PDF ou HTML avec un modele Document Studio explicite ou le modele par defaut du type demande.",
            parameters={"properties": {
                "kind": {"type": "string", "description": "Type documentaire canonique en français: attestation, bon_commande, bulletin_paie, contrat_prestation, devis, facture, fiche_poste, lettre_officielle, nda, note_interne, proces_verbal, rapport_activite ou relance_impaye. Les alias anglais usuels comme quote et invoice sont acceptés."},
                "data": {"type": "string", "description": "Objet JSON des donnees du document."},
                "output_format": {"type": "string", "default": "pdf", "description": "pdf ou html."},
                "template_id": {"type": "string", "default": "", "description": "Modele explicite; vide utilise le defaut configure."},
                "filename": {"type": "string", "default": "", "description": "Nom de sortie sans extension."},
            }, "required": ["kind", "data"]}, handler=generate_studio_document_handler, category="documents", source_module="handlers.documents",
        ),
        HandlerDef(
            name="generate_studio_documents",
            description=(
                "Genere de 1 a 30 documents Document Studio en une seule action, dans l'ordre et "
                "sequentiellement. Chaque element accepte kind, data partiel, filename, output_format "
                "et template_id. Les donnees partielles sont fusionnees avec l'exemple du modele. "
                "Le preflight valide tout le lot avant le premier rendu: si une requete est invalide, "
                "aucun document n'est genere. Corrige les erreurs indiquees puis renvoie le lot complet. "
                "Apres un preflight valide, chaque rendu retourne sa propre preuve et son propre statut."
            ),
            parameters={"properties": {
                "requests": {
                    "type": "string",
                    "description": (
                        "Liste JSON de 1 a 30 objets, par exemple "
                        "[{\"kind\":\"devis\",\"data\":{\"numero\":\"DEV-42\"},\"filename\":\"devis-atlas\"}]."
                    ),
                },
            }, "required": ["requests"]},
            handler=generate_studio_documents_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="open_document_delivery",
            description=(
                "Rouvre exactement tous les fichiers d'un lot Document Studio deja livre, "
                "a partir de l'identifiant doclot_ ou docbundle_ affiche dans la reponse. "
                "Utilise cet outil pour « ouvre-les » au lieu de rechercher ou lister un dossier."
            ),
            parameters={"properties": {
                "receipt_id": {
                    "type": "string",
                    "description": "Identifiant exact: doclot_<empreinte> ou docbundle_<empreinte>.",
                },
            }, "required": ["receipt_id"]},
            handler=open_document_delivery_handler,
            category="files",
            source_module="handlers.documents",
        ),
        HandlerDef(name="import_document", description="Importe et indexe un document local dans la bibliotheque Document Studio.", parameters={"properties": {"path": {"type": "string", "description": "Chemin du document."}}, "required": ["path"]}, handler=import_document_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="search_document_library", description="Recherche plein texte dans les documents indexes localement, avec filtres de provenance et de date.", parameters={"properties": {"query": {"type": "string", "description": "Texte a rechercher dans le titre et le contenu extrait."}, "formats": {"type": "string", "default": "", "description": "Extensions separees par des virgules, par exemple pdf,docx."}, "source": {"type": "string", "default": "", "description": "Type de provenance, par exemple upload, mail ou web_download."}, "date_from": {"type": "string", "default": "", "description": "Date minimale ISO incluse."}, "date_to": {"type": "string", "default": "", "description": "Date maximale ISO incluse."}, "template_id": {"type": "string", "default": "", "description": "Identifiant du modele ayant genere le document."}, "mission_id": {"type": "string", "default": "", "description": "Identifiant de mission associe au document."}, "limit": {"type": "integer", "default": 20, "description": "Nombre maximal de resultats, borne par le service."}}, "required": ["query"]}, handler=search_document_library_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="get_document_record", description="Retourne le contenu et la provenance d'un document indexe.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document dans la bibliotheque."}}, "required": ["document_id"]}, handler=get_document_record_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="get_document_history", description="Retourne les transformations prouvées d'un document indexé.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document."}}, "required": ["document_id"]}, handler=get_document_history_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="preview_document_edit", description="Valide sans mutation une liste d'opérations transactionnelles sur un DOCX, XLSX ou PPTX indexé.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document."}, "operations": {"type": "string", "description": "Liste JSON d'opérations adaptées au format. Appelle d'abord get_document_record."}}, "required": ["document_id", "operations"]}, handler=preview_document_edit_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="apply_document_edit", description="Applique des opérations validées à un DOCX, XLSX ou PPTX indexé et crée une nouvelle version sans écraser l'original.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document original."}, "operations": {"type": "string", "description": "Liste JSON d'opérations transactionnelles."}}, "required": ["document_id", "operations"]}, handler=apply_document_edit_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="revise_studio_document", description="Modifie un PDF ou HTML généré par Document Studio en réutilisant exactement sa recette, son modèle versionné et ses données, puis crée une nouvelle version.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant du document Studio généré."}, "data": {"type": "string", "description": "Objet JSON contenant seulement les champs à modifier, ou toutes les données si replace_data=true."}, "replace_data": {"type": "boolean", "default": False, "description": "Remplace toutes les données au lieu de fusionner le patch."}, "output_format": {"type": "string", "default": "", "description": "Vide conserve le format original; sinon pdf ou html."}, "filename": {"type": "string", "default": "", "description": "Nom optionnel sans extension pour la nouvelle version."}}, "required": ["document_id", "data"]}, handler=revise_studio_document_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="convert_library_document", description="Convertit un document indexé selon la matrice de conversion certifiée et conserve la provenance.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document."}, "output_format": {"type": "string", "description": "Format de sortie supporté."}}, "required": ["document_id", "output_format"]}, handler=convert_library_document_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="export_library_document", description="Exporte une copie locale non écrasante d'un document indexé et retourne une preuve de chemin.", parameters={"properties": {"document_id": {"type": "string", "description": "Identifiant stable du document."}, "filename": {"type": "string", "default": "", "description": "Nom de copie optionnel."}}, "required": ["document_id"]}, handler=export_library_document_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="search_documents_web", description="Recherche sur Internet des PDF, documents Office, CSV et formats ouverts, sans telecharger.", parameters={"properties": {"query": {"type": "string", "description": "Sujet ou document a rechercher sur Internet."}, "formats": {"type": "string", "default": "pdf,docx,xlsx,pptx,csv", "description": "Formats recherches, separes par des virgules."}, "count": {"type": "integer", "default": 12, "description": "Nombre maximal de candidats a retourner."}}, "required": ["query"]}, handler=search_documents_web_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="inspect_document_source", description="Inspecte les en-tetes d'une URL documentaire sans l'importer. Une URL publique ne prouve jamais les droits de reutilisation: le statut reste unknown sans licence explicite.", parameters={"properties": {"url": {"type": "string", "description": "URL HTTP(S) publique du document a inspecter."}}, "required": ["url"]}, handler=inspect_document_source_handler, category="documents", source_module="handlers.documents"),
        HandlerDef(name="download_document", description="Telecharge par URL avec garde SSRF, limite, reprise, controle du type et indexation. Le telechargement ne certifie pas les droits de reutilisation; ceux-ci restent unknown sans preuve de licence.", parameters={"properties": {"url": {"type": "string", "description": "URL HTTP(S) publique du document a telecharger."}}, "required": ["url"]}, handler=download_document_handler, category="documents", source_module="handlers.documents"),
    ]


def _get_legacy_documents_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des handlers documents."""
    return [
        HandlerDef(
            name="create_pdf",
            description=(
                "Génère un fichier PDF depuis du contenu texte/markdown. "
                "Supporte : # ## ### titres, **gras**, *italique*, `code`, listes (- ou 1.), séparateurs ---. "
                "Retourne le chemin absolu du fichier créé (dans workspace/YYYY-MM-DD/)."
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: rapport.pdf). L'extension .pdf est ajoutée automatiquement si absente."},
                    "title": {"type": "string", "description": "Titre principal du document, affiché en haut de la première page."},
                    "content": {"type": "string", "description": "Contenu du document en markdown (# titres, **gras**, - listes, --- séparateurs, etc.)."},
                    "font_size": {"type": "integer", "description": "Taille de police corps de texte", "default": 11},
                },
                "required": ["filename", "content"],
            },
            handler=create_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_invoice_pdf",
            description=(
                "Génère un document commercial PDF (facture, devis, bon de commande, avoir, proforma, note de frais) "
                "avec mise en page professionnelle : en-tête prestataire/client, "
                "tableau des lignes (désignation, quantité, PU HT, TVA%, TVA€, Total TTC), calcul automatique "
                "des sous-totaux HT + TVA par taux + Total TTC en bandeau coloré, conditions de paiement. "
                "Le paramètre document_type détermine le titre du document (facture, devis, bon_commande, avoir, proforma, note_frais). "
                "Tous les paramètres JSON (issuer, client, items, invoice_meta) doivent être passés en string JSON valide."
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier PDF (ex: devis-2026-001.pdf)."},
                    "document_type": {
                        "type": "string",
                        "description": (
                            "Type de document commercial: facture, devis, bon_commande, avoir, proforma, note_frais. "
                            "Détermine le titre affiché sur le PDF. Défaut: facture."
                        ),
                        "default": "facture",
                    },
                    "issuer": {
                        "type": "string",
                        "description": (
                            'JSON string prestataire. Champs: name (requis), address, city, phone, email, siret, website. '
                            'Ex: {"name":"Mon SARL","address":"12 rue..."  ,"siret":"123 456 789 00012","email":"contact@ma-sarl.fr"}'
                        ),
                    },
                    "client": {
                        "type": "string",
                        "description": (
                            'JSON string client. Champs: name (requis), address, city, email, phone. '
                            'Ex: {"name":"Jean Dupont","address":"5 avenue Y","city":"75001 Paris"}'
                        ),
                    },
                    "items": {
                        "type": "string",
                        "description": (
                            'JSON array des lignes. Chaque ligne: description (requis), qty, unit_price, vat_rate (%), unit. '
                            'Ex: [{"description":"Dev web","qty":3,"unit_price":600,"vat_rate":20,"unit":"j"}]'
                        ),
                    },
                    "invoice_meta": {
                        "type": "string",
                        "description": (
                            'JSON string métadonnées. Champs: number, date, due_date, payment_terms, notes, validity_days (pour devis). '
                            'Ex: {"number":"2026-001","date":"13/03/2026","due_date":"12/04/2026","notes":"Paiement par virement"}'
                        ),
                        "default": "{}",
                    },
                    "accent_color": {
                        "type": "string",
                        "description": "Couleur principale en hex (ex: #1a1a2e). Défaut: #1a1a2e (bleu nuit)",
                        "default": "#1a1a2e",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Symbole monétaire",
                        "default": "€",
                    },
                    "logo_path": {
                        "type": "string",
                        "description": "Chemin absolu vers une image de logo à afficher en en-tête du PDF (png, jpg). Optionnel.",
                        "default": "",
                    },
                    "watermark": {
                        "type": "string",
                        "description": "Texte en filigrane sur chaque page (ex: BROUILLON, CONFIDENTIEL). Optionnel.",
                        "default": "",
                    },
                    "page_size": {
                        "type": "string",
                        "description": "Format de page: A4, letter, legal. Défaut: A4.",
                        "default": "A4",
                    },
                    "orientation": {
                        "type": "string",
                        "description": "Orientation: portrait ou landscape. Défaut: portrait.",
                        "default": "portrait",
                    },
                },
                "required": ["filename", "issuer", "client", "items"],
            },
            handler=create_invoice_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_docx",
            description=(
                "Génère un fichier Word .docx depuis du contenu texte/markdown. "
                "Supporte : # ## ### titres, **gras**, *italique*, `code`, listes (- ou 1.), séparateurs ---. "
                "Retourne le chemin absolu du fichier créé."
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: contrat.docx). L'extension .docx est ajoutée automatiquement si absente."},
                    "title": {"type": "string", "description": "Titre principal du document."},
                    "content": {"type": "string", "description": "Contenu en markdown."},
                },
                "required": ["filename", "content"],
            },
            handler=create_docx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_xlsx",
            description=(
                "Génère un fichier Excel .xlsx avec des données structurées (tableaux, RH, rapports, spreadsheets). "
                "UTILISE CET OUTIL pour toute demande de tableau Excel, fichier .xlsx, données tabulaires ou spreadsheet. "
                "NE PAS utiliser execute_python pour créer des fichiers CSV/Excel — utiliser create_xlsx à la place. "
                "Retourne le chemin absolu du fichier créé. "
                "IMPORTANT: sheets doit être du JSON valide (voir format ci-dessous). "
                'Format sheets: [{"name": "NomFeuille", "title": "TitreOpt", '
                '"headers": ["Col1", "Col2"], "rows": [[val1, val2], ...]}]'
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: tableau.xlsx)."},
                    "sheets": {"type": "string", "description": 'JSON array de feuilles. Chaque feuille: {"name":str, "title":str_opt, "headers":[str,...], "rows":[[val,...],...]}'},
                },
                "required": ["filename", "sheets"],
            },
            handler=create_xlsx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_pptx",
            description=(
                "Génère une présentation PowerPoint .pptx. "
                "Crée automatiquement une slide de titre + les slides de contenu. "
                "Retourne le chemin absolu du fichier créé. "
                'Format slides: [{"title": "Titre slide", "content": "texte markdown"}]. '
                "Dans content: ## sous-titre, - bullet, texte libre."
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: presentation.pptx)."},
                    "title": {"type": "string", "description": "Titre principal de la présentation (slide de couverture)."},
                    "slides": {"type": "string", "description": 'JSON array de slides: [{"title": str, "content": str_markdown}]'},
                    "theme_color": {"type": "string", "description": "Couleur hex 6 caractères SANS # (ex: 1a1a2e, 8B0000, 4A9EFF). NE PAS utiliser de nom de couleur.", "default": "1a1a2e"},
                },
                "required": ["filename", "slides"],
            },
            handler=create_pptx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="read_document",
            description=(
                "Lit le contenu textuel d'un document existant (.docx, .xlsx, .pptx, .pdf). "
                "Retourne le contenu structuré pour permettre de le modifier puis recréer le fichier avec create_docx/xlsx/pptx. "
                "Accepte un nom de fichier (cherché automatiquement dans le workspace) ou un chemin absolu complet."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Nom du fichier (ex: rapport.docx) ou chemin absolu Windows complet."},
                },
                "required": ["path"],
            },
            handler=read_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="generate_chart",
            description=(
                "Génère un graphique PNG professionnel (dark theme) à partir de données JSON. "
                "Types disponibles : bar (barres), line (lignes), pie (camembert), scatter (nuage de points), "
                "area (aires), horizontal_bar (barres horizontales). "
                "TOUJOURS utiliser avant create_meeting_report si le rapport doit contenir des graphiques. "
                "Retourne le chemin absolu du PNG pour l'intégrer dans un rapport/PDF."
            ),
            parameters={
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "description": "Type de graphique : bar | line | pie | scatter | area | horizontal_bar",
                        "enum": ["bar", "line", "pie", "scatter", "area", "horizontal_bar"],
                    },
                    "data": {
                        "type": "string",
                        "description": (
                            'JSON string décrivant les données. Format : '
                            '{"labels": ["Jan","Fév",...], "datasets": [{"label": "CA", "values": [100,200,...]}]}. '
                            'Pour scatter : datasets[].x et datasets[].y au lieu de values.'
                        ),
                    },
                    "filename": {"type": "string", "description": "Nom du fichier PNG (ex: ca_mensuel.png)."},
                    "title": {"type": "string", "description": "Titre affiché en haut du graphique."},
                    "xlabel": {"type": "string", "description": "Label de l'axe X.", "default": ""},
                    "ylabel": {"type": "string", "description": "Label de l'axe Y.", "default": ""},
                    "width": {"type": "integer", "description": "Largeur en pixels.", "default": 900},
                    "height": {"type": "integer", "description": "Hauteur en pixels.", "default": 500},
                    "color_palette": {
                        "type": "string",
                        "description": 'JSON array de couleurs hex optionnel (ex: ["#6366f1","#22c55e"]). Laissez vide pour le palette par défaut.',
                        "default": "",
                    },
                },
                "required": ["chart_type", "data", "filename"],
            },
            handler=generate_chart_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_meeting_report",
            description=(
                "Génère un rapport de réunion professionnel complet en PDF (ou HTML). "
                "Inclut résumé exécutif, ordre du jour, tableau participants avec présence, "
                "liste des décisions, plan d'action (qui/quoi/deadline/priorité), et graphiques embarqués. "
                "Pour intégrer des graphiques : créer les PNG avec generate_chart d'abord, "
                "puis passer leurs chemins dans le paramètre charts. "
                "TOUJOURS utiliser cet outil pour les comptes-rendus de réunion — ne pas utiliser create_pdf."
            ),
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: cr_reunion_2026-03-29.pdf)."},
                    "title": {"type": "string", "description": "Titre du rapport (ex: Réunion hebdo Marketing — 29/03/2026)."},
                    "date": {"type": "string", "description": "Date de la réunion (ex: 29/03/2026). Automatique si absent."},
                    "location": {"type": "string", "description": "Lieu ou lien (ex: Salle Athéna, Google Meet, Teams).", "default": ""},
                    "summary": {"type": "string", "description": "Résumé exécutif de la réunion (1-3 phrases). Affiché en encadré coloré en haut.", "default": ""},
                    "participants": {
                        "type": "string",
                        "description": (
                            'JSON array des participants. Format simple : ["Alice", "Bob"] '
                            'ou enrichi : [{"name":"Alice","role":"DG","present":true}, {"name":"Bob","role":"Dev","present":false}]'
                        ),
                        "default": "[]",
                    },
                    "agenda": {
                        "type": "string",
                        "description": (
                            'JSON array de l\'ordre du jour. Format : ["Point 1", "Point 2"] '
                            'ou [{"item":"Bilan Q1","duration":"20 min"}, {"item":"Roadmap","duration":"30 min"}]'
                        ),
                        "default": "[]",
                    },
                    "decisions": {
                        "type": "string",
                        "description": 'JSON array des décisions prises. Ex: ["Lancer la v2 en avril", "Recruter un dev senior"]',
                        "default": "[]",
                    },
                    "action_items": {
                        "type": "string",
                        "description": (
                            'JSON array des actions. Format : '
                            '[{"who":"Alice","what":"Rédiger le cahier des charges","deadline":"05/04/2026","priority":"haute"}]'
                        ),
                        "default": "[]",
                    },
                    "charts": {
                        "type": "string",
                        "description": (
                            'JSON array des graphiques PNG à embarquer (chemins créés par generate_chart). '
                            'Format : [{"path":"C:/...chart.png","caption":"CA mensuel","position":"body"}] '
                            'Positions : before_sections | body | after_actions'
                        ),
                        "default": "[]",
                    },
                    "next_meeting": {"type": "string", "description": "Date/heure de la prochaine réunion.", "default": ""},
                    "tags": {"type": "string", "description": 'JSON array de tags (ex: ["Marketing","Q2","Urgent"])', "default": "[]"},
                    "accent_color": {"type": "string", "description": "Couleur hex du thème (ex: #6366f1 violet, #22c55e vert, #f59e0b orange).", "default": "#6366f1"},
                    "output_format": {"type": "string", "description": "Format de sortie : pdf ou html.", "default": "pdf", "enum": ["pdf", "html"]},
                },
                "required": ["filename", "title"],
            },
            handler=create_meeting_report_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P1: Nouveaux outils ──
        HandlerDef(
            name="html_to_pdf",
            description="Convertit du HTML brut en PDF (WeasyPrint ou Playwright). Accepte du CSS optionnel.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier PDF à créer."},
                    "html_content": {"type": "string", "description": "Contenu HTML à convertir."},
                    "content": {"type": "string", "description": "Alias pour html_content (contenu HTML à convertir)."},
                    "css": {"type": "string", "description": "CSS additionnel (optionnel).", "default": ""},
                },
                "required": ["filename"],
            },
            handler=html_to_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="merge_pdfs",
            description="Fusionne plusieurs fichiers PDF en un seul. Passer les chemins absolus en JSON array.",
            parameters={
                "properties": {
                    "output_filename": {"type": "string", "description": "Nom du PDF fusionné."},
                    "input_paths": {"type": "string", "description": 'JSON array de chemins PDF à fusionner. Ex: ["C:/a.pdf","C:/b.pdf"]'},
                },
                "required": ["output_filename", "input_paths"],
            },
            handler=merge_pdfs_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="split_pdf",
            description="Découpe un PDF selon les pages spécifiées. Ex: pages='1-3,5,8-10'.",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF à découper."},
                    "pages": {"type": "string", "description": "Pages à extraire (ex: '1-3,5,8-10')."},
                },
                "required": ["input_path", "pages"],
            },
            handler=split_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_csv",
            description="Crée un fichier CSV depuis des headers et des rows.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier CSV."},
                    "headers": {"type": "string", "description": 'JSON array des en-têtes (ex: ["Nom","Age"]).'},
                    "rows": {"type": "string", "description": 'JSON array de rows (ex: [["Alice",30],["Bob",25]]).'},
                    "delimiter": {"type": "string", "description": "Séparateur (défaut: virgule).", "default": ","},
                    "encoding": {"type": "string", "description": "Encodage du fichier.", "default": "utf-8"},
                },
                "required": ["filename", "headers", "rows"],
            },
            handler=create_csv_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="convert_document",
            description=(
                "Convertit un document d'un format à un autre. "
                "Conversions supportées: DOCX→PDF, DOCX→HTML, XLSX→CSV, HTML→PDF, MD→PDF, MD→DOCX, CSV→XLSX."
            ),
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du fichier source."},
                    "output_format": {"type": "string", "description": "Format cible (pdf, html, csv, xlsx, docx)."},
                },
                "required": ["input_path", "output_format"],
            },
            handler=convert_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P1B: Édition de documents existants ──
        HandlerDef(
            name="edit_docx",
            description=(
                "Édite un fichier DOCX existant. Opérations: replace_text, add_paragraph, delete_paragraph, "
                "add_image, set_header, set_footer, replace_in_table."
            ),
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du DOCX à éditer."},
                    "operations": {
                        "type": "string",
                        "description": (
                            'JSON array d\'opérations. Ex: [{"op":"replace_text","find":"ancien","replace":"nouveau"}, '
                            '{"op":"add_paragraph","text":"Nouveau paragraphe","style":"Normal"}]'
                        ),
                    },
                    "output_path": {"type": "string", "description": "Chemin du fichier de sortie (optionnel, écrase l'original si absent).", "default": ""},
                },
                "required": ["input_path", "operations"],
            },
            handler=edit_docx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="edit_xlsx",
            description=(
                "Édite un fichier XLSX existant. Opérations: set_cell, set_formula, add_row, delete_row, "
                "add_sheet, rename_sheet."
            ),
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du XLSX à éditer."},
                    "operations": {
                        "type": "string",
                        "description": (
                            'JSON array d\'opérations. Ex: [{"op":"set_cell","cell":"A1","value":"Hello"}, '
                            '{"op":"add_row","values":["a","b","c"]}]'
                        ),
                    },
                    "output_path": {"type": "string", "description": "Chemin du fichier de sortie (optionnel).", "default": ""},
                },
                "required": ["input_path", "operations"],
            },
            handler=edit_xlsx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="edit_pptx",
            description=(
                "Édite un fichier PPTX existant. Opérations: replace_text, add_slide, delete_slide, add_image."
            ),
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PPTX à éditer."},
                    "operations": {
                        "type": "string",
                        "description": (
                            'JSON array d\'opérations. Ex: [{"op":"replace_text","slide":1,"find":"ancien","replace":"nouveau"}, '
                            '{"op":"add_slide","title":"Nouvelle slide","content":"texte"}]'
                        ),
                    },
                    "output_path": {"type": "string", "description": "Chemin du fichier de sortie (optionnel).", "default": ""},
                },
                "required": ["input_path", "operations"],
            },
            handler=edit_pptx_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="annotate_pdf",
            description=(
                "Annote un PDF existant: ajouter du texte, surligner, apposer un tampon. "
                "Types d'annotation: text, highlight, stamp."
            ),
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF à annoter."},
                    "annotations": {
                        "type": "string",
                        "description": (
                            'JSON array d\'annotations. Ex: [{"page":0,"type":"text","x":100,"y":500,"text":"Approuvé","font_size":14}, '
                            '{"page":0,"type":"stamp","text":"VALIDÉ","position":"top-right"}]'
                        ),
                    },
                    "output_path": {"type": "string", "description": "Chemin de sortie (optionnel, génère *_annotated.pdf si absent).", "default": ""},
                },
                "required": ["input_path", "annotations"],
            },
            handler=annotate_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P2: Templates ──
        HandlerDef(
            name="create_from_template",
            description=(
                "Génère un document professionnel depuis un template Jinja2 pré-défini avec un design HTML/CSS soigné. "
                "Idéal pour : contrats, attestations, NDA, lettres officielles, rapports d'activité, fiches de poste, "
                "procès-verbaux, notes internes, bulletins de paie, relances impayées. "
                "Templates dispo: devis, facture, contrat_prestation, lettre_officielle, nda, bon_commande, "
                "attestation, rapport_activite, relance_impaye, note_interne, proces_verbal, "
                "fiche_poste, bulletin_paie. Utiliser list_templates pour voir les variables requises."
            ),
            parameters={
                "properties": {
                    "template_name": {"type": "string", "description": "Nom du template (ex: devis, contrat_prestation, nda)."},
                    "variables": {"type": "string", "description": "JSON dict des variables Jinja2 à injecter dans le template."},
                    "output_filename": {"type": "string", "description": "Nom du fichier de sortie (ex: devis-001.pdf)."},
                    "output_format": {"type": "string", "description": "Format de sortie: pdf ou html.", "default": "pdf"},
                },
                "required": ["template_name", "variables", "output_filename"],
            },
            handler=create_from_template_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="list_templates",
            description="Liste tous les templates de documents disponibles (builtin + custom).",
            parameters={"properties": {}, "required": []},
            handler=list_templates_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="save_template",
            description="Sauvegarde un template HTML Jinja2 personnalisé dans assets/templates/custom/.",
            parameters={
                "properties": {
                    "template_name": {"type": "string", "description": "Nom du template (lettres, chiffres, tirets)."},
                    "html_content": {"type": "string", "description": "Contenu HTML Jinja2 du template."},
                },
                "required": ["template_name", "html_content"],
            },
            handler=save_template_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P3: Intelligence documentaire ──
        HandlerDef(
            name="add_watermark",
            description="Ajoute un filigrane texte sur toutes les pages d'un PDF (CONFIDENTIEL, BROUILLON, etc.).",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF."},
                    "text": {"type": "string", "description": "Texte du filigrane.", "default": "CONFIDENTIEL"},
                    "opacity": {"type": "string", "description": "Opacité (0.0-1.0).", "default": "0.15"},
                    "angle": {"type": "string", "description": "Angle de rotation.", "default": "45"},
                    "output_path": {"type": "string", "description": "Chemin de sortie (optionnel).", "default": ""},
                },
                "required": ["input_path"],
            },
            handler=add_watermark_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="sign_document",
            description="Ajoute une image de signature sur un PDF (dernière page par défaut).",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF."},
                    "signature_image_path": {"type": "string", "description": "Chemin de l'image signature (PNG/JPG)."},
                    "page": {"type": "string", "description": "N° de page (-1 = dernière).", "default": "-1"},
                    "position": {"type": "string", "description": "Position: bottom-right, bottom-left, bottom-center.", "default": "bottom-right"},
                    "date_text": {"type": "string", "description": "Date sous la signature ('auto' = aujourd'hui).", "default": "auto"},
                    "output_path": {"type": "string", "description": "Chemin de sortie (optionnel).", "default": ""},
                },
                "required": ["input_path", "signature_image_path"],
            },
            handler=sign_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="fill_pdf_form",
            description="Remplit les champs d'un formulaire PDF interactif.",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF formulaire."},
                    "fields": {"type": "string", "description": 'JSON dict des champs à remplir (ex: {"nom":"Dupont","prenom":"Jean"}).'},
                    "output_filename": {"type": "string", "description": "Nom du fichier de sortie.", "default": ""},
                },
                "required": ["input_path", "fields"],
            },
            handler=fill_pdf_form_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="list_pdf_fields",
            description="Liste les champs remplissables d'un formulaire PDF.",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF."},
                },
                "required": ["input_path"],
            },
            handler=list_pdf_fields_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="analyze_document",
            description="Analyse structurée d'un document (extraction type, contenu, longueur).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du document à analyser."},
                },
                "required": ["path"],
            },
            handler=analyze_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="compare_documents",
            description="Compare deux documents et produit un rapport de différences (ajouts/suppressions).",
            parameters={
                "properties": {
                    "path_a": {"type": "string", "description": "Chemin du premier document."},
                    "path_b": {"type": "string", "description": "Chemin du second document."},
                },
                "required": ["path_a", "path_b"],
            },
            handler=compare_documents_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="protect_pdf",
            description="Protège un PDF par mot de passe (read-only, no-edit, ou full).",
            parameters={
                "properties": {
                    "input_path": {"type": "string", "description": "Chemin du PDF."},
                    "password": {"type": "string", "description": "Mot de passe de protection."},
                    "output_path": {"type": "string", "description": "Chemin de sortie (optionnel).", "default": ""},
                    "permissions": {"type": "string", "description": "Permissions: read-only, no-edit, full.", "default": "read-only"},
                },
                "required": ["input_path", "password"],
            },
            handler=protect_pdf_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="image_to_document",
            description="Convertit une image ou PDF scanné en document éditable via OCR.",
            parameters={
                "properties": {
                    "image_path": {"type": "string", "description": "Chemin de l'image ou PDF scanné."},
                    "output_format": {"type": "string", "description": "Format de sortie: docx ou pdf.", "default": "docx"},
                    "language": {"type": "string", "description": "Langue OCR (fra, eng, etc.).", "default": "fra"},
                },
                "required": ["image_path"],
            },
            handler=image_to_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P5 — Formats supplémentaires ──
        HandlerDef(
            name="create_markdown",
            description="Crée un fichier Markdown (.md) à partir d'un titre et contenu.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: notes.md)."},
                    "title": {"type": "string", "description": "Titre du document."},
                    "content": {"type": "string", "description": "Contenu markdown."},
                },
                "required": ["filename", "title", "content"],
            },
            handler=create_markdown_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_html",
            description="Crée un fichier HTML standalone avec CSS embarqué. Templates: default, report, email, print.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: page.html)."},
                    "title": {"type": "string", "description": "Titre de la page."},
                    "content": {"type": "string", "description": "Contenu texte/markdown."},
                    "css": {"type": "string", "description": "CSS additionnel (optionnel).", "default": ""},
                    "template": {"type": "string", "description": "Template: default, report, email, print.", "default": "default"},
                },
                "required": ["filename", "title", "content"],
            },
            handler=create_html_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_email_html",
            description="Crée un email HTML responsive compatible Gmail/Outlook (CSS inline, tables).",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier."},
                    "subject": {"type": "string", "description": "Sujet de l'email."},
                    "body": {"type": "string", "description": "Corps de l'email (texte/markdown)."},
                    "sender_name": {"type": "string", "description": "Nom de l'expéditeur.", "default": "Lumena"},
                    "sender_logo_url": {"type": "string", "description": "URL du logo (optionnel).", "default": ""},
                },
                "required": ["filename", "subject", "body"],
            },
            handler=create_email_html_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_ics",
            description="Crée un fichier iCalendar (.ics) RFC 5545, importable dans Google Calendar / Outlook.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: reunion.ics)."},
                    "events": {"type": "string", "description": "JSON array d'événements: [{title, start, end, location, description, attendees[]}]."},
                },
                "required": ["filename", "events"],
            },
            handler=create_ics_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="create_vcard",
            description="Crée un fichier vCard 3.0 (.vcf) pour contacts, importable dans les applications.",
            parameters={
                "properties": {
                    "filename": {"type": "string", "description": "Nom du fichier (ex: contact.vcf)."},
                    "contacts": {"type": "string", "description": "JSON array de contacts: [{name, company, phone, email, address, website}]."},
                },
                "required": ["filename", "contacts"],
            },
            handler=create_vcard_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        # ── P6 — Batch et automatisation ──
        HandlerDef(
            name="batch_documents",
            description="Génère un lot de documents à partir d'un template et d'une source de données (CSV, XLSX, JSON).",
            parameters={
                "properties": {
                    "template_name": {"type": "string", "description": "Nom du template (ex: devis, facture, contrat_prestation)."},
                    "data_source": {"type": "string", "description": "Chemin vers fichier CSV/XLSX/JSON, ou JSON array de dicts."},
                    "output_format": {"type": "string", "description": "Format de sortie: pdf ou html.", "default": "pdf"},
                },
                "required": ["template_name", "data_source"],
            },
            handler=batch_documents_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="zip_documents",
            description="Crée un fichier ZIP contenant les documents listés.",
            parameters={
                "properties": {
                    "output_filename": {"type": "string", "description": "Nom du fichier ZIP (ex: lot-factures.zip)."},
                    "paths": {"type": "string", "description": "JSON array de chemins fichiers à zipper."},
                },
                "required": ["output_filename", "paths"],
            },
            handler=zip_documents_handler,
            category="documents",
            source_module="handlers.documents",
        ),
        HandlerDef(
            name="assemble_document",
            description="Assemble un document PDF composite à partir de blocs (PDF, sections, texte).",
            parameters={
                "properties": {
                    "output_filename": {"type": "string", "description": "Nom du fichier final (ex: rapport-complet.pdf)."},
                    "parts": {"type": "string", "description": 'JSON array de parties: [{"type": "pdf"|"section"|"text", "path": "...", "title": "...", "content": "..."}].'},
                },
                "required": ["output_filename", "parts"],
            },
            handler=assemble_document_handler,
            category="documents",
            source_module="handlers.documents",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
