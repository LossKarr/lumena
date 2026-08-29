"""📓 NotionHub — Intégration Notion complète pour Lumena.

Nécessite dans .env:
    NOTION_API_KEY=<ton_integration_token>

Obtenir le token:
    1. Aller sur https://www.notion.so/my-integrations
    2. Cliquer "New integration" → copier le "Internal Integration Token"
    3. Dans chaque page/database Notion à partager:
       cliquer "..." → "Add connections" → choisir ton intégration

Utilise httpx (déjà installé dans Lumena) — pas de dépendance supplémentaire.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from loguru import logger


class NotionHub:
    """Client Notion API v1 — recherche, lecture, création et modification."""

    BASE_URL = "https://api.notion.com/v1"
    VERSION = "2022-06-28"  # stable, compatible all endpoints

    def __init__(self) -> None:
        self.token = os.getenv("NOTION_API_KEY", "").strip()
        if self.token:
            logger.info("NotionHub: token Notion configuré")
        else:
            logger.warning("NotionHub: NOTION_API_KEY manquant dans .env")

    def available(self) -> bool:
        return bool(self.token)

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            raise RuntimeError(
                "NOTION_API_KEY manquant dans .env\n"
                "Voir: https://www.notion.so/my-integrations"
            )
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": self.VERSION,
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Dict = None) -> Dict:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}{path}",
                headers=self._headers(),
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, body: Dict) -> Dict:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.BASE_URL}{path}",
                headers=self._headers(),
                json=body,
            )
            if resp.status_code >= 400:
                detail = resp.text[:500]
                logger.error(f"Notion POST {path} → {resp.status_code}: {detail}")
            resp.raise_for_status()
            return resp.json()

    async def _patch(self, path: str, body: Dict) -> Dict:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{self.BASE_URL}{path}",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def _delete(self, path: str) -> Dict:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{self.BASE_URL}{path}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    # ── Recherche ─────────────────────────────────────────────────────────────

    async def search(self, query: str) -> Dict[str, Any]:
        """Recherche dans tout le workspace Notion."""
        data = await self._post("/search", {"query": query, "page_size": 10})
        results = []
        for item in data.get("results", []):
            results.append(
                {
                    "type": item.get("object", ""),
                    "id": item.get("id", ""),
                    "title": self._extract_title(item),
                    "url": item.get("url", ""),
                }
            )
        return {"results": results, "count": len(results)}

    # ── Pages ─────────────────────────────────────────────────────────────────

    async def read_page(self, page_id: str) -> Dict[str, Any]:
        """Lit une page et retourne son contenu en Markdown."""
        page_id = self._clean_id(page_id)
        page = await self._get(f"/pages/{page_id}")
        blocks = await self._get_blocks(page_id)
        content_md = self._blocks_to_markdown(blocks)
        return {
            "id": page_id,
            "title": self._extract_title(page),
            "url": page.get("url", ""),
            "content": content_md,
        }

    async def create_page(
        self, parent_id: str, title: str, content: str
    ) -> Dict[str, Any]:
        """Crée une nouvelle page dans un parent (page ou database).

        Args:
            parent_id: ID d'une page ou database Notion (requis pour intégrations internes)
            title: Titre de la page
            content: Contenu en Markdown
        """
        if not parent_id:
            raise ValueError(
                "Notion n'autorise pas la création de pages à la racine pour les intégrations internes. "
                "Utilise notion_search pour trouver une page ou database parente, "
                "puis fournis son ID dans parent_id."
            )

        parent_id = self._clean_id(parent_id)

        # Détecter si c'est une database ou une page
        is_database = False
        db_title_prop = "title"  # default property name
        try:
            db_info = await self._get(f"/databases/{parent_id}")
            is_database = True
            # Find the actual title property name in the database schema
            for prop_name, prop_def in db_info.get("properties", {}).items():
                if prop_def.get("type") == "title":
                    db_title_prop = prop_name
                    break
        except Exception as e:
            logger.debug("[notion] schema lookup: {}", e)

        blocks = self._markdown_to_blocks(content)

        if is_database:
            body = {
                "parent": {"database_id": parent_id},
                "properties": {
                    db_title_prop: {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
                "children": blocks,
            }
        else:
            body = {
                "parent": {"page_id": parent_id},
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
                "children": blocks,
            }

        result = await self._post("/pages", body)
        return {
            "id": result.get("id", ""),
            "url": result.get("url", ""),
            "title": title,
        }

    async def update_page(self, page_id: str, content: str) -> Dict[str, Any]:
        """Remplace le contenu d'une page existante.

        Args:
            page_id: ID de la page à modifier
            content: Nouveau contenu en Markdown (remplace l'ancien)
        """
        page_id = self._clean_id(page_id)

        # Supprimer les blocs existants
        existing = await self._get_blocks(page_id)
        for block in existing:
            try:
                await self._delete(f"/blocks/{block['id']}")
            except Exception:
                pass  # Ignorer les erreurs de suppression individuelle

        # Ajouter les nouveaux blocs
        blocks = self._markdown_to_blocks(content)
        if blocks:
            await self._patch(f"/blocks/{page_id}/children", {"children": blocks})

        return {"id": page_id, "blocks_added": len(blocks)}

    # ── Databases ─────────────────────────────────────────────────────────────

    async def list_databases(self) -> Dict[str, Any]:
        """Liste toutes les databases du workspace."""
        data = await self._post(
            "/search",
            {"filter": {"value": "database", "property": "object"}, "page_size": 20},
        )
        dbs = []
        for item in data.get("results", []):
            dbs.append(
                {
                    "id": item.get("id", ""),
                    "title": self._extract_title(item),
                    "url": item.get("url", ""),
                }
            )
        return {"databases": dbs, "count": len(dbs)}

    async def query_database(
        self, database_id: str, filter_json: str = ""
    ) -> Dict[str, Any]:
        """Interroge une database Notion.

        Args:
            database_id: ID de la database
            filter_json: Filtre JSON optionnel au format Notion API
                         Ex: '{"property": "Status", "select": {"equals": "Done"}}'
        """
        database_id = self._clean_id(database_id)
        body: Dict[str, Any] = {"page_size": 20}

        if filter_json.strip():
            try:
                body["filter"] = json.loads(filter_json)
            except json.JSONDecodeError as e:
                return {"error": f"filter_json invalide: {e}"}

        data = await self._post(f"/databases/{database_id}/query", body)
        rows = []
        for item in data.get("results", []):
            props = {}
            for key, val in item.get("properties", {}).items():
                props[key] = self._extract_property_value(val)
            rows.append(
                {
                    "id": item.get("id", ""),
                    "url": item.get("url", ""),
                    "properties": props,
                }
            )
        return {"rows": rows, "count": len(rows)}

    async def add_to_database(
        self, database_id: str, properties_json: str
    ) -> Dict[str, Any]:
        """Ajoute une entrée dans une database Notion.

        Args:
            database_id: ID de la database
            properties_json: JSON des propriétés
                Ex: '{"Nom": "Tâche 1", "Priorité": "Haute", "Terminé": false}'
        """
        database_id = self._clean_id(database_id)
        try:
            user_props = json.loads(properties_json)
        except json.JSONDecodeError as e:
            return {"error": f"properties_json invalide: {e}"}

        # Construire les propriétés au format Notion
        notion_props: Dict[str, Any] = {}
        for key, value in user_props.items():
            if isinstance(value, str):
                notion_props[key] = {
                    "rich_text": [{"type": "text", "text": {"content": value}}]
                }
            elif isinstance(value, (int, float)):
                notion_props[key] = {"number": value}
            elif isinstance(value, bool):
                notion_props[key] = {"checkbox": value}
            else:
                notion_props[key] = {
                    "rich_text": [{"type": "text", "text": {"content": str(value)}}]
                }

        body = {
            "parent": {"database_id": database_id},
            "properties": notion_props,
        }
        result = await self._post("/pages", body)
        return {"id": result.get("id", ""), "url": result.get("url", "")}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_id(page_id: str) -> str:
        """Normalise un ID Notion (accepte UUIDs avec ou sans tirets, et URLs)."""
        if not page_id:
            raise ValueError("page_id ne peut pas être vide ou None")
        page_id = str(page_id).strip()
        # URL Notion → extraire l'ID (32 derniers chars hex)
        if "notion.so" in page_id:
            # Ex: https://www.notion.so/Mon-titre-abc123def456...
            parts = page_id.rstrip("/").split("/")[-1].split("-")
            if parts:
                page_id = parts[-1]
        # Retirer les tirets si c'est un UUID standard (32 hex chars)
        cleaned = page_id.replace("-", "")
        if len(cleaned) == 32 and all(c in '0123456789abcdefABCDEF' for c in cleaned):
            return cleaned
        return page_id

    @staticmethod
    def _extract_title(item: Dict) -> str:
        """Extrait le titre d'un objet Notion (page ou database)."""
        # Database: title est directement une liste de rich_text
        if item.get("object") == "database":
            title_list = item.get("title", [])
            if title_list:
                return "".join(t.get("plain_text", "") for t in title_list)

        # Page: titre dans properties
        props = item.get("properties", {})
        for key in ("title", "Name", "Titre", "Title", "Nom"):
            if key in props:
                rich = props[key].get("title", [])
                text = "".join(t.get("plain_text", "") for t in rich)
                if text:
                    return text

        # Fallback: premier champ de type "title"
        for val in props.values():
            if val.get("type") == "title":
                text = "".join(
                    t.get("plain_text", "") for t in val.get("title", [])
                )
                if text:
                    return text

        return item.get("id", "Sans titre")

    async def _get_blocks(self, block_id: str) -> List[Dict]:
        """Récupère tous les blocs d'une page (avec pagination)."""
        blocks = []
        cursor = None

        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._get(f"/blocks/{block_id}/children", params)
            blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return blocks

    @staticmethod
    def _blocks_to_markdown(blocks: List[Dict]) -> str:
        """Convertit des blocs Notion en Markdown."""
        lines = []
        for block in blocks:
            btype = block.get("type", "")
            content = block.get(btype, {})
            rich = content.get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rich)

            if btype == "heading_1":
                lines.append(f"# {text}")
            elif btype == "heading_2":
                lines.append(f"## {text}")
            elif btype == "heading_3":
                lines.append(f"### {text}")
            elif btype == "bulleted_list_item":
                lines.append(f"- {text}")
            elif btype == "numbered_list_item":
                lines.append(f"1. {text}")
            elif btype == "to_do":
                checked = content.get("checked", False)
                lines.append(f"{'- [x]' if checked else '- [ ]'} {text}")
            elif btype == "code":
                lang = content.get("language", "")
                lines.append(f"```{lang}\n{text}\n```")
            elif btype == "quote":
                lines.append(f"> {text}")
            elif btype == "callout":
                icon = content.get("icon", {}).get("emoji", "💡")
                lines.append(f"> {icon} {text}")
            elif btype == "divider":
                lines.append("---")
            elif btype == "paragraph":
                lines.append(text if text else "")
            else:
                if text:
                    lines.append(text)

        return "\n\n".join(line for line in lines if line.strip())

    @staticmethod
    def _markdown_to_blocks(content: str) -> List[Dict]:
        """Convertit du Markdown en blocs Notion."""
        blocks = []
        for line in content.split("\n"):
            line = line.rstrip()
            if not line:
                continue

            if line.startswith("### "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {
                            "rich_text": [{"type": "text", "text": {"content": line[4:]}}]
                        },
                    }
                )
            elif line.startswith("## "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                        },
                    }
                )
            elif line.startswith("# "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        },
                    }
                )
            elif line.startswith("- [ ] "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "to_do",
                        "to_do": {
                            "rich_text": [
                                {"type": "text", "text": {"content": line[6:]}}
                            ],
                            "checked": False,
                        },
                    }
                )
            elif line.startswith("- [x] "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "to_do",
                        "to_do": {
                            "rich_text": [
                                {"type": "text", "text": {"content": line[6:]}}
                            ],
                            "checked": True,
                        },
                    }
                )
            elif line.startswith("- "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        },
                    }
                )
            elif line.startswith("> "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "quote",
                        "quote": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        },
                    }
                )
            elif line == "---" or line == "***":
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            else:
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": line}}]
                        },
                    }
                )
        return blocks

    @staticmethod
    def _extract_property_value(prop: Dict) -> Any:
        """Extrait la valeur lisible d'une propriété Notion."""
        ptype = prop.get("type", "")
        val = prop.get(ptype)

        if ptype in ("title", "rich_text"):
            return "".join(t.get("plain_text", "") for t in (val or []))
        elif ptype == "number":
            return val
        elif ptype == "checkbox":
            return val
        elif ptype == "select":
            return val.get("name", "") if val else ""
        elif ptype == "multi_select":
            return [v.get("name", "") for v in (val or [])]
        elif ptype == "date":
            return val.get("start", "") if val else ""
        elif ptype in ("url", "email", "phone_number"):
            return val or ""
        elif ptype == "people":
            return [p.get("name", "") for p in (val or [])]
        elif ptype == "formula":
            formula_val = val or {}
            ftype = formula_val.get("type", "")
            return formula_val.get(ftype, "")
        else:
            return str(val) if val is not None else ""
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
