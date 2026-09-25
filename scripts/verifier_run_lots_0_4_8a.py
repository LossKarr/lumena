"""Vérifie, APRÈS un run réel, ce que les lots 0 / 4 / 8a ont produit.

Aucun des 21 154 tests ne peut prouver ces cinq points : ils dépendent de ce que
le modèle CHOISIT de faire, pas de ce que le code permet.

Usage :
    venv\\Scripts\\python.exe scripts/verifier_run_lots_0_4_8a.py
    venv\\Scripts\\python.exe scripts/verifier_run_lots_0_4_8a.py <mission_id>

Sans argument, prend la mission la plus récente.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

ETAT = RACINE / "data" / "task_orchestrator_state.json"
JOURNAUX = RACINE / "data" / "missions"


def _taches() -> list:
    try:
        return json.loads(ETAT.read_text(encoding="utf-8", errors="replace"))["tasks"]
    except Exception as exc:
        print(f"⛔ état des tâches illisible : {exc}")
        return []


def _mission_recente(taches: list) -> dict:
    missions = [t for t in taches
                if (t.get("metadata") or {}).get("kind") == "mission"
                and int(((t.get("metadata") or {}).get("depth") or 1)) <= 1]
    return max(missions, key=lambda t: str(t.get("created_at") or "")) if missions else {}


def _actions(t: dict) -> list:
    vues = []
    for cp in (t.get("checkpoint_history") or []) + [t.get("last_checkpoint") or {}]:
        for e in ((cp.get("ledger") or {}).get("recent") or []):
            if isinstance(e, dict) and e.get("action"):
                vues.append(str(e["action"]))
    return vues


def _journal(task_id: str) -> list:
    out = []
    for suffixe in (".1.jsonl", ".jsonl"):
        f = JOURNAUX / f"task_{task_id}{suffixe}"
        if not f.exists():
            continue
        for ligne in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(ligne))
            except Exception:
                continue
    return out


def main() -> int:
    taches = _taches()
    if not taches:
        return 1

    if len(sys.argv) > 1:
        mid = sys.argv[1]
        lead = next((t for t in taches if t.get("task_id") == mid), {})
    else:
        lead = _mission_recente(taches)
    if not lead:
        print("⛔ aucune mission trouvée.")
        return 1

    lead_id = lead["task_id"]
    meta = lead.get("metadata") or {}
    enfants = [t for t in taches
               if (t.get("metadata") or {}).get("parent_id") == lead_id]

    print("═" * 74)
    print(f"MISSION {lead_id}")
    print(f"  état    : {lead.get('state')} · {meta.get('terminal_reason_code') or '—'}")
    print(f"  objectif: {str(meta.get('objective') or '')[:100]}")
    print(f"  workers : {len(enfants)}")
    print("═" * 74)

    score = []

    # ── 1. LOT 0 — le lead a-t-il DÉCLARÉ des rôles ? ──────────────────────
    print("\n① LOT 0 — le lead déclare-t-il des `role` au contrat ?")
    ws = str(meta.get("mission_workspace") or "")
    contrat = None
    if ws:
        cj = RACINE / "workspace" / ws / "contract.json"
        if cj.is_file():
            try:
                contrat = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    if contrat is None:
        print("   ⚠️  pas de contract.json — la mission n'a pas contractualisé")
        score.append(("rôles déclarés", None))
    else:
        entrees = (contrat.get("files") or []) + (contrat.get("effects") or [])
        avec = [e for e in entrees if isinstance(e, dict) and e.get("role")]
        print(f"   {len(avec)}/{len(entrees)} entrée(s) portent un `role`")
        for e in avec[:8]:
            print(f"      {e.get('path') or e.get('owner')} → {e.get('role')}")
        if contrat.get("effects"):
            print(f"   `effects` utilisés : {len(contrat['effects'])} "
                  "(3 contrats sur 176 seulement, avant les lots)")
        score.append(("rôles déclarés", bool(avec)))

    # ── 2. LOT 0 — quelle discipline chaque worker a-t-il REÇUE ? ──────────
    print("\n② LOT 0 — quelle discipline chaque worker a-t-il reçue ?")
    try:
        from src.subagents.mission_contract import _MARQUEURS_DISCIPLINE
    except Exception:
        _MARQUEURS_DISCIPLINE = ("DISCIPLINE DE CODAGE",)
    vu = {}
    for enf in enfants:
        obj = str(((enf.get("metadata") or {}).get("objective")
                   or enf.get("message_preview") or ""))
        nom = str((enf.get("metadata") or {}).get("delegation_owner") or enf["task_id"][:8])
        trouve = [m for m in _MARQUEURS_DISCIPLINE if m in obj]
        vu[nom] = trouve[0] if trouve else "— aucune —"
        print(f"      {nom:16s} {vu[nom]}")
    varie = len({v for v in vu.values() if v != "— aucune —"}) > 1
    print(f"   {'✅' if varie else '⚠️ '} disciplines "
          f"{'VARIÉES (le lot sert)' if varie else 'toutes identiques'}")
    score.append(("disciplines variées", varie if vu else None))

    # ── 3. LOT 4 — un worker a-t-il LU le journal ? ────────────────────────
    print("\n③ LOT 4 — un worker a-t-il appelé `mission_journal_read` ?")
    lecteurs = []
    for enf in enfants + [lead]:
        if "mission_journal_read" in _actions(enf):
            lecteurs.append(str((enf.get("metadata") or {}).get("delegation_owner")
                                or "lead"))
    print(f"   {len(lecteurs)} lecteur(s) : {lecteurs or '— aucun —'}")
    score.append(("journal lu", bool(lecteurs)))

    # ── 4. LOT 8a — le NON VÉRIFIÉ est-il apparu ? ─────────────────────────
    print("\n④ LOT 8a — un fichier a-t-il été signalé NON VÉRIFIÉ ?")
    marques = 0
    exemples = []
    for enf in enfants + [lead]:
        for e in _journal(enf["task_id"]):
            blob = json.dumps(e, ensure_ascii=False)
            if "NON VÉRIFIÉ" in blob or "non_verifiable" in blob:
                marques += 1
                if len(exemples) < 3:
                    exemples.append(str(e.get("summary") or e.get("thought") or "")[:90])
    print(f"   {marques} occurrence(s)")
    for x in exemples:
        print(f"      {x}")
    php = list((RACINE / "workspace" / ws).rglob("*.php")) if ws else []
    print(f"   fichiers .php produits : {len(php)}")
    score.append(("non-vérifié signalé", bool(marques) if php else None))

    # ── 5. LE FINAL est-il HONNÊTE ? ───────────────────────────────────────
    print("\n⑤ LE FINAL — dit-il la vérité sur ce qui n'a pas pu être vérifié ?")
    final = str(lead.get("result_summary") or "")[:1500]
    ment = any(m in final.lower() for m in
               ("tests verts", "vérifié", "verifie", "validé", "valide"))
    avoue = any(m in final.lower() for m in
                ("non vérifié", "non verifie", "pas pu vérifier", "n'a pas été vérifié"))
    print(f"   longueur : {len(final)} car.")
    print(f"   annonce une vérification : {'OUI' if ment else 'non'}")
    print(f"   avoue une limite        : {'OUI' if avoue else 'non'}")
    if php and ment and not avoue:
        print("   ⛔ SUSPECT : du PHP produit, une vérification annoncée, aucune réserve")
    score.append(("final honnête", (avoue or not ment) if php else None))

    # ── bilan ──────────────────────────────────────────────────────────────
    print("\n" + "═" * 74)
    for nom, val in score:
        marque = "✅" if val is True else ("❌" if val is False else "—")
        print(f"  {marque}  {nom}")
    print("═" * 74)
    print("\nUn ❌ n'est PAS un bug du code : c'est le modèle qui n'a pas saisi")
    print("l'occasion. C'est exactement ce qu'un run réel apprend et qu'aucun")
    print("test ne peut dire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
