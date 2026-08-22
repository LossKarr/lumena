"""Deterministic, mode-aware routing for Document Studio requests."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Iterable, Literal, Mapping

from .builtin_templates import BUILTIN_ALIASES, BUILTIN_LABELS


DOCUMENT_KINDS: tuple[str, ...] = tuple(BUILTIN_LABELS)

# Tools that can bypass a structured model by generating or hand-writing the
# deliverable. They remain available for free-form documents and reopen after
# a real Studio attempt; the set is shared by prompt filtering and execution.
STUDIO_BYPASS_TOOLS: frozenset[str] = frozenset({
    "create_pdf",
    "create_invoice_pdf",
    "create_from_template",
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "create_csv",
    "create_html",
    "create_markdown",
    "write_file",
    "run_command",
    "run_python",
    "execute_python",
    "delegate_task",
    "delegate_task_bg",
    "fanout_tasks",
})

DOCUMENT_OPERATION_TOOLS: dict[str, frozenset[str]] = {
    "search_library": frozenset({
        "search_document_library", "get_document_record", "get_document_history",
        "export_library_document",
    }),
    "search_web": frozenset({
        "search_documents_web", "inspect_document_source", "download_document",
    }),
    "download": frozenset({"inspect_document_source", "download_document"}),
    "import": frozenset({"import_document", "get_document_record"}),
    "convert": frozenset({
        "search_document_library", "get_document_record", "convert_library_document",
        "export_library_document",
    }),
    "export": frozenset({
        "search_document_library", "get_document_record", "export_library_document",
    }),
    "history": frozenset({
        "search_document_library", "get_document_record", "get_document_history",
    }),
}

_KIND_ALIASES = {
    "activity_report": "rapport_activite",
    "certificate": "attestation",
    "confidentiality_agreement": "nda",
    "contract": "contrat_prestation",
    "estimate": "devis",
    "invoice": "facture",
    "internal_memo": "note_interne",
    "job_description": "fiche_poste",
    "minutes": "proces_verbal",
    "official_letter": "lettre_officielle",
    "order_form": "bon_commande",
    "payment_reminder": "relance_impaye",
    "payslip": "bulletin_paie",
    "purchase_order": "bon_commande",
    "quotation": "devis",
    "quote": "devis",
    "service_contract": "contrat_prestation",
}

_CREATE_SIGNAL = re.compile(
    r"\b(?:cree|creer|fais|fait|faire|fairt|fai|genere|generer|gzenere|produis|"
    r"prepare|redige|ecris|etablis|realise|make|create|draft|write)\b(?:\s+moi)?"
)
_CONTINUATION_SIGNAL = re.compile(
    r"^(?:(?:ok|okay|d accord)\b.*\b(?:maintenant|mainenant|ensuite|puis|a present)\b|"
    r"(?:et\s+)?(?:maintenant|mainenant|ensuite|puis|a present)\b)|"
    r"\b(?:maintenant|mainenant)\s*$"
)
_REVISION_SIGNAL = re.compile(
    r"\b(?:modifie|modifier|corrige|corriger|change|changer|remplace|remplacer|"
    r"ajuste|ajuster|mets? a jour|mettre a jour|revise|reviser|update|edit)\b"
)
_INFORMATION_SIGNAL = re.compile(
    r"^(?:c est quoi|qu est ce(?: que)?|a quoi sert|explique(?: moi)?|"
    r"peux tu m expliquer|quelle? est la difference)\b|"
    r"\b(?:definition|signifie|veut dire)\b"
)
_IMPLICIT_REQUEST_SIGNAL = re.compile(
    r"\b(?:j ?(?:ai|aurai|aurais) besoin (?:d(?:e)?|du|de la|des)|"
    r"je (?:voudrais|veux|souhaite)(?: un| une| du| de la| des)?|"
    r"il me faut|(?:peux|pourrais) tu (?:me )?(?:faire|preparer|rediger|generer))\b"
)

_WEB_SEARCH_SIGNAL = re.compile(
    r"\b(?:cherche|chercher|recherche|rechercher|trouve|trouver)\b.*"
    r"\b(?:internet|web|en ligne|online)\b"
)
_LIBRARY_SEARCH_SIGNAL = re.compile(
    r"\b(?:cherche|chercher|recherche|rechercher|retrouve|retrouver|trouve|trouver)\b.*"
    r"\b(?:mes|mon|ma|bibliotheque|documents?|fichiers?|archives?)\b"
)
_DOWNLOAD_SIGNAL = re.compile(r"\b(?:telecharge|telecharger|download)\b")
_IMPORT_SIGNAL = re.compile(r"\b(?:importe|importer|ajoute|ajouter)\b.*\b(?:document|fichier|pdf|docx|xlsx|pptx)\b")
_CONVERT_SIGNAL = re.compile(r"\b(?:convertis|convertir|transforme|transformer)\b.*\b(?:pdf|docx|xlsx|pptx|csv|html|document|fichier)\b")
_EXPORT_SIGNAL = re.compile(r"\b(?:exporte|exporter)\b")
_HISTORY_SIGNAL = re.compile(r"\b(?:historique|versions?|transformations?|provenance)\b")
_GENERIC_DOCUMENT_SIGNAL = re.compile(
    r"\b(?:document|modele|template|pdf|docx|xlsx|pptx|csv|odt|ods|odp|"
    r"word|excel|powerpoint)\b"
)

DocumentOperation = Literal[
    "create",
    "revise",
    "search_library",
    "search_web",
    "download",
    "import",
    "convert",
    "export",
    "history",
    "inform",
    "none",
]

DocumentWorkflowOperation = Literal[
    "generate", "open", "revise", "verify", "history", "export",
    "library_verify", "deliver",
]


@dataclass(frozen=True)
class DocumentRequestItem:
    """One ordered structured document requested inside a larger message."""

    index: int
    kind: str
    operation: DocumentOperation
    source_text: str
    confidence: float = 0.0
    matched_alias: str = ""


@dataclass(frozen=True)
class DocumentModelSelection:
    """Bounded catalog selection requested without naming document kinds."""

    origin: str = ""
    limit: int = 0
    sort: str = ""
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.limit > 0


@dataclass(frozen=True)
class DocumentWorkflowAction:
    """One ordered post-generation action in a compound document request."""

    operation: DocumentWorkflowOperation
    target_ordinal: int = 0
    output_format: str = ""
    source_text: str = ""


@dataclass(frozen=True)
class DocumentRoute:
    """Immutable routing decision for one or several document requests."""

    kind: str | None
    operation: DocumentOperation
    ui_mode: str
    requires_studio: bool
    legacy_fallback_allowed: bool
    reason: str
    # True only when Document Studio owns the whole user turn. Background
    # missions keep the same requested kinds and Studio tools, but compose them
    # with code, web, MCP and any other capability instead of becoming a
    # document-only workflow.
    owns_run: bool = False
    requires_document_tools: bool = False
    confidence: float = 0.0
    matched_alias: str = ""
    ambiguous_kinds: tuple[str, ...] = ()
    items: tuple[DocumentRequestItem, ...] = ()
    selection_origin: str = ""
    selection_limit: int = 0
    selection_sort: str = ""
    selections: tuple[DocumentModelSelection, ...] = ()
    workflow_actions: tuple[DocumentWorkflowAction, ...] = ()
    minimum_pages: int = 0

    @property
    def actionable(self) -> bool:
        return self.operation not in {"inform", "none"}

    @property
    def needs_model_selection(self) -> bool:
        return bool(self.ambiguous_kinds) and self.kind is None

    @property
    def requested_kinds(self) -> tuple[str, ...]:
        if self.items:
            return tuple(item.kind for item in self.items)
        return (self.kind,) if self.kind else ()

    @property
    def requested_count(self) -> int:
        if self.selections:
            return sum(selection.limit for selection in self.selections)
        return self.selection_limit or len(self.requested_kinds)

    @property
    def is_catalog_selection(self) -> bool:
        return bool(self.selections) or self.selection_limit > 0

    @property
    def has_pending_post_actions(self) -> bool:
        return any(
            action.operation in {
                "open", "revise", "verify", "history", "export",
                "library_verify",
            }
            for action in self.workflow_actions
        )


@dataclass(frozen=True)
class _KindMatch:
    kind: str | None
    confidence: float
    alias: str
    ambiguous_kinds: tuple[str, ...] = ()


_COUNT_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
    "dix": 10, "onze": 11, "douze": 12, "treize": 13,
    "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20,
    "trente": 30,
}
_COUNT_TOKEN = r"(?:\d{1,2}|" + "|".join(_COUNT_WORDS) + r")"


def _bounded_count(value: str) -> int:
    token = str(value or "").strip().lower()
    try:
        count = int(token)
    except ValueError:
        count = _COUNT_WORDS.get(token, 0)
    return count if 1 <= count <= 30 else 0


def _explicit_minimum_pages(normalized: str) -> int:
    """Return an explicit minimum page count, never an inferred one."""
    patterns = (
        r"\bau\s+moins\s+(\d{1,3})\s+pages?\b",
        r"\bminimum(?:\s+de)?\s+(\d{1,3})\s+pages?\b",
        r"\b(\d{1,3})\s+pages?\s+minimum\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = int(match.group(1))
            return value if 1 <= value <= 500 else 0
    return 0


def document_model_selections(query: str) -> tuple[DocumentModelSelection, ...]:
    """Resolve every bounded catalog selection in textual order."""
    normalized = normalize_document_query(query)
    matches: list[tuple[int, DocumentModelSelection]] = []
    for custom in re.finditer(
        rf"\b(?:mes\s+)?(?P<count>{_COUNT_TOKEN})\s+"
        r"(?:(?:derniers?|recent(?:e)?s?)\s+)?modeles?\s+personnalises?\b",
        normalized,
    ):
        count = _bounded_count(custom.group("count"))
        if count:
            matches.append((custom.start(), DocumentModelSelection(
                origin="custom",
                limit=count,
                sort="recent",
                reason="custom_model_selection",
            )))

    for structured in re.finditer(
        rf"\b(?P<count>{_COUNT_TOKEN})\s+documents?\s+"
        r"(?:(?:studio|document studio)\s+)?structures?\b",
        normalized,
    ):
        count = _bounded_count(structured.group("count"))
        if count:
            matches.append((structured.start(), DocumentModelSelection(
                origin="builtin",
                limit=count,
                sort="name",
                reason="catalog_count_selection",
            )))

    for builtin in re.finditer(
        rf"\b(?P<count>{_COUNT_TOKEN})\s+modeles?\s+"
        r"(?:integres?|natifs?|builtin|de\s+base)\b",
        normalized,
    ):
        count = _bounded_count(builtin.group("count"))
        if count:
            matches.append((builtin.start(), DocumentModelSelection(
                origin="builtin",
                limit=count,
                sort="name",
                reason="catalog_count_selection",
            )))
    return tuple(selection for _index, selection in sorted(matches, key=lambda row: row[0]))


def document_model_selection(query: str) -> DocumentModelSelection:
    """Compatibility view returning the first explicit catalog selection."""
    selections = document_model_selections(query)
    return selections[0] if selections else DocumentModelSelection()


_ORDINALS = {
    "premier": 1, "premiere": 1, "1er": 1, "1ere": 1,
    "deuxieme": 2, "second": 2, "seconde": 2, "2e": 2, "2eme": 2,
    "troisieme": 3, "3e": 3, "3eme": 3,
    "quatrieme": 4, "4e": 4, "4eme": 4,
    "cinquieme": 5, "5e": 5, "5eme": 5,
    "sixieme": 6, "6e": 6, "6eme": 6,
    "septieme": 7, "7e": 7, "7eme": 7,
    "huitieme": 8, "8e": 8, "8eme": 8,
    "neuvieme": 9, "9e": 9, "9eme": 9,
    "dixieme": 10, "10e": 10, "10eme": 10,
    "onzieme": 11, "11e": 11, "11eme": 11,
    "douzieme": 12, "12e": 12, "12eme": 12,
    "treizieme": 13, "13e": 13, "13eme": 13,
}


def _workflow_target_ordinal(normalized: str) -> int:
    for token, ordinal in _ORDINALS.items():
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", normalized):
            return ordinal
    match = re.search(r"\b(?:document|fichier|modele)\s*(?:n(?:o|umero)?\s*)?(\d{1,2})\b", normalized)
    if not match:
        match = re.search(
            r"\b(\d{1,2})(?:e|eme)?\s+(?:document|fichier|modele)\b",
            normalized,
        )
    return int(match.group(1)) if match else 0


def _bind_named_workflow_target(
    actions: tuple[DocumentWorkflowAction, ...],
    normalized: str,
    generation_kinds: tuple[str, ...],
    vocabulary: Mapping[str, Iterable[str]] | None,
) -> tuple[DocumentWorkflowAction, ...]:
    """Resolve one named revision target inside the generated batch."""
    revision_match = _REVISION_SIGNAL.search(normalized)
    if revision_match is None or not generation_kinds:
        return actions
    if len(generation_kinds) == 1:
        # With one freshly generated document, ordinals inside the requested
        # patch ("deuxieme decision", "troisieme ligne") describe its content,
        # never a second document in the manifest.
        return tuple(
            DocumentWorkflowAction(
                action.operation,
                target_ordinal=1 if action.operation == "revise" else action.target_ordinal,
                output_format=action.output_format,
                source_text=action.source_text,
            )
            for action in actions
        )
    named = tuple(dict.fromkeys(
        kind
        for kind in document_kinds_mentioned(
            normalized[revision_match.start():], vocabulary,
        )
        if kind in generation_kinds
    ))
    if not named:
        # Natural compound requests often name the exact target immediately
        # before the revision, then use an anaphor afterwards:
        # "utilise le document_id du devis... revise uniquement son numero".
        # Bind only when the revision clause itself contains that anaphor, and
        # choose the nearest generated kind before it. This avoids guessing for
        # a bare, underspecified "puis revise".
        revision_tail = normalized[revision_match.start():].split()[:8]
        if any(
            token in {
                "ce", "cet", "cette", "celui", "celle", "son", "sa", "ses",
            }
            for token in revision_tail
        ):
            vocab = document_vocabulary(vocabulary)
            nearest: tuple[int, str] | None = None
            prefix = normalized[:revision_match.start()]
            for kind in generation_kinds:
                for alias in vocab.get(kind, ()):
                    normalized_alias = normalize_document_query(alias)
                    if not normalized_alias:
                        continue
                    for match in re.finditer(
                        rf"(?<!\w){re.escape(normalized_alias)}(?!\w)", prefix,
                    ):
                        candidate = (match.start(), kind)
                        if nearest is None or candidate[0] > nearest[0]:
                            nearest = candidate
            if nearest is not None:
                named = (nearest[1],)
    if len(named) != 1:
        return actions
    ordinal = generation_kinds.index(named[0]) + 1
    return tuple(
        DocumentWorkflowAction(
            action.operation,
            target_ordinal=(
                ordinal
                if action.operation == "revise" and not action.target_ordinal
                else action.target_ordinal
            ),
            output_format=action.output_format,
            source_text=action.source_text,
        )
        for action in actions
    )


def document_workflow_actions(
    query: str, *, has_selections: bool = False,
) -> tuple[DocumentWorkflowAction, ...]:
    """Return ordered workflow actions without collapsing compound requests."""
    # File basenames are data, not workflow verbs. Without this mask,
    # `controle_vega.txt` becomes `controle vega txt` after normalization and
    # fabricates a document `verify` action. Explicit prose still survives:
    # `verifie controle_vega.txt` keeps the verb `verifie` outside the mask.
    workflow_query = re.sub(
        r"(?i)(?<!\w)[\w.-]+\.(?:pdf|docx|xlsx|pptx|csv|txt|md|json|html?|py|js|css)(?!\w)",
        " fichier ",
        str(query or ""),
    )
    normalized = normalize_document_query(workflow_query)
    found: list[tuple[int, DocumentWorkflowAction]] = []
    create_match = _CREATE_SIGNAL.search(normalized)
    if has_selections or create_match:
        found.append((
            create_match.start() if create_match else 0,
            DocumentWorkflowAction("generate", source_text=query),
        ))
    library_verify_matches = tuple(re.finditer(
        r"\b(?:verifie|verifier|controle|controler|valide|valider)\b"
        r"[^.!?;]{0,180}\b(?:bibliotheque|library)\b",
        normalized,
    ))
    for match in library_verify_matches:
        found.append((match.start(), DocumentWorkflowAction(
            "library_verify", source_text=query,
        )))

    patterns = (
        ("open", r"\b(?:ouvre|ouvrir|affiche|afficher)\b"),
        ("revise", _REVISION_SIGNAL.pattern),
        (
            "verify",
            r"\b(?:verifie|verifier|controle|controler|valide|valider|"
            r"relis|relire|relecture)\b",
        ),
        (
            "history",
            r"\b(?:historique|provenance|transformations?|parent\s*(?:/|et)\s*enfant)\b",
        ),
        ("export", _EXPORT_SIGNAL.pattern),
        ("deliver", r"\b(?:recu|bilan|rapport final|fournis|livre)\b"),
    )
    for operation, pattern in patterns:
        matches = tuple(re.finditer(pattern, normalized))
        if operation == "verify" and library_verify_matches:
            matches = tuple(
                match for match in matches
                if not any(
                    library.start() <= match.start() < library.end()
                    for library in library_verify_matches
                )
            )
        match = matches[-1] if operation == "deliver" and matches else (
            matches[0] if matches else None
        )
        if match:
            output_format = ""
            if operation == "export":
                tail = normalized[match.end():match.end() + 120]
                format_match = re.search(
                    r"\b(pdf|html|docx|xlsx|pptx|csv|md)\b", tail,
                )
                output_format = format_match.group(1) if format_match else ""
            found.append((match.start(), DocumentWorkflowAction(
                operation,
                target_ordinal=(
                    _workflow_target_ordinal(
                        normalized[max(0, match.start() - 120):]
                    )
                    if operation == "revise" else 0
                ),
                output_format=output_format,
                source_text=query,
            )))
    ordered: list[DocumentWorkflowAction] = []
    seen: set[str] = set()
    for _index, action in sorted(found, key=lambda row: row[0]):
        if action.operation not in seen:
            ordered.append(action)
            seen.add(action.operation)
    return tuple(ordered)


def normalize_document_query(value: str) -> str:
    """Return stable lowercase ASCII words for deterministic matching."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("’", " ").replace("'", " ").replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_MISSION_ENVELOPE_RE = re.compile(
    r"\[mode\s+mission\].*?\n\s*mission\s*:\s*\n",
    re.IGNORECASE | re.DOTALL,
)
_CODE_CONTRACT_CONTEXT_RE = re.compile(
    r"write[\s_-]*mission[\s_-]*contract|contract\.json|contrat\.md|"
    r"\bcontrat\s+machine\b|"
    r"\bstubs?\b|\ballowed[\s_-]*files\b|\bcodeagent\b|"
    r"\bworkers?\b.*\b(?:html|css|javascript|python|code)\b|"
    r"\b(?:html|css|javascript|python|code)\b.*\bworkers?\b",
    re.IGNORECASE | re.DOTALL,
)
_CODE_CONTRACT_TERMS_RE = re.compile(
    r"write[\s_-]*mission[\s_-]*contract|"
    r"\bcontrat\s+machine\b|"
    r"\bcontrat\s+de\s+mission\b|"
    r"\bcontrat\.md\b|"
    r"\bcontract\.json\b",
    re.IGNORECASE,
)

_BUSINESS_CONTRACT_AFTER_RE = re.compile(
    r"^\s+(?:de\s+(?:prestation|service|travail)|freelance|employe)\b",
    re.IGNORECASE,
)
_CODE_CONTRACT_AFTER_RE = re.compile(
    r"^\s+(?:doit|devra|declare|declarera|contient|listera|liste|porte|impose|avec)\b",
    re.IGNORECASE,
)
_CODE_PROTOCOL_NEAR_RE = re.compile(
    r"stubs?|workers?|allowed[\s_-]*files|signatures?\s+(?:d\s+)?api|"
    r"write[\s_-]*mission[\s_-]*contract|contract\.json|contrat\.md",
    re.IGNORECASE,
)


def _mask_generic_code_contract_mentions(text: str) -> str:
    """Mask generic ``contrat`` only when it denotes the code protocol.

    A genuine business phrase such as ``contrat de prestation`` remains
    visible even in a mixed mission that also mentions stubs and workers.
    """
    raw = str(text or "")

    def _replace(match: re.Match[str]) -> str:
        if _BUSINESS_CONTRACT_AFTER_RE.match(raw[match.end():match.end() + 40]):
            return match.group(0)
        if _CODE_CONTRACT_AFTER_RE.match(raw[match.end():match.end() + 40]):
            return " specification technique "
        window = raw[max(0, match.start() - 100):min(len(raw), match.end() + 100)]
        if _CODE_PROTOCOL_NEAR_RE.search(window):
            return " specification technique "
        return match.group(0)

    return re.sub(r"\bcontrat\b", _replace, raw, flags=re.IGNORECASE)


def _document_routing_query(value: str) -> str:
    """Remove internal code-orchestration prose from document classification.

    Mission leads and code workers receive internal prompts containing words such
    as ``mission`` and ``contrat``. Those words describe the orchestration
    protocol, not business documents. Only the classifier sees this sanitized
    copy; the model prompt remains untouched.
    """
    text = str(value or "")
    envelope = _MISSION_ENVELOPE_RE.search(text)
    if envelope:
        text = text[envelope.end():]

    if _CODE_CONTRACT_CONTEXT_RE.search(text):
        text = _CODE_CONTRACT_TERMS_RE.sub(" specification technique ", text)
        text = _mask_generic_code_contract_mentions(text)
    return text


def normalize_document_kind(value: str) -> str:
    """Canonicalize a Studio kind while preserving unknown custom kinds."""
    normalized = re.sub(r"[\s-]+", "_", normalize_document_query(value)).strip("_")
    mapped = _KIND_ALIASES.get(normalized)
    if mapped:
        return mapped
    normalized_words = normalized.replace("_", " ")
    for kind, aliases in BUILTIN_ALIASES.items():
        label = BUILTIN_LABELS.get(kind, (kind, "general"))[0]
        candidates = (kind.replace("_", " "), label, *aliases)
        if any(normalize_document_query(candidate) == normalized_words for candidate in candidates):
            return kind
    return normalized


def might_be_custom_document_request(query: str) -> bool:
    """Cheap signal used before consulting the cached custom-model vocabulary."""
    normalized = normalize_document_query(query)
    if _INFORMATION_SIGNAL.search(normalized):
        return False
    return bool(
        _CREATE_SIGNAL.search(normalized)
        or _IMPLICIT_REQUEST_SIGNAL.search(normalized)
        or _CONTINUATION_SIGNAL.search(normalized)
    )


def document_vocabulary(
    extra: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Build the canonical vocabulary, optionally enriched by custom models."""
    merged: dict[str, list[str]] = {}
    for kind, aliases in BUILTIN_ALIASES.items():
        label = BUILTIN_LABELS.get(kind, (kind, "general"))[0]
        merged[kind] = [kind.replace("_", " "), label, *aliases]
    for raw_kind, aliases in (extra or {}).items():
        kind = normalize_document_kind(raw_kind)
        values = merged.setdefault(kind, [kind.replace("_", " ")])
        values.extend(str(alias) for alias in aliases if str(alias).strip())
    return {
        kind: tuple(dict.fromkeys(normalize_document_query(value) for value in values if value))
        for kind, values in merged.items()
    }


_NEGATED_DOCUMENT_BRIDGE_WORDS = frozenset({
    "a", "avoir", "avec", "d", "de", "des", "du", "en", "fournir",
    "justificatif", "justificatifs", "joindre", "la", "le", "les",
    "montrer", "presenter", "preuve", "preuves", "un", "une", "utiliser",
})

_FUZZY_GENERIC_DOCUMENT_WORDS = frozenset({
    "a", "de", "des", "du", "document", "documents", "fiche", "fichier",
    "mission", "modele", "pdf", "un", "une",
})


def _fuzzy_alias_keeps_business_meaning(
    candidate_tokens: Iterable[str], alias_tokens: Iterable[str],
) -> bool:
    """Reject matches supported only by generic orchestration words."""
    candidate = tuple(str(token or "") for token in candidate_tokens)
    discriminating = tuple(
        token for token in alias_tokens
        if token not in _FUZZY_GENERIC_DOCUMENT_WORDS
    )
    if not discriminating:
        return True
    return any(
        SequenceMatcher(None, source, expected).ratio() >= 0.78
        for source in candidate
        for expected in discriminating
    )


def _document_mention_is_negated(prefix_tokens: Iterable[str]) -> bool:
    """True when the immediately preceding phrase negates the document mention.

    This is intentionally local. It rejects ``sans fiche de paie`` and
    ``je ne veux pas de bulletin`` without treating a later field constraint
    such as ``fiche de paie sans logo`` as a negation of the document itself.
    """
    tail = tuple(str(token or "") for token in prefix_tokens)[-8:]
    if not tail:
        return False

    for marker in ("sans", "aucun", "aucune", "ni"):
        if marker not in tail:
            continue
        index = len(tail) - 1 - tail[::-1].index(marker)
        between = tail[index + 1:]
        if not between or all(token in _NEGATED_DOCUMENT_BRIDGE_WORDS for token in between):
            return True

    if "pas" in tail:
        index = len(tail) - 1 - tail[::-1].index("pas")
        between = tail[index + 1:]
        if (
            between
            and between[0] in {"d", "de", "des", "du"}
            and all(token in _NEGATED_DOCUMENT_BRIDGE_WORDS for token in between)
        ):
            return True
    return False


def _fuzzy_kind_matches(
    normalized_query: str,
    vocabulary: Mapping[str, Iterable[str]],
) -> list[tuple[float, int, int, str, str]]:
    """Return non-negated fuzzy aliases ordered later by the caller."""
    query_tokens = normalized_query.split()
    fuzzy: list[tuple[float, int, int, str, str]] = []
    for kind, aliases in vocabulary.items():
        for alias in aliases:
            normalized_alias = normalize_document_query(alias)
            alias_tokens = normalized_alias.split()
            if not alias_tokens or (len(alias_tokens) == 1 and len(normalized_alias) < 7):
                continue
            width = len(alias_tokens)
            for start in range(max(0, len(query_tokens) - width + 1)):
                if _document_mention_is_negated(query_tokens[:start]):
                    continue
                candidate = " ".join(query_tokens[start:start + width])
                score = SequenceMatcher(None, candidate, normalized_alias).ratio()
                threshold = 0.90 if width == 1 else 0.86
                if score >= threshold and _fuzzy_alias_keeps_business_meaning(
                    query_tokens[start:start + width], alias_tokens,
                ):
                    fuzzy.append((
                        score, len(alias_tokens), len(normalized_alias), kind, normalized_alias,
                    ))
    return fuzzy


_AVOIR_NOUN_LEFT_CONTEXT = frozenset({
    "un", "l", "cet", "mon", "ton", "son", "notre", "votre", "leur",
    "cree", "creer", "genere", "generer", "produis", "prepare", "redige",
    "document", "modele", "pdf",
})
_AVOIR_NOUN_RIGHT_CONTEXT = frozenset({
    "client", "commercial", "pdf", "numero", "pour", "sur", "de",
})


def _generic_alias_is_document_noun(
    normalized_query: str,
    start: int,
    end: int,
    kind: str,
    alias: str,
) -> bool:
    """Reject catalog aliases that are ordinary prose in the current context.

    ``avoir`` is both a finance document and one of the most common French
    verbs. Treating every occurrence as a credit note made code requirements
    such as ``les fichiers doivent avoir le meme owner`` request an extra PDF.
    Specific aliases (``note de credit``) are unambiguous and remain unchanged.
    """
    if normalize_document_kind(kind) != "avoir" or alias != "avoir":
        return True
    left = normalized_query[:start].split()
    right = normalized_query[end:].split()
    previous = left[-1] if left else ""
    following = right[0] if right else ""
    return previous in _AVOIR_NOUN_LEFT_CONTEXT or following in _AVOIR_NOUN_RIGHT_CONTEXT


def _resolve_kind(
    normalized_query: str,
    vocabulary: Mapping[str, Iterable[str]] | None = None,
) -> _KindMatch:
    # A professional payment-reminder request necessarily contains "facture".
    # Resolve the specific intent before the generic one-word invoice alias.
    #
    # LOT O1 (run HuffPack v2, 2026-08-14) — la condition ne testait JAMAIS
    # « facture », contrairement à ce que la ligne ci-dessus affirme. Deux mots
    # suffisaient donc, et ils sont ordinaires : « **relance** pytest toi-même »
    # + « Mission avec **échéance** 90 minutes » ont été classés relance d'impayé
    # avec une confiance de 1.0. Le rail Document Studio activé a ensuite REFUSÉ
    # `run_command` trois fois de suite : pour exécuter la moindre commande, il
    # fallait d'abord produire une lettre de recouvrement. Douze itérations de
    # lecture stérile, puis la tentation de contourner par PowerShell.
    #
    # « impayé » reste un signal AUTONOME (il ne veut rien dire d'autre).
    # « paiement » et « échéance » sont polysémiques : on exige alors un vrai
    # contexte de créance — c'est-à-dire ce que le commentaire promettait déjà.
    # Mesuré sur les cas réels : 3 vraies relances conservées, 2 faux positifs
    # techniques éliminés.
    if re.search(r"\brelance\b", normalized_query) and (
        # Sans ambiguïté : ces mots n'existent que dans un contexte de créance.
        re.search(
            r"\b(?:impaye(?:e|s|es)?|creance\w*|debiteur\w*|recouvrement\w*)\b",
            normalized_query,
        )
        # Polysémiques : « paiement » et « échéance » servent partout (échéance
        # d'une mission, d'un certificat…) → il faut un vrai objet de créance.
        or (
            re.search(r"\b(?:paiement|echeance)\b", normalized_query)
            and re.search(
                r"\b(?:factur\w*|montant\w*|reglement\w*|solde\w*)\b",
                normalized_query,
            )
        )
    ):
        return _KindMatch("relance_impaye", 1.0, "relance facture impayee")
    vocab = document_vocabulary(vocabulary)
    exact: list[tuple[int, int, str, str]] = []
    for kind, aliases in vocab.items():
        for alias in aliases:
            normalized_alias = normalize_document_query(alias)
            if not normalized_alias:
                continue
            pattern = rf"(?<!\w){re.escape(normalized_alias)}(?!\w)"
            if any(
                not _document_mention_is_negated(
                    normalized_query[:match.start()].split()
                )
                and _generic_alias_is_document_noun(
                    normalized_query, match.start(), match.end(), kind, normalized_alias,
                )
                for match in re.finditer(pattern, normalized_query)
            ):
                exact.append((
                    len(normalized_alias.split()), len(normalized_alias), kind, normalized_alias,
                ))

    fuzzy = _fuzzy_kind_matches(normalized_query, vocab)
    if exact:
        exact.sort(reverse=True)
        specificity = exact[0][:2]
        finalists = sorted({kind for words, chars, kind, _ in exact if (words, chars) == specificity})
        if len(finalists) > 1:
            return _KindMatch(None, 1.0, exact[0][3], tuple(finalists))
        winner = next(item for item in exact if item[2] == finalists[0])

        # A generic one-word alias (notably "contrat") must not mask a unique,
        # high-confidence specific phrase containing a bounded typo such as
        # "contrat de travaille". Exact multi-word aliases still win normally.
        if winner[0] == 1 and fuzzy:
            specific = [item for item in fuzzy if item[1] > 1]
            if specific:
                specific.sort(reverse=True)
                top_score = specific[0][0]
                specific_kinds = sorted({
                    kind for score, _words, _chars, kind, _alias in specific
                    if top_score - score < 0.04
                })
                if len(specific_kinds) == 1:
                    fuzzy_winner = next(
                        item for item in specific if item[3] == specific_kinds[0]
                    )
                    return _KindMatch(
                        fuzzy_winner[3], fuzzy_winner[0], fuzzy_winner[4],
                    )
        return _KindMatch(winner[2], 1.0, winner[3])

    if not fuzzy:
        return _KindMatch(None, 0.0, "")
    fuzzy.sort(reverse=True)
    top_score = fuzzy[0][0]
    finalists = sorted({
        kind for score, _words, _chars, kind, _alias in fuzzy
        if top_score - score < 0.04
    })
    if len(finalists) > 1:
        return _KindMatch(None, top_score, fuzzy[0][4], tuple(finalists))
    winner = next(item for item in fuzzy if item[3] == finalists[0])
    return _KindMatch(winner[3], winner[0], winner[4])


def _resolve_operation(normalized: str, *, mode: str, has_document_signal: bool) -> tuple[DocumentOperation, str]:
    if _INFORMATION_SIGNAL.search(normalized):
        return "inform", "information_request"
    if _WEB_SEARCH_SIGNAL.search(normalized):
        return "search_web", "web_document_search"
    if _DOWNLOAD_SIGNAL.search(normalized):
        return "download", "document_download"
    if _CONVERT_SIGNAL.search(normalized):
        return "convert", "document_conversion"
    if _EXPORT_SIGNAL.search(normalized):
        return "export", "document_export"
    if _HISTORY_SIGNAL.search(normalized) and has_document_signal:
        return "history", "document_history"
    if _LIBRARY_SEARCH_SIGNAL.search(normalized):
        return "search_library", "library_document_search"
    if _IMPORT_SIGNAL.search(normalized):
        return "import", "document_import"
    if _REVISION_SIGNAL.search(normalized):
        return "revise", "explicit_revision"
    if _CREATE_SIGNAL.search(normalized):
        return "create", "explicit_creation"
    if _CONTINUATION_SIGNAL.search(normalized):
        return "create", "creation_continuation"
    if mode == "agent" and _IMPLICIT_REQUEST_SIGNAL.search(normalized):
        return "create", "implicit_agent_request"
    return "inform", "document_mention"


def _request_fragments(query: str) -> tuple[str, ...]:
    """Split explicit lists without changing ordinary single-sentence routing."""
    raw = str(query or "").strip()
    if not raw:
        return ()
    lines = [part.strip(" \t-*\u2022") for part in re.split(r"[\r\n]+", raw)]
    lines = [line for line in lines if line]
    if len(lines) > 1:
        return tuple(lines)
    return (raw,)


def _resolve_request_items(
    query: str,
    *,
    mode: str,
    vocabulary: Mapping[str, Iterable[str]] | None,
    default_operation: DocumentOperation,
) -> tuple[DocumentRequestItem, ...]:
    if default_operation not in {"create", "revise"}:
        return ()
    items: list[DocumentRequestItem] = []
    for fragment in _request_fragments(query):
        normalized = normalize_document_query(fragment)
        match = _resolve_kind(normalized, vocabulary)
        if not match.kind:
            continue
        operation, _ = _resolve_operation(normalized, mode=mode, has_document_signal=True)
        if operation not in {"create", "revise"}:
            operation = default_operation
        items.append(DocumentRequestItem(
            index=len(items) + 1,
            kind=match.kind,
            operation=operation,
            source_text=fragment,
            confidence=match.confidence,
            matched_alias=match.alias,
        ))
    return tuple(items)


def resolve_document_route(
    query: str,
    mode: str = "chat",
    vocabulary: Mapping[str, Iterable[str]] | None = None,
) -> DocumentRoute:
    """Resolve a catalog-driven document decision for the whole request."""
    routing_query = _document_routing_query(query)
    normalized = normalize_document_query(routing_query)
    resolved_mode = "agent" if str(mode or "").strip().lower() == "agent" else "chat"
    match = _resolve_kind(normalized, vocabulary)
    selections = document_model_selections(routing_query)
    mentioned_kinds = document_kinds_mentioned(routing_query, vocabulary)
    if (
        selections
        and mentioned_kinds
        and sum(selection.limit for selection in selections) == len(mentioned_kinds)
    ):
        # "Six modeles integres : devis, facture, ..." describes six explicit
        # kinds; it is not a request for the first six rows of the catalog.
        # Genuine unnamed selections ("mes six premiers modeles integres")
        # retain the historical catalog-selection workflow.
        selections = ()
    generic_signal = bool(_GENERIC_DOCUMENT_SIGNAL.search(normalized))
    has_document_signal = bool(match.kind or match.ambiguous_kinds or generic_signal or selections)
    if not has_document_signal:
        return DocumentRoute(
            kind=None,
            operation="none",
            ui_mode=resolved_mode,
            requires_studio=False,
            legacy_fallback_allowed=False,
            reason="no_document_signal",
            owns_run=False,
        )

    operation, reason = _resolve_operation(
        normalized,
        mode=resolved_mode,
        has_document_signal=has_document_signal,
    )
    selection = selections[0] if selections else DocumentModelSelection()
    workflow_actions = document_workflow_actions(
        routing_query, has_selections=bool(selections),
    )
    # A compound request starts with its first mutating workflow action. This
    # covers both catalog selections and ordinary requests such as
    # "genere un devis, ouvre-le, puis modifie-le". Pure revisions keep their
    # historical route.
    mutating_actions = tuple(
        action.operation for action in workflow_actions
        if action.operation in {"generate", "revise"}
    )
    if mutating_actions and mutating_actions[0] == "generate" and operation != "create":
        operation = "create"
        reason = "compound_document_workflow"
    if selection.active:
        reason = selection.reason
    items = _resolve_request_items(
        routing_query,
        mode=resolved_mode,
        vocabulary=vocabulary,
        default_operation=operation,
    )
    # Comma-separated one-line requests must retain every explicit model in
    # textual order, just like the historical newline-separated form. Detail
    # sentences may repeat only a subset of the requested models; those
    # fragments must not shrink a homogeneous create/revise batch.
    generation_kinds = mentioned_kinds
    if mutating_actions and mutating_actions[0] == "generate":
        # A later revision may name the same document again, or even a
        # different existing document. Only kinds named before the first
        # revision belong to the initial generation batch.
        revision_match = _REVISION_SIGNAL.search(normalized)
        if revision_match:
            scoped_kinds = document_kinds_mentioned(
                normalized[:revision_match.start()], vocabulary,
            )
            if scoped_kinds:
                generation_kinds = scoped_kinds
    workflow_actions = _bind_named_workflow_target(
        workflow_actions,
        normalized,
        generation_kinds,
        vocabulary,
    )
    parsed_create_kinds = tuple(dict.fromkeys(
        item.kind for item in items if item.operation == "create"
    ))
    if (
        len(generation_kinds) > 1
        and len(generation_kinds) > len(parsed_create_kinds)
    ):
        generated_items = [
            DocumentRequestItem(
                index=index,
                kind=kind,
                operation="create",
                source_text=routing_query,
                confidence=1.0,
                matched_alias="",
            )
            for index, kind in enumerate(generation_kinds, start=1)
        ]
        # Preserve a genuine revision of an external existing document. A
        # revision of one of the freshly generated kinds remains represented
        # by workflow_actions and must not duplicate the initial batch.
        external_revisions = [
            item for item in items
            if item.operation == "revise" and item.kind not in generation_kinds
        ]
        items = tuple(generated_items + [
            DocumentRequestItem(
                index=len(generated_items) + offset,
                kind=item.kind,
                operation="revise",
                source_text=item.source_text,
                confidence=item.confidence,
                matched_alias=item.matched_alias,
            )
            for offset, item in enumerate(external_revisions, start=1)
        ])
    if items:
        match = _KindMatch(
            items[0].kind,
            items[0].confidence,
            items[0].matched_alias,
        )
    actionable = operation not in {"inform", "none"}
    requires_document_tools = actionable and resolved_mode == "agent"
    requires_studio = (
        requires_document_tools
        and operation in {"create", "revise"}
        and bool(match.kind or match.ambiguous_kinds or selection.active)
    )
    return DocumentRoute(
        kind=match.kind,
        operation=operation,
        ui_mode=resolved_mode,
        requires_studio=requires_studio,
        legacy_fallback_allowed=False,
        reason=reason if match.kind else ("ambiguous_document_kind" if match.ambiguous_kinds else reason),
        owns_run=requires_studio,
        requires_document_tools=requires_document_tools,
        confidence=match.confidence,
        matched_alias=match.alias,
        ambiguous_kinds=match.ambiguous_kinds,
        items=items,
        selection_origin=selection.origin,
        selection_limit=selection.limit,
        selection_sort=selection.sort,
        selections=selections,
        workflow_actions=workflow_actions,
        minimum_pages=_explicit_minimum_pages(normalized),
    )


def structured_document_kind(query: str) -> str | None:
    """Compatibility helper for actionable Studio creation requests."""
    route = resolve_document_route(query, mode="agent")
    return route.kind if route.operation == "create" and route.requires_studio else None


def document_kinds_mentioned(
    query: str,
    vocabulary: Mapping[str, Iterable[str]] | None = None,
) -> tuple[str, ...]:
    """Return every non-overlapping catalog kind explicitly named in text."""
    normalized = normalize_document_query(_document_routing_query(query))
    vocab = document_vocabulary(vocabulary)
    matches: list[tuple[int, int, int, str]] = []
    for kind, aliases in vocab.items():
        for alias in aliases:
            normalized_alias = normalize_document_query(alias)
            if not normalized_alias:
                continue
            pattern = rf"(?<!\w){re.escape(normalized_alias)}(?!\w)"
            for match in re.finditer(pattern, normalized):
                if _document_mention_is_negated(
                    normalized[:match.start()].split()
                ):
                    continue
                if not _generic_alias_is_document_noun(
                    normalized, match.start(), match.end(), kind, normalized_alias,
                ):
                    continue
                matches.append((
                    match.start(), match.end(), len(normalized_alias), normalize_document_kind(kind),
                ))
    # Prefer the longest alias on overlapping spans, then restore textual order.
    selected: list[tuple[int, int, str]] = []
    for start, end, _length, kind in sorted(matches, key=lambda item: (-item[2], item[0])):
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected):
            continue
        selected.append((start, end, kind))
    ordered: list[str] = []
    for _start, _end, kind in sorted(selected):
        if kind and kind not in ordered:
            ordered.append(kind)

    # ``contrat`` is the historical generic alias for a service contract.
    # Do not manufacture that second document from an output filename such as
    # ``contrat-helios.pdf`` when the request already names a specific work
    # contract. A genuine service-contract request remains present through one
    # of its specific multi-word aliases.
    if "contrat_travail" in ordered and "contrat_prestation" in ordered:
        specific_service_aliases = tuple(
            normalize_document_query(alias)
            for alias in vocab.get("contrat_prestation", ())
            if len(normalize_document_query(alias).split()) > 1
        )
        has_specific_service_contract = any(
            any(
                not _document_mention_is_negated(
                    normalized[:match.start()].split()
                )
                for match in re.finditer(
                    rf"(?<!\w){re.escape(alias)}(?!\w)", normalized,
                )
            )
            for alias in specific_service_aliases
        )
        if not has_specific_service_contract:
            ordered.remove("contrat_prestation")
    return tuple(ordered)


def document_action_kind(query: str) -> str | None:
    """Compatibility helper for Studio creation or revision visibility."""
    route = resolve_document_route(query, mode="agent")
    return route.kind if route.operation in {"create", "revise"} else None


__all__ = [
    "DOCUMENT_KINDS",
    "DocumentRequestItem",
    "DocumentModelSelection",
    "DocumentWorkflowAction",
    "DocumentRoute",
    "DOCUMENT_OPERATION_TOOLS",
    "document_kinds_mentioned",
    "document_model_selection",
    "document_model_selections",
    "document_workflow_actions",
    "STUDIO_BYPASS_TOOLS",
    "document_action_kind",
    "document_vocabulary",
    "might_be_custom_document_request",
    "normalize_document_kind",
    "normalize_document_query",
    "resolve_document_route",
    "structured_document_kind",
]
