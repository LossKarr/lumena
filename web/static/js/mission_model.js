/* Panel Missions — lot 1 : le MODELE. Logique pure, aucun DOM.
 *
 * Meme patron que `mission_tree.js` (lot 5.4) : script classique UMD, expose en
 * global ET en module, donc executable par node depuis pytest.
 *
 * --- Ce qu'il fait ---
 *
 * Il reduit DEUX sources en un seul objet que les quatre vues consomment :
 *   - le payload REST `/api/missions` (etat, objectif, metadata, arbre)
 *   - le flux SSE `/api/trace/stream` (evenements temps reel, keyes par task_id)
 *
 * Changer de vue ne doit jamais retoucher la donnee : c'est tout l'interet.
 *
 * --- Tolerance ---
 *
 * Un champ absent ne casse rien. Le backend d'un serveur pas encore a jour
 * n'enverra ni `thought` ni `codeagent_wait_*` : le modele rend alors des
 * valeurs neutres que les vues savent afficher. On ne suppose RIEN.
 */
(function (root) {
  'use strict';

  /* Etats REELS du corpus (665 taches persistees) :
   *   done 597 · cancelled 47 · failed 14 · checkpointed 7
   *
   * `cancelled` etait rendu « échec » et `checkpointed` « travaille ». Les deux
   * etaient faux, et le second l'etait doublement : une tache interrompue au
   * reboot n'est pas rejouee automatiquement — elle attend une revue. La
   * presenter comme active, c'est exactement le defaut que ce panneau existe
   * pour fermer. */
  var TERMINAL = { done: 1, failed: 1, cancelled: 1 };
  var LIVE_GRACE_MS = 5 * 60 * 1000;

  function _s(v) { return (v == null) ? '' : String(v); }
  function _n(v) { var x = Number(v); return isFinite(x) ? x : 0; }

  /* Etat d'un worker, deduit de son etat de tache ET de sa derniere trace.
   * `waiting` n'existe pas cote serveur : il se deduit de l'attente du verrou
   * du CodeAgent, qui est justement ce que le lot 0.c a rendu mesurable. */
  function liveIsFresh(live, nowMs) {
    if (!live || !live.lastAt) return false;
    var at = Date.parse(live.lastAt);
    if (isNaN(at)) return false;
    var age = (nowMs == null ? Date.now() : Number(nowMs)) - at;
    return age >= -5000 && age <= LIVE_GRACE_MS;
  }

  function workerState(mission, live, nowMs) {
    var st = _s(mission && mission.state);
    if (st === 'done') return 'done';
    if (st === 'cancelled') return 'cancelled';   // arretee, PAS echouee
    if (TERMINAL[st]) return 'failed';
    if (st === 'checkpointed') {
      // `checkpointed` est aussi l'etat persiste ENTRE deux iterations d'un
      // run vivant. Le registre runtime est la preuve principale. Le flux recent
      // garde la compatibilite avec un backend plus ancien qui n'expose pas
      // encore `runtime_active`. Sans aucune preuve vivante, le checkpoint reste
      // bien une interruption a revoir apres redemarrage.
      var runtimeKnown = mission && typeof mission.runtime_active === 'boolean';
      var active = runtimeKnown ? mission.runtime_active : liveIsFresh(live, nowMs);
      if (active) return live && live.waitingSince ? 'waiting' : 'running';
      return 'stalled';
    }
    if (live && live.waitingSince) return 'waiting';
    return 'running';
  }

  /* Reduction du flux SSE. Rend un dictionnaire task_id -> etat vivant.
   * Tolerant a l'ordre : un `wait_end` sans `wait_start` ne casse pas. */
  function reduceEvents(events) {
    var out = {};
    var list = Array.isArray(events) ? events : [];
    for (var i = 0; i < list.length; i++) {
      var ev = list[i];
      if (!ev || ev.task_id == null) continue;
      var id = String(ev.task_id);
      var cur = out[id] || { thought: '', iteration: 0, maxIter: 0, lastTool: '',
                             lastAt: null, waitingSince: null, waitedMs: 0, events: 0,
                             trail: [], history: [] };
      cur.events += 1;
      var stage = _s(ev.stage);
      // Fil brut, BORNE. La case « Journal brut » du menu Affichage ne pilotait
      // rien : le modele comptait les evenements sans en garder un seul. Douze
      // lignes suffisent a comprendre ce qui vient de se passer, et une mission
      // longue en produit des milliers.
      var trace = {
        stage: stage,
        tool: _s(ev.tool_name),
        summary: _s(ev.summary),
        status: _s(ev.status),
        ts: _s(ev.ts)
      };
      cur.trail.push(trace);
      if (cur.trail.length > 12) cur.trail.splice(0, cur.trail.length - 12);
      // Le battement reste volontairement borne a 12, mais le journal brut
      // expose tout ce que le tampon du panneau possede. La limite de 400 est
      // la meme que celle du flux cote chassis : aucune seconde troncature
      // silencieuse n'est ajoutee par worker.
      cur.history.push(trace);
      if (cur.history.length > 400) cur.history.splice(0, cur.history.length - 400);

      if (stage === 'codeagent_wait_start') {
        cur.waitingSince = ev.ts || ev.seq || true;
      } else if (stage === 'codeagent_wait_end') {
        cur.waitingSince = null;
        cur.waitedMs += _n(ev.duration_ms);
      } else {
        // Toute autre trace prouve que la tache travaille : elle n'attend plus.
        if (stage === 'codeagent_iteration') cur.waitingSince = null;
      }

      if (ev.thought) cur.thought = _s(ev.thought);
      if (ev.iteration != null) cur.iteration = _n(ev.iteration);
      if (ev.max_iter != null) cur.maxIter = _n(ev.max_iter);
      if (ev.tool_name) cur.lastTool = _s(ev.tool_name);
      if (ev.ts) cur.lastAt = ev.ts;
      out[id] = cur;
    }
    return out;
  }

  /* Rang dans la file du CodeAgent : 0 = en train de coder, 1..n = position.
   * Ordre d'arrivee = ordre des `wait_start`, donc l'ordre du tableau. */
  function queueRanks(live) {
    var attente = [];
    for (var id in live) {
      if (Object.prototype.hasOwnProperty.call(live, id) && live[id].waitingSince) {
        attente.push(id);
      }
    }
    var ranks = {};
    for (var i = 0; i < attente.length; i++) ranks[attente[i]] = i + 1;
    return ranks;
  }

  /* Temps restant avant echeance, en millisecondes. null si aucune echeance.
   * `deadline_ts` est une chaine ISO NAIVE, locale (lecon Z32 : ne pas la
   * normaliser, le runtime est coherent avec lui-meme). */
  function remainingMs(mission, nowMs) {
    var meta = (mission && mission.metadata) || {};
    if (!meta.deadline_ts) return null;
    var t = Date.parse(meta.deadline_ts);
    if (isNaN(t)) return null;
    return t - (nowMs || Date.now());
  }

  function formatDuration(ms) {
    if (ms == null) return '';
    var neg = ms < 0;
    var s = Math.floor(Math.abs(ms) / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var txt = h > 0
      ? h + ':' + String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0')
      : m + ':' + String(r).padStart(2, '0');
    return (neg ? '-' : '') + txt;
  }

  /* Chronologie persistee — `checkpoint_history`, presente sur 669 taches sur
   * 670 et deja transmise par l'API. Le panneau ne lisait que l'instantane
   * `last_checkpoint` et jetait la suite.
   *
   * On garde les DOUZE derniers points : c'est le contrat de la sparkline
   * d'une tuile de statistique, et la mediane des historiques reels est 9.
   *
   * Rend null en dessous de deux points : un point isole n'est pas une
   * tendance, et une sparkline a un seul point est un mensonge graphique. */
  function timeline(mission, max) {
    var h = (mission && mission.checkpoint_history) || [];
    if (!Array.isArray(h) || h.length < 2) return null;
    var n = max || 12;
    var pts = h.slice(-n).map(function (e) {
      var p = (e && e.payload) || {};
      var l = p.ledger || {};
      return {
        ts: _s(e && e.ts),
        phase: _s(p.phase),
        iteration: _n(p.iteration),
        actions: _n(l.total_actions),
        successPct: (l.success_rate == null) ? null
          : Math.round(_n(l.success_rate) * 100)
      };
    });
    var vals = pts.map(function (p) { return p.actions; });
    var hi = Math.max.apply(null, vals);
    var lo = Math.min.apply(null, vals);
    return {
      points: pts,
      min: lo,
      max: hi,
      total: h.length,          // le TOTAL, pas les douze gardes
      phaseFinale: pts[pts.length - 1].phase
    };
  }

  /* Part du budget consommee. `created_at` est un ISO UTC-aware, `deadline_ts`
   * un ISO naif local : `Date.parse` rend le bon instant pour les deux, et la
   * difference est donc coherente. Verifie sur les 553 taches du corpus qui
   * portent les deux champs — zero duree negative, mediane 29,6 minutes.
   *
   * Rend null plutot que zero quand on ne sait pas : « pas de budget » et
   * « budget a peine entame » ne se dessinent pas pareil. */
  function budget(mission, nowMs) {
    var meta = (mission && mission.metadata) || {};
    if (!meta.deadline_ts || !mission || !mission.created_at) return null;
    var fin = Date.parse(meta.deadline_ts);
    var debut = Date.parse(mission.created_at);
    if (isNaN(fin) || isNaN(debut)) return null;
    var total = fin - debut;
    if (total <= 0) return null;
    var ecoule = (nowMs || Date.now()) - debut;
    return {
      totalMs: total,
      elapsedMs: ecoule,
      pct: Math.max(0, Math.min(100, Math.round((ecoule / total) * 100)))
    };
  }

  /* Perimetre d'ecriture du worker, tel que pose en metadata. Toujours un
   * tableau : une valeur absente rend [], jamais null. */
  function perimeter(mission) {
    var meta = (mission && mission.metadata) || {};
    var v = meta.allowed_files;
    if (Array.isArray(v)) return v.map(_s);
    if (typeof v === 'string' && v) return [v];
    return [];
  }

  /* Objectif COMPLET, jamais tronque a l'affichage (le panneau actuel coupait
   * a mi-phrase). Retombe sur le preview si la metadata est absente. */
  function objective(mission) {
    var meta = (mission && mission.metadata) || {};
    return _s(meta.objective || (mission && mission.message_preview) || '');
  }

  /* ══════════════════════════════════════════════════════════════════════
   *  LE LEDGER ET LES PREUVES — ils etaient DEJA la.
   *
   *  La vue Controle annoncait « Ledger non expose par l'API » et laissait
   *  deux colonnes sur trois vides. C'etait faux : `to_dict()` de la tache est
   *  un `asdict()` du disque entier, donc `/api/missions` envoyait deja tout
   *  au navigateur. Verifie sur le corpus reel — 665 taches, `last_checkpoint`
   *  present sur 665, `completion_proof` sur 132.
   *
   *  La moitie vraie de l'annonce : le PLAN du lead, lui, n'est nulle part
   *  dans les donnees. Il n'y a pas de plan a afficher, il y a une
   *  PROGRESSION — et ce n'est pas la meme chose. La colonne le dit.
   * ══════════════════════════════════════════════════════════════════════ */

  /* Projection du ledger, telle que `enrich_checkpoint` la pose :
   *   { total_actions, successful_mutations, success_rate, recent: [...] }
   * Rend null si la tache n'a pas encore de checkpoint — le panneau affiche
   * alors une absence nommee plutot qu'un tableau vide. */
  function ledger(mission) {
    var cp = (mission && mission.last_checkpoint) || {};
    var l = cp.ledger;
    if (!l || typeof l !== 'object') return null;
    var recent = Array.isArray(l.recent) ? l.recent : [];
    return {
      actions: _n(l.total_actions),
      mutations: _n(l.successful_mutations),
      // `success_rate` vaut 0..1 cote runtime ; on rend un pourcentage entier.
      successPct: (l.success_rate == null) ? null : Math.round(_n(l.success_rate) * 100),
      phase: _s(cp.phase),
      iteration: _n(cp.iteration),
      recent: recent.slice(-6).reverse().map(function (a) {
        return {
          action: _s(a && a.action),
          target: _s(a && a.target),
          success: !(a && a.success === false)
        };
      })
    };
  }

  /* Preuves de completion. Chaque ligne est un FAIT du disque, jamais une
   * deduction : une preuve requise et non etablie se dit « non prouvé », elle
   * ne disparait pas. C'est la doctrine 2.13.A appliquee a l'affichage. */
  function proofs(mission) {
    var meta = (mission && mission.metadata) || {};
    var cp = meta.completion_proof;
    var out = [];
    if (cp && typeof cp === 'object') {
      out.push({ cle: 'delivery', lib: 'Livraison', ok: cp.delivery_proven === true });
      if (cp.tests_required) {
        out.push({ cle: 'tests', lib: 'Tests', ok: cp.tests_green === true });
      }
      if (cp.browser_required) {
        out.push({ cle: 'browser', lib: 'Navigateur', ok: cp.browser_proven === true });
      }
      if (cp.delegation_complete != null) {
        out.push({ cle: 'delegation', lib: 'Délégation', ok: cp.delegation_complete === true });
      }
      // Les manquants ne sont PAS une preuve : ce sont des trous, et ils ont
      // droit a leur ligne. Les taire serait mentir par omission (lot Z24).
      var trous = [].concat(
        (Array.isArray(cp.missing_files) ? cp.missing_files : []).map(function (f) {
          return { cle: 'missing', lib: 'Manquant : ' + _s(f), ok: false };
        }),
        (Array.isArray(cp.stub_files) ? cp.stub_files : []).map(function (f) {
          return { cle: 'stub', lib: 'Ébauche : ' + _s(f), ok: false };
        })
      );
      out = out.concat(trous);
    } else if (meta.tests_green != null) {
      // Pas de `completion_proof`, mais un verdict de tests : on le montre.
      out.push({ cle: 'tests', lib: 'Tests', ok: meta.tests_green === true });
    }
    return out;
  }

  /* Ce qui a ete PUBLIE, distinct de ce qui a ete ecrit. Publier fige un
   * instantane (lot Z24) : ce sont deux faits differents. */
  function delivered(mission) {
    var meta = (mission && mission.metadata) || {};
    var pub = Array.isArray(meta.published_files) ? meta.published_files : [];
    var art = Array.isArray(meta.artifacts) ? meta.artifacts : [];
    return {
      published: pub.map(_s),
      publishedAt: _s(meta.published_at),
      artifacts: art.map(function (a) {
        var s = _s(a);
        // Chemin absolu : on ne garde que la fin, seule partie lisible.
        var m = /[^\\/]+$/.exec(s);
        return { full: s, nom: m ? m[0] : s };
      })
    };
  }

  /* Etat AGREGE d'une mission — ce qui la distingue de la mission d'a cote.
   *
   * Deux missions cote a cote se ressemblaient trait pour trait : meme en-tete,
   * meme gris, et il fallait lire les chiffres pour savoir laquelle demandait
   * de l'attention. L'agregat repond a la question qu'on se pose d'abord :
   * laquelle est en difficulte ?
   *
   * ORDRE : etat propre terminal > echec/interruption d'un worker > clos par
   * ses workers > echeance depassee > tout en attente > en cours.
   *
   * L'etat propre passe en premier parce qu'il est le seul FAIT : le reste est
   * deduit. Une mission que le runtime declare terminee est terminee, meme si
   * son echeance est loin derriere — et une mission sans worker n'a que lui.
   *
   * `late` est volontairement BAS : le retard a deja son canal, le compte a
   * rebours rouge de l'en-tete. La pastille sert a montrer ce qui n'a pas
   * d'autre porte-voix.
   */
  function aggregateState(kids, remaining, own) {
    var enRetard = (remaining != null && remaining < 0);

    // L'ETAT PROPRE DE LA MISSION D'ABORD. Sans lui, une mission solo — le
    // lead travaille seul, 65 racines sur 199 — n'avait aucun enfant a
    // examiner et retombait sur `late` : 43 missions closes s'affichaient
    // « échéance dépassée » avec un bouton « Arrêter », dont 3 dont l'echec
    // etait ainsi masque par son propre symptome.
    if (own === 'done') return 'done';
    if (own === 'cancelled') return 'cancelled';
    if (own === 'failed') return 'failed';
    if (own === 'stalled') return 'stalled';
    var list = Array.isArray(kids) ? kids : [];
    var vivants = 0, attente = 0, arretes = 0, echec = false, coupe = false;
    // On COLLECTE, puis on decide. Sortir de la boucle des le premier etat
    // grave faisait dependre la gravite de l'ORDRE DU TABLEAU : ['stalled',
    // 'failed'] rendait `stalled`, l'inverse rendait `failed`. C'est mon
    // propre test qui l'a trouve.
    for (var i = 0; i < list.length; i++) {
      var st = list[i].state;
      if (st === 'failed') { echec = true; continue; }
      // Une tache interrompue au reboot demande une decision humaine : elle
      // pese lourd, mais moins qu'un echec.
      if (st === 'stalled') { coupe = true; continue; }
      if (st === 'cancelled') { arretes++; continue; }
      if (st !== 'done') { vivants++; if (st === 'waiting') attente++; }
    }
    if (echec) return 'failed';
    if (coupe) return 'stalled';
    // Une mission CLOSE passe avant le retard. La premiere version testait
    // `late` en tout premier : sur le panneau reel, trois missions terminees
    // 5/5 s'affichaient « échéance dépassée » avec un compte a rebours qui
    // tournait encore — quatorze jours de retard sur un travail fini. Une
    // echeance ne s'applique qu'a ce qui court encore.
    if (list.length && !vivants) return arretes ? 'cancelled' : 'done';
    if (enRetard) return 'late';
    if (!list.length) return 'running';
    // Tous les workers encore en vie attendent le verrou : la mission n'avance
    // PAS, meme si rien n'a echoue. C'est l'etat que le panneau existe pour
    // rendre visible.
    return (attente === vivants) ? 'waiting' : 'running';
  }

  /* LE modele. `tree` = sortie de buildMissionTree ; `events` = flux SSE brut. */
  function buildModel(tree, events, nowMs) {
    var live = reduceEvents(events);
    var ranks = queueRanks(live);
    var roots = Array.isArray(tree) ? tree : [];

    function noeud(n) {
      var m = (n && n.mission) || {};
      var id = _s(m.task_id);
      var l = live[id] || { thought: '', iteration: 0, maxIter: 0, lastTool: '',
                            waitingSince: null, waitedMs: 0, events: 0, trail: [], history: [] };
      return {
        id: id,
        state: workerState(m, l, nowMs),
        runtimeActive: m.runtime_active === true,
        objective: objective(m),
        thought: l.thought,
        iteration: l.iteration,
        maxIter: l.maxIter,
        lastTool: l.lastTool,
        waitedMs: l.waitedMs,
        trail: (l.trail || []).slice().reverse(),   // le plus recent en tete
        logs: (l.history || l.trail || []).slice().reverse(),
        events: l.events,
        queueRank: ranks[id] || 0,
        perimeter: perimeter(m),
        resultSummary: _s(m.result_summary),
        ledger: ledger(m),
        proofs: proofs(m),
        delivered: delivered(m),
        children: (n.children || []).map(noeud)
      };
    }

    return roots.map(function (n) {
      var base = noeud(n);
      var kids = base.children;
      var faits = 0;
      for (var i = 0; i < kids.length; i++) {
        if (kids[i].state === 'done' || kids[i].state === 'failed') faits++;
      }
      base.workers = { done: faits, total: kids.length };
      base.remainingMs = remainingMs(n.mission, nowMs);
      base.queueLength = Object.keys(ranks).length;
      // `base.state` est calcule par `noeud()` juste au-dessus. Il etait pose
      // sur l'objet rendu, puis jamais consulte ici.
      base.aggregate = aggregateState(kids, base.remainingMs, base.state);
      base.timeline = timeline(n.mission);
      // Poids du journal archive, annote par `/api/missions`. Sans lui, rien
      // ne distingue une mission finie QUI A GARDE une trace d'une mission
      // finie muette — il faudrait deplier chacune pour le decouvrir.
      base.archiveBytes = _n((n.mission || {}).journal_bytes);
      // Un compte a rebours n'a de sens que sur ce qui court. Sur une mission
      // close il continuait d'egrener les jours de retard, en rouge, en haut
      // de l'ecran — l'element le plus voyant du panneau disait une chose
      // sans objet. On le TAIT, et l'etat agrege dit deja « terminée ».
      // DEUX notions, que la premiere version confondait sous un seul nom :
      //
      //   terminal  la mission ne tournera plus (terminee, annulee, echouee).
      //             Son compte a rebours n'a plus d'objet, et on ne peut plus
      //             l'arreter. Sept missions echouees du corpus egrenaient
      //             encore leur retard.
      //   closed    elle se replie d'elle-meme. Un ECHEC ne se replie pas :
      //             c'est justement ce qu'il faut regarder.
      base.terminal = (base.aggregate === 'done' || base.aggregate === 'cancelled'
                       || base.aggregate === 'failed');
      // Le budget se tait sur ce qui ne tourne plus, comme le compte a rebours.
      base.budget = base.terminal ? null : budget(n.mission, nowMs);
      base.closed = (base.aggregate === 'done' || base.aggregate === 'cancelled');
      base.deadlineLabel = base.terminal ? '' : formatDuration(base.remainingMs);
      return base;
    });
  }

  var api = {
    reduceEvents: reduceEvents,
    queueRanks: queueRanks,
    workerState: workerState,
    liveIsFresh: liveIsFresh,
    remainingMs: remainingMs,
    formatDuration: formatDuration,
    perimeter: perimeter,
    objective: objective,
    budget: budget,
    timeline: timeline,
    aggregateState: aggregateState,
    ledger: ledger,
    proofs: proofs,
    delivered: delivered,
    buildModel: buildModel
  };

  root.missionReduceEvents = reduceEvents;
  root.missionQueueRanks = queueRanks;
  root.missionWorkerState = workerState;
  root.missionRemainingMs = remainingMs;
  root.missionFormatDuration = formatDuration;
  root.missionPerimeter = perimeter;
  root.missionBudget = budget;
  root.missionTimeline = timeline;
  root.missionObjective = objective;
  root.missionAggregateState = aggregateState;
  root.missionLedger = ledger;
  root.missionProofs = proofs;
  root.missionDelivered = delivered;
  root.buildMissionModel = buildModel;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
