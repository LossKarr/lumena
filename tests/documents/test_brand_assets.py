from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src.documents.brand_assets import BrandAssetStore
from src.documents.studio import DocumentStudio
from src.documents.template_models import TemplateValidationError


ROOT = Path(__file__).parents[2]


def _image_bytes(color=(217, 119, 6, 255), *, fmt="PNG") -> bytes:
    output = BytesIO()
    Image.new("RGBA", (120, 48), color).save(output, fmt)
    return output.getvalue()


def test_multiple_logos_allow_exactly_one_active(tmp_path):
    store = BrandAssetStore(tmp_path / "logos")
    first = store.add(_image_bytes(), filename="marque.png", name="Marque principale")
    second = store.add(_image_bytes((15, 118, 110, 255)), filename="second.webp", name="Secondaire")

    assert first["active"] is True
    assert sum(item["active"] for item in store.list_logos()) == 1
    store.set_active(second["id"])
    listed = store.list_logos()
    assert [item["id"] for item in listed if item["active"]] == [second["id"]]
    assert store.active_data_uri().startswith("data:image/png;base64,")


def test_logo_is_sanitized_deduplicated_and_deleted(tmp_path):
    store = BrandAssetStore(tmp_path / "logos")
    first = store.add(_image_bytes(), filename="brand.png")
    duplicate = store.add(_image_bytes(), filename="other-name.png")
    assert duplicate["id"] == first["id"]
    assert len(store.list_logos()) == 1
    assert store.content_path(first["id"]).read_bytes().startswith(b"\x89PNG")
    store.delete(first["id"])
    assert store.list_logos() == []
    assert store.active_record() is None


@pytest.mark.parametrize("payload", [b"", b"<svg><script>alert(1)</script></svg>", b"not-an-image"])
def test_logo_rejects_empty_svg_and_invalid_payloads(tmp_path, payload):
    with pytest.raises(TemplateValidationError):
        BrandAssetStore(tmp_path / "logos").add(payload, filename="logo.svg")


@pytest.mark.asyncio
async def test_active_logo_is_injected_in_studio_html_generation(tmp_path):
    studio = DocumentStudio(
        tmp_path / "studio",
        builtin_root=ROOT / "assets" / "templates",
        output_root=tmp_path / "output",
    )
    studio.logos.add(_image_bytes(), filename="brand.png")
    record = studio.catalog.get("facture")
    result = await studio.generate(
        kind="facture",
        output_format="html",
        data=studio.catalog.read_sample_data(record),
        filename="facture-marquee",
    )
    html = Path(result["path"]).read_text(encoding="utf-8")
    assert "lumena-document-brand" in html
    assert "data:image/png;base64," in html

