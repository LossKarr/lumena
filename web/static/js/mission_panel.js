/* Panel Missions — lot 3 : le CHASSIS.
 *
 * Assemble : preferences <- modele <- vues, et monte le tout dans
 * `#missions-list`. C'est la seule piece qui touche au DOM.
 *
 * --- Preferences ---
 *
 * Meme patron que `overview.js` (readLayout / saveLayout / renderCustomizer),
 * deja en place cote produit : une cle localStorage, une lecture defensive, des
 * cases a cocher. Rien d'invente.
 *
 * Lecture DEFENSIVE, non negociable : un stockage corrompu, un navigateur en
 * navigation privee ou un utilisateur qui a bloque le stockage local ne doivent
 * pas casser le panneau. Toute erreur retombe sur les valeurs d'usine.
 *
 * --- La feuille de style ---
 *
 * `mission-panel.css` est liee ICI, pas dans `index.html` : ce fichier fait
 * partie d'un chantier en cours et ne doit pas etre touche. La version est donc
 * pilotee cote JS, ce qui garde le cache correct sans dependre de lui.
 */
(function (root) {
  'use strict';

  var CLE = 'lumena_missions_panel';
  var CSS_VERSION = '5';

  var DEFAUTS = {
    view: 'workshop',
    density: 'standard',
    filter: 'all',
    query: '',
    selectedMission: '',
    selectedWorker: '',
    blocks: { thought: true, perimeter: true, queue: true, countdown: true, rawlog: false },
    // Choix EXPLICITES de repli, par mission. L'absence d'entree ne veut pas
    // dire « deplie » : elle veut dire « pas encore decide », et c'est la
    // regle par defaut qui tranche (une mission close se replie seule). Un
    // choix de l'utilisateur gagne toujours contre la regle.
    folded: {}
  };

  /* Borne du dictionnaire de repli : sans elle, une entree s'accumule par
   * mission vue, et le stockage local grossit sans fin. */
  var MAX_FOLD = 80;

  var BLOCS = [
    ['thought', 'Pensée de l’agent'],
    ['perimeter', 'Périmètre des fichiers'],
    ['queue', 'File du CodeAgent'],
    ['countdown', 'Compte à rebours'],
    ['rawlog', 'Journal brut']
  ];

  /* Une preference `view` deja ecrite et devenue inconnue (la Constellation a
   * ete retiree) retombe sur l'Atelier : `normalise` valide contre cette
   * liste. Rien a migrer, aucun ecran vide. */
  var VUES = [
    ['workshop', 'Atelier'],
    ['ribbon', 'Ruban'],
    ['control', 'Contrôle']
  ];

  /* ── Preferences (pur, testable hors navigateur) ───────────────────────── */

  function normalise(brut) {
    var p = {
      view: DEFAUTS.view,
      density: DEFAUTS.density,
      filter: DEFAUTS.filter,
      query: DEFAUTS.query,
      selectedMission: DEFAUTS.selectedMission,
      selectedWorker: DEFAUTS.selectedWorker,
      blocks: {},
      folded: {}
    };
    for (var k in DEFAUTS.blocks) {
      if (Object.prototype.hasOwnProperty.call(DEFAUTS.blocks, k)) p.blocks[k] = DEFAUTS.blocks[k];
    }
    if (!brut || typeof brut !== 'object') return p;
    var vues = VUES.map(function (v) { return v[0]; });
    if (vues.indexOf(brut.view) !== -1) p.view = brut.view;
    if (brut.density === 'compact' || brut.density === 'standard') p.density = brut.density;
    if (['all', 'attention', 'active', 'done'].indexOf(brut.filter) !== -1) {
      p.filter = brut.filter;
    }
    if (typeof brut.query === 'string') p.query = brut.query.slice(0, 160);
    if (typeof brut.selectedMission === 'string') p.selectedMission = brut.selectedMission.slice(0, 200);
    if (typeof brut.selectedWorker === 'string') p.selectedWorker = brut.selectedWorker.slice(0, 200);
    if (brut.blocks && typeof brut.blocks === 'object') {
      for (var b in p.blocks) {
        if (typeof brut.blocks[b] === 'boolean') p.blocks[b] = brut.blocks[b];
      }
    }
    if (brut.folded && typeof brut.folded === 'object') {
      var ids = Object.keys(brut.folded);
      // On garde les DERNIERES entrees : ce sont les missions recemment vues.
      if (ids.length > MAX_FOLD) ids = ids.slice(ids.length - MAX_FOLD);
      for (var i = 0; i < ids.length; i++) {
        var v = brut.folded[ids[i]];
        if (typeof v === 'boolean') p.folded[ids[i]] = v;
      }
    }
    return p;
  }

  function lirePrefs(store) {
    try {
      var s = store || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!s) return normalise(null);
      return normalise(JSON.parse(s.getItem(CLE) || 'null'));
    } catch (e) {
      return normalise(null);   // stockage illisible, refuse ou corrompu
    }
  }

  function ecrirePrefs(prefs, store) {
    try {
      var s = store || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!s) return false;
      s.setItem(CLE, JSON.stringify(normalise(prefs)));
      return true;
    } catch (e) {
      return false;             // quota plein, mode prive : on n'insiste pas
    }
  }

  /* ── Tampon d'evenements SSE ───────────────────────────────────────────── */
  /* Borne : une mission longue produit des milliers de traces, et on n'a besoin
   * que du DERNIER etat de chaque tache. */
  var MAX_EV = 400;
  var _events = [];

  function pousserEvenement(ev) {
    if (!ev || ev.task_id == null) return;
    _events.push(ev);
    if (_events.length > MAX_EV) _events.splice(0, _events.length - MAX_EV);
  }

  function viderEvenements() { _events.length = 0; }

  /* ── Journaux archives — ce qui reste quand la mission est finie ─────────
   *
   * Le flux SSE ne porte que le PRESENT : au rechargement de la page, une
   * mission terminee n'a plus ni pensee ni battement. Le serveur grave
   * desormais un journal par mission ; on le charge A LA DEMANDE, quand
   * l'utilisateur DEPLIE une mission close.
   *
   * Pourquoi a la demande : 188 des 199 missions du corpus sont terminales.
   * Les charger au rendu ferait 188 requetes pour rien. Deplier une mission,
   * c'est precisement dire « je veux regarder celle-ci ».
   */
  var _journaux = {};

  function poserJournal(id, evs) {
    if (!id) return false;
    _journaux[id] = Array.isArray(evs) ? evs : [];
    return true;
  }

  function journalCharge(id) {
    return !!id && Object.prototype.hasOwnProperty.call(_journaux, id);
  }

  function oublierJournaux() { _journaux = {}; }

  /* Le direct D'ABORD, l'archive ENSUITE : le modele garde la derniere valeur
   * vue pour chaque champ, donc un evenement live doit gagner contre sa copie
   * archivee. Une mission qui repart apres un rechargement affiche ce qu'elle
   * fait maintenant, pas ce qu'elle faisait avant. */
  function evenements() {
    var out = [];
    for (var id in _journaux) {
      if (Object.prototype.hasOwnProperty.call(_journaux, id)) {
        out = out.concat(_journaux[id]);
      }
    }
    return out.concat(_events);
  }

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function libelleEtat(aggregate) {
    return ({
      late: 'En retard', failed: 'Échec', stalled: 'À revoir', waiting: 'En attente',
      cancelled: 'Annulée', done: 'Validée', running: 'En cours'
    })[aggregate] || 'En cours';
  }

  function estAttention(mission) {
    return ['late', 'failed', 'stalled'].indexOf(mission.aggregate) !== -1;
  }

  function correspond(mission, prefs) {
    var agg = mission.aggregate || 'running';
    if (prefs.filter === 'attention' && !estAttention(mission)) return false;
    if (prefs.filter === 'active' && ['running', 'waiting'].indexOf(agg) === -1) return false;
    if (prefs.filter === 'done' && ['done', 'cancelled'].indexOf(agg) === -1) return false;
    var q = String(prefs.query || '').trim().toLocaleLowerCase();
    if (!q) return true;
    var texte = [mission.id, mission.objective, mission.thought];
    (mission.children || []).forEach(function (w) {
      texte.push(w.id, w.objective, w.thought, w.lastTool);
      (w.perimeter || []).forEach(function (f) { texte.push(f); });
    });
    return texte.join(' ').toLocaleLowerCase().indexOf(q) !== -1;
  }

  function choisirMission(missions, prefs) {
    if (!missions.length) return null;
    for (var i = 0; i < missions.length; i++) {
      if (missions[i].id === prefs.selectedMission) return missions[i];
    }
    var avecWorkers = function (m) { return (m.children || []).length > 0; };
    var attention = missions.filter(function (m) { return estAttention(m) && avecWorkers(m); })[0];
    if (attention) return attention;
    var active = missions.filter(function (m) {
      return (m.aggregate === 'running' || m.aggregate === 'waiting') && avecWorkers(m);
    })[0];
    return active || missions.filter(estAttention)[0]
      || missions.filter(function (m) {
        return m.aggregate === 'running' || m.aggregate === 'waiting';
      })[0] || missions[0];
  }

  function statistiques(missions) {
    var out = { total: missions.length, active: 0, attention: 0, done: 0, workers: 0 };
    missions.forEach(function (m) {
      out.workers += (m.children || []).length;
      if (estAttention(m)) out.attention += 1;
      else if (m.aggregate === 'done' || m.aggregate === 'cancelled') out.done += 1;
      else out.active += 1;
    });
    return out;
  }

  function rendreFiltres(prefs, stats) {
    var options = [
      ['all', 'Toutes'], ['attention', 'À examiner'], ['active', 'Actives'], ['done', 'Terminées']
    ].map(function (o) {
      return '<option value="' + o[0] + '"' + (prefs.filter === o[0] ? ' selected' : '')
        + '>' + o[1] + '</option>';
    }).join('');
    return '<div class="mp-command">'
      + '<label class="mp-search"><i data-lucide="search" aria-hidden="true"></i>'
      + '<input type="search" data-mp-query="1" value="' + esc(prefs.query) + '"'
      + ' placeholder="Rechercher une mission, un worker ou un fichier"'
      + ' aria-label="Rechercher dans les missions"></label>'
      + '<label class="mp-filter"><span>État</span><select data-mp-filter="1"'
      + ' aria-label="Filtrer les missions par état">' + options + '</select></label>'
      + '<div class="mp-fleet" aria-label="Résumé des missions">'
      + '<span><b class="mp-mono">' + esc(stats.active) + '</b> actives</span>'
      + '<span class="is-attention"><b class="mp-mono">' + esc(stats.attention) + '</b> à examiner</span>'
      + '<span><b class="mp-mono">' + esc(stats.workers) + '</b> workers</span>'
      + '</div></div>';
  }

  function rendreNavigateur(missions, prefs, selectedId) {
    if (!missions.length) {
      return '<aside class="mp-nav"><div class="mp-nav-empty">Aucune mission ne correspond.</div></aside>';
    }
    var boutons = missions.map(function (m) {
      var actif = m.id === selectedId;
      var workers = m.workers || { done: 0, total: (m.children || []).length };
      return '<button type="button" class="mp-nav-item' + (actif ? ' is-selected' : '') + '"'
        + ' data-mp-mission="' + esc(m.id) + '" aria-current="' + (actif ? 'true' : 'false') + '">'
        + '<i class="mp-agg-dot" data-agg="' + esc(m.aggregate || 'running') + '"></i>'
        + '<span class="mp-nav-copy"><b>' + esc(m.objective || m.id) + '</b>'
        + '<small>' + esc(libelleEtat(m.aggregate)) + ' · '
        + esc(workers.done || 0) + '/' + esc(workers.total || 0) + ' workers</small></span>'
        + (m.deadlineLabel ? '<span class="mp-nav-time mp-mono">' + esc(m.deadlineLabel) + '</span>' : '')
        + '</button>';
    }).join('');
    return '<aside class="mp-nav" aria-label="Liste des missions">'
      + '<div class="mp-nav-head"><span>Missions</span><b class="mp-mono">' + missions.length + '</b></div>'
      + '<div class="mp-nav-list">' + boutons + '</div></aside>';
  }

  /* ── Montage (seule partie qui touche au DOM) ──────────────────────────── */

  function lierFeuille(doc) {
    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d || d.getElementById('mp-css')) return false;
    var l = d.createElement('link');
    l.id = 'mp-css';
    l.rel = 'stylesheet';
    l.href = '/static/css/mission-panel.css?v=' + CSS_VERSION;
    (d.head || d.documentElement).appendChild(l);
    return true;
  }

  function rendreCustomizer(prefs) {
    // `aria-pressed` plutot que la seule classe `is-on` : un lecteur d'ecran
    // doit savoir QUELLE vue est active, pas seulement qu'il y a quatre boutons.
    var vues = VUES.map(function (v) {
      var actif = (prefs.view === v[0]);
      return '<button type="button" class="mp-tab' + (actif ? ' is-on' : '') + '"'
        + ' data-mp-view="' + v[0] + '" aria-pressed="' + (actif ? 'true' : 'false') + '">'
        + v[1] + '</button>';
    }).join('');
    var cases = BLOCS.map(function (b) {
      return '<label class="mp-toggle"><input type="checkbox" data-mp-block="' + b[0] + '"'
        + (prefs.blocks[b[0]] ? ' checked' : '') + '><span>' + b[1] + '</span></label>';
    }).join('');
    // Le bouton de remise a zero vit DANS le panneau des cases : la premiere
    // version le posait en absolu avec un `translateY` en dur, qui se decalait
    // des qu'on ajoutait ou retirait un bloc a la liste.
    return '<div class="mp-bar">'
      + '<div class="mp-tabs" role="group" aria-label="Vue du panneau missions">'
      + vues + '</div>'
      + '<button type="button" class="mp-tab mp-foldall" data-mp-foldall="1"'
      + ' title="Replier ou déplier toutes les missions">Tout replier</button>'
      + '<button type="button" class="mp-tab mp-density" data-mp-density="1"'
      + ' aria-pressed="' + (prefs.density === 'compact' ? 'true' : 'false') + '">Densité : '
      + (prefs.density === 'compact' ? 'compacte' : 'standard') + '</button>'
      + '<details class="mp-custo"><summary>Affichage</summary>'
      + '<div class="mp-toggles">' + cases
      + '<button type="button" class="mp-reset" data-mp-reset="1">Réinitialiser</button>'
      + '</div></details></div>';
  }

  function rendre(el, tree, prefs, nowMs) {
    if (!el) return;
    var p = normalise(prefs);
    var modele = root.buildMissionModel ? root.buildMissionModel(tree, evenements(), nowMs) : [];
    var visibles = modele.filter(function (m) { return correspond(m, p); });
    var selection = choisirMission(visibles, p);
    var effectif = normalise(p);
    effectif.selectedMission = selection ? selection.id : '';
    if (selection && effectif.selectedWorker) {
      var connu = (selection.children || []).some(function (w) { return w.id === effectif.selectedWorker; });
      if (!connu) effectif.selectedWorker = '';
    }
    // `missionRenderView` retombe sur l'Atelier si le nom est inconnu : une
    // preference ecrite par une version future ne peut pas vider l'ecran.
    var rendu = root.missionRenderView
      ? root.missionRenderView(effectif.view, selection ? [selection] : [], effectif)
      : (root.missionRenderWorkshop ? root.missionRenderWorkshop(selection ? [selection] : [], effectif) : '');
    if (root.missionScene && typeof root.missionScene.dispose === 'function') {
      root.missionScene.dispose(el);
    }
    el.classList.toggle('mp-compact', p.density === 'compact');
    el.innerHTML = '<div class="mp-shell">'
      + rendreFiltres(p, statistiques(modele))
      + rendreCustomizer(p)
      + '<div class="mp-workspace">'
      + rendreNavigateur(visibles, p, selection ? selection.id : '')
      + '<main class="mp-stage" aria-live="polite">' + rendu + '</main>'
      + '</div></div>';
    if (root.missionScene && typeof root.missionScene.mount === 'function') {
      root.missionScene.mount(el);
    }
  }

  var api = {
    CLE: CLE,
    DEFAUTS: DEFAUTS,
    BLOCS: BLOCS,
    VUES: VUES,
    normalise: normalise,
    lirePrefs: lirePrefs,
    ecrirePrefs: ecrirePrefs,
    pousserEvenement: pousserEvenement,
    viderEvenements: viderEvenements,
    evenements: evenements,
    poserJournal: poserJournal,
    journalCharge: journalCharge,
    oublierJournaux: oublierJournaux,
    lierFeuille: lierFeuille,
    rendreCustomizer: rendreCustomizer,
    rendreFiltres: rendreFiltres,
    rendreNavigateur: rendreNavigateur,
    correspond: correspond,
    choisirMission: choisirMission,
    statistiques: statistiques,
    rendre: rendre
  };

  root.missionPanel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
