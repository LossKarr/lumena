"""
🌐 LUMENA - Crawler Web autonome

Crawler HTTP orienté extraction utile avec limites de sécurité.
Objectif: exploration robuste de grands corpus sans bloquer le runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urldefrag
import asyncio
import collections
import fnmatch
import json
import re
import threading
import uuid
from ..utils.persistence import atomic_write_json

import httpx
from bs4 import BeautifulSoup
from loguru import logger



@dataclass
class CrawlPage:
    url: str
    depth: int
    status_code: Optional[int]
    title: str
    excerpt: str
    score: float
    interesting: bool
    insights: Dict[str, Any]
    error: Optional[str] = None


class WebCrawler:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.campaigns_dir = self.data_dir / "campaigns"
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)
        # Guard anti-double-run par campaign_id (in-memory, reset au redémarrage)
        self._running_campaigns: set = set()
        self._campaigns_lock = threading.Lock()

    @staticmethod
    def _normalize_url(base_url: str, candidate: str) -> Optional[str]:
        if not candidate:
            return None
        absolute = urljoin(base_url, candidate.strip())
        absolute, _ = urldefrag(absolute)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.netloc:
            return None
        return absolute

    @staticmethod
    def _domain(url: str) -> str:
        return (urlparse(url).netloc or "").lower()

    @staticmethod
    def _is_allowed_by_patterns(url: str, includes: List[str], excludes: List[str]) -> bool:
        lowered = url.lower()
        if includes and not any(fnmatch.fnmatch(lowered, pat) for pat in includes):
            return False
        if excludes and any(fnmatch.fnmatch(lowered, pat) for pat in excludes):
            return False
        return True

    @staticmethod
    def _normalize_globs(patterns: Any) -> List[str]:
        if patterns is None:
            return []

        raw_items: List[str] = []
        if isinstance(patterns, str):
            raw_items = patterns.split(",")
        elif isinstance(patterns, (list, tuple, set)):
            for item in patterns:
                if item is None:
                    continue
                if isinstance(item, str):
                    raw_items.extend(item.split(","))
                else:
                    raw_items.append(str(item))
        else:
            raw_items = str(patterns).split(",")

        return [p.strip().lower() for p in raw_items if p and p.strip()]

    def _campaign_dir(self, campaign_id: str) -> Path:
        return self.campaigns_dir / campaign_id

    def _campaign_state_path(self, campaign_id: str) -> Path:
        return self._campaign_dir(campaign_id) / "state.json"

    def _reports_archive_dir(self, campaign_id: str) -> Path:
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        from ..utils.paths import WORKSPACE_DIR
        workspace_root = WORKSPACE_DIR
        base = workspace_root / day_key / "reports" / "web_crawler" / campaign_id
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _default_campaign_id() -> str:
        return datetime.now(timezone.utc).strftime("crawl_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]

    @staticmethod
    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _save_campaign_state(self, campaign_id: str, state: Dict[str, Any]) -> None:
        state_path = self._campaign_state_path(campaign_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_path, state)

    def _load_campaign_state(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        state_path = self._campaign_state_path(campaign_id)
        if not state_path.exists():
            return None
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Impossible de lire l'état campaign {}: {}", campaign_id, exc)
            return None

    def _append_run_report(self, campaign_id: str, run_payload: Dict[str, Any]) -> Path:
        runs_dir = self._campaign_dir(campaign_id) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = run_payload.get("run_id") or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
        report_path = runs_dir / f"{run_id}.json"
        atomic_write_json(report_path, run_payload)
        return report_path

    @staticmethod
    def _extract_links(base_url: str, soup: BeautifulSoup, max_links_per_page: int) -> List[str]:
        links: List[str] = []
        seen = set()
        for anchor in soup.find_all("a", href=True):
            normalized = WebCrawler._normalize_url(base_url, anchor.get("href", ""))
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            links.append(normalized)
            if len(links) >= max_links_per_page:
                break
        return links

    @staticmethod
    def _extract_text_and_title(html: str, max_chars: int) -> Tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas"]):
            tag.decompose()

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{2,}", "\n\n", text)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text, title

    @staticmethod
    def _score_content(title: str, text: str, keyword_hint: str) -> float:
        score = 0.0
        lowered_title = (title or "").lower()
        lowered_text = (text or "").lower()

        if len(text) >= 1500:
            score += 2.0
        elif len(text) >= 700:
            score += 1.0

        if title:
            score += 1.0

        quality_markers = ["report", "analysis", "archive", "document", "official", "pdf", "research"]
        for marker in quality_markers:
            if marker in lowered_title:
                score += 0.6

        if keyword_hint:
            tokens = [t.strip().lower() for t in re.split(r"[,\s]+", keyword_hint) if t.strip()]
            for token in tokens[:12]:
                if token in lowered_title:
                    score += 1.2
                if token in lowered_text:
                    score += 0.7

        return round(score, 2)

    @staticmethod
    def _extract_page_insights(url: str, title: str, text: str) -> Dict[str, Any]:
        normalized = re.sub(r"\s+", " ", text or " ").strip()
        lowered = normalized.lower()

        sentences = [s.strip() for s in re.split(r"(?<=[\.!?])\s+", normalized) if s.strip()]

        def _pick_sentence(patterns: List[str]) -> str:
            for sent in sentences[:120]:
                lowered_sent = sent.lower()
                if any(re.search(pattern, lowered_sent) for pattern in patterns):
                    return sent[:240]
            return ""

        offer_patterns = [
            r"\boffre\b", r"\bpropose\b", r"\bsolution\b", r"\bservice\b",
            r"\bplateforme\b", r"\bproduit\b", r"\baccompagnement\b",
        ]
        audience_patterns = [
            r"\bpour\b", r"\bdestin[ée] à\b", r"\bentreprise\b", r"\bpm[eé]\b",
            r"\bparticulier\b", r"\bclient\b", r"\bprofessionnel\b",
        ]

        offer_summary = _pick_sentence(offer_patterns)
        audience_summary = _pick_sentence(audience_patterns)

        email_matches = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized)))[:5]
        phone_matches = sorted(
            set(
                re.findall(
                    r"(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,5}\d{2,4}",
                    normalized,
                )
            )
        )[:5]
        phone_matches = [p.strip() for p in phone_matches if len(re.sub(r"\D", "", p)) >= 8]

        price_matches = sorted(
            set(
                re.findall(
                    r"(?:€|\$|£)\s?\d+[\d\s.,]*|\d+[\d\s.,]*\s?(?:€|\$|£)|\b\d+[\d\s.,]*\s?(?:eur|euros?)\b",
                    lowered,
                    flags=re.IGNORECASE,
                )
            )
        )[:8]

        cta_keywords = [
            "contact", "devis", "essai", "demo", "démarrer", "commencer", "inscription",
            "réserver", "acheter", "book", "join", "signup",
        ]
        cta_hits = [word for word in cta_keywords if word in lowered]

        location_sentence = _pick_sentence([
            r"\bfrance\b", r"\bparis\b", r"\blyon\b", r"\bmarseille\b", r"\bbelgique\b",
            r"\bsuisse\b", r"\bcanada\b", r"\badresse\b", r"\bsi[eè]ge\b", r"\blocat",
        ])

        domain = urlparse(url).netloc or ""
        key_points: List[str] = []
        if offer_summary:
            key_points.append(f"Offre: {offer_summary}")
        if audience_summary:
            key_points.append(f"Cible: {audience_summary}")
        if location_sentence:
            key_points.append(f"Localisation: {location_sentence}")
        if price_matches:
            key_points.append(f"Prix détectés: {', '.join(price_matches[:3])}")
        if cta_hits:
            key_points.append(f"CTA clés: {', '.join(cta_hits[:4])}")

        return {
            "domain": domain,
            "offer_summary": offer_summary,
            "audience_summary": audience_summary,
            "location_summary": location_sentence,
            "contact_emails": email_matches,
            "contact_phones": phone_matches,
            "pricing_signals": price_matches,
            "cta_signals": cta_hits,
            "key_points": key_points[:6],
            "title": title or "",
        }

    @staticmethod
    async def _fetch_with_retry(
        client: httpx.AsyncClient,
        url: str,
        request_retries: int,
        retry_backoff_sec: float,
    ) -> httpx.Response:
        retries = max(0, int(request_retries))
        backoff = max(0.0, float(retry_backoff_sec))
        last_error: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                # 4xx (hors 408/429) => pas de retry, généralement définitif
                if 400 <= status < 500 and status not in {408, 429}:
                    raise
                last_error = exc
            except Exception as exc:
                last_error = exc

            if attempt < retries and backoff > 0:
                await asyncio.sleep(backoff * (attempt + 1))

        if last_error is not None:
            raise last_error
        raise RuntimeError("échec requête web")

    async def crawl(
        self,
        *,
        start_url: str,
        max_pages: int = 100,
        max_depth: int = 2,
        keyword_hint: str = "",
        same_domain_only: bool = True,
        request_timeout_sec: int = 20,
        request_retries: int = 1,
        retry_backoff_sec: float = 0.35,
        delay_sec: float = 0.0,
        max_links_per_page: int = 40,
        include_patterns: str = "",
        exclude_patterns: str = "",
    ) -> Dict[str, Any]:
        seed = self._normalize_url("", start_url)
        if not seed:
            return {"success": False, "error": f"URL invalide: {start_url}"}
        from ..utils.url_safety import assert_url_safe
        try:
            assert_url_safe(seed)
        except ValueError as e:
            return {"success": False, "error": f"URL bloquée (SSRF): {e}"}

        max_pages = max(1, min(int(max_pages), 2000))
        max_depth = max(0, min(int(max_depth), 8))
        request_timeout_sec = max(5, min(int(request_timeout_sec), 120))
        request_retries = max(0, min(int(request_retries), 4))
        retry_backoff_sec = max(0.0, min(float(retry_backoff_sec), 3.0))
        max_links_per_page = max(5, min(int(max_links_per_page), 200))
        delay_sec = max(0.0, min(float(delay_sec), 10.0))

        include_globs = self._normalize_globs(include_patterns)
        exclude_globs = self._normalize_globs(exclude_patterns)

        seed_domain = self._domain(seed)
        queue: List[Tuple[str, int]] = [(seed, 0)]
        visited: set[str] = set()
        pages: List[CrawlPage] = []
        started_at = datetime.now(timezone.utc)

        logger.info(
            "Crawler start: seed={} max_pages={} max_depth={} same_domain_only={}",
            seed,
            max_pages,
            max_depth,
            int(same_domain_only),
        )

        headers = {
            "User-Agent": "LumenaCrawler/1.0 (+https://lumena.local)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        async with httpx.AsyncClient(timeout=request_timeout_sec, follow_redirects=True, headers=headers) as client:
            while queue and len(visited) < max_pages:
                url, depth = queue.pop(0)
                if url in visited:
                    continue

                visited.add(url)

                if same_domain_only and self._domain(url) != seed_domain:
                    continue

                if not self._is_allowed_by_patterns(url, include_globs, exclude_globs):
                    continue

                status_code: Optional[int] = None
                title = ""
                excerpt = ""
                score = 0.0
                interesting = False
                insights: Dict[str, Any] = {}
                error: Optional[str] = None

                try:
                    response = await self._fetch_with_retry(
                        client=client,
                        url=url,
                        request_retries=request_retries,
                        retry_backoff_sec=retry_backoff_sec,
                    )
                    status_code = response.status_code

                    content_type = (response.headers.get("content-type", "") or "").lower()
                    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                        error = f"content-type ignoré: {content_type[:80]}"
                    else:
                        text, title = self._extract_text_and_title(response.text, max_chars=12000)
                        excerpt = text[:1200]
                        score = self._score_content(title, text, keyword_hint)
                        insights = self._extract_page_insights(url=url, title=title, text=text)
                        interesting = score >= 3.5

                        if depth < max_depth:
                            soup = BeautifulSoup(response.text, "html.parser")
                            links = self._extract_links(url, soup, max_links_per_page=max_links_per_page)
                            for candidate in links:
                                if candidate not in visited:
                                    queue.append((candidate, depth + 1))

                except Exception as exc:
                    error = str(exc)[:240]

                page = CrawlPage(
                    url=url,
                    depth=depth,
                    status_code=status_code,
                    title=title,
                    excerpt=excerpt,
                    score=score,
                    interesting=interesting,
                    insights=insights,
                    error=error,
                )
                pages.append(page)

                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)

        interesting_pages = [p for p in pages if p.interesting and not p.error]
        interesting_pages.sort(key=lambda p: p.score, reverse=True)

        finished_at = datetime.now(timezone.utc)
        duration_sec = (finished_at - started_at).total_seconds()

        run_id = started_at.strftime("%Y%m%d_%H%M%S")
        json_path = self.data_dir / f"crawl_{run_id}.json"
        md_path = self.data_dir / f"crawl_{run_id}.md"

        payload = {
            "run_id": run_id,
            "start_url": seed,
            "started_at": started_at.isoformat() + "Z",
            "finished_at": finished_at.isoformat() + "Z",
            "duration_sec": round(duration_sec, 2),
            "limits": {
                "max_pages": max_pages,
                "max_depth": max_depth,
                "same_domain_only": same_domain_only,
                "request_timeout_sec": request_timeout_sec,
                "request_retries": request_retries,
                "retry_backoff_sec": retry_backoff_sec,
                "delay_sec": delay_sec,
                "max_links_per_page": max_links_per_page,
            },
            "stats": {
                "visited": len(visited),
                "captured": len(pages),
                "interesting": len(interesting_pages),
                "errors": len([p for p in pages if p.error]),
            },
            "pages": [
                {
                    "url": p.url,
                    "depth": p.depth,
                    "status_code": p.status_code,
                    "title": p.title,
                    "score": p.score,
                    "interesting": p.interesting,
                    "error": p.error,
                    "excerpt": p.excerpt,
                    "insights": p.insights,
                }
                for p in pages
            ],
        }

        atomic_write_json(json_path, payload)

        lines = [
            f"# Crawl report {run_id}",
            "",
            f"- URL de départ: {seed}",
            f"- Durée: {round(duration_sec, 2)}s",
            f"- Pages visitées: {len(visited)}",
            f"- Pages capturées: {len(pages)}",
            f"- Pages intéressantes: {len(interesting_pages)}",
            f"- Erreurs: {len([p for p in pages if p.error])}",
            "",
            "## Top pages intéressantes",
            "",
        ]

        for rank, p in enumerate(interesting_pages[:30], start=1):
            lines.append(f"{rank}. [{p.title or p.url}]({p.url}) — score={p.score}")
            if p.excerpt:
                lines.append(f"   - Extrait: {p.excerpt[:220].replace(chr(10), ' ')}")
            key_points = (p.insights or {}).get("key_points") or []
            for point in key_points[:2]:
                lines.append(f"   - {point}")

        if not interesting_pages:
            lines.append("Aucune page n'a dépassé le seuil d'intérêt.")

        md_path.write_text("\n".join(lines), encoding="utf-8")

        return {
            "success": True,
            "run_id": run_id,
            "visited": len(visited),
            "captured": len(pages),
            "interesting": len(interesting_pages),
            "errors": len([p for p in pages if p.error]),
            "duration_sec": round(duration_sec, 2),
            "report_json": str(json_path),
            "report_md": str(md_path),
            "top": [
                {"url": p.url, "title": p.title, "score": p.score, "insights": p.insights}
                for p in interesting_pages[:10]
            ],
        }

    async def crawl_campaign(
        self,
        *,
        start_url: str,
        campaign_id: str = "",
        pages_per_run: int = 200,
        max_total_pages: int = 5000,
        max_depth: int = 3,
        keyword_hint: str = "",
        same_domain_only: bool = True,
        request_timeout_sec: int = 20,
        request_retries: int = 1,
        retry_backoff_sec: float = 0.35,
        delay_sec: float = 0.0,
        max_links_per_page: int = 40,
        include_patterns: str = "",
        exclude_patterns: str = "",
    ) -> Dict[str, Any]:
        seed = self._normalize_url("", start_url)
        if not seed:
            return {"success": False, "error": f"URL invalide: {start_url}"}

        pages_per_run = max(1, min(int(pages_per_run), 2000))
        max_total_pages = max(1, min(int(max_total_pages), 200000))
        max_depth = max(0, min(int(max_depth), 8))
        request_timeout_sec = max(5, min(int(request_timeout_sec), 120))
        request_retries = max(0, min(int(request_retries), 4))
        retry_backoff_sec = max(0.0, min(float(retry_backoff_sec), 3.0))
        max_links_per_page = max(5, min(int(max_links_per_page), 200))
        delay_sec = max(0.0, min(float(delay_sec), 10.0))

        include_globs = self._normalize_globs(include_patterns)
        exclude_globs = self._normalize_globs(exclude_patterns)

        if not campaign_id.strip():
            campaign_id = self._default_campaign_id()
        campaign_id = campaign_id.strip()

        with self._campaigns_lock:
            if campaign_id in self._running_campaigns:
                return {"success": False, "error": f"Campagne '{campaign_id}' déjà en cours d'exécution."}
            self._running_campaigns.add(campaign_id)

        try:
            return await self._run_campaign_inner(
                seed=seed, campaign_id=campaign_id,
                pages_per_run=pages_per_run, max_total_pages=max_total_pages,
                max_depth=max_depth, keyword_hint=keyword_hint,
                same_domain_only=same_domain_only,
                request_timeout_sec=request_timeout_sec,
                request_retries=request_retries,
                retry_backoff_sec=retry_backoff_sec,
                delay_sec=delay_sec, max_links_per_page=max_links_per_page,
                include_globs=include_globs, exclude_globs=exclude_globs,
            )
        finally:
            with self._campaigns_lock:
                self._running_campaigns.discard(campaign_id)

    async def _run_campaign_inner(
        self, *, seed, campaign_id, pages_per_run, max_total_pages,
        max_depth, keyword_hint, same_domain_only,
        request_timeout_sec, request_retries, retry_backoff_sec,
        delay_sec, max_links_per_page, include_globs, exclude_globs,
    ) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id)
        if state is None:
            seed_domain = self._domain(seed)
            state = {
                "campaign_id": campaign_id,
                "created_at": self._utcnow_iso(),
                "updated_at": self._utcnow_iso(),
                "seed_url": seed,
                "seed_domain": seed_domain,
                "options": {
                    "same_domain_only": bool(same_domain_only),
                    "max_depth": max_depth,
                    "request_timeout_sec": request_timeout_sec,
                    "request_retries": request_retries,
                    "retry_backoff_sec": retry_backoff_sec,
                    "delay_sec": delay_sec,
                    "max_links_per_page": max_links_per_page,
                    "include_patterns": include_globs,
                    "exclude_patterns": exclude_globs,
                    "keyword_hint": keyword_hint,
                },
                "limits": {
                    "max_total_pages": max_total_pages,
                },
                "stats": {
                    "runs": 0,
                    "pages_crawled_total": 0,
                    "errors_total": 0,
                    "interesting_total": 0,
                },
                "queue": [{"url": seed, "depth": 0}],
                "visited": [],
                "interesting": [],
                "last_run": None,
            }
        else:
            seed = state.get("seed_url", seed)
            seed_domain = state.get("seed_domain", self._domain(seed))

        visited_set = set(state.get("visited", []))
        queue: List[Tuple[str, int]] = []
        for item in state.get("queue", []):
            try:
                queue.append((str(item.get("url", "")), int(item.get("depth", 0))))
            except Exception:
                continue  # entrée queue invalide

        if not queue:
            queue.append((seed, 0))

        global_pages_crawled = int(state.get("stats", {}).get("pages_crawled_total", 0))
        if global_pages_crawled >= max_total_pages:
            return {
                "success": True,
                "campaign_id": campaign_id,
                "message": "Campagne déjà au maximum configuré",
                "done": True,
                "pages_crawled_total": global_pages_crawled,
                "max_total_pages": max_total_pages,
            }

        started_at = datetime.now(timezone.utc)
        run_id = started_at.strftime("run_%Y%m%d_%H%M%S")
        run_pages: List[Dict[str, Any]] = []
        run_visited = 0
        run_errors = 0
        run_interesting = 0

        headers = {
            "User-Agent": "LumenaCrawler/1.0 (+https://lumena.local)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        async with httpx.AsyncClient(timeout=request_timeout_sec, follow_redirects=True, headers=headers) as client:
            while queue and run_visited < pages_per_run and global_pages_crawled < max_total_pages:
                url, depth = queue.pop(0)
                if not url or url in visited_set:
                    continue

                visited_set.add(url)
                run_visited += 1
                global_pages_crawled += 1

                if same_domain_only and self._domain(url) != seed_domain:
                    run_pages.append(
                        {
                            "url": url,
                            "depth": depth,
                            "status_code": None,
                            "title": "",
                            "score": 0.0,
                            "interesting": False,
                            "error": "hors domaine",
                            "excerpt": "",
                        }
                    )
                    continue

                if not self._is_allowed_by_patterns(url, include_globs, exclude_globs):
                    run_pages.append(
                        {
                            "url": url,
                            "depth": depth,
                            "status_code": None,
                            "title": "",
                            "score": 0.0,
                            "interesting": False,
                            "error": "filtré par patterns",
                            "excerpt": "",
                        }
                    )
                    continue

                status_code: Optional[int] = None
                title = ""
                excerpt = ""
                score = 0.0
                interesting = False
                insights: Dict[str, Any] = {}
                error: Optional[str] = None

                try:
                    response = await self._fetch_with_retry(
                        client=client,
                        url=url,
                        request_retries=request_retries,
                        retry_backoff_sec=retry_backoff_sec,
                    )
                    status_code = response.status_code

                    content_type = (response.headers.get("content-type", "") or "").lower()
                    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                        error = f"content-type ignoré: {content_type[:80]}"
                    else:
                        text, title = self._extract_text_and_title(response.text, max_chars=12000)
                        excerpt = text[:1200]
                        score = self._score_content(title, text, keyword_hint)
                        insights = self._extract_page_insights(url=url, title=title, text=text)
                        interesting = score >= 3.5

                        if depth < max_depth:
                            soup = BeautifulSoup(response.text, "html.parser")
                            links = self._extract_links(url, soup, max_links_per_page=max_links_per_page)
                            for candidate in links:
                                if candidate not in visited_set:
                                    queue.append((candidate, depth + 1))
                except Exception as exc:
                    error = str(exc)[:240]

                if error:
                    run_errors += 1
                if interesting and not error:
                    run_interesting += 1
                    state_interesting = state.get("interesting", [])
                    existing_idx = None
                    for i, item in enumerate(state_interesting):
                        if item.get("url") == url:
                            existing_idx = i
                            break
                    candidate_item = {
                        "url": url,
                        "title": title,
                        "score": score,
                        "last_seen": self._utcnow_iso(),
                        "excerpt": excerpt[:600],
                        "insights": insights,
                    }
                    if existing_idx is None:
                        state_interesting.append(candidate_item)
                    else:
                        prev = state_interesting[existing_idx]
                        if float(candidate_item["score"]) >= float(prev.get("score", 0.0)):
                            state_interesting[existing_idx] = candidate_item

                run_pages.append(
                    {
                        "url": url,
                        "depth": depth,
                        "status_code": status_code,
                        "title": title,
                        "score": score,
                        "interesting": interesting,
                        "error": error,
                        "excerpt": excerpt,
                        "insights": insights,
                    }
                )

                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)

        finished_at = datetime.now(timezone.utc)
        duration_sec = (finished_at - started_at).total_seconds()

        run_payload = {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "started_at": started_at.isoformat() + "Z",
            "finished_at": finished_at.isoformat() + "Z",
            "duration_sec": round(duration_sec, 2),
            "stats": {
                "visited": run_visited,
                "interesting": run_interesting,
                "errors": run_errors,
                "queue_remaining": len(queue),
            },
            "pages": run_pages,
        }
        run_report_path = self._append_run_report(campaign_id, run_payload)

        state_stats = state.setdefault("stats", {})
        state_stats["runs"] = int(state_stats.get("runs", 0)) + 1
        state_stats["pages_crawled_total"] = global_pages_crawled
        state_stats["errors_total"] = int(state_stats.get("errors_total", 0)) + run_errors
        state_stats["interesting_total"] = len(state.get("interesting", []))

        state["visited"] = list(visited_set)
        state["queue"] = [{"url": u, "depth": d} for (u, d) in queue[:200000]]
        state["updated_at"] = self._utcnow_iso()
        state["last_run"] = {
            "run_id": run_id,
            "duration_sec": round(duration_sec, 2),
            "visited": run_visited,
            "interesting": run_interesting,
            "errors": run_errors,
            "run_report": str(run_report_path),
        }
        self._save_campaign_state(campaign_id, state)

        done = global_pages_crawled >= max_total_pages or not queue
        next_recommendation = "Terminé" if done else "Relancer web_crawl_campaign avec le même campaign_id"

        return {
            "success": True,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "done": done,
            "run_report": str(run_report_path),
            "state_file": str(self._campaign_state_path(campaign_id)),
            "run_visited": run_visited,
            "run_interesting": run_interesting,
            "run_errors": run_errors,
            "pages_crawled_total": global_pages_crawled,
            "interesting_total": len(state.get("interesting", [])),
            "queue_remaining": len(queue),
            "max_total_pages": max_total_pages,
            "duration_sec": round(duration_sec, 2),
            "next": next_recommendation,
        }

    def campaign_status(self, campaign_id: str) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id.strip())
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        stats = state.get("stats", {})
        queue = state.get("queue", [])
        interesting = state.get("interesting", [])
        limits = state.get("limits", {})
        max_total_pages = int(limits.get("max_total_pages", 0) or 0)
        pages_total = int(stats.get("pages_crawled_total", 0) or 0)
        done = (max_total_pages > 0 and pages_total >= max_total_pages) or len(queue) == 0

        top = sorted(interesting, key=lambda x: float(x.get("score", 0.0)), reverse=True)[:10]

        return {
            "success": True,
            "campaign_id": campaign_id,
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "seed_url": state.get("seed_url"),
            "runs": int(stats.get("runs", 0) or 0),
            "pages_crawled_total": pages_total,
            "interesting_total": int(stats.get("interesting_total", 0) or len(interesting)),
            "errors_total": int(stats.get("errors_total", 0) or 0),
            "queue_remaining": len(queue),
            "max_total_pages": max_total_pages,
            "done": done,
            "last_run": state.get("last_run"),
            "top": top,
        }

    def campaign_export_index(self, campaign_id: str, top_n: int = 500) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id.strip())
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        top_n = max(1, min(int(top_n), 50000))
        interesting = sorted(
            state.get("interesting", []),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[:top_n]

        campaign_dir = self._campaign_dir(campaign_id)
        index_json = campaign_dir / "index_interesting.json"
        index_md = campaign_dir / "index_interesting.md"

        payload = {
            "campaign_id": campaign_id,
            "generated_at": self._utcnow_iso(),
            "count": len(interesting),
            "items": interesting,
        }
        atomic_write_json(index_json, payload)

        lines = [
            f"# Index des pages intéressantes — {campaign_id}",
            "",
            f"- Généré: {payload['generated_at']}",
            f"- Éléments: {len(interesting)}",
            "",
        ]
        for i, item in enumerate(interesting, start=1):
            title = item.get("title") or item.get("url")
            lines.append(f"{i}. [{title}]({item.get('url')}) — score={item.get('score')}")
            excerpt = (item.get("excerpt") or "").replace("\n", " ").strip()
            if excerpt:
                lines.append(f"   - {excerpt[:280]}")

        index_md.write_text("\n".join(lines), encoding="utf-8")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "count": len(interesting),
            "index_json": str(index_json),
            "index_md": str(index_md),
        }

    def campaign_generate_pro_report(
        self,
        campaign_id: str,
        top_n_findings: int = 40,
        include_last_runs: int = 5,
        report_title: str = "",
    ) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id.strip())
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        campaign_id = campaign_id.strip()
        top_n_findings = max(5, min(int(top_n_findings), 500))
        include_last_runs = max(1, min(int(include_last_runs), 30))

        campaign_dir = self._campaign_dir(campaign_id)
        runs_dir = campaign_dir / "runs"

        run_files: List[Path] = []
        if runs_dir.exists():
            run_files = sorted(runs_dir.glob("*.json"), key=lambda p: p.name, reverse=True)[:include_last_runs]

        run_payloads: List[Dict[str, Any]] = []
        for run_file in run_files:
            try:
                run_payloads.append(json.loads(run_file.read_text(encoding="utf-8")))
            except Exception:
                continue  # fichier run illisible

        stats = state.get("stats", {})
        options = state.get("options", {})
        limits = state.get("limits", {})
        max_total_pages = int(limits.get("max_total_pages", 0) or 0)
        pages_crawled_total = int(stats.get("pages_crawled_total", 0) or 0)
        errors_total = int(stats.get("errors_total", 0) or 0)
        interesting_items = state.get("interesting", []) or []
        interesting_total = int(stats.get("interesting_total", 0) or len(interesting_items))
        queue_remaining = len(state.get("queue", []) or [])

        success_pages = max(0, pages_crawled_total - errors_total)
        reliability_score = 100.0 if pages_crawled_total == 0 else round((success_pages / pages_crawled_total) * 100.0, 2)
        coverage_score = 100.0
        if max_total_pages > 0:
            coverage_score = round(min(100.0, (pages_crawled_total / max_total_pages) * 100.0), 2)
        relevance_ratio = 0.0 if pages_crawled_total == 0 else (interesting_total / pages_crawled_total)
        relevance_score = round(min(100.0, relevance_ratio * 220.0), 2)
        depth_score = round(min(100.0, max(0.0, float(options.get("max_depth", 0) or 0) / 4.0 * 100.0)), 2)

        overall_score = round(
            reliability_score * 0.35
            + coverage_score * 0.20
            + relevance_score * 0.30
            + depth_score * 0.15,
            2,
        )

        freshness = state.get("updated_at") or state.get("created_at") or self._utcnow_iso()
        title = (report_title or "").strip() or f"Rapport Premium Web Scalping — {campaign_id}"

        interesting_sorted = sorted(
            interesting_items,
            key=lambda item: float(item.get("score", 0.0) or 0.0),
            reverse=True,
        )
        top_findings = interesting_sorted[:top_n_findings]

        offer_summaries = [
            str(((item.get("insights") or {}).get("offer_summary") or "")).strip()
            for item in top_findings
        ]
        offer_summaries = [x for x in offer_summaries if x]
        audience_summaries = [
            str(((item.get("insights") or {}).get("audience_summary") or "")).strip()
            for item in top_findings
        ]
        audience_summaries = [x for x in audience_summaries if x]
        location_summaries = [
            str(((item.get("insights") or {}).get("location_summary") or "")).strip()
            for item in top_findings
        ]
        location_summaries = [x for x in location_summaries if x]

        all_emails: List[str] = []
        all_phones: List[str] = []
        all_prices: List[str] = []
        all_cta: List[str] = []
        for item in top_findings:
            insights = item.get("insights") or {}
            all_emails.extend(insights.get("contact_emails") or [])
            all_phones.extend(insights.get("contact_phones") or [])
            all_prices.extend(insights.get("pricing_signals") or [])
            all_cta.extend(insights.get("cta_signals") or [])

        all_emails = list(dict.fromkeys([x.strip() for x in all_emails if x.strip()]))[:8]
        all_phones = list(dict.fromkeys([x.strip() for x in all_phones if x.strip()]))[:8]
        all_prices = list(dict.fromkeys([x.strip() for x in all_prices if x.strip()]))[:10]
        all_cta = list(dict.fromkeys([x.strip() for x in all_cta if x.strip()]))[:10]

        business_summary = {
            "what_page_offers": offer_summaries[:5],
            "target_audience": audience_summaries[:5],
            "where_or_contact": {
                "location_signals": location_summaries[:5],
                "emails": all_emails,
                "phones": all_phones,
            },
            "pricing_signals": all_prices,
            "cta_signals": all_cta,
        }

        error_kinds: collections.Counter[str] = collections.Counter()
        last_run_pages = 0
        for payload in run_payloads:
            pages = payload.get("pages", []) or []
            last_run_pages += len(pages)
            for page in pages:
                raw_error = (page.get("error") or "").strip().lower()
                if not raw_error:
                    continue
                if "timeout" in raw_error:
                    error_kinds["timeout"] += 1
                elif "404" in raw_error:
                    error_kinds["404"] += 1
                elif "403" in raw_error or "forbidden" in raw_error:
                    error_kinds["403"] += 1
                elif "content-type" in raw_error:
                    error_kinds["content_type"] += 1
                else:
                    error_kinds["autres"] += 1

        top_errors = [{"type": kind, "count": count} for kind, count in error_kinds.most_common(8)]

        key_risks: List[str] = []
        if reliability_score < 85:
            key_risks.append("Fiabilité réseau insuffisante (taux d'erreurs élevé).")
        if queue_remaining > 0 and (max_total_pages == 0 or pages_crawled_total < max_total_pages):
            key_risks.append("Couverture incomplète: la campagne n'a pas encore épuisé la file.")
        if relevance_ratio < 0.12:
            key_risks.append("Faible densité de pages à forte valeur: ciblage à affiner.")
        if error_kinds.get("timeout", 0) >= 5:
            key_risks.append("Multiples timeouts détectés: budgets temps et parallélisme à calibrer.")
        if not key_risks:
            key_risks.append("Aucun risque critique bloquant détecté sur l'échantillon actuel.")

        recommendations: List[str] = []
        recommendations.append("Prioriser les URLs top score pour extraction approfondie et synthèse métier.")
        if error_kinds.get("timeout", 0) > 0:
            recommendations.append("Réduire le timeout HTTP unitaire et ajouter retries courts avec jitter.")
        if queue_remaining > 0:
            recommendations.append("Relancer web_crawl_campaign par batches pour atteindre la couverture cible.")
        if relevance_ratio < 0.20:
            recommendations.append("Renforcer include_patterns/exclude_patterns et keyword_hint pour améliorer le signal.")
        recommendations.append("Automatiser un export quotidien + envoi email décisionnel à l'équipe.")

        executive = {
            "title": title,
            "generated_at": self._utcnow_iso(),
            "campaign_id": campaign_id,
            "seed_url": state.get("seed_url"),
            "overall_score": overall_score,
            "scores": {
                "reliability": reliability_score,
                "coverage": coverage_score,
                "relevance": relevance_score,
                "depth": depth_score,
            },
            "stats": {
                "pages_crawled_total": pages_crawled_total,
                "errors_total": errors_total,
                "interesting_total": interesting_total,
                "queue_remaining": queue_remaining,
                "runs": int(stats.get("runs", 0) or 0),
                "max_total_pages": max_total_pages,
                "analysed_recent_run_pages": last_run_pages,
            },
            "key_risks": key_risks,
            "recommendations": recommendations,
            "top_errors": top_errors,
            "freshness": freshness,
            "business_summary": business_summary,
        }

        report_json_path = campaign_dir / "report_pro.json"
        report_md_path = campaign_dir / "report_pro.md"

        payload = {
            "executive": executive,
            "options": options,
            "business_summary": business_summary,
            "top_findings": top_findings,
            "latest_runs": [
                {
                    "run_id": run.get("run_id"),
                    "started_at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                    "duration_sec": run.get("duration_sec"),
                    "stats": run.get("stats", {}),
                }
                for run in run_payloads
            ],
        }
        atomic_write_json(report_json_path, payload)

        md_lines: List[str] = [
            f"# {title}",
            "",
            "## Résumé exécutif",
            "",
            f"- Campagne: `{campaign_id}`",
            f"- URL seed: {state.get('seed_url')}",
            f"- Note globale: **{overall_score}/100**",
            f"- Dernière mise à jour: {freshness}",
            "",
            "## Scorecard",
            "",
            f"- Fiabilité: {reliability_score}/100",
            f"- Couverture: {coverage_score}/100",
            f"- Pertinence: {relevance_score}/100",
            f"- Profondeur: {depth_score}/100",
            "",
            "## KPI opérationnels",
            "",
            f"- Pages crawlé total: {pages_crawled_total}",
            f"- Pages intéressantes: {interesting_total}",
            f"- Erreurs total: {errors_total}",
            f"- File restante: {queue_remaining}",
            f"- Runs exécutés: {int(stats.get('runs', 0) or 0)}",
            "",
            "## Risques majeurs",
            "",
        ]
        for item in key_risks:
            md_lines.append(f"- {item}")

        md_lines.extend(["", "## Recommandations prioritaires", ""])
        for item in recommendations:
            md_lines.append(f"- {item}")

        md_lines.extend(["", "## Compréhension business de la page", ""])
        if business_summary["what_page_offers"]:
            md_lines.append("### Ce que la page propose")
            for item in business_summary["what_page_offers"]:
                md_lines.append(f"- {item}")
        if business_summary["target_audience"]:
            md_lines.append("")
            md_lines.append("### Public cible détecté")
            for item in business_summary["target_audience"]:
                md_lines.append(f"- {item}")

        where_or_contact = business_summary["where_or_contact"]
        md_lines.append("")
        md_lines.append("### Où / Contact")
        location_signals = where_or_contact.get("location_signals") or []
        emails = where_or_contact.get("emails") or []
        phones = where_or_contact.get("phones") or []
        if location_signals:
            for item in location_signals:
                md_lines.append(f"- Localisation: {item}")
        if emails:
            md_lines.append(f"- Emails: {', '.join(emails)}")
        if phones:
            md_lines.append(f"- Téléphones: {', '.join(phones)}")
        if not location_signals and not emails and not phones:
            md_lines.append("- Aucun signal explicite trouvé dans l'échantillon.")

        md_lines.append("")
        md_lines.append("### Signaux prix et actions")
        if business_summary["pricing_signals"]:
            md_lines.append(f"- Prix détectés: {', '.join(business_summary['pricing_signals'][:8])}")
        else:
            md_lines.append("- Prix: non détectés clairement.")
        if business_summary["cta_signals"]:
            md_lines.append(f"- CTA détectés: {', '.join(business_summary['cta_signals'][:8])}")
        else:
            md_lines.append("- CTA: non détectés clairement.")

        md_lines.extend(["", "## Top pages à valeur", ""])
        for idx, item in enumerate(top_findings, start=1):
            link = item.get("url") or ""
            title_value = item.get("title") or link
            score_value = item.get("score")
            md_lines.append(f"{idx}. [{title_value}]({link}) — score={score_value}")
            excerpt = (item.get("excerpt") or "").replace("\n", " ").strip()
            if excerpt:
                md_lines.append(f"   - {excerpt[:280]}")

        md_lines.extend(["", "## Erreurs fréquentes (échantillon runs récents)", ""])
        if top_errors:
            for err in top_errors:
                md_lines.append(f"- {err.get('type')}: {err.get('count')}")
        else:
            md_lines.append("- Aucune erreur significative détectée dans les runs analysés.")

        md_lines.extend(["", "## Annexes", ""])
        md_lines.append(f"- Rapport JSON: `{report_json_path}`")
        md_lines.append(f"- Campagne état: `{self._campaign_state_path(campaign_id)}`")
        if run_files:
            md_lines.append("- Runs inclus:")
            for path in run_files:
                md_lines.append(f"  - `{path}`")

        report_md_content = "\n".join(md_lines)
        report_md_path.write_text(report_md_content, encoding="utf-8")

        archive_dir = self._reports_archive_dir(campaign_id)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_json_path = archive_dir / f"report_pro_{ts}.json"
        archive_md_path = archive_dir / f"report_pro_{ts}.md"
        atomic_write_json(archive_json_path, payload)
        archive_md_path.write_text(report_md_content, encoding="utf-8")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "overall_score": overall_score,
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
            "report_json_archive": str(archive_json_path),
            "report_md_archive": str(archive_md_path),
            "top_findings_count": len(top_findings),
            "risks_count": len(key_risks),
        }

    def campaign_explain_page(
        self,
        campaign_id: str,
        page_url: str = "",
    ) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id.strip())
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        items = sorted(
            state.get("interesting", []) or [],
            key=lambda x: float(x.get("score", 0.0) or 0.0),
            reverse=True,
        )
        if not items:
            return {"success": False, "error": "aucune page intéressante disponible"}

        selected: Optional[Dict[str, Any]] = None
        target = (page_url or "").strip().lower()
        if target:
            for item in items:
                if (item.get("url") or "").strip().lower() == target:
                    selected = item
                    break
            if selected is None:
                for item in items:
                    if target in (item.get("url") or "").strip().lower():
                        selected = item
                        break
        if selected is None:
            selected = items[0]

        insights = selected.get("insights") or {}
        explanation = {
            "page_url": selected.get("url"),
            "title": selected.get("title") or selected.get("url"),
            "score": selected.get("score"),
            "what_page_offers": insights.get("offer_summary") or "Information insuffisante pour résumer l'offre précisément.",
            "target_audience": insights.get("audience_summary") or "Public cible non explicite dans l'échantillon.",
            "where_or_contact": {
                "location": insights.get("location_summary") or "Localisation non explicite.",
                "emails": insights.get("contact_emails") or [],
                "phones": insights.get("contact_phones") or [],
            },
            "pricing_signals": insights.get("pricing_signals") or [],
            "cta_signals": insights.get("cta_signals") or [],
            "important_points": insights.get("key_points") or [
                "Consulte le rapport premium pour plus de détails multi-pages."
            ],
            "excerpt": (selected.get("excerpt") or "")[:900],
        }

        return {
            "success": True,
            "campaign_id": campaign_id,
            "explanation": explanation,
        }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
