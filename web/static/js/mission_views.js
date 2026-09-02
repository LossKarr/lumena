/* Panel Missions — les VUES. Rendu pur, aucun DOM, aucune dependance.
 *
 * Trois vues, une seule signature : `render(modele, prefs) -> string`. Pures :
 * elles ne lisent ni `document`, ni `window`, ni `panels.js`. C'est ce qui les
 * rend executables par node depuis pytest, et ce qui empeche le coeur de se
 * reemmeler dans le fichier de 7 400 lignes.
 *
 * --- Identite visuelle ---
 *
 * AUCUNE couleur en dur. Tout passe par les tokens de `tokens.css`
 * (`--accent`, `--card`, `--muted`, `--ok`…), donc le panneau suit
 * automatiquement le theme clair et le theme sombre de Lumena. Un test
 * structurel fait rougir la suite si une valeur en dur reapparait.
 *
 * --- Les trois vues ---
 *
 *   C « Atelier »   une carte par worker, la PENSEE en element principal
 *   B « Ruban »     une piste par worker, rend la file du CodeAgent visible
 *   A « Controle »  trois colonnes : progression, workers, preuves
 *
 * Une quatrieme, « Constellation », a existe : le lead au centre, les workers
 * en orbite, le CodeAgent en porte unique. Retiree a la demande, et RETIREE —
 * pas desactivee : un onglet fantome et du code mort seraient exactement la
 * classe de defaut que les lots 14 et 15 ont fermee.
 */
(function (root) {
  'use strict';

  var ETATS = {
    running: { cls: 'on', lib: 'en cours' },
    waiting: { cls: 'wait', lib: 'en file' },
    done: { cls: 'done', lib: 'validé' },
    failed: { cls: 'fail', lib: 'échec' },
    cancelled: { cls: 'stop', lib: 'annulée' },
    stalled: { cls: 'stall', lib: 'interrompue' }
  };

  /* Echappement HTML. Local : les vues ne dependent de rien. */
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _on(prefs, bloc) {
    if (!prefs || !prefs.blocks) return true;          // defaut : tout visible
    return prefs.blocks[bloc] !== false;
  }

  /* ── Trois marques qui se lisent sans se lire ────────────────────────────
   *
   * Un tableau de bord se balaie. La pastille « 1/5 » demandait une lecture ;
   * l'anneau donne la meme chose d'un coup d'oeil. Toutes prennent `--et`, le
   * canal d'etat deja en place : aucune couleur nouvelle, et l'accent de
   * Lumena reste reserve a ce qui est vraiment Lumena.
   */

  /* Anneau d'avancement. SVG a la main, 13 px de rayon, aucune bibliotheque. */
  function anneau(faits, total) {
    if (!total) return '';
    var r = 9, c = 2 * Math.PI * r;
    var part = Math.max(0, Math.min(1, faits / total));
    return '<span class="mp-ring" role="img" aria-label="'
      + esc(faits) + ' worker' + (faits > 1 ? 's' : '') + ' terminé'
      + (faits > 1 ? 's' : '') + ' sur ' + esc(total) + '">'
      + '<svg viewBox="0 0 24 24" aria-hidden="true">'
      + '<circle class="mp-ring-b" cx="12" cy="12" r="' + r + '"/>'
      + '<circle class="mp-ring-f" cx="12" cy="12" r="' + r + '"'
      + ' stroke-dasharray="' + (c * part).toFixed(2) + ' ' + c.toFixed(2) + '"'
      + ' transform="rotate(-90 12 12)"/>'
      + '</svg>'
      + '<b class="mp-mono">' + esc(faits) + '<i>/</i>' + esc(total) + '</b>'
      + '</span>';
  }

  /* Rail de budget : la part du temps alloue deja consommee. Il repond a une
   * question que le compte a rebours seul ne repond pas — « 12 minutes », est-ce
   * la fin d'une heure ou le debut d'une journee ? */
  function rail(b) {
    if (!b) return '';
    return '<div class="mp-rail-b" role="img" aria-label="'
      + esc(b.pct) + ' % du temps alloué consommé">'
      + '<i style="width:' + esc(b.pct) + '%"></i></div>';
  }

  /* Battement : les douze dernieres traces, une encoche chacune. La seule
   * marque qui dise si ca AVANCE ou si ca repete. Muette par conception —
   * c'est de la texture, pas un graphique. */
  function battement(w) {
    var fil = w.trail || [];
    if (fil.length < 2) return '';
    // Le fil arrive du plus recent au plus ancien : on le remet dans le sens
    // de la lecture, gauche a droite.
    var enc = fil.slice().reverse().map(function (e) {
      var k = 'n';
      if (e.status === 'error') k = 'k';
      else if (e.stage === 'codeagent_wait_start') k = 'w';
      else if (e.stage === 'codeagent_iteration') k = 'o';
      return '<i class="mp-beat-' + k + '"></i>';
    }).join('');
    return '<span class="mp-beat" role="img" aria-label="'
      + esc(fil.length) + ' dernières traces">' + enc + '</span>';
  }

  function chips(w, prefs) {
    var perimetre = (w && Array.isArray(w.perimeter)) ? w.perimeter : [];
    if (!_on(prefs, 'perimeter') || !perimetre.length) return '';
    return '<div class="mp-chips">' + perimetre.map(function (f) {
      return '<span class="mp-chip">' + esc(f) + '</span>';
    }).join('') + '</div>';
  }

  function pensee(w, prefs) {
    if (!_on(prefs, 'thought')) return '';
    if (!w.thought) {
      // Une ligne, pas un pave. Vu au navigateur : cinq cartes sur onze
      // etaient des placeholders aussi hauts qu'un vrai raisonnement, et
      // l'ecran disait surtout du vide.
      return '<div class="mp-thought mp-thought-empty">'
        + esc(w.state === 'waiting'
          ? 'en attente du CodeAgent'
          : (w.state === 'stalled'
             ? 'interrompue au redémarrage — attend une revue'
             : (w.state === 'cancelled' ? 'annulée'
                : (w.state === 'done' ? 'terminée' : 'aucun raisonnement transmis'))))
        + '</div>';
    }
    return '<div class="mp-thought">' + esc(w.thought) + '</div>';
  }

  /* Journal brut — la case du menu Affichage qui ne pilotait rien.
   *
   * Repliee par defaut : c'est un outil de diagnostic, pas une lecture. On
   * montre ce que le flux a vraiment dit, sans reformuler — c'est tout
   * l'interet d'un journal BRUT, et la demande d'origine de ce chantier etait
   * justement « on ne voit pas leurs pensees alors qu'elles sont dans les
   * logs ». */
  function journal(w, prefs) {
    if (!_on(prefs, 'rawlog')) return '';
    var fil = w.logs || w.trail || [];
    // L'absence vit DANS le repli, pas au-dessus. La premiere version posait
    // une phrase visible en permanence : vu au navigateur, la carte du lead
    // empilait trois lignes de vide — « aucun raisonnement transmis », puis
    // le titre du journal, puis « aucune trace reçue ». Un trou nomme reste
    // nomme ; il n'a pas besoin d'occuper l'ecran pour cela.
    if (!fil.length) {
      return '<details class="mp-log"><summary>Journal brut · 0</summary>'
        + '<p class="mp-log-vide">Aucune trace reçue pour cette tâche.</p></details>';
    }
    var lignes = fil.map(function (e) {
      var bouts = [esc(e.stage || '—')];
      if (e.tool) bouts.push(esc(e.tool));
      if (e.summary) bouts.push(esc(e.summary));
      return '<li class="mp-log-l' + (e.status === 'error' ? ' is-ko' : '') + '">'
        + bouts.join('<i class="mp-sep">·</i>') + '</li>';
    }).join('');
    return '<details class="mp-log"><summary>Journal brut · '
      + esc(w.events || fil.length) + '</summary>'
      + '<ol class="mp-log-list">' + lignes + '</ol></details>';
  }

  /* Activite lisible sans ouvrir le journal de diagnostic. Une mission peut
   * travailler entierement dans le lead, sans aucun worker : dans ce cas ces
   * lignes sont la seule reponse honnete a « que se passe-t-il ? ». */
  function heureTrace(ts) {
    var s = String(ts || '');
    var m = /T(\d{2}:\d{2}:\d{2})/.exec(s);
    return m ? m[1] : '';
  }

  function activiteRecente(w, limite, montrerVide) {
    var fil = (w && (w.logs || w.trail)) || [];
    if (!fil.length) {
      return montrerVide
        ? '<div class="mp-live-empty">Aucune télémétrie récente pour cette tâche.</div>'
        : '';
    }
    var lignes = fil.slice(0, limite || 4).map(function (e) {
      var titre = e.tool || e.stage || 'activité';
      return '<li class="mp-live-line' + (e.status === 'error' ? ' is-ko' : '') + '">'
        + '<time class="mp-mono">' + esc(heureTrace(e.ts)) + '</time>'
        + '<span class="mp-live-stage">' + esc(titre) + '</span>'
        + (e.summary ? '<span class="mp-live-summary">' + esc(e.summary) + '</span>' : '')
        + '</li>';
    }).join('');
    return '<div class="mp-live"><span class="mp-live-title">Activité récente · '
      + esc(w.events || fil.length) + '</span><ol>' + lignes + '</ol></div>';
  }

  function pied(w, prefs) {
    var bouts = [];
    if (w.maxIter) {
      bouts.push('<span class="mp-mono">itér. ' + esc(w.iteration) + '/' + esc(w.maxIter) + '</span>');
    }
    if (w.lastTool) bouts.push('<span>' + esc(w.lastTool) + '</span>');
    if (_on(prefs, 'queue') && w.queueRank) {
      bouts.push('<span class="mp-queue">file · ' + esc(w.queueRank) + '<sup>e</sup></span>');
    }
    var bat = battement(w);
    if (!bouts.length && !bat) return '';
    return '<div class="mp-foot">'
      + bouts.join('<i class="mp-sep">·</i>')
      + (bat ? '<span class="mp-foot-sp"></span>' + bat : '')
      + '</div>';
  }

  function carte(w, prefs, role, options) {
    var opt = options || {};
    var selectable = opt.selectable !== false;
    var e = ETATS[w.state] || ETATS.running;
    var choisi = !!(selectable && prefs && prefs.selectedWorker && prefs.selectedWorker === w.id);
    return '<article class="mp-post' + (choisi ? ' is-selected' : '') + '"'
      + ' data-state="' + esc(w.state) + '"'
      + (selectable ? ' data-mp-worker="' + esc(w.id) + '" tabindex="0" role="button"'
          + ' aria-pressed="' + (choisi ? 'true' : 'false') + '"' : '') + '>'
      + '<header class="mp-post-head">'
      + '<span class="mp-dot mp-dot-' + e.cls + '" title="' + esc(e.lib) + '"></span>'
      + '<span class="mp-post-name">' + esc(role || w.objective || w.id) + '</span>'
      + '<span class="mp-post-state">' + esc(e.lib) + '</span>'
      + '</header>'
      + chips(w, prefs)
      + pensee(w, prefs)
      + activiteRecente(w, opt.activityLimit || 3, opt.showActivityEmpty === true)
      + journal(w, prefs)
      + pied(w, prefs)
      + '</article>';
  }

  /* Nom court du worker : « [Worker w_api] … » -> « w_api ». Sinon, l'id. */
  function nomWorker(w) {
    var m = /^\s*\[\s*Worker\s+([^\]]+)\]/i.exec(w.objective || '');
    if (m) return m[1].trim();
    return w.id ? w.id.slice(0, 14) : 'worker';
  }

  /* Libelle de l'etat agrege d'une mission. `late` n'existe pas au niveau
   * worker : c'est un fait de mission, et le plus grave de tous. */
  var AGREGATS = {
    late: 'échéance dépassée',
    failed: 'un worker en échec',
    stalled: 'interrompue — attend une revue',
    waiting: 'tout attend le CodeAgent',
    cancelled: 'annulée',
    done: 'terminée',
    running: 'en cours'
  };

  /* Bandeau de synthese — n'apparait qu'a partir de DEUX missions.
   *
   * Avec une seule mission il ne dirait rien que l'en-tete ne dise deja ;
   * avec cinq, c'est la seule chose qui reponde en deux secondes a « laquelle
   * demande mon attention ». Les missions en difficulte sont nommees, pas
   * comptees : un compteur n'aide personne a choisir ou regarder.
   */
  function synthese(missions) {
    var list = Array.isArray(missions) ? missions : [];
    if (list.length < 2) return '';
    var ennuis = list.filter(function (m) {
      var a = m.aggregate || 'running';
      return a === 'late' || a === 'failed' || a === 'stalled' || a === 'waiting';
    });
    var workers = 0, enFile = 0;
    list.forEach(function (m) {
      (m.children || []).forEach(function (w) {
        if (w.state !== 'done' && w.state !== 'failed') workers++;
        if (w.queueRank) enFile++;
      });
    });
    var puces = ennuis.map(function (m) {
      return '<span class="mp-sum-bad" data-agg="' + esc(m.aggregate) + '">'
        + '<i class="mp-agg-dot"></i>' + esc(AGREGATS[m.aggregate]) + '</span>';
    }).join('');
    return '<div class="mp-sum">'
      + '<span class="mp-sum-n"><b class="mp-mono">' + esc(list.length) + '</b> missions</span>'
      + '<span class="mp-sum-n"><b class="mp-mono">' + esc(workers) + '</b> workers actifs</span>'
      + (enFile ? '<span class="mp-sum-n"><b class="mp-mono">' + esc(enFile)
                  + '</b> en file</span>' : '')
      + (puces ? '<div class="mp-sum-bads">' + puces + '</div>'
               : '<span class="mp-sum-ok">aucune mission en difficulté</span>')
      + '</div>';
  }

  /* ── Le repli d'une mission ──────────────────────────────────────────────
   *
   * Demande devant l'ecran : deux missions font onze cartes, et rien ne
   * permettait d'en fermer une.
   *
   * La REGLE par defaut : une mission close (terminee ou annulee) se replie
   * toute seule — elle n'a plus rien a montrer qui vaille la moitie de
   * l'ecran. Une mission vivante reste ouverte.
   *
   * Le CHOIX de l'utilisateur gagne toujours contre la regle : `prefs.folded`
   * ne contient que des decisions explicites, et l'absence d'entree signifie
   * « pas encore decide », jamais « deplie ».
   */
  function estReplie(m, prefs) {
    var f = (prefs && prefs.folded) || {};
    var choix = f[m.id];
    if (typeof choix === 'boolean') return choix;
    var a = m.aggregate || 'running';
    return (a === 'done' || a === 'cancelled');
  }

  /* Enveloppe commune aux quatre vues. Quand la mission est repliee, le corps
   * n'est pas RENDU du tout : ce n'est pas un `display:none`, c'est du travail
   * qu'on ne fait pas. Avec dix missions closes, la difference est reelle. */
  function section(m, prefs, vue, corps) {
    var replie = estReplie(m, prefs);
    // `data-terminal` sert au chassis : c'est en depliant une mission FINIE
    // qu'il va chercher son journal archive. Sans ce marqueur il devrait
    // relire le modele depuis le DOM, ce qui est toujours une mauvaise idee.
    return '<section class="mp-mission" data-view="' + esc(vue) + '"'
      + ' data-mission="' + esc(m.id) + '"'
      + (m.terminal ? ' data-terminal="1"' : '')
      + (replie ? ' data-folded="1"' : '') + '>'
      + enTete(m, prefs, replie)
      + (replie ? resumeReplie(m, prefs) : corps())
      + '</section>';
  }

  /* Poids lisible. « 12 452 » ne dit rien ; « 12 Ko » se lit d'un coup. */
  function poids(o) {
    var n = Number(o) || 0;
    if (n <= 0) return '';
    if (n < 1024) return n + ' o';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' Ko';
    return (n / (1024 * 1024)).toFixed(1).replace('.', ',') + ' Mo';
  }

  /* Repliee, la mission garde une LIGNE de fait : combien de workers, combien
   * en file. Un titre seul ne dirait pas s'il se passe quelque chose dedans. */
  function resumeReplie(m, prefs) {
    var kids = m.children || [];
    if (!kids.length) {
      // Une mission solo — 65 racines sur 199 — n'a pas de worker a compter,
      // mais elle peut avoir un journal. Le taire serait perdre le seul fait
      // qu'elle a a offrir une fois repliee.
      var seul = poids(m.archiveBytes);
      return seul
        ? '<div class="mp-fold-sum"><span class="mp-arch">journal · '
          + esc(seul) + '</span></div>'
        : '';
    }
    var enFile = kids.filter(function (w) { return w.queueRank; }).length;
    var actifs = kids.filter(function (w) {
      return w.state === 'running' || w.state === 'waiting';
    }).length;
    var bouts = [esc(kids.length) + (kids.length > 1 ? ' workers' : ' worker')];
    if (actifs) bouts.push('<b>' + esc(actifs) + '</b> actif' + (actifs > 1 ? 's' : ''));
    if (enFile && _on(prefs, 'queue')) {
      bouts.push('<b>' + esc(enFile) + '</b> en file');
    }
    // L'ARCHIVE se signale. Devant une liste de missions finies, rien ne
    // distinguait celles qui gardent une trace de celles qui sont muettes :
    // il fallait deplier chacune pour le decouvrir.
    var p = poids(m.archiveBytes);
    if (p) bouts.push('<span class="mp-arch">journal · ' + esc(p) + '</span>');
    return '<div class="mp-fold-sum">' + bouts.join('<i class="mp-sep">·</i>') + '</div>';
  }

  function enTete(mission, prefs, replie) {
    var bits = [];
    if (_on(prefs, 'countdown') && mission.deadlineLabel) {
      var tard = (mission.remainingMs != null && mission.remainingMs < 0);
      bits.push('<span class="mp-countdown' + (tard ? ' is-late' : '') + '">'
        + '<b class="mp-mono">' + esc(mission.deadlineLabel) + '</b>'
        + '<em>' + (tard ? 'dépassée' : 'restant') + '</em>'
        + rail(mission.budget) + '</span>');
    }
    if (mission.workers && mission.workers.total) {
      bits.push(anneau(mission.workers.done, mission.workers.total));
    }
    // ARRETER LA MISSION. Le handler `cancelMissionUi` n'a jamais cesse
    // d'exister ni de marcher : c'est le BOUTON qui a disparu quand le rendu
    // v2 a remplace l'ancien. Une capacite orpheline, exactement le motif que
    // ce panneau passe son temps a fermer ailleurs.
    // `terminal`, pas `closed` : un echec ne se replie pas mais ne s'arrete
    // pas non plus — il est deja arrete.
    if (!mission.terminal) {
      bits.push('<button type="button" class="mp-stop" data-mp-cancel="'
        + esc(mission.id) + '" title="Arrêter cette mission — elle s’arrête au '
        + 'prochain checkpoint, jamais en plein milieu">Arrêter</button>');
    }
    // L'agregat teinte l'en-tete ENTIER : c'est ce qui permet de distinguer
    // deux missions l'une de l'autre sans lire un seul chiffre.
    var agg = mission.aggregate || 'running';
    var objectifLong = String(mission.objective || '').length > 280;
    var bouton = '<button type="button" class="mp-fold" data-mp-fold="'
      + esc(mission.id) + '" aria-expanded="' + (replie ? 'false' : 'true')
      + '" title="' + (replie ? 'Déplier cette mission' : 'Replier cette mission')
      + '"><span class="mp-fold-ic" aria-hidden="true">' + (replie ? '▸' : '▾')
      + '</span></button>';
    return '<header class="mp-head" data-agg="' + esc(agg) + '">'
      + bouton
      + '<div class="mp-head-main">'
      + '<span class="mp-agg"><i class="mp-agg-dot"></i>'
      + esc(AGREGATS[agg] || AGREGATS.running) + '</span>'
      + '<h3 class="mp-obj" title="' + esc(mission.objective) + '">'
      + esc(mission.objective) + '</h3>'
      + (objectifLong && !replie
          ? '<details class="mp-objective-full"><summary>Objectif complet</summary>'
            + '<p>' + esc(mission.objective) + '</p></details>'
          : '')
      + '</div>'
      + (bits.length ? '<div class="mp-head-meta">' + bits.join('') + '</div>' : '')
      + '</header>';
  }

  function bandeauFile(mission, prefs) {
    if (!_on(prefs, 'queue')) return '';
    var enFile = (mission.children || []).filter(function (w) { return w.queueRank; })
      .sort(function (a, b) { return a.queueRank - b.queueRank; });
    var actif = (mission.children || []).filter(function (w) {
      return w.state === 'running' && !w.queueRank;
    })[0];
    if (!enFile.length && !actif) return '';
    var jetons = [];
    if (actif) jetons.push('<span class="mp-token is-active">' + esc(nomWorker(actif)) + '</span>');
    enFile.forEach(function (w) {
      jetons.push('<span class="mp-token">' + esc(nomWorker(w)) + '</span>');
    });
    return '<div class="mp-queue-bar">'
      + '<span class="mp-queue-label">CodeAgent · sérialisé</span>'
      + '<div class="mp-queue-list">' + jetons.join('<i class="mp-arrow">›</i>') + '</div>'
      + '</div>';
  }

  /* Vue C — Atelier. */
  function renderWorkshop(missions, prefs) {
    var list = Array.isArray(missions) ? missions : [];
    if (!list.length) {
      return '<div class="mp-empty">Aucune mission en cours.</div>';
    }
    return synthese(list) + list.map(function (m) {
      var kids = m.children || [];
      var postes = kids.map(function (w) { return carte(w, prefs, nomWorker(w)); }).join('');
      // La mission EST le lead. La reconstruire champ par champ supprimait
      // ses logs, son trail et son compteur d'evenements : Journal brut · 0
      // alors que sa pensee etait visible juste au-dessus.
      var lead = carte(m, prefs, 'Lead', {
        selectable: false, activityLimit: 6, showActivityEmpty: true
      });
      return section(m, prefs, 'workshop', function () {
        return bandeauFile(m, prefs)
          + '<div class="mp-grid">' + lead + postes + '</div>';
      });
    }).join('');
  }

  /* ══════════════════════════════════════════════════════════════════════
   *  Vue B — RUBAN
   *
   *  Une piste par worker. C'est la SEULE vue qui rend visible la
   *  serialisation du CodeAgent : la part hachuree est le temps passe a
   *  attendre son tour, pas a travailler.
   *
   *  Honnetete : on ne dessine que ce qu'on MESURE. `waitedMs` vient des
   *  evenements `codeagent_wait_*` (lot 0.c) ; sans eux la piste reste pleine
   *  au lieu d'inventer une chronologie.
   * ══════════════════════════════════════════════════════════════════════ */

  function pisteWorker(w, totalMs, libelle, estLead) {
    var att = Math.max(0, w.waitedMs || 0);
    var base = Math.max(totalMs, att, 1);
    var pctAtt = Math.min(100, Math.round((att / base) * 100));
    var pctTrav = 100 - pctAtt;
    var e = ETATS[w.state] || ETATS.running;
    var segs = '';
    if (pctTrav > 0) {
      segs += '<i class="mp-seg mp-seg-' + e.cls + '" style="flex:' + pctTrav + '"></i>';
    }
    if (pctAtt > 0) {
      segs += '<i class="mp-seg mp-seg-wait" style="flex:' + pctAtt + '"'
        + ' title="' + esc(Math.round(att / 1000)) + ' s d attente"></i>';
    }
    return '<div class="mp-lane' + (estLead ? ' mp-lane-lead' : '') + '">'
      + '<span class="mp-lane-name mp-mono">' + esc(libelle || nomWorker(w)) + '</span>'
      + '<div class="mp-rail">' + segs + '</div>'
      + '<span class="mp-lane-meta mp-mono">'
      + (att ? esc(Math.round(att / 1000)) + 's att.'
             : (w.maxIter ? esc(w.iteration) + '/' + esc(w.maxIter) : ''))
      + '</span></div>';
  }

  function renderRibbon(missions, prefs) {
    var list = Array.isArray(missions) ? missions : [];
    if (!list.length) return '<div class="mp-empty">Aucune mission en cours.</div>';
    return synthese(list) + list.map(function (m) {
      var kids = m.children || [];
      var maxAtt = 0;
      kids.forEach(function (w) { if (w.waitedMs > maxAtt) maxAtt = w.waitedMs; });
      var pistes = pisteWorker(m, maxAtt * 1.6 || 1, 'Lead', true)
        + kids.map(function (w) { return pisteWorker(w, maxAtt * 1.6 || 1); }).join('');
      var cumul = 0;
      kids.forEach(function (w) { cumul += (w.waitedMs || 0); });
      var note = cumul
        ? '<p class="mp-lane-note">Attente cumulée dans la file du CodeAgent : '
          + '<b class="mp-mono">' + esc(Math.round(cumul / 1000)) + ' s</b> — '
          + 'les workers réfléchissent en parallèle mais codent chacun leur tour.</p>'
        : '<p class="mp-lane-note">Aucune attente mesurée pour l instant.</p>';
      return section(m, prefs, 'ribbon', function () {
        return bandeauFile(m, prefs)
          + '<div class="mp-ribbon-lead"><div class="mp-ribbon-lead-head">'
          + '<span>Lead de mission</span>' + pied(m, prefs) + '</div>'
          + pensee(m, prefs) + activiteRecente(m, 6, true) + '</div>'
          + '<div class="mp-lanes">' + pistes
          + '<div class="mp-legend">'
          + '<span><i class="mp-key mp-seg-on"></i>travail</span>'
          + '<span><i class="mp-key mp-seg-wait"></i>attente du CodeAgent</span>'
          + '<span><i class="mp-key mp-seg-done"></i>validé</span>'
          + '</div>' + note + '</div>';
      });
    }).join('');
  }

  /* ══════════════════════════════════════════════════════════════════════
   *  Vue A — CONTROLE
   *
   *  Trois colonnes : plan, workers, preuves. DEGRADATION GRACIEUSE assumee :
   *  le plan du lead et le ledger ne sont pas encore exposes par l'API (lot
   *  5.a du plan, isole parce qu'il touche `react.py`). Les colonnes le
   *  DISENT au lieu de faire semblant — c'est la regle de la maison.
   * ══════════════════════════════════════════════════════════════════════ */

  function ligneControle(w, prefs, libelle, selectable) {
    if (selectable == null) selectable = true;
    var e = ETATS[w.state] || ETATS.running;
    var pct = w.maxIter ? Math.min(100, Math.round((w.iteration / w.maxIter) * 100)) : 0;
    var choisi = !!(selectable && prefs && prefs.selectedWorker && prefs.selectedWorker === w.id);
    return '<div class="mp-row' + (choisi ? ' is-selected' : '') + '"'
      + (selectable ? ' data-mp-worker="' + esc(w.id) + '" tabindex="0" role="button"'
          + ' aria-pressed="' + (choisi ? 'true' : 'false') + '"' : '') + '>'
      + '<div class="mp-row-head">'
      + '<span class="mp-dot mp-dot-' + e.cls + '"></span>'
      + '<span class="mp-row-name">' + esc(libelle || nomWorker(w)) + '</span>'
      + '<span class="mp-gauge"><i style="width:' + pct + '%"></i></span>'
      + '<span class="mp-mono mp-row-iter">'
      + (w.maxIter ? esc(w.iteration) + '/' + esc(w.maxIter) : '—') + '</span>'
      + '</div>'
      + pensee(w, prefs)
      + activiteRecente(w, selectable ? 3 : 6, selectable === false)
      + journal(w, prefs)
      // Le pied s'affiche des qu'il y a QUELQUE CHOSE a dire. La premiere
      // version le conditionnait au dernier outil : un worker en attente, qui
      // n'en a pas encore appele, perdait son rang dans la file — pile
      // l'information qui explique pourquoi il ne fait rien.
      + ((w.lastTool || w.queueRank)
          ? '<div class="mp-row-foot">'
            + (w.lastTool ? esc(w.lastTool) : '')
            + (w.lastTool && w.queueRank ? ' · ' : '')
            + (w.queueRank ? '<b>file ' + esc(w.queueRank) + '<sup>e</sup></b>' : '')
            + '</div>'
          : '')
      + '</div>';
  }

  function colonneAbsente(titre, raison) {
    return '<div class="mp-col"><span class="mp-col-title">' + esc(titre) + '</span>'
      + '<p class="mp-col-none">' + esc(raison) + '</p></div>';
  }

  /* Sparkline de la tuile Progression — la tendance des actions du ledger.
   *
   * Une seule serie, donc AUCUNE legende : le libelle de la tuile la nomme.
   * Pas d'axes, pas de grille — c'est une sparkline, pas un graphique.
   *
   * Une seule teinte en DEUX intensites : `--et` attenue pour le trace et
   * l'aire, plein pour le dernier point. Mesure du contraste sur `--card` :
   * `--muted-strong` tombe a 2,13:1, sous le seuil de 3:1, donc inutilisable
   * pour un trait de 1,5 px.
   *
   * `vector-effect="non-scaling-stroke"` : le SVG s'etire en largeur, le trait
   * ne doit pas s'epaissir avec lui.
   *
   * Aucune infobulle : les valeurs sont DEJA lisibles a cote — le compteur
   * d'actions de la tuile, et le journal brut en dessous. Une infobulle qui
   * serait le seul acces a une valeur serait une faute ; ici elle serait un
   * doublon.
   */
  function sparkline(tl) {
    if (!tl || tl.points.length < 2) return '';
    var W = 120, H = 22, P = 2;
    var n = tl.points.length;
    var etendue = Math.max(1, tl.max - tl.min);
    var xy = tl.points.map(function (p, i) {
      var x = P + (i * (W - 2 * P)) / (n - 1);
      var y = H - P - ((p.actions - tl.min) / etendue) * (H - 2 * P);
      return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
    });
    var trace = xy.map(function (c, i) {
      return (i ? 'L' : 'M') + c[0] + ' ' + c[1];
    }).join(' ');
    var aire = trace + ' L' + xy[n - 1][0] + ' ' + H + ' L' + xy[0][0] + ' ' + H + ' Z';
    var fin = xy[n - 1];
    var mot = (tl.phaseFinale === 'done') ? 'terminée'
      : (tl.max > tl.min ? 'en progression' : 'à l’arrêt');
    return '<span class="mp-spark" role="img" aria-label="'
      + esc(tl.total) + ' points de reprise, de ' + esc(tl.min) + ' à '
      + esc(tl.max) + ' actions — ' + mot + '">'
      + '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none"'
      + ' aria-hidden="true">'
      + '<path class="mp-spark-a" d="' + aire + '"/>'
      + '<path class="mp-spark-l" d="' + trace + '"'
      + ' vector-effect="non-scaling-stroke"/>'
      + '<circle class="mp-spark-p" cx="' + fin[0] + '" cy="' + fin[1] + '" r="2.4"/>'
      + '</svg></span>';
  }

  /* ── Colonne 1 : la PROGRESSION, pas le plan ─────────────────────────────
   *
   * Il n'y a pas de plan dans les donnees — le plan du lead vit dans la boucle
   * ReAct et n'est persiste nulle part. Cette moitie de l'ancienne annonce
   * etait juste. L'autre moitie etait fausse : le LEDGER, lui, est sur le
   * disque depuis toujours (`last_checkpoint.ledger`) et `/api/missions` le
   * transmettait deja. La colonne montre donc ce qui existe et nomme ce qui
   * n'existe pas, au lieu de tout declarer absent.
   */
  function colonneProgression(m) {
    // La note d'honnetete est HORS des deux branches. Sa premiere version ne
    // vivait que dans la branche « il y a un ledger » : une mission qui vient
    // de demarrer perdait l'aveu, c'est-a-dire exactement au moment ou l'on
    // pourrait croire que le plan va s'afficher.
    var note = '<p class="mp-col-note">Le plan du lead n’est persisté nulle part : '
      + 'ceci est la progression réelle, pas une intention.</p>';
    var l = m.ledger;
    if (!l) {
      return '<div class="mp-col"><span class="mp-col-title">Progression</span>'
        + '<p class="mp-col-none">Aucun point de reprise encore écrit : '
        + 'la mission vient de démarrer.</p>' + note + '</div>';
    }
    var lignes = l.recent.map(function (a) {
      return '<div class="mp-act' + (a.success ? '' : ' is-ko') + '">'
        + '<span class="mp-act-ic">' + (a.success ? '●' : '✕') + '</span>'
        + '<span class="mp-act-n">' + esc(a.action) + '</span>'
        + (a.target ? '<span class="mp-act-t">' + esc(a.target) + '</span>' : '')
        + '</div>';
    }).join('');
    return '<div class="mp-col"><span class="mp-col-title">Progression</span>'
      + '<div class="mp-kpis">'
      + '<span class="mp-kpi"><b class="mp-mono">' + esc(l.actions) + '</b>'
      + (l.actions > 1 ? 'actions' : 'action') + '</span>'
      + '<span class="mp-kpi"><b class="mp-mono">' + esc(l.mutations) + '</b>'
      + (l.mutations > 1 ? 'écritures' : 'écriture') + '</span>'
      + (l.successPct == null ? ''
         : '<span class="mp-kpi"><b class="mp-mono">' + esc(l.successPct)
           + '%</b>réussite</span>')
      + '</div>'
      + sparkline(m.timeline)
      + (lignes ? '<div class="mp-acts">' + lignes + '</div>'
                : '<p class="mp-col-none">Aucune action au ledger.</p>')
      + note
      + '</div>';
  }

  /* ── Colonne 3 : les PREUVES ─────────────────────────────────────────────
   *
   * Une preuve requise et non etablie se dit « non prouvé » : elle ne
   * disparait pas de la liste. Un trou nomme vaut mieux qu'une preuve
   * inventee, et une omission silencieuse est le defaut que le lot Z24 a
   * ferme cote runtime — il n'a pas a revenir par l'affichage.
   */
  function colonnePreuves(m) {
    var toutes = (m.proofs || []).slice();
    var liv = m.delivered || { published: [], artifacts: [] };
    (m.children || []).forEach(function (w) {
      (w.proofs || []).forEach(function (p) {
        // Une preuve de worker n'est montree que si elle manque : le lead
        // porte deja les preuves etablies de la mission.
        if (!p.ok) toutes.push({ cle: p.cle, lib: nomWorker(w) + ' · ' + p.lib, ok: false });
      });
    });
    if (!toutes.length && !liv.published.length && !liv.artifacts.length) {
      return colonneAbsente('Preuves',
        'Aucune preuve de complétion encore établie pour cette mission.');
    }
    var lignes = toutes.map(function (p) {
      return '<div class="mp-proof' + (p.ok ? '' : ' is-ko') + '">'
        + '<span class="mp-proof-ic">' + (p.ok ? '✔' : '✕') + '</span>'
        + esc(p.lib) + (p.ok ? '' : ' <em>non prouvé</em>') + '</div>';
    }).join('');
    var fichiers = liv.published.length
      ? '<div class="mp-deliv"><span class="mp-col-title">Publié · '
        + esc(liv.published.length) + '</span>'
        + liv.published.map(function (f) {
            return '<div class="mp-chip">' + esc(f) + '</div>';
          }).join('') + '</div>'
      : '';
    var arts = liv.artifacts.length
      ? '<div class="mp-deliv"><span class="mp-col-title">Livrables · '
        + esc(liv.artifacts.length) + '</span>'
        + liv.artifacts.map(function (a) {
            return '<div class="mp-chip" title="' + esc(a.full) + '">' + esc(a.nom) + '</div>';
          }).join('') + '</div>'
      : '';
    return '<div class="mp-col"><span class="mp-col-title">Preuves</span>'
      + (lignes || '<p class="mp-col-none">Aucune preuve établie.</p>')
      + fichiers + arts + '</div>';
  }

  function renderControl(missions, prefs) {
    var list = Array.isArray(missions) ? missions : [];
    if (!list.length) return '<div class="mp-empty">Aucune mission en cours.</div>';
    return synthese(list) + list.map(function (m) {
      var kids = m.children || [];
      var lignes = '<div class="mp-lead-row">'
        + ligneControle(m, prefs, 'Lead', false) + '</div>'
        + kids.map(function (w) { return ligneControle(w, prefs); }).join('');
      var perim = '';
      if (_on(prefs, 'perimeter')) {
        var tout = [];
        kids.forEach(function (w) {
          w.perimeter.forEach(function (f) { if (tout.indexOf(f) === -1) tout.push(f); });
        });
        // En BANDEAU sous les trois colonnes, plus en colonne : le perimetre
        // et les preuves se disputaient la meme place, et l'un chassait
        // l'autre — on n'avait jamais les deux.
        perim = tout.length
          ? '<div class="mp-ctrl-foot"><span class="mp-col-title">Périmètre d’écriture · '
            + esc(tout.length) + '</span><div class="mp-chips">'
            + tout.map(function (f) {
                return '<span class="mp-chip">' + esc(f) + '</span>';
              }).join('') + '</div></div>'
          : '';
      }
      return section(m, prefs, 'control', function () {
        return bandeauFile(m, prefs)
          + '<div class="mp-ctrl">'
          + colonneProgression(m)
          + '<div class="mp-col mp-col-wide"><span class="mp-col-title">Exécution · Lead'
          + (kids.length ? ' + ' + esc(kids.length) + ' worker' + (kids.length > 1 ? 's' : '') : '')
          + '</span>' + lignes + '</div>'
          + colonnePreuves(m)
          + '</div>'
          + perim;
      });
    }).join('');
  }

  var VUES_RENDU = {
    workshop: renderWorkshop,
    ribbon: renderRibbon,
    control: renderControl
  };

  function render(nom, missions, prefs) {
    return (VUES_RENDU[nom] || renderWorkshop)(missions, prefs);
  }

  var api = {
    renderWorkshop: renderWorkshop,
    renderRibbon: renderRibbon,
    renderControl: renderControl,
    render: render,
    synthese: synthese,
    estReplie: estReplie,
    anneau: anneau,
    sparkline: sparkline,
    poids: poids,
    battement: battement,
    VUES: VUES_RENDU,
    esc: esc,
    nomWorker: nomWorker
  };
  root.missionRenderWorkshop = renderWorkshop;
  root.missionRenderRibbon = renderRibbon;
  root.missionRenderControl = renderControl;
  root.missionRenderView = render;
  root.missionViewEsc = esc;
  root.missionSynthese = synthese;
  root.missionEstReplie = estReplie;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
