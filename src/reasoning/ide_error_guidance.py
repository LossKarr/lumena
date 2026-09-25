"""LOT CONN-7a — chaque refus IDE dit sa cause et le geste qui repare.

Exigence de CONN-7 : « diagnostic reparateur, jamais simple message mort ».

Audit du 23 septembre 2026 : **62 codes techniques remontaient tels quels** a
l'utilisateur, sous la forme `IDE: ide_mission_workspace_mismatch`. Aucune table de
traduction n'existait dans l'un ou l'autre depot.

Le code technique est **conserve** en tete : les journaux, les tests et le
diagnostic s'appuient dessus. Il est suivi de sa cause et du geste.

Deux regles de redaction, tenues par un test :

* une cause SANS geste reste un message mort - elle nomme le probleme sans issue ;
* un guide qui recopie le code n'explique rien.

Le gel d'exhaustivite (`test_conn7a_erreurs_ide_reparables`) lit les codes
reellement leves dans `src/reasoning/` : un code ajoute demain casse le test au lieu
d'atterrir brut chez l'utilisateur.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# code -> (ce qui s'est passe, ce qu'il faut faire)
GUIDES_ERREURS_IDE: Dict[str, Tuple[str, str]] = {
    # ── IDE-1 : les deux codes que le run reel du 24/09 a fait tomber ────────
    #
    # `ide_snapshot_stale` est tombe SEPT fois dans ce seul run, sans aucune
    # guidance — il ne figurait pas parmi les 64 codes couverts. Or ce n'est meme
    # pas une panne : c'est l'etat NORMAL apres un lancement reussi.
    #
    # Le mecanisme, mesure : `is_current()` compare la session ancree a celle du
    # snapshot. Un snapshot capture quand AUCUNE IDE n'etait connectee porte
    # `session is None`, et n'est donc « courant » que tant qu'aucune IDE ne l'est.
    # Des que `ide_launch` reussit, une session existe : le snapshot devient perime.
    # **Le succes invalide son propre snapshot.** C'est correct — le catalogue passe
    # de `launch_only` a 135 commandes — mais le mot « perime » a fait croire au
    # modele qu'il avait echoue, et il a tourne cinq iterations a l'aveugle.
    #
    # ── LOT IDE-8 (24/09, 20 h) : mon geste d'IDE-1 envoyait dans un MUR ────────
    #
    # La guidance disait « redemande l'etat (`ide__get_status`) ». Or `ide__get_status`
    # est LUI-MEME derriere le meme snapshot : il levait le meme refus. Le run l'a
    # prouve a la lettre. Lumena a lu le conseil et l'a compris parfaitement —
    # « le message est enfin explicite [...] la consigne est claire : ne pas relancer,
    # mais redemander l'etat pour reprendre une photo fraiche » — l'a applique, et a
    # recu `ide_snapshot_stale` une fois de plus. Puis : « j'ai fait exactement ce que
    # le message demandait et c'est ENCORE stale. C'est donc un mur systemique. »
    #
    # **28 refus sur 32 iterations.** Un conseil sans voie de sortie coute plus cher
    # qu'une absence de conseil : il fait insister sur la mauvaise porte.
    #
    # IDE-6 a ouvert la voie : le catalogue perime se reprend tout seul, une fois, au
    # point d'entree du rail. Le geste devient donc « refais ton action » — la reprise
    # a deja eu lieu. S'il retombe, c'est que l'IDE bouge vraiment sous nos pieds, et
    # la seule chose utile est de REGARDER (`lumena_ide(status)`, qui ne depend
    # d'aucun snapshot), pas de marteler la meme commande.
    "ide_snapshot_stale": (
        "la photo du catalogue IDE ne decrivait plus l'etat courant — le plus souvent "
        "parce qu'une IDE vient de se connecter, donc parce que ton lancement a REUSSI. "
        "Elle a ete reprise automatiquement",
        "ne relance pas le lancement : la photo a deja ete reprise automatiquement, "
        "refais simplement l'action que tu voulais faire. Si le meme "
        "refus revient, n'insiste sur aucune commande de l'IDE — elles dependent "
        "TOUTES de cette photo, y compris celles qui ne font que lire un etat. "
        "Regarde avec `lumena_ide(action=status)`, qui n'en depend pas, et dis "
        "honnetement ce que tu constates",
    ),
    "ide_launch_failed": (
        "le lanceur n'a pas obtenu de handshake dans le delai imparti",
        "verifie qu'aucune autre instance ne tient le verrou, puis redemande l'etat "
        "avant de relancer",
    ),

    # ── Perimetre et autorisation ────────────────────────────────────────────
    # LOT IDE-4 — le geste envoyait dans une IMPASSE.
    #
    # Mesure du 19 h 12 : Charles demande d'ouvrir les fichiers de `CaveAVin` dans
    # l'IDE. L'IDE est ouverte sur `calc_project`, donc il faut changer de workspace.
    # `ide__navigate` -> refuse (modifie l'hote). `ide__open_file` -> refuse (hors
    # workspace actif). Lumena a tourne sur ces deux refus jusqu'a renoncer, avec
    # pour seul conseil « demande-la depuis une mission, ou fais-la toi-meme » —
    # alors qu'elle EST dans le chat, et que « toi-meme » designe l'utilisateur.
    #
    # Or la voie existe : `lumena_ide` echappe au routage `ide_*`
    # (`is_ide_tool_name` rend False, cf. L5-2) et son action `ensure_workspace` est
    # autorisee hors mission. Un refus doit nommer la porte ouverte, pas seulement
    # celle qu'il ferme.
    "ide_host_authorization_not_connected": (
        "cette action modifie quelque chose, et le chat n'autorise que la lecture "
        "et la navigation dans l'IDE",
        "pour CHANGER DE DOSSIER ou ouvrir une fenetre de plus, passe par "
        "`lumena_ide` (action `ensure_workspace` ou `new_instance`) : cette voie "
        "est autorisee au chat. Sinon, demande-la depuis une mission",
    ),
    "ide_owner_context_required": (
        "l'appel arrive sans demandeur identifie, donc sans personne au nom de qui agir",
        "relance depuis le chat ou depuis une mission, jamais depuis un contexte detache",
    ),
    "ide_mission_workspace_mismatch": (
        "l'IDE jointe n'est pas ouverte sur le dossier de cette mission",
        "attends que la mission ouvre sa propre instance, ou ouvre ce dossier dans l'IDE",
    ),
    "ide_mission_policy_forbidden": (
        "cet outil est interdit aux missions, quelle que soit leur autorisation",
        "utilise-le depuis le chat, ou passe par l'equivalent natif de Lumena",
    ),
    "ide_mission_caller_unknown": (
        "l'appelant ne declare pas son identite, et une mission ne peut pas agir anonymement",
        "verifie que le worker ou le CodeAgent transmet bien son identite d'appel",
    ),
    "ide_mission_scope_divergent": (
        "le perimetre de la mission a change entre l'autorisation et l'envoi",
        "relance l'appel : le perimetre sera revalide sur l'etat courant",
    ),
    "ide_mission_scope_incomplete": (
        "la projection de mission manque des informations necessaires au controle",
        "verifie les metadonnees de la tache : espace de travail et role du worker",
    ),
    "ide_mission_scope_unsupported": (
        "l'IDE jointe parle une version de protocole qui ignore le perimetre de mission",
        "mets l'IDE a jour : le perimetre de mission exige le protocole 4",
    ),
    "ide_tool_outside_run_scope": (
        "cet outil ne fait pas partie de ceux autorises pour ce tour de travail",
        "ajoute-le au perimetre du run, ou choisis un outil deja autorise",
    ),
    "ide_mission_read_unconfined": (
        "la lecture demandee sort du dossier de la mission",
        "vise un chemin situe dans le dossier de mission",
    ),
    "ide_mission_target_outside": (
        "la cible est en dehors du dossier de la mission",
        "corrige le chemin pour qu'il reste dans le dossier de mission",
    ),

    # ── Ecriture ─────────────────────────────────────────────────────────────
    "ide_mission_mutation_not_connected": (
        "aucune IDE authentifiee n'est jointe pour porter cette modification",
        "verifie que l'IDE est lancee et appairee, puis relance",
    ),
    "ide_mission_write_busy": (
        "une autre ecriture est deja en cours sur cette mission",
        "attends la fin de l'ecriture courante : elles sont volontairement sequentielles",
    ),
    "ide_mission_write_parameters_invalid": (
        "les parametres d'ecriture ne decrivent pas une cible et un contenu exploitables",
        "fournis un chemin relatif au dossier de mission et le contenu complet du fichier",
    ),

    # ── Execution, taches et tests ───────────────────────────────────────────
    "ide_mission_cancelled": (
        "la mission a ete annulee pendant que l'operation etait en cours",
        "relance la mission si le travail doit reprendre",
    ),
    "ide_mission_command_interactive": (
        "la commande attend une saisie humaine, ce qu'une mission ne peut pas fournir",
        "rends la commande non interactive, par exemple avec une option de confirmation automatique",
    ),
    "ide_mission_command_invalid": (
        "la commande demandee n'est pas exploitable telle quelle",
        "verifie la commande : elle doit etre une ligne executable, sans enchainement cache",
    ),
    "ide_mission_task_invalid": (
        "la tache demandee ne correspond a aucune definition exploitable",
        "verifie l'identifiant de tache aupres de la liste des taches du projet",
    ),
    "ide_mission_task_not_found": (
        "aucune tache ne porte cet identifiant dans le projet",
        "liste les taches disponibles et reprends l'identifiant exact",
    ),
    "ide_mission_task_outside": (
        "la tache visee appartient a un projet autre que celui de la mission",
        "choisis une tache definie dans le dossier de la mission",
    ),
    "ide_mission_task_pipeline_unsupported": (
        "cette tache en declenche d'autres, ce qu'une mission ne peut pas lancer d'un bloc",
        "lance les etapes une par une, en verifiant chacune",
    ),
    "ide_mission_task_source_forbidden": (
        "la tache provient d'une source que la mission n'a pas le droit d'executer",
        "utilise une tache definie dans le projet lui-meme",
    ),
    "ide_mission_test_adapter_forbidden": (
        "l'executeur de tests demande n'est pas autorise pour une mission",
        "utilise l'executeur declare par le projet",
    ),
    "ide_mission_test_not_found": (
        "aucun test ne correspond a cet identifiant",
        "liste les tests du projet et reprends l'identifiant exact",
    ),
    "ide_mission_test_outside": (
        "le test vise est hors du dossier de la mission",
        "choisis un test situe dans le dossier de mission",
    ),
    "ide_mission_listing_unavailable": (
        "l'IDE n'a pas rendu la liste demandee",
        "verifie que l'IDE repond, puis relance : une liste vide n'est pas une erreur",
    ),
    "ide_followup_unavailable": (
        "le suivi de l'operation n'est pas expose par cette IDE",
        "mets l'IDE a jour : le suivi d'operation exige une version plus recente",
    ),

    # ── Resultats et preuves ─────────────────────────────────────────────────
    "ide_result_invalid": (
        "l'IDE a repondu quelque chose qui n'est pas un resultat exploitable",
        "regarde le journal de l'IDE : sa reponse est malformee",
    ),
    "ide_result_binding_invalid": (
        "le resultat ne se rattache pas a la commande envoyee",
        "relance : un resultat non rattachable est refuse plutot que devine",
    ),
    "ide_result_metadata_invalid": (
        "le resultat porte des metadonnees incoherentes avec la commande",
        "verifie la version de l'IDE : son format de reponse ne correspond pas",
    ),
    "ide_result_proof_invalid": (
        "la preuve d'execution fournie ne correspond pas a ce qui a ete demande",
        "relance l'operation : sans preuve valable, aucun succes n'est declare",
    ),
    "ide_result_status_inconsistent": (
        "le statut annonce contredit le contenu du resultat",
        "regarde le journal de l'IDE : elle annonce un etat qu'elle ne tient pas",
    ),
    "ide_result_status_invalid": (
        "le statut rendu n'est pas un statut connu",
        "verifie la version de l'IDE : son vocabulaire de statut est plus recent ou plus ancien",
    ),
    "ide_result_target_invalid": (
        "la cible rendue ne correspond pas a celle demandee",
        "relance : une cible divergente est refusee plutot que suivie",
    ),
    "ide_result_timestamp_invalid": (
        "les horodatages du resultat ne sont pas exploitables",
        "verifie l'horloge de la machine, puis relance",
    ),

    # ── Catalogue et outils externes ─────────────────────────────────────────
    "external_tool_not_in_snapshot": (
        "cet outil n'est pas dans le catalogue actif de l'IDE jointe",
        "verifie que l'IDE est bien connectee, ou choisis un outil qu'elle expose",
    ),
    "external_tool_not_exposed": (
        "cet outil existe mais n'est jamais propose au modele",
        "fais l'action directement dans l'IDE : elle n'est pas pilotable a distance",
    ),
    "external_catalog_required": (
        "aucun catalogue d'outils n'est disponible pour cet appel",
        "verifie que l'IDE est lancee et appairee",
    ),
    "external_unavailable_tools": (
        "le catalogue annonce des outils que le fournisseur ne sert pas",
        "redemarre l'IDE : son catalogue et son etat ont diverge",
    ),
    "external_call_unresolved": (
        "l'appel ne designe pas un outil identifiable",
        "verifie le nom de l'outil",
    ),
    "external_parameters_invalid": (
        "les parametres ne respectent pas le schema de l'outil",
        "relis le schema de l'outil : un champ manque ou porte un type inattendu",
    ),
    "external_payload_invalid": (
        "le contenu envoye n'est pas exploitable par l'IDE",
        "verifie la forme des donnees envoyees",
    ),
    "external_payload_too_large": (
        "le contenu depasse la taille qu'une seule commande peut porter",
        "decoupe l'operation en plusieurs envois plus petits",
    ),
    "external_effect_capacity_reached": (
        "trop d'operations a effet sont deja en cours simultanement",
        "attends la fin des operations en cours avant d'en lancer d'autres",
    ),
    "external_effect_unresolved": (
        "l'effet de cet outil n'est pas determine, donc il ne peut pas etre autorise",
        "signale-le : un outil doit declarer son effet avant d'etre exposable",
    ),
    "external_provider_identity_changed": (
        "le fournisseur d'outils a change d'identite en cours de route",
        "relance : le catalogue sera recapture sur la nouvelle identite",
    ),
    "external_provider_state_invalid": (
        "le fournisseur annonce un etat incoherent",
        "redemarre l'IDE pour repartir d'un etat propre",
    ),
    "external_provider_invalid": (
        "le fournisseur d'outils ne repond pas au contrat attendu",
        "verifie la version de l'IDE",
    ),
    "external_providers_invalid": (
        "la liste des fournisseurs d'outils est malformee",
        "redemarre Lumena pour reconstruire le registre",
    ),
    "external_provider_collision": (
        "deux fournisseurs revendiquent la meme identite",
        "verifie qu'une seule IDE est enregistree sous cette identite",
    ),
    "external_tool_collision": (
        "deux outils portent le meme nom dans le catalogue",
        "signale-le : un catalogue ne doit jamais exposer deux fois le meme nom",
    ),
    "external_native_collision": (
        "un outil de l'IDE porte le nom d'un outil natif de Lumena",
        "signale-le : les noms d'outils IDE doivent rester dans leur espace reserve",
    ),
    "external_namespace_reserved": (
        "ce nom appartient a un espace reserve de Lumena",
        "signale-le : l'IDE tente d'exposer un nom qui ne lui appartient pas",
    ),
    "external_semantics_required": (
        "l'outil n'annonce pas ce qu'il fait, donc il ne peut pas etre autorise",
        "signale-le : chaque outil doit declarer effet, risque et confirmation",
    ),
    "external_description_invalid": (
        "la description de l'outil n'est pas exploitable",
        "verifie la version de l'IDE : son catalogue est malforme",
    ),
    "external_schema_invalid": (
        "le schema de parametres de l'outil est malforme",
        "verifie la version de l'IDE",
    ),
    "external_schema_must_be_closed": (
        "le schema accepte des champs non declares, ce qui interdit tout controle",
        "signale-le : un schema d'outil doit refuser les champs inconnus",
    ),
    "external_schema_reference_forbidden": (
        "le schema renvoie a une definition externe, impossible a verifier ici",
        "signale-le : un schema doit etre autonome",
    ),
    "external_resolver_invalid": (
        "la resolution conditionnelle de l'outil ne respecte pas son contrat",
        "signale-le : le comportement conditionnel de cet outil est incoherent",
    ),
    "external_resolver_expanded_mission_policy": (
        "la resolution a tente d'elargir ce qu'une mission a le droit de faire",
        "signale-le : c'est un refus de securite, pas une erreur de manipulation",
    ),
    "external_resolver_mutated_parameters": (
        "la resolution a modifie les parametres apres leur validation",
        "signale-le : les parametres juges doivent etre ceux envoyes",
    ),
    "external_tools_invalid": (
        "la liste d'outils annoncee est malformee",
        "verifie la version de l'IDE",
    ),

    "ide_launch_workspace_refuse": (
        "le dossier demande sort de ce que le chat peut ouvrir",
        "vise un dossier du workspace, ou celui du projet en cours",
    ),

    # ── Echec interne ────────────────────────────────────────────────────────
    "ide_dispatch_failed": (
        "l'envoi vers l'IDE a echoue avant d'obtenir une reponse",
        "verifie que l'IDE est toujours lancee et connectee, puis relance",
    ),
}


def expliquer_erreur_ide(code: Optional[str]) -> str:
    """Le code technique, suivi de sa cause et du geste - ou le code nu.

    Le code reste EN TETE : les journaux, les tests et le diagnostic s'appuient
    dessus. Un code inconnu est rendu tel quel : mieux vaut un code nu qu'une
    explication fabriquee.
    """
    texte = str(code or "").strip()
    if not texte:
        return texte
    guide = GUIDES_ERREURS_IDE.get(texte)
    if guide is None:
        return texte
    cause, geste = guide
    return f"{texte} — {cause}. À faire : {geste}."
