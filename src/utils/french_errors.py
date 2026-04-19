"""P11 — Messages d'erreur en français (CodeAgent).

Traduit les messages d'erreur techniques en français clair pour améliorer
l'UX. Gardé par flag LUMENA_FRENCH_ERRORS.
"""
from __future__ import annotations

import re

# (regex_pattern, french_replacement_template)
# Les groupes nommés peuvent être réutilisés via {name} dans le template.
_TRANSLATIONS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"No such file or directory: ['\"]?(?P<path>[^'\"]+)['\"]?"),
     "Fichier ou dossier introuvable : {path}"),
    (re.compile(r"\[Errno 2\] No such file or directory:?\s*['\"]?(?P<path>[^'\"]+)['\"]?"),
     "Fichier introuvable : {path}"),
    (re.compile(r"Permission denied:?\s*['\"]?(?P<path>[^'\"]+)['\"]?"),
     "Permission refusée : {path}"),
    (re.compile(r"\bFileNotFoundError\b:?\s*(?P<rest>.*)"),
     "Fichier introuvable : {rest}"),
    (re.compile(r"\bPermissionError\b:?\s*(?P<rest>.*)"),
     "Permission refusée : {rest}"),
    (re.compile(r"\bModuleNotFoundError\b:?\s*No module named ['\"](?P<mod>[^'\"]+)['\"]"),
     "Module Python introuvable : {mod} (installe-le ou vérifie l'import)"),
    (re.compile(r"\bSyntaxError\b:?\s*(?P<rest>.*)"),
     "Erreur de syntaxe : {rest}"),
    (re.compile(r"\bNameError\b:?\s*name ['\"](?P<n>[^'\"]+)['\"] is not defined"),
     "Variable/fonction non définie : {n}"),
    (re.compile(r"\bTimeoutError\b|timed out"),
     "Délai d'attente dépassé (timeout)"),
    (re.compile(r"\bConnectionError\b|\bConnectionRefusedError\b"),
     "Erreur de connexion réseau"),
    (re.compile(r"\bUnicodeDecodeError\b:?\s*(?P<rest>.*)"),
     "Erreur d'encodage (probablement fichier non-UTF-8) : {rest}"),
)


def translate_error(message: str) -> str:
    """Traduit un message d'erreur en français si flag actif. Sinon no-op."""
    if not isinstance(message, str) or not message:
        return message
    try:
        from src.config.codeagent_flags import FRENCH_ERRORS
        if not FRENCH_ERRORS:
            return message
    except Exception:
        return message

    for pattern, template in _TRANSLATIONS:
        m = pattern.search(message)
        if m:
            try:
                fr = template.format(**m.groupdict())
                # Remplace le segment détecté par sa traduction, ou préfixe
                return message[: m.start()] + fr + message[m.end():]
            except Exception:
                continue
    return message


__all__ = ["translate_error"]
