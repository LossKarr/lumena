/* ============================================================
   WORKSPACES — Panel "Projets" CodeAgent
   ============================================================ */
import { esc, logC, loadingDots } from './utils.js';
import { switchPanel } from './navigation.js';

let _wsData = null;

export async function loadWorkspaces() {
  const container = document.getElementById('workspaces-list');
  if (container) container.innerHTML = loadingDots('Chargement des projets...');
  try {
    const r = await fetch(`${API_BASE}/api/workspaces`, {
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    _wsData = await r.json();
  } catch (e) {
    if (container) container.innerHTML = `<div style="color:var(--muted);padding:20px">Erreur: ${esc(e.message)}</div>`;
    return;
  }
  renderWorkspaces();
}

export function renderWorkspaces() {
  const container = document.getElementById('workspaces-list');
  if (!container || !_wsData) return;
  const workspaces = _wsData.workspaces || [];
  if (!workspaces.length) {
    container.innerHTML = '<div style="color:var(--muted);padding:24px;text-align:center">Aucun projet CodeAgent trouvé.<br><small>Demandez à Lumena de créer un projet !</small></div>';
    return;
  }
  container.innerHTML = workspaces.map(ws => _buildWsCard(ws)).join('');
}

function _buildWsCard(ws) {
  const liveHtml = ws.is_serving
    ? `<span style="background:var(--ok);color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">● EN LIVE</span>`
    : '';
  const indexBadge = ws.has_index_html
    ? `<span style="background:var(--accent);color:#fff;padding:2px 6px;border-radius:8px;font-size:11px">HTML</span>`
    : '';
  const pkgBadge = ws.has_package_json
    ? `<span style="background:#f39c12;color:#fff;padding:2px 6px;border-radius:8px;font-size:11px">NPM</span>`
    : '';
  const openBtn = ws.has_index_html
    ? `<button class="btn primary" style="font-size:12px;padding:5px 12px" onclick="serveAndOpenWorkspace('${esc(ws.slug)}')">▶ Ouvrir</button>`
    : '';
  const continueBtn = `<button class="btn" style="font-size:12px;padding:5px 12px" onclick="continueWorkspace('${esc(ws.slug)}','${esc(ws.path.replace(/\\/g,'/'))}')">↩ Continuer</button>`;
  const deleteBtn = `<button class="btn danger" style="font-size:12px;padding:5px 12px" onclick="deleteWorkspace('${esc(ws.slug)}')"><i data-lucide="trash-2" style="width:13px;height:13px;pointer-events:none"></i></button>`;
  const openLiveBtn = ws.is_serving && ws.serve_url
    ? `<a href="${esc(ws.serve_url)}" target="_blank" rel="noopener" class="btn ok" style="font-size:12px;padding:5px 12px;text-decoration:none">Voir</a>`
    : '';
  const stopBtn = ws.is_serving
    ? `<button class="btn" style="font-size:12px;padding:5px 12px;background:var(--danger);color:#fff;border-color:var(--danger)" onclick="stopWorkspace('${esc(ws.slug)}')">⏹ Arrêter</button>`
    : '';

  return `
<div class="card ws-card" id="ws-card-${esc(ws.slug)}" style="margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
    <div style="flex:1;min-width:0">
      <div style="font-weight:700;font-size:15px;margin-bottom:4px;display:flex;align-items:center;gap:8px">
        <span style="font-family:monospace">${esc(ws.slug)}</span>
        ${liveHtml}
      </div>
      <div style="color:var(--muted);font-size:12px;margin-bottom:6px">${esc(ws.date)} · ${ws.files_count} fichiers · ${ws.total_size_kb} KB</div>
      <div style="display:flex;gap:4px;flex-wrap:wrap">${indexBadge}${pkgBadge}</div>
    </div>
    <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">
      ${openLiveBtn}${stopBtn}${openBtn}${continueBtn}${deleteBtn}
    </div>
  </div>
</div>`;
}

export async function serveAndOpenWorkspace(slug) {
  logC(`▶ Serving workspace: ${slug}`, 'info');
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}/serve`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    window.open(data.url, '_blank', 'noopener');
    logC(`Workspace servi sur ${data.url}`, 'success');
    loadWorkspaces(); // refresh badge live
  } catch (e) {
    logC(`Erreur serve: ${e.message}`, 'error');
    alert(`Impossible de servir le workspace: ${e.message}`);
  }
}

export function continueWorkspace(slug, wsPath) {
  const msg = `Continue le projet "${slug}" situé dans ${wsPath}`;
  switchPanel('chat');
  const input = document.getElementById('message-input');
  if (input) {
    input.value = msg;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }
  logC(`↩ Continuer projet: ${slug}`, 'info');
}

export async function stopWorkspace(slug) {
  logC(`⏹ Arrêt serveur: ${slug}`, 'info');
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}/serve`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    logC(`Serveur "${slug}" arrêté`, 'success');
    loadWorkspaces(); // refresh pour retirer badge EN LIVE
  } catch (e) {
    logC(`Erreur arrêt: ${e.message}`, 'error');
    alert(`Impossible d'arrêter le serveur: ${e.message}`);
  }
}

export async function deleteWorkspace(slug) {
  if (!confirm(`Supprimer définitivement le workspace "${slug}" et tous ses fichiers ?`)) return;
  logC(`Suppression workspace: ${slug}`, 'info');
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    logC(`Workspace "${slug}" supprimé`, 'success');
    // Remove card from DOM
    const card = document.getElementById(`ws-card-${slug}`);
    if (card) card.remove();
    if (_wsData) {
      _wsData.workspaces = (_wsData.workspaces || []).filter(w => w.slug !== slug);
    }
  } catch (e) {
    logC(`Erreur suppression: ${e.message}`, 'error');
    alert(`Impossible de supprimer: ${e.message}`);
  }
}
