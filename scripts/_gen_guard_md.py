# -*- coding: utf-8 -*-
import json, tempfile, collections
from pathlib import Path

merged = json.loads((Path(tempfile.gettempdir()) / "lumena_merged.json").read_text(encoding="utf-8"))
F = json.loads((Path(tempfile.gettempdir()) / "lumena_fam.json").read_text(encoding="utf-8"))
hc_of = json.loads((Path(tempfile.gettempdir()) / "lumena_hcof.json").read_text(encoding="utf-8"))
by = {r["name"]: r for r in merged}
mod = collections.defaultdict(list)
for r in merged:
    mod[r["module"] or "(runtime)"].append(r)

RO = "—"

# Familles d'action existantes dans react.py (pour la colonne "Déjà codé")
HC_EXISTING = {"FILE","DOC","SITE","TASK","MAIL","DISCORD","MESSAGING","SOCIAL","STRIPE",
               "GITHUB","IMAGE","NOTION","TYPE","OPEN_APP","CLICK","LOGIN"}

FAM_DOC = {
 "FILE": "Écriture/édition/suppression de fichiers locaux + archives.",
 "DOC": "Génération/édition de documents bureautiques (PDF, DOCX, XLSX, PPTX, CSV, charts).",
 "SITE": "Génération/édition de sites web & projets (delegate au CodeAgent inclus).",
 "TASK": "Planification (tâches/rappels), plans d'action, écriture mémoire/journal.",
 "MAIL": "Envoi / réponse d'e-mail (SMTP).",
 "MAIL_ADMIN": "Administration boîte mail (comptes IMAP, déplacement/suppression de messages).",
 "DISCORD": "Mutations Discord (envoi, salons, rôles, modération).",
 "MESSAGING": "Envoi de messages hors-Discord : Telegram, WhatsApp, SMS, appel vocal critique.",
 "SOCIAL": "Publication réseaux sociaux (Twitter/X : tweet, reply, like, thread).",
 "STRIPE": "Mutations Stripe (produits, prix, clients, factures, abonnements, remboursements).",
 "GITHUB": "Mutations Git/GitHub locales et distantes (commit, push, repo, issue, fichier).",
 "IMAGE": "Génération/édition d'images, logos, vignettes et vidéos (Remotion inclus).",
 "NOTION": "Mutations Notion (création/MAJ de pages, ajout en database).",
 "TYPE": "Saisie de texte clavier (Computer Use natif + champs navigateur).",
 "OPEN_APP": "Ouverture d'app / navigation (open_app, open_url, browser navigate/start/onglets).",
 "CLICK": "Clic / interaction pointeur (souris, UI Automation, clics navigateur, fermeture fenêtre).",
 "LOGIN": "Connexion/authentification (browser_login/verify + la frappe des identifiants).",
 "MEDIA": "Contrôle lecture média Spotify (play, pause, next, volume, queue…).",
 "EXEC": "Exécution de code/commandes & processus (run_command, process_*, tests, exec multi-lang, offensif).",
 "IDE": "Mutations de l'IDE Lumena (écriture éditeur, terminal, sidebar, fenêtre).",
 "DEPLOY": "Déploiement/transfert de fichiers distants IONOS (SFTP, sites).",
 "DB": "Écriture structurelle BDD IONOS via bridge (install bridge, sandbox).",
 "DB_PROPOSE": "Proposition d'écriture/suppression BDD IONOS (n'exécute rien, attend approbation).",
 "DB_CONFIG": "Bascule de flags de configuration BDD IONOS (kill-switches, allowlists).",
 "NETWORK": "Actions sur machines distantes (exec, transfert fichier, WOL, shutdown, self-deploy).",
 "N8N": "Mutations workflows n8n (create, update, activate, trigger, delete).",
 "SKILL": "Auto-modification : création de skill/outil custom, edit_own_code.",
 "HTTP": "Requêtes HTTP sortantes mutatives (POST/PUT/DELETE, upload, webhook, register).",
 "PEER": "Délégation/échange avec une autre instance Lumena (peer).",
 "CONFIG": "Modification de la configuration Lumena / heartbeat.",
 "MEMORY": "Ingestion de document dans la mémoire vectorielle.",
 "CU_TASK": "Tâche Computer Use autonome multi-étapes (prise de contrôle écran).",
 "BROWSER_TECH": "Mutations techniques navigateur (storage, cookies, émulation, dialogs, trace, batch).",
}

# Claim-patterns (familles visées) — extrait fidèle de _HALLUCINATION_CLAIM_PATTERNS
CLAIMS = [
 ("j'ai créé / planifié / enregistré / configuré / ajouté / sauvegardé", "ANY_CREATE"),
 ("j'ai envoyé / expédié", "ANY_SEND"),
 ("la tâche a été créée/planifiée/enregistrée", "TASK"),
 ("c'est fait / configuré / planifié / enregistré / créé", "ANY_CREATE  ⚠️ trop large"),
 ("j'ai tapé / saisi   •   texte … tapé   •   j'ai rempli le champ/formulaire", "TYPE"),
 ("j'ai ouvert / lancé / démarré <app : notepad, spotify, chrome…>", "OPEN_APP"),
 ("j'ai cliqué", "CLICK"),
 ("connexion réussie / login réussi / authentification réussie", "LOGIN"),
 ("discord … animé/géré/organisé  •  salon créé/supprimé", "DISCORD"),
 ("message/fichier/document envoyé/posté/publié", "MESSAGING|MAIL|DISCORD|SOCIAL"),
 ("push réussi / repo créé / poussé sur github / commit réussi", "GITHUB"),
 ("mail/email envoyé", "MAIL"),
 ("image/logo/vignette/svg/vidéo généré(e)/produit(e)/rendu(e)", "IMAGE"),
 ("produit/abonnement/facture/paiement/remboursement créé(e)/annulé(e)", "STRIPE"),
 ("page/base de données créée/ajoutée/mise à jour", "NOTION"),
]

lines = []
A = lines.append
A("# Référentiel des garde-fous anti-hallucination — outils natifs Lumena")
A("")
A("> **But.** Pour chaque outil **natif** (hors outils MCP dynamiques), on déclare sa *garde* :")
A("> soit **Lecture** (`—`, hors-garde : jamais bloquant, ne constitue pas une preuve d'action),")
A("> soit une **famille d'action**. Quand Lumena *prétend* avoir agi (FINAL), le guard exige")
A("> qu'au moins un outil **réussi** de la famille attendue ait tourné dans la session ; sinon →")
A("> retry forcé. Les outils MCP (`mcp__<serveur>__<Tool>`) sont **dynamiques** et restent couverts")
A("> par une règle générique (un outil MCP réussi = preuve plausible) — **on ne les liste jamais ici**.")
A("")
A(f"- **Total outils natifs enregistrés au runtime : {len(merged)}**")
nb_ro = sum(1 for v in F.values() if v == RO)
A(f"- Lecture / hors-garde : **{nb_ro}**  •  Action (sous garde) : **{len(merged)-nb_ro}**")
A("- Source autoritative : registre `ToolRegistry` chargé en live (`568 handlers` + `discover_tools`).")
A("")
A("## Légende du tableau")
A("")
A("| Colonne | Sens |")
A("|---|---|")
A("| **Type** | `Lecture` = read-only, jamais bloquant. `Action` = mutation, preuve requise. |")
A("| **Garde** | Famille sémantique de preuve (ou `—` si lecture). |")
A("| **HC** | `✓` = déjà présent dans une famille `_HC_TOOLS_*` de react.py ; `+` = **à ajouter** (trou). |")
A("")

# ── Glossaire des familles ──
A("## Familles de garde")
A("")
A("| Famille | Déjà codée | Définition |")
A("|---|:--:|---|")
order = ["FILE","DOC","SITE","TASK","MAIL","MAIL_ADMIN","DISCORD","MESSAGING","SOCIAL","STRIPE",
         "GITHUB","IMAGE","NOTION","MEDIA","TYPE","OPEN_APP","CLICK","LOGIN","CU_TASK",
         "EXEC","IDE","BROWSER_TECH","DEPLOY","DB","DB_PROPOSE","DB_CONFIG","NETWORK","N8N",
         "SKILL","HTTP","PEER","CONFIG","MEMORY"]
for fam in order:
    flag = "✓" if fam in HC_EXISTING else "**+**"
    A(f"| `{fam}` | {flag} | {FAM_DOC.get(fam,'')} |")
A("")

# ── Patterns de claim ──
A("## Phrases-claim couvertes (déclencheurs du guard)")
A("")
A("| Affirmation détectée dans le FINAL | Famille de preuve exigée |")
A("|---|---|")
for txt, fam in CLAIMS:
    A(f"| {txt} | `{fam}` |")
A("")

# ── Tables par module ──
A("## Inventaire complet par module")
A("")
for m in sorted(mod):
    rows = sorted(mod[m], key=lambda r: r["name"])
    nact = sum(1 for r in rows if F[r["name"]] != RO)
    A(f"### `{m}` — {len(rows)} outils ({nact} action, {len(rows)-nact} lecture)")
    A("")
    A("| Outil | Type | Garde | HC | Description |")
    A("|---|---|---|:--:|---|")
    for r in rows:
        n = r["name"]
        fam = F[n]
        typ = "Lecture" if fam == RO else "Action"
        if fam == RO:
            hc = ""
        elif fam in HC_EXISTING and n in hc_of:
            hc = "✓"
        else:
            hc = "**+**"
        desc = (r["desc"] or "").replace("|", "\\|").strip()
        A(f"| `{n}` | {typ} | `{fam}` | {hc} | {desc} |")
    A("")

# ── Trous / décisions ──
A("## Trous identifiés (à intégrer) & décisions ouvertes")
A("")
A("1. **Famille `MEDIA` inexistante** dans react.py → `spotify_play` (computer_use) et les 7 "
  "`spotify_*` (API) ne sont dans aucune famille. C'est la cause des **faux positifs** observés : "
  "`spotify_play` réussit, mais le FINAL « c'est fait » / « j'ai lancé Spotify » ne trouve aucune "
  "preuve dans `ANY_CREATE` / `OPEN_APP`. **Décision :** créer `MEDIA` et l'inclure dans le mapping "
  "des claims média + ajouter `spotify_play`, `spotify_api_play` à `OPEN_APP`.")
A("")
A("2. **Pattern `c'est fait …` → `ANY_CREATE` trop large.** Il matche n'importe quelle action "
  "(media, exec, login…) qui n'est pas une *création*. **Décision :** soit le restreindre, soit "
  "élargir sa famille de preuve à *toute action réussie* (`ANY_ACTION`).")
A("")
A("3. **LEDGER GUARD** (guard distinct, react.py:~6821) fait aussi un faux positif sur `spotify_play` "
  "car aucune mutation ledger. À aligner avec la même logique `ANY_ACTION`.")
A("")
A("4. **Familles d'action nouvelles à câbler** (colonne HC = `+`) : `MEDIA, EXEC, IDE, BROWSER_TECH, "
  "DEPLOY, DB, DB_PROPOSE, DB_CONFIG, NETWORK, N8N, SKILL, HTTP, PEER, CONFIG, MEMORY, MAIL_ADMIN, CU_TASK`. "
  "Beaucoup n'ont pas (encore) de phrase-claim dédiée : les lister sert surtout au **test anti-dérive** "
  "(échec CI si un outil enregistré n'est classé nulle part) et à élargir la preuve générique.")
A("")
A("5. **Anti-dérive (régression CI proposée).** Un test parcourt le registre live et vérifie que "
  "**chaque** outil natif est soit `Lecture`, soit rattaché à une famille — interdiction d'un outil "
  "non classé. Les noms `mcp__*` sont exclus (dynamiques).")
A("")

Path("docs").mkdir(exist_ok=True)
out = Path("docs/tool_guard_classification.md")
out.write_text("\n".join(lines), encoding="utf-8")
print("written", out, len(lines), "lignes")
