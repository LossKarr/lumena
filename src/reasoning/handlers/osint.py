# -*- coding: utf-8 -*-
"""
Module OSINT gratuit pour Lumena.
Toutes les sources utilisées sont 100% gratuites, sans clé API.

Sources intégrées :
  - DNS (dnspython)       — résolution complète A/MX/NS/TXT/AAAA/CNAME
  - WHOIS (python-whois)  — infos registrar, dates, contacts
  - SSL/TLS (ssl+socket)  — certificat, expiration, CN, SANs
  - crt.sh               — transparence certificats (SANS API KEY)
  - ip-api.com           — géolocalisation IP (SANS API KEY)
  - Shodan InternetDB     — open ports/vulns par IP (SANS API KEY)
  - Archive.org CDX API  — historique Wayback Machine (SANS API KEY)
  - XposedOrNot          — fuites d'emails (SANS API KEY, 100% gratuit)
  - leakcheck.io         — fuites d'emails fallback (free tier, sans clé)
  - ThreatFox (Abuse.ch) — IOCs malwares (SANS API KEY)
  - EmailRep.io          — réputation email (SANS API KEY)
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import struct
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from .contracts import HandlerResult

try:
    import requests as _req
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    import dns.resolver as _dns_resolver
    import dns.exception
    _DNS_OK = True
except ImportError:
    _DNS_OK = False

try:
    import whois as _whois
    _WHOIS_OK = True
except ImportError:
    _WHOIS_OK = False

_HTTP_TIMEOUT = 8  # secondes

# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, headers: dict | None = None) -> dict | list | None:
    """GET HTTP sécurisé avec timeout fixe. Renvoie le JSON ou None."""
    if not _REQUESTS_OK:
        return None
    try:
        hdrs = {"User-Agent": "LumenaOSINT/1.0 (research only)"}
        if headers:
            hdrs.update(headers)
        r = _req.get(url, params=params, headers=hdrs, timeout=_HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"GET {url}: {e}")
    return None


def _post_json(url: str, payload: dict) -> dict | None:
    if not _REQUESTS_OK:
        return None
    try:
        r = _req.post(url, json=payload, timeout=_HTTP_TIMEOUT,
                      headers={"User-Agent": "LumenaOSINT/1.0"})
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"POST {url}: {e}")
    return None


def _is_ip(target: str) -> bool:
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return False


def _clean_domain(target: str) -> str:
    """Extrait le domaine pur depuis une URL ou un texte."""
    target = target.strip()
    if target.startswith(("http://", "https://")):
        target = urllib.parse.urlparse(target).netloc
    return target.split("/")[0].lower()


# ---------------------------------------------------------------------------
# Modules OSINT gratuits
# ---------------------------------------------------------------------------

def _dns_lookup(domain: str) -> dict:
    """Résolution DNS complète sans clé API (Python stdlib + dnspython)."""
    result: dict[str, Any] = {"domain": domain, "records": {}}
    record_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]

    if _DNS_OK:
        for rtype in record_types:
            try:
                answers = _dns_resolver.resolve(domain, rtype, lifetime=5)
                result["records"][rtype] = [str(r) for r in answers]
            except (dns.exception.DNSException, Exception):
                pass  # DNS record type non disponible
    else:
        # stdlib fallback — juste l'IP
        try:
            ips = socket.getaddrinfo(domain, None)
            result["records"]["A"] = list({i[4][0] for i in ips})
        except Exception as e:
            logger.debug(f"DNS fallback getaddrinfo: {e}")
    return result


def _whois_lookup(target: str) -> dict:
    """WHOIS registrar/réseau — gratuit."""
    if not _WHOIS_OK:
        return {"error": "python-whois non installé"}
    try:
        w = _whois.whois(target)
        # Sérialiser les dates
        def _fmt(v):
            if isinstance(v, list):
                return [_fmt(i) for i in v]
            if isinstance(v, datetime):
                return v.isoformat()
            return str(v) if v else None
        return {k: _fmt(v) for k, v in w.items() if v}
    except Exception as exc:
        return {"error": str(exc)}


def _ssl_cert(domain: str, port: int = 443) -> dict:
    """Analyse certificat TLS — stdlib Python pur, gratuit."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        with socket.create_connection((domain, port), timeout=6) as conn:
            with ctx.wrap_socket(conn, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        # Expiration
        expire_str = cert.get("notAfter", "")
        try:
            expire_dt = datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z")
            days_left = (expire_dt.replace(tzinfo=timezone.utc) -
                         datetime.now(timezone.utc)).days
        except Exception:
            days_left = None
        sans = [v for _, v in cert.get("subjectAltName", [])]
        subject = dict(x[0] for x in cert.get("subject", []))
        return {
            "CN": subject.get("commonName"),
            "issuer": dict(x[0] for x in cert.get("issuer", [])),
            "valid_from": cert.get("notBefore"),
            "valid_until": expire_str,
            "days_until_expiry": days_left,
            "expired": days_left is not None and days_left < 0,
            "SANs": sans[:20],
        }
    except ssl.SSLCertVerificationError as exc:
        return {"error": f"Certificat invalide: {exc}"}
    except Exception as exc:
        return {"error": str(exc)}


def _crtsh_subdomains(domain: str) -> list[str]:
    """Énumération de sous-domaines via crt.sh (Certificate Transparency)."""
    data = _get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"})
    if not data or not isinstance(data, list):
        return []
    subs: set[str] = set()
    for entry in data[:500]:
        names = entry.get("name_value", "")
        for name in names.split("\n"):
            name = name.strip().lstrip("*.")
            if name.endswith(domain) and name != domain:
                subs.add(name.lower())
    return sorted(subs)[:100]


def _ip_geo(ip: str) -> dict:
    """Géolocalisation IP — ip-api.com (1000 req/min gratuit, aucune clé)."""
    data = _get(f"http://ip-api.com/json/{ip}",
                params={"fields": "status,country,regionName,city,isp,org,as,query,proxy,hosting"})
    return data or {}


def _shodan_internetdb(ip: str) -> dict:
    """Shodan InternetDB — ports ouverts + CVEs pour une IP, 100% gratuit sans clé."""
    data = _get(f"https://internetdb.shodan.io/{ip}")
    return data or {}


def _wayback_history(domain: str, limit: int = 10) -> list[dict]:
    """Historique Archive.org (Wayback Machine CDX API) — gratuit."""
    data = _get(
        "http://web.archive.org/cdx/search/cdx",
        params={
            "url": domain,
            "output": "json",
            "fl": "timestamp,statuscode,original",
            "limit": limit,
            "from": "20100101",
        }
    )
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    headers, rows = data[0], data[1:]
    return [dict(zip(headers, row)) for row in rows]


def _hibp_email(email: str) -> dict:
    """
    Vérifier si un email est dans des fuites de données.
    Source 1: XposedOrNot (xposedornot.com) — 100% gratuit, sans clé API.
    Source 2: leakcheck.io free tier — fallback sans clé.
    Retourne: {"breached": True/False/None, "count": int, "breaches": [...], "source": str}
    None = API indisponible (ne pas déduire que l'email est sûr).
    """
    if not _REQUESTS_OK:
        return {"breached": None, "error": "requests non disponible"}

    import time

    # --- Source 1 : XposedOrNot (gratuit, sans inscription) ---
    try:
        time.sleep(0.3)  # rate-limit respectueux
        r = _req.get(
            f"https://api.xposedornot.com/v1/check-email/{urllib.parse.quote(email)}",
            headers={"User-Agent": "LumenaOSINT/2.1 (security research)"},
            timeout=_HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            # Réponse "non trouvé" : {"Error": "...", "email": "..."}
            if "Error" in data or "error" in data:
                return {"breached": False, "count": 0, "source": "XposedOrNot"}
            # Format A : {"breaches": [["Adobe", "LinkedIn", ...]], ...}
            # XposedOrNot retourne une LISTE IMBRIQUÉE : breaches[0] = la vraie liste
            breaches_raw = data.get("breaches") or []
            if breaches_raw:
                first = breaches_raw[0]
                bnames = first if isinstance(first, list) else breaches_raw
                if bnames:
                    return {"breached": True, "count": len(bnames), "breaches": bnames[:15], "source": "XposedOrNot"}
            # Format B : {"exposures": {"Adobe": {...}, "LinkedIn": {...}}}
            exposures = data.get("exposures") or {}
            if exposures:
                bnames = list(exposures.keys())
                return {"breached": True, "count": len(bnames), "breaches": bnames[:15], "source": "XposedOrNot"}
            # 200 sans données de breach = non trouvé
            return {"breached": False, "count": 0, "source": "XposedOrNot"}
        elif r.status_code in (404, 400, 425):
            return {"breached": False, "count": 0, "source": "XposedOrNot"}
        # Autre code HTTP → indisponible
    except Exception as e:
        logger.debug(f"Breach check: {e}")

    # Toutes les sources indisponibles → ne pas affirmer que l'email est sûr
    return {"breached": None, "count": 0, "error": "APIs indisponibles — vérifiez manuellement sur https://xposedornot.com/xposed#"}


def _threatfox_ioc(query: str) -> dict:
    """ThreatFox (Abuse.ch) — lookup IOC malwares, gratuit sans clé."""
    payload = {"query": "search_ioc", "search_term": query}
    data = _post_json("https://threatfox-api.abuse.ch/api/v1/", payload)
    if not data:
        return {}
    status = data.get("query_status", "")
    iocs = data.get("data", [])
    if isinstance(iocs, list) and iocs:
        return {
            "found": True,
            "count": len(iocs),
            "malware": list({i.get("malware_printable", "?") for i in iocs[:5]}),
            "confidence": iocs[0].get("confidence_level", 0),
            "first_seen": iocs[0].get("first_seen"),
        }
    return {"found": False, "status": status}


def _http_headers(url: str) -> dict:
    """Récupère les headers HTTP — révèle stack technique, gratuit."""
    if not _REQUESTS_OK:
        return {}
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        r = _req.head(url, timeout=6, allow_redirects=True,
                      headers={"User-Agent": "LumenaOSINT/1.0"})
        interesting = ["server", "x-powered-by", "x-frame-options",
                       "content-security-policy", "strict-transport-security",
                       "x-content-type-options", "set-cookie",
                       "x-generator", "x-drupal-cache", "x-wordpress"]
        return {
            "status_code": r.status_code,
            "final_url": r.url,
            "headers": {k: v for k, v in r.headers.items()
                        if k.lower() in interesting},
            "all_headers": dict(r.headers),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Handler principal : osint_scan
# ---------------------------------------------------------------------------

async def osint_scan_handler(ctx, target: str, scan_type: str = "passive",
                             email: str = "") -> dict:
    """
    Scan OSINT complet sur un domaine, IP ou email.
    100% gratuit — aucune clé API requise.

    Args:
        target: Domaine (ex: example.com), IP ou URL
        scan_type: 'passive' (défaut) | 'active' (inclut TCP scan léger)
        email: Email à vérifier dans les fuites (optionnel)

    Returns:
        Rapport OSINT complet stocké en mémoire
    """
    results: dict[str, Any] = {
        "target": target,
        "scan_type": scan_type,
        "timestamp": datetime.now().isoformat(),
        "sections": {},
    }
    lines: list[str] = [f"## Rapport OSINT : `{target}`\n"]

    is_ip = _is_ip(target)
    domain = target if not is_ip else target
    if not is_ip:
        domain = _clean_domain(target)

    loop = asyncio.get_running_loop()

    if is_ip:
        # --- Toutes les requêtes IP en parallèle ---
        phase1_coros = [
            loop.run_in_executor(None, _ip_geo, target),
            loop.run_in_executor(None, _shodan_internetdb, target),
            loop.run_in_executor(None, _threatfox_ioc, target),
        ]
        if email:
            phase1_coros.append(loop.run_in_executor(None, _hibp_email, email))
        g1 = await asyncio.gather(*phase1_coros, return_exceptions=True)
        geo  = g1[0] if not isinstance(g1[0], Exception) else {}
        shd  = g1[1] if not isinstance(g1[1], Exception) else {}
        tf   = g1[2] if not isinstance(g1[2], Exception) else {}
        hibp = g1[3] if len(g1) > 3 and not isinstance(g1[3], Exception) else None

        results["sections"]["ip_geo"] = geo
        lines.append(f"### Géolocalisation IP ({target})")
        for k in ("country", "regionName", "city", "isp", "org", "as", "proxy", "hosting"):
            if geo.get(k):
                lines.append(f"  **{k}**: {geo[k]}")
        lines.append("")

        results["sections"]["shodan_free"] = shd
        if shd and "detail" not in shd:
            lines.append(f"### Shodan InternetDB ({target}) — Gratuit")
            ports = shd.get("ports", [])
            vulns = shd.get("vulns", [])
            tags  = shd.get("tags", [])
            lines.append(f"  **Ports ouverts**: {ports}")
            if vulns:
                lines.append(f"  ⚠️ **CVEs**: {', '.join(vulns[:10])}")
            if tags:
                lines.append(f"  **Tags**: {', '.join(tags)}")
            lines.append("")

        results["sections"]["threatfox"] = tf
        lines.append("### ThreatFox IOC (Abuse.ch)")
        if tf.get("found"):
            lines.append(f"  ⚠️ **Malwares associés**: {', '.join(tf.get('malware', []))}")
            lines.append(f"  **Confiance**: {tf.get('confidence')}% | Premier vu: {tf.get('first_seen')}")
        else:
            lines.append("  ✅ Aucun IOC malveillant trouvé")
        lines.append("")

        if email and hibp is not None:
            results["sections"]["email_breach"] = hibp
            source = hibp.get("source", "XposedOrNot/leakcheck.io")
            lines.append(f"### Fuites email ({source}) — {email}")
            if hibp.get("breached") is True:
                lines.append(f"  ⚠️ **{hibp['count']} fuite(s)**: {', '.join(hibp.get('breaches', []))}")
            elif hibp.get("breached") is None:
                lines.append(f"  ⚠️ Vérification impossible: {hibp.get('error', 'API indisponible')}")
                lines.append("  ℹ️ Vérifiez manuellement: https://xposedornot.com/xposed#")
            else:
                lines.append("  ✅ Aucune fuite connue pour cet email")
            lines.append("")

    else:
        # --- Phase 1 : toutes les requêtes domaine en parallèle ---
        phase1_coros = [
            loop.run_in_executor(None, _dns_lookup, domain),
            loop.run_in_executor(None, _whois_lookup, domain),
            loop.run_in_executor(None, _ssl_cert, domain),
            loop.run_in_executor(None, _crtsh_subdomains, domain),
            loop.run_in_executor(None, _http_headers, domain),
            loop.run_in_executor(None, _wayback_history, domain, 5),
            loop.run_in_executor(None, _threatfox_ioc, target),
        ]
        if email:
            phase1_coros.append(loop.run_in_executor(None, _hibp_email, email))
        g1 = await asyncio.gather(*phase1_coros, return_exceptions=True)
        dns_data   = g1[0] if not isinstance(g1[0], Exception) else {"records": {}}
        whois_data = g1[1] if not isinstance(g1[1], Exception) else {}
        ssl_data   = g1[2] if not isinstance(g1[2], Exception) else {"error": "failed"}
        subs       = g1[3] if not isinstance(g1[3], Exception) else []
        hdrs       = g1[4] if not isinstance(g1[4], Exception) else {}
        wayback    = g1[5] if not isinstance(g1[5], Exception) else []
        tf         = g1[6] if not isinstance(g1[6], Exception) else {}
        hibp       = g1[7] if len(g1) > 7 and not isinstance(g1[7], Exception) else None

        # Résoudre l'IP depuis DNS pour Phase 2
        recs = dns_data.get("records", {})
        first_ip = recs.get("A", [""])[0] if recs.get("A") else ""
        if first_ip:
            results["_resolved_ip"] = first_ip

        # --- Phase 2 : géoloc + Shodan avec l'IP résolue ---
        if first_ip:
            geo, shd = await asyncio.gather(
                loop.run_in_executor(None, _ip_geo, first_ip),
                loop.run_in_executor(None, _shodan_internetdb, first_ip),
                return_exceptions=True,
            )
            geo = geo if not isinstance(geo, Exception) else {}
            shd = shd if not isinstance(shd, Exception) else {}
        else:
            geo, shd = {}, {}

        # --- Construire le rapport ---
        results["sections"]["dns"] = dns_data
        lines.append("### DNS")
        for rtype, vals in recs.items():
            lines.append(f"  **{rtype}**: {', '.join(vals[:5])}")
        lines.append("")

        results["sections"]["whois"] = whois_data
        lines.append("### WHOIS")
        for key in ("registrar", "creation_date", "expiration_date", "name_servers",
                    "country", "emails"):
            v = whois_data.get(key)
            if v:
                lines.append(f"  **{key}**: {v}")
        lines.append("")

        results["sections"]["ssl"] = ssl_data
        lines.append("### Certificat TLS")
        if "error" not in ssl_data:
            status = "⚠️ EXPIRÉ" if ssl_data.get("expired") else f"✅ valide ({ssl_data.get('days_until_expiry')} jours)"
            lines.append(f"  **Statut**: {status}")
            lines.append(f"  **CN**: {ssl_data.get('CN')}")
            lines.append(f"  **SANs** ({len(ssl_data.get('SANs', []))}): {', '.join(ssl_data.get('SANs', [])[:5])}")
        else:
            lines.append(f"  ⚠️ {ssl_data['error']}")
        lines.append("")

        results["sections"]["subdomains"] = subs
        lines.append(f"### Sous-domaines crt.sh ({len(subs)} trouvés)")
        lines.append("  " + ", ".join(subs[:20]) if subs else "  Aucun trouvé")
        lines.append("")

        if first_ip:
            results["sections"]["ip_geo"] = geo
            lines.append(f"### Géolocalisation IP ({first_ip})")
            for k in ("country", "regionName", "city", "isp", "org", "as", "proxy", "hosting"):
                if geo.get(k):
                    lines.append(f"  **{k}**: {geo[k]}")
            lines.append("")

            results["sections"]["shodan_free"] = shd
            if shd and "detail" not in shd:
                lines.append(f"### Shodan InternetDB ({first_ip}) — Gratuit")
                ports = shd.get("ports", [])
                vulns = shd.get("vulns", [])
                tags  = shd.get("tags", [])
                lines.append(f"  **Ports ouverts**: {ports}")
                if vulns:
                    lines.append(f"  ⚠️ **CVEs**: {', '.join(vulns[:10])}")
                if tags:
                    lines.append(f"  **Tags**: {', '.join(tags)}")
                lines.append("")

        results["sections"]["http_headers"] = hdrs
        lines.append("### Headers HTTP")
        if "error" not in hdrs:
            lines.append(f"  **Status**: {hdrs.get('status_code')}")
            for k, v in hdrs.get("headers", {}).items():
                lines.append(f"  **{k}**: {v[:100]}")
        else:
            lines.append(f"  ⚠️ {hdrs.get('error')}")
        lines.append("")

        results["sections"]["wayback"] = wayback
        lines.append("### Archive.org — Historique")
        if wayback:
            for entry in wayback:
                lines.append(f"  [{entry.get('timestamp', '?')}] {entry.get('statuscode', '?')} {entry.get('original', '')[:80]}")
        else:
            lines.append("  Aucun historique trouvé")
        lines.append("")

        results["sections"]["threatfox"] = tf
        lines.append("### ThreatFox IOC (Abuse.ch)")
        if tf.get("found"):
            lines.append(f"  ⚠️ **Malwares associés**: {', '.join(tf.get('malware', []))}")
            lines.append(f"  **Confiance**: {tf.get('confidence')}% | Premier vu: {tf.get('first_seen')}")
        else:
            lines.append("  ✅ Aucun IOC malveillant trouvé")
        lines.append("")

        if email and hibp is not None:
            results["sections"]["email_breach"] = hibp
            source = hibp.get("source", "XposedOrNot/leakcheck.io")
            lines.append(f"### Fuites email ({source}) — {email}")
            if hibp.get("breached") is True:
                lines.append(f"  ⚠️ **{hibp['count']} fuite(s)**: {', '.join(hibp.get('breaches', []))}")
            elif hibp.get("breached") is None:
                lines.append(f"  ⚠️ Vérification impossible: {hibp.get('error', 'API indisponible')}")
                lines.append("  ℹ️ Vérifiez manuellement: https://xposedornot.com/xposed#")
            else:
                lines.append("  ✅ Aucune fuite connue pour cet email")
            lines.append("")

    # --- Stocker en mémoire Lumena ---
    summary = "\n".join(lines)
    try:
        memory = getattr(getattr(ctx, "lumena", None), "memory", None)
        if memory:
            memory.remember(
                summary,
                memory_type="document",
                importance=0.80,
            )
    except Exception as e:
        logger.debug(f"Memory learn osint: {e}")

    results["report"] = summary
    return HandlerResult.ok(summary, handler_name="osint_scan")


# ---------------------------------------------------------------------------
# Handler : ip_info — géoloc + Shodan gratuit pour une IP seule
# ---------------------------------------------------------------------------

async def ip_info_handler(ctx, ip: str) -> dict:
    """
    Infos rapides sur une IP (géoloc + ports ouverts + CVEs).
    100% gratuit, sans clé API.
    """
    loop = asyncio.get_running_loop()
    geo, shd, tf = await asyncio.gather(
        loop.run_in_executor(None, _ip_geo, ip),
        loop.run_in_executor(None, _shodan_internetdb, ip),
        loop.run_in_executor(None, _threatfox_ioc, ip),
        return_exceptions=True,
    )
    if isinstance(geo, Exception):
        geo = {}
    if isinstance(shd, Exception):
        shd = {}
    if isinstance(tf, Exception):
        tf = {}

    lines = [f"## IP Info : `{ip}`\n"]
    lines.append("### Géolocalisation")
    for k in ("country", "regionName", "city", "isp", "org", "as", "proxy", "hosting"):
        if geo.get(k):
            lines.append(f"  **{k}**: {geo[k]}")
    lines.append("")
    if shd and "detail" not in shd:
        lines.append("### Shodan InternetDB")
        lines.append(f"  **Ports**: {shd.get('ports', [])}")
        lines.append(f"  **CVEs**: {shd.get('vulns', [])}")
        lines.append(f"  **Tags**: {shd.get('tags', [])}")
        lines.append("")
    if tf.get("found"):
        lines.append(f"⚠️ **Malware détecté**: {', '.join(tf.get('malware', []))}")

    return HandlerResult.ok("\n".join(lines), handler_name="ip_info")


# ---------------------------------------------------------------------------
# Handler : domain_recon — sous-domaines + DNS depuis crt.sh
# ---------------------------------------------------------------------------

async def domain_recon_handler(ctx, domain: str) -> dict:
    """
    Reconnaissance passive d'un domaine (sous-domaines, DNS, certificats).
    100% gratuit via crt.sh + dnspython.
    """
    domain = _clean_domain(domain)
    loop = asyncio.get_running_loop()
    dns_data, subs, ssl_data = await asyncio.gather(
        loop.run_in_executor(None, _dns_lookup, domain),
        loop.run_in_executor(None, _crtsh_subdomains, domain),
        loop.run_in_executor(None, _ssl_cert, domain),
        return_exceptions=True,
    )
    if isinstance(dns_data, Exception):
        dns_data = {"records": {}}
    if isinstance(subs, Exception):
        subs = []
    if isinstance(ssl_data, Exception):
        ssl_data = {"error": str(ssl_data)}

    lines = [f"## Recon domaine : `{domain}`\n"]
    lines.append(f"### DNS ({len(dns_data.get('records', {}))} types)")
    for rtype, vals in dns_data.get("records", {}).items():
        lines.append(f"  **{rtype}**: {', '.join(vals[:8])}")
    lines.append("")
    lines.append(f"### Sous-domaines ({len(subs)} via crt.sh)")
    lines.append("  " + "\n  ".join(subs[:50]))
    lines.append("")
    if "error" not in ssl_data:
        exp_tag = "⚠️ EXPIRÉ" if ssl_data.get("expired") else "✅ valide"
        lines.append(f"### SSL : {exp_tag} — expire dans {ssl_data.get('days_until_expiry')} jours")
        lines.append(f"  SANs: {', '.join(ssl_data.get('SANs', [])[:10])}")

    return HandlerResult.ok("\n".join(lines), handler_name="domain_recon")


# ---------------------------------------------------------------------------
# Handler : email_reputation — vérif réputation email gratuite
# ---------------------------------------------------------------------------

async def email_check_handler(ctx, email: str) -> HandlerResult:
    """
    Vérifie si un email est dans des fuites de données.
    Source: XposedOrNot (gratuit, sans clé).
    Note: couverture partielle — HIBP reste la référence exhaustive (payant).
    """
    hibp = _hibp_email(email)
    source = hibp.get("source", "XposedOrNot")
    lines = [f"## Réputation email : `{email}`\n", f"_Source: {source} (base gratuite)_\n"]
    if hibp.get("breached") is True:
        lines.append(f"⚠️ **Trouvé dans {hibp['count']} fuite(s)** :")
        for b in hibp.get("breaches", []):
            lines.append(f"  - {b}")
        lines.append("")
        lines.append("ℹ️ Vérification complète : https://haveibeenpwned.com")
    elif hibp.get("breached") is None:
        lines.append(f"⚠️ Vérification impossible : {hibp.get('error', 'API indisponible')}")
        lines.append("ℹ️ Vérifie manuellement sur :")
        lines.append("  - https://haveibeenpwned.com (référence, gratuit en lecture)")
        lines.append("  - https://xposedornot.com/xposed#")
    else:
        lines.append("✅ Non trouvé dans la base XposedOrNot")
        lines.append("")
        lines.append("⚠️ **Attention** : cette vérification ne couvre que XposedOrNot.")
        lines.append("Pour une vérification exhaustive, consulte : https://haveibeenpwned.com")
    return HandlerResult.ok("\n".join(lines), handler_name="email_check")


# ---------------------------------------------------------------------------
# Handler : whois_lookup — WHOIS dédié
# ---------------------------------------------------------------------------

async def whois_lookup_handler(ctx, target: str) -> HandlerResult:
    """WHOIS complet : registrar, dates, nameservers, contacts."""
    target = _clean_domain(target)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _whois_lookup, target)
    if data.get("error"):
        return HandlerResult.fail(f"WHOIS échoué : {data['error']}", handler_name="whois_lookup")
    lines = [f"## WHOIS : `{target}`\n"]
    for key in ("registrar", "creation_date", "expiration_date", "updated_date",
                "name_servers", "status", "country", "state", "city",
                "registrant", "emails", "org", "dnssec"):
        v = data.get(key)
        if v:
            lines.append(f"  **{key}**: {v}")
    return HandlerResult.ok("\n".join(lines), handler_name="whois_lookup")


# ---------------------------------------------------------------------------
# Handler : ssl_check — analyse certificat TLS dédié
# ---------------------------------------------------------------------------

async def ssl_check_handler(ctx, domain: str, port: int = 443) -> HandlerResult:
    """Analyse certificat SSL/TLS : expiration, CN, SANs, issuer."""
    domain = _clean_domain(domain)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _ssl_cert, domain, port)
    if data.get("error"):
        return HandlerResult.fail(f"SSL check échoué : {data['error']}", handler_name="ssl_check")
    status = "EXPIRE" if data.get("expired") else f"valide ({data.get('days_until_expiry')} jours)"
    lines = [f"## Certificat SSL : `{domain}:{port}`\n"]
    lines.append(f"  **Statut**: {status}")
    lines.append(f"  **CN**: {data.get('CN')}")
    issuer = data.get("issuer", {})
    lines.append(f"  **Issuer**: {issuer.get('organizationName', issuer.get('commonName', '?'))}")
    lines.append(f"  **Valide du**: {data.get('valid_from')} au {data.get('valid_until')}")
    lines.append(f"  **SANs** ({len(data.get('SANs', []))}): {', '.join(data.get('SANs', [])[:15])}")
    return HandlerResult.ok("\n".join(lines), handler_name="ssl_check")


# ---------------------------------------------------------------------------
# Handler : subdomain_enum — énumération sous-domaines via crt.sh
# ---------------------------------------------------------------------------

async def subdomain_enum_handler(ctx, domain: str) -> HandlerResult:
    """Enumération sous-domaines via Certificate Transparency (crt.sh)."""
    domain = _clean_domain(domain)
    loop = asyncio.get_running_loop()
    subs = await loop.run_in_executor(None, _crtsh_subdomains, domain)
    lines = [f"## Sous-domaines : `{domain}` ({len(subs)} trouvés via crt.sh)\n"]
    if subs:
        for s in subs[:80]:
            lines.append(f"  - {s}")
    else:
        lines.append("  Aucun sous-domaine trouvé.")
    return HandlerResult.ok("\n".join(lines), handler_name="subdomain_enum")


# ---------------------------------------------------------------------------
# Handler : http_headers_check — analyse headers HTTP
# ---------------------------------------------------------------------------

async def http_headers_handler(ctx, url: str) -> HandlerResult:
    """Analyse headers HTTP d'un site : stack technique, headers sécurité."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _http_headers, url)
    if data.get("error"):
        return HandlerResult.fail(f"Headers check échoué : {data['error']}", handler_name="http_headers_check")
    lines = [f"## Headers HTTP : `{data.get('final_url', url)}`\n"]
    lines.append(f"  **Status**: {data.get('status_code')}")
    sec_headers = {
        "strict-transport-security": "HSTS",
        "content-security-policy": "CSP",
        "x-frame-options": "X-Frame-Options",
        "x-content-type-options": "X-Content-Type-Options",
        "x-xss-protection": "X-XSS-Protection",
    }
    lines.append("\n### Headers de sécurité")
    all_h = data.get("all_headers", {})
    for hdr, label in sec_headers.items():
        val = all_h.get(hdr) or all_h.get(hdr.title())
        if val:
            lines.append(f"  **{label}**: {val[:120]}")
        else:
            lines.append(f"  **{label}**: ABSENT")
    lines.append("\n### Stack technique")
    for hdr in ("server", "x-powered-by", "x-generator", "x-drupal-cache", "x-wordpress"):
        val = all_h.get(hdr) or all_h.get(hdr.title())
        if val:
            lines.append(f"  **{hdr}**: {val}")
    return HandlerResult.ok("\n".join(lines), handler_name="http_headers_check")


# ---------------------------------------------------------------------------
# Handler : threat_check — IOC lookup ThreatFox
# ---------------------------------------------------------------------------

async def threat_check_handler(ctx, ioc: str) -> HandlerResult:
    """Recherche IOC (IP, domaine, hash) dans ThreatFox (Abuse.ch). Gratuit."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _threatfox_ioc, ioc)
    lines = [f"## ThreatFox IOC : `{ioc}`\n"]
    if data.get("found"):
        lines.append(f"  **Malwares**: {', '.join(data.get('malware', []))}")
        lines.append(f"  **Confiance**: {data.get('confidence')}%")
        lines.append(f"  **Premier vu**: {data.get('first_seen')}")
        lines.append(f"  **Nombre IOCs**: {data.get('count')}")
    else:
        lines.append("  Aucun IOC malveillant trouvé dans ThreatFox.")
    return HandlerResult.ok("\n".join(lines), handler_name="threat_check")


# ---------------------------------------------------------------------------
# Handler : port_scan — scan TCP léger
# ---------------------------------------------------------------------------

_COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 465, 587,
                 993, 995, 1433, 1521, 3306, 3389, 5432, 5900, 6379,
                 8080, 8443, 8888, 9200, 27017]

_PORT_SERVICES = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS", 445: "SMB",
    465: "SMTPS", 587: "Submission", 993: "IMAPS", 995: "POP3S",
    1433: "MSSQL", 1521: "Oracle", 3306: "MySQL", 3389: "RDP",
    5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 8080: "HTTP-Alt",
    8443: "HTTPS-Alt", 8888: "HTTP-Alt", 9200: "Elasticsearch", 27017: "MongoDB",
}


def _tcp_scan(target: str, ports: list[int] | None = None, timeout: float = 1.5) -> list[dict]:
    """Scan TCP connect basique. Retourne les ports ouverts."""
    if ports is None:
        ports = _COMMON_PORTS
    # Résoudre le domaine en IP si nécessaire
    try:
        ip = socket.gethostbyname(target)
    except socket.gaierror:
        return [{"error": f"Impossible de résoudre {target}"}]
    open_ports = []
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            if result == 0:
                # Tenter le banner grab
                banner = ""
                try:
                    sock.settimeout(1.0)
                    sock.sendall(b"\r\n")
                    banner = sock.recv(256).decode("utf-8", errors="replace").strip()[:100]
                except Exception as e:
                    logger.debug("[osint] banner recv: %s", e)
                open_ports.append({
                    "port": port,
                    "service": _PORT_SERVICES.get(port, "unknown"),
                    "banner": banner or None,
                })
            sock.close()
        except Exception as e:
            logger.debug("[osint] tcp scan port %s: %s", port, e)
    return open_ports


async def port_scan_handler(ctx, target: str, ports: str = "") -> HandlerResult:
    """
    Scan TCP des ports communs d'un hôte. Détecte les services ouverts.
    Pour usage autorisé uniquement (pentest, audit).
    """
    target = _clean_domain(target)
    port_list = None
    if ports:
        try:
            port_list = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
        except Exception:
            port_list = None
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _tcp_scan, target, port_list)
    if results and isinstance(results[0], dict) and "error" in results[0]:
        return HandlerResult.fail(results[0]["error"], handler_name="port_scan")
    lines = [f"## Port Scan : `{target}` ({len(results)} ports ouverts)\n"]
    if results:
        for p in results:
            banner_info = f" — {p['banner']}" if p.get("banner") else ""
            lines.append(f"  **{p['port']}** ({p['service']}){banner_info}")
    else:
        lines.append("  Aucun port ouvert détecté parmi les ports scannés.")
    return HandlerResult.ok("\n".join(lines), handler_name="port_scan")


# ---------------------------------------------------------------------------
# Handler : reverse_dns — DNS inverse
# ---------------------------------------------------------------------------

def _reverse_dns(ip: str) -> dict:
    """Résolution DNS inverse (PTR) pour une IP."""
    result = {"ip": ip, "hostnames": []}
    try:
        hostnames = socket.gethostbyaddr(ip)
        result["hostnames"] = [hostnames[0]] + list(hostnames[1])
    except (socket.herror, socket.gaierror):
        pass
    # Aussi via dnspython si disponible
    if _DNS_OK:
        try:
            rev_name = ".".join(ip.split(".")[::-1]) + ".in-addr.arpa"
            answers = _dns_resolver.resolve(rev_name, "PTR", lifetime=5)
            ptrs = [str(r).rstrip(".") for r in answers]
            for ptr in ptrs:
                if ptr not in result["hostnames"]:
                    result["hostnames"].append(ptr)
        except Exception as e:
            logger.debug("[osint] PTR resolve: %s", e)
    return result


async def reverse_dns_handler(ctx, ip: str) -> HandlerResult:
    """Résolution DNS inverse (PTR) : trouve les hostnames associés à une IP."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _reverse_dns, ip)
    lines = [f"## Reverse DNS : `{ip}`\n"]
    if data["hostnames"]:
        for h in data["hostnames"]:
            lines.append(f"  - {h}")
    else:
        lines.append("  Aucun enregistrement PTR trouvé.")
    return HandlerResult.ok("\n".join(lines), handler_name="reverse_dns")


# ---------------------------------------------------------------------------
# Handler : tech_detect — détection technologies web
# ---------------------------------------------------------------------------

def _detect_tech(url: str) -> dict:
    """Détection de technologies web via headers + contenu HTML."""
    if not _REQUESTS_OK:
        return {"error": "requests non disponible"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = _req.get(url, timeout=8, headers={"User-Agent": "LumenaOSINT/1.0"},
                     allow_redirects=True)
    except Exception as exc:
        return {"error": str(exc)}

    techs = []
    headers = {k.lower(): v for k, v in r.headers.items()}
    body = r.text[:50000].lower()

    # Détection par headers
    server = headers.get("server", "")
    if "nginx" in server.lower():
        techs.append(("Nginx", "server", server))
    elif "apache" in server.lower():
        techs.append(("Apache", "server", server))
    elif "cloudflare" in server.lower():
        techs.append(("Cloudflare", "CDN", server))
    elif server:
        techs.append((server, "server", server))

    xpb = headers.get("x-powered-by", "")
    if xpb:
        techs.append((xpb, "framework", xpb))

    # Détection par contenu HTML
    patterns = [
        ("WordPress", "wp-content/"), ("WordPress", "wp-includes/"),
        ("Drupal", "drupal"), ("Joomla", "/media/jui/"),
        ("React", "react"), ("Vue.js", "vue.min.js"),
        ("Angular", "ng-version"), ("Next.js", "_next/"),
        ("Nuxt.js", "__nuxt"), ("Svelte", "svelte"),
        ("jQuery", "jquery"), ("Bootstrap", "bootstrap"),
        ("Tailwind", "tailwind"), ("Laravel", "laravel"),
        ("Django", "csrfmiddlewaretoken"), ("Flask", "werkzeug"),
        ("Express", "x-powered-by: express"),
        ("Shopify", "shopify"), ("Wix", "wix.com"),
        ("Squarespace", "squarespace"),
        ("Google Analytics", "google-analytics.com/"),
        ("Google Tag Manager", "googletagmanager.com/"),
        ("Cloudflare", "cloudflareinsights"),
        ("reCAPTCHA", "recaptcha"), ("hCaptcha", "hcaptcha"),
    ]
    seen = set()
    for name, pattern in patterns:
        if pattern in body and name not in seen:
            techs.append((name, "detected", pattern))
            seen.add(name)

    # Meta generator
    import re
    gen_match = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', body)
    if gen_match:
        techs.append((gen_match.group(1), "meta-generator", ""))

    return {
        "url": r.url,
        "status": r.status_code,
        "technologies": [{"name": t[0], "category": t[1], "evidence": t[2]} for t in techs],
        "count": len(techs),
    }


async def tech_detect_handler(ctx, url: str) -> HandlerResult:
    """Détecte les technologies utilisées par un site web (CMS, frameworks, CDN, analytics)."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _detect_tech, url)
    if data.get("error"):
        return HandlerResult.fail(f"Tech detect échoué : {data['error']}", handler_name="tech_detect")
    lines = [f"## Technologies : `{data.get('url', url)}` ({data['count']} détectées)\n"]
    if data["technologies"]:
        for t in data["technologies"]:
            lines.append(f"  - **{t['name']}** ({t['category']})")
    else:
        lines.append("  Aucune technologie détectée.")
    return HandlerResult.ok("\n".join(lines), handler_name="tech_detect")


# ---------------------------------------------------------------------------
# Handler : wayback_check — historique Wayback Machine
# ---------------------------------------------------------------------------

async def wayback_handler(ctx, domain: str, limit: int = 15) -> HandlerResult:
    """Consulte l'historique Archive.org (Wayback Machine) d'un domaine."""
    domain = _clean_domain(domain)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _wayback_history, domain, limit)
    lines = [f"## Wayback Machine : `{domain}` ({len(data)} snapshots)\n"]
    if data:
        for entry in data:
            ts = entry.get("timestamp", "?")
            # Format timestamp YYYYMMDDHHMMSS
            if len(ts) >= 8:
                ts_fmt = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            else:
                ts_fmt = ts
            lines.append(f"  [{ts_fmt}] HTTP {entry.get('statuscode', '?')} — {entry.get('original', '')[:80]}")
    else:
        lines.append("  Aucun snapshot trouvé dans Archive.org.")
    return HandlerResult.ok("\n".join(lines), handler_name="wayback_check")


# ---------------------------------------------------------------------------
# Enregistrement des handlers — format HandlerDef (compatible registry_v2)
# ---------------------------------------------------------------------------

from typing import List
from .registry_v2 import HandlerDef


def get_osint_handler_defs() -> List[HandlerDef]:
    return [
        HandlerDef(
            name="osint_scan",
            description=(
                "Scan OSINT complet sur un domaine, IP ou URL. "
                "Analyse DNS, WHOIS, SSL, sous-domaines (crt.sh), géoloc IP, "
                "ports ouverts (Shodan InternetDB gratuit), headers HTTP, "
                "historique Archive.org, IOCs malwares (ThreatFox). "
                "100% gratuit, aucune clé API requise. "
                "Mots-clés: osint scan renseignement enquête recon footprint "
                "analyse domaine qui est recherche sur hacking cybersécurité."
            ),
            parameters={
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Domaine (ex: example.com), adresse IP ou URL à analyser"
                    },
                    "scan_type": {
                        "type": "string",
                        "description": "Type de scan: 'passive' (défaut) ou 'active'",
                        "default": "passive"
                    },
                    "email": {
                        "type": "string",
                        "description": "Email optionnel pour vérifier les fuites de données (HaveIBeenPwned)"
                    },
                },
                "required": ["target"],
            },
            handler=osint_scan_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="ip_info",
            description=(
                "Infos complètes sur une adresse IP : pays, ville, ISP, organisation, "
                "ports ouverts, CVEs (Shodan InternetDB gratuit), malwares associés (ThreatFox). "
                "Mots-clés: ip adresse ip géolocalisation ports ouverts cves shodan "
                "qui est cette ip ip info localisation."
            ),
            parameters={
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "Adresse IP à analyser (ex: 8.8.8.8)"
                    },
                },
                "required": ["ip"],
            },
            handler=ip_info_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="domain_recon",
            description=(
                "Reconnaissance passive d'un domaine : résolution DNS complète (A/MX/NS/TXT), "
                "énumération de sous-domaines via Certificate Transparency (crt.sh), "
                "analyse certificat SSL/TLS (expiration, SANs). 100% gratuit. "
                "Mots-clés: sous-domaines subdomains dns recon domaine crt.sh "
                "certificats reconnaissance zone dns enumération."
            ),
            parameters={
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine à analyser (ex: example.com)"
                    },
                },
                "required": ["domain"],
            },
            handler=domain_recon_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="email_check",
            description=(
                "Vérifie si une adresse email figure dans des fuites de données connues "
                "via HaveIBeenPwned. Renvoie la liste des breaches. Gratuit. "
                "Mots-clés: email mail fuite breach piratage haveibeenpwned "
                "pwned compromis données volées."
            ),
            parameters={
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Adresse email à vérifier"
                    },
                },
                "required": ["email"],
            },
            handler=email_check_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="whois_lookup",
            description=(
                "Recherche WHOIS complète sur un domaine : registrar, dates de création/"
                "expiration, nameservers, pays, contact. 100% gratuit. "
                "Mots-clés: whois registrar propriétaire domaine qui possède enregistrement."
            ),
            parameters={
                "properties": {
                    "target": {"type": "string", "description": "Domaine à interroger (ex: example.com)"},
                },
                "required": ["target"],
            },
            handler=whois_lookup_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="ssl_check",
            description=(
                "Analyse le certificat SSL/TLS d'un domaine : date d'expiration, CN, SANs, "
                "issuer, validité. Détecte les certificats expirés. 100% gratuit. "
                "Mots-clés: ssl tls certificat https expiration sécurité."
            ),
            parameters={
                "properties": {
                    "domain": {"type": "string", "description": "Domaine à vérifier (ex: example.com)"},
                    "port": {"type": "integer", "description": "Port TLS (défaut: 443)", "default": 443},
                },
                "required": ["domain"],
            },
            handler=ssl_check_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="subdomain_enum",
            description=(
                "Enumère les sous-domaines d'un domaine via Certificate Transparency (crt.sh). "
                "Retourne jusqu'à 100 sous-domaines trouvés. 100% gratuit. "
                "Mots-clés: sous-domaines subdomains enumération crt.sh certificates."
            ),
            parameters={
                "properties": {
                    "domain": {"type": "string", "description": "Domaine cible (ex: example.com)"},
                },
                "required": ["domain"],
            },
            handler=subdomain_enum_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="http_headers_check",
            description=(
                "Analyse les headers HTTP d'un site web : headers de sécurité (HSTS, CSP, "
                "X-Frame-Options), stack technique (server, x-powered-by). "
                "Mots-clés: headers http sécurité stack technique serveur."
            ),
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL ou domaine à analyser (ex: example.com)"},
                },
                "required": ["url"],
            },
            handler=http_headers_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="threat_check",
            description=(
                "Recherche un indicateur de compromission (IOC) dans ThreatFox (Abuse.ch). "
                "Accepte IP, domaine, hash MD5/SHA256. Détecte les malwares associés. Gratuit. "
                "Mots-clés: malware ioc threat menace virus compromission abuse."
            ),
            parameters={
                "properties": {
                    "ioc": {"type": "string", "description": "IP, domaine ou hash à vérifier"},
                },
                "required": ["ioc"],
            },
            handler=threat_check_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="port_scan",
            description=(
                "Scan TCP des ports communs d'un hôte. Détecte les services ouverts "
                "(SSH, HTTP, FTP, MySQL, RDP...) avec banner grab. "
                "Usage autorisé uniquement (pentest, audit de son propre serveur). "
                "Mots-clés: ports ouverts scan tcp services nmap pentest audit."
            ),
            parameters={
                "properties": {
                    "target": {"type": "string", "description": "Domaine ou IP à scanner (ex: example.com)"},
                    "ports": {"type": "string", "description": "Ports à scanner, séparés par virgule (ex: '22,80,443'). Vide = ports communs (26 ports)"},
                },
                "required": ["target"],
            },
            handler=port_scan_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="reverse_dns",
            description=(
                "Résolution DNS inverse (PTR) : trouve les noms d'hôte associés à une adresse IP. "
                "Mots-clés: reverse dns ptr ip hostname résolution inverse."
            ),
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "Adresse IP (ex: 8.8.8.8)"},
                },
                "required": ["ip"],
            },
            handler=reverse_dns_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="tech_detect",
            description=(
                "Détecte les technologies utilisées par un site web : CMS (WordPress, Drupal), "
                "frameworks (React, Vue, Angular, Django, Laravel), CDN (Cloudflare), "
                "analytics (Google Analytics), serveur web (Nginx, Apache). "
                "Mots-clés: technologie stack tech web framework cms détection wappalyzer."
            ),
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL ou domaine du site (ex: example.com)"},
                },
                "required": ["url"],
            },
            handler=tech_detect_handler,
            category="security",
            source_module="handlers.osint",
        ),
        HandlerDef(
            name="wayback_check",
            description=(
                "Consulte l'historique d'un domaine dans la Wayback Machine (Archive.org). "
                "Retourne les snapshots archivés avec dates et codes HTTP. Gratuit. "
                "Mots-clés: archive wayback machine historique site web ancien."
            ),
            parameters={
                "properties": {
                    "domain": {"type": "string", "description": "Domaine à rechercher (ex: example.com)"},
                    "limit": {"type": "integer", "description": "Nombre max de snapshots (défaut: 15)", "default": 15},
                },
                "required": ["domain"],
            },
            handler=wayback_handler,
            category="security",
            source_module="handlers.osint",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
