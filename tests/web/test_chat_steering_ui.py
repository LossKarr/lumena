from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_composer_keeps_send_and_stop_as_distinct_controls():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    main = (ROOT / "web/static/js/main.js").read_text(encoding="utf-8")
    chat = (ROOT / "web/static/js/chat.js").read_text(encoding="utf-8")
    assert 'id="send-btn"' in html
    assert 'id="stop-btn"' in html
    assert "q('send-btn', () => sendMessage())" in main
    assert "q('stop-btn', () => cancelStream())" in main
    assert "initChatSteering();\n  setupTextarea();" in main
    assert "if(isLoading||window.LumenaSteering?.isActive()){await window.LumenaSteering?.submitFromComposer();return;}" in chat
    assert "if(isLoading)return;" not in chat
    assert "if(isStop)window.LumenaSteering?.beginWork()" in chat
    assert "lumenaTextareaReady" in chat
    assert "function isActive(){return active}" in (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")


def test_two_delivery_policies_and_accessible_live_status_are_wired():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert 'id="steering-live" hidden aria-live="polite"' in html
    assert 'data-steering-policy="next_checkpoint"' in html
    assert 'data-steering-policy="urgent_safe_boundary"' in html
    assert "sessionStorage.getItem(STORAGE_KEY)" in module
    assert "/steering`" in module
    assert "idempotency_key:idempotency" in module
    assert "/api/work/active" in module
    assert "if(!activeTaskId)await discoverActiveTask()" in module
    assert "sessionStorage.removeItem(STORAGE_KEY)" in module


def test_stream_task_id_drives_steering_without_canned_assistant_message():
    chat = (ROOT / "web/static/js/chat.js").read_text(encoding="utf-8")
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert "LumenaSteering?.observeStreamEvent(data)" in chat
    assert "if(data?.task_id)setActive(true,String(data.task_id))" in module
    assert "window.addMsg('assistant'" not in module
    assert "input.value=''" in module
    assert "else{\n      setActive(false);\n      input.focus();" in module


def test_live_steering_owns_enter_and_send_controls_while_active():
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert "event.target?.id!=='message-input'" in module
    assert "event.target?.closest?.('#send-btn')" in module
    assert "event.stopImmediatePropagation()" in module
    assert "if(submitting)return false" in module


def test_live_steering_resolves_restart_ambiguity_deterministically():
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert "const restored=roots.find" in module
    assert "b.state==='running'" in module
    assert "Date.parse(b.updated_at||0)-Date.parse(a.updated_at||0)" in module
    assert "data?.conversation_id" in module
    assert "document.addEventListener('lumena:app-ready',()=>discoverActiveTask())" in module


def test_chat_steering_assets_have_explicit_cache_versions():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    main = (ROOT / "web/static/js/main.js").read_text(encoding="utf-8")
    assert "/static/css/chat-steering.css?v=2" in html
    assert "./chat.js?v=5" in main
    assert "./chat-steering.js?v=7" in main
    assert "/static/js/main.js?v=62" in html


def test_task_orchestrator_is_enabled_by_default_for_live_steering():
    lifespan = (ROOT / "web/routes/lifespan.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert '_env_flag("LUMENA_TASK_ORCHESTRATOR_V1", True)' in lifespan
    assert "# LUMENA_TASK_ORCHESTRATOR_V1=True" in env_example


def test_le_steering_envoie_le_token_comme_le_reste_du_front():
    """Trois appels partaient sans en-tete d'autorisation, donc 401 muet.

    Le token est rempli par `startup.js` dans la liaison globale de script. Il
    n'atterrit sur l'objet global du navigateur que pendant le wizard
    (`setup.js`), jamais en session normale. Un module ES qui n'interroge que cet
    objet repart donc vide. `local-models.js`, ecrit le meme jour, utilise deja
    la lecture correcte : ce module doit faire pareil.
    """
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    ligne = next((texte for texte in module.splitlines()
                  if texte.lstrip().startswith("function headers(")), "")
    assert ligne, "la fabrique d'en-tetes a disparu"
    assert "typeof ADMIN_TOKEN" in ligne, (
        "le steering ne lit que l'objet global du navigateur : en session normale il "
        "part sans en-tete, recoit 401, et l'orientation devient silencieusement inerte"
    )


def test_un_refus_d_authentification_ne_reste_pas_silencieux():
    """Un `return` nu sur reponse non-ok rend un defaut reseau invisible."""
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert "status===401" in module, (
        "un refus d'autorisation n'est pas signale : c'est ce qui a masque le defaut"
    )


def test_le_panneau_s_eteint_a_la_fin_du_tour():
    """Un tour termine doit rendre la main : sinon le panneau reste allume.

    Observe la fin de flux : rafraichir les statuts ne suffit pas, l'etat actif
    doit retomber, sans quoi le composer reste en mode orientation apres coup.
    """
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    ligne = next((texte for texte in module.splitlines()
                  if "data?.type" in texte and "error" in texte), "")
    assert ligne, "l'observation de fin de flux a disparu"
    assert "setActive(false)" in ligne, (
        "la fin d'un tour ne desactive jamais le mode orientation : le panneau "
        "reste affiche alors que plus rien ne travaille"
    )


def test_aucune_cible_trouvee_eteint_le_panneau():
    """Ne rien trouver doit eteindre, pas seulement oublier l'identifiant."""
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    bloc = module.split("if(!target?.task_id){", 1)
    assert len(bloc) == 2, "le chemin sans cible a disparu"
    corps = bloc[1].split("}", 1)[0]
    assert "setActive(false)" in corps, (
        "sans cible, l'identifiant est oublie mais l'etat actif reste vrai"
    )


def test_le_rechargement_ne_croit_pas_la_session_sur_parole():
    """Au chargement, un identifiant garde en session n'est pas une preuve.

    Le travail correspondant peut etre termine depuis des heures : l'etat doit
    etre reverifie aupres du serveur avant d'afficher quoi que ce soit.
    """
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    assert "if(activeTaskId){active=true;startPolling()}" not in module, (
        "le module rallume le panneau sur la seule foi de la session, sans "
        "verifier que ce travail existe encore"
    )


def test_l_interface_permet_d_annuler_une_orientation_en_attente():
    """La route d'annulation existe cote serveur — l'interface ne l'appelait pas.

    `POST /api/tasks/{id}/steering/{command_id}/cancel` est declaree dans
    `web/routes/steering.py` depuis le chantier ORI. Le seul « cancel » du module
    front etait un libelle d'affichage : aucun moyen d'annuler ni de corriger une
    orientation partie par erreur.
    """
    module = (ROOT / "web/static/js/chat-steering.js").read_text(encoding="utf-8")
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert "/cancel" in module, (
        "l'interface n'appelle jamais la route d'annulation pourtant disponible"
    )
    assert "data-steering-cancel" in module, (
        "aucun controle d'annulation n'est rendu sur les orientations"
    )
    assert "steering-history" in html
