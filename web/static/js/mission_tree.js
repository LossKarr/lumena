/* Lot 5.4 — Logique PURE de l'arbre missions (lead → workers).
 *
 * Sans dépendance ni DOM → testable en isolation (node) et réutilisée par panels.js.
 * Chargé en script classique (UMD) : expose globalThis.buildMissionTree + module.exports.
 */
(function (root) {
  'use strict';

  var TERMINAL = { done: 1, failed: 1, cancelled: 1 };

  function _pid(m) {
    return (m && m.metadata && m.metadata.parent_id != null)
      ? String(m.metadata.parent_id) : null;
  }

  // True si `ancestorId` est un ancêtre de `startId` (remonte la chaîne parent_id).
  // Sert de garde anti-cycle : on n'attache pas un noeud sous un de ses descendants.
  function _isAncestor(nodes, ancestorId, startId) {
    var cur = startId;
    var seen = {};
    while (cur != null && !seen[cur]) {
      seen[cur] = 1;
      var node = nodes[cur];
      var pid = node ? _pid(node.mission) : null;
      if (pid === ancestorId) return true;
      cur = pid;
    }
    return false;
  }

  /* Construit l'arbre lead→workers à partir de la liste PLATE des missions.
   * - Racine = mission sans parent_id, OU dont le parent est absent de la liste.
   * - Chaque noeud : { mission, children: [...] } (récursif, gère petits-enfants).
   * - Ordre d'entrée préservé ; pas de doublon ; cycles/auto-parent ignorés (→ racine).
   */
  function buildMissionTree(missions) {
    var list = Array.isArray(missions) ? missions : [];
    var nodes = {};
    var i, m, id;
    for (i = 0; i < list.length; i++) {
      m = list[i];
      if (m && m.task_id != null) nodes[String(m.task_id)] = { mission: m, children: [] };
    }
    var roots = [];
    for (i = 0; i < list.length; i++) {
      m = list[i];
      if (!m || m.task_id == null) continue;
      id = String(m.task_id);
      var node = nodes[id];
      var pid = _pid(m);
      if (pid && pid !== id && nodes[pid] && !_isAncestor(nodes, id, pid)) {
        nodes[pid].children.push(node);
      } else {
        roots.push(node);
      }
    }
    return roots;
  }

  /* Avancement des workers directs d'un noeud : { done, total }.
   * done = workers en état terminal (done/failed/cancelled). */
  function workerProgress(node) {
    var kids = (node && node.children) || [];
    var done = 0;
    for (var i = 0; i < kids.length; i++) {
      var st = kids[i].mission && kids[i].mission.state;
      if (TERMINAL[st]) done++;
    }
    return { done: done, total: kids.length };
  }

  /* Durée écoulée (ms) : de created_at jusqu'à updated_at si terminal, sinon nowMs. */
  function missionElapsedMs(mission, nowMs) {
    if (!mission || !mission.created_at) return 0;
    var start = Date.parse(mission.created_at);
    if (isNaN(start)) return 0;
    var terminal = !!TERMINAL[mission.state];
    var end = terminal && mission.updated_at ? Date.parse(mission.updated_at) : (nowMs || Date.now());
    if (isNaN(end)) end = nowMs || Date.now();
    return Math.max(0, end - start);
  }

  function isActiveState(state) {
    return !TERMINAL[state];
  }

  var api = {
    buildMissionTree: buildMissionTree,
    workerProgress: workerProgress,
    missionElapsedMs: missionElapsedMs,
    isActiveState: isActiveState,
  };

  // Expose en global (script classique) ET en module (tests node).
  root.buildMissionTree = buildMissionTree;
  root.workerProgress = workerProgress;
  root.missionElapsedMs = missionElapsedMs;
  root.missionIsActiveState = isActiveState;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
