/* ============================================================
   WORKSPACES — Panel "Projets" CodeAgent
   ============================================================ */
import { esc, logC, loadingDots } from './utils.js';
import { switchPanel } from './navigation.js';

let _wsData = null;
let _wsSearch = '';
let _wsSort = 'date';
const _wsOpen = new Set();
const _wsTreeCache = {};

export async function loadWorkspaces() {
  const container = document.getElementById('workspaces-list');
  if (container) container.innerHTML = `<div style="padding:32px;text-align:center;color:var(--muted)">${loadingDots('Chargement des projets...')}</div>`;
  try {
    const r = await fetch(`${API_BASE}/api/workspaces`, {
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    _wsData = await r.json();
  } catch (e) {
    if (container) container.innerHTML = `<div style="color:var(--danger);padding:24px;text-align:center">Erreur: ${esc(e.message)}</div>`;
    return;
  }
  renderWorkspaces();
}

export function filterWorkspaces(q) {
  _wsSearch = q.trim().toLowerCase();
  renderWorkspaces();
}

export function sortWorkspaces(v) {
  _wsSort = v;
  renderWorkspaces();
}

export function renderWorkspaces() {
  const container = document.getElementById('workspaces-list');
  if (!container || !_wsData) return;

  let workspaces = (_wsData.workspaces || []).filter(ws => {
    if (!_wsSearch) return true;
    return ws.slug.toLowerCase().includes(_wsSearch) || ws.date.includes(_wsSearch)
      || (ws.tech_stack || []).some(t => t.toLowerCase().includes(_wsSearch));
  });

  if (_wsSort === 'name') workspaces = [...workspaces].sort((a, b) => a.slug.localeCompare(b.slug));
  else if (_wsSort === 'size') workspaces = [...workspaces].sort((a, b) => b.total_size_kb - a.total_size_kb);

  // Stats
  const statsEl = document.getElementById('ws-stats');
  if (statsEl) {
    const totalKb = (_wsData.workspaces || []).reduce((s, w) => s + w.total_size_kb, 0);
    const totalStr = totalKb >= 1024 ? `${(totalKb / 1024).toFixed(1)} MB` : `${totalKb.toFixed(0)} KB`;
    statsEl.innerHTML = `<span>${(_wsData.workspaces || []).length} projet${(_wsData.workspaces || []).length !== 1 ? 's' : ''}</span>
      <span class="ws-stats-sep">·</span><span>${totalStr} total</span>`;
  }

  if (!workspaces.length) {
    container.innerHTML = `<div class="ws-empty">
      <i data-lucide="folder-x"></i>
      <p>${_wsSearch ? 'Aucun résultat pour "' + esc(_wsSearch) + '"' : 'Aucun projet trouvé'}</p>
      <small>${_wsSearch ? '' : 'Demandez à Lumena de créer un projet !'}</small>
    </div>`;
    if (typeof lucide !== 'undefined') lucide.createIcons({ el: container });
    return;
  }

  let html = '';
  if (_wsSort !== 'date') {
    html = workspaces.map(ws => _buildCard(ws)).join('');
  } else {
    const groups = {};
    for (const ws of workspaces) (groups[ws.date] || (groups[ws.date] = [])).push(ws);
    html = Object.keys(groups).map(date => {
      const items = groups[date];
      return `<div class="ws-date-group">
        <div class="ws-date-hdr">
          <span class="ws-date-dot"></span>
          <span class="ws-date-label">${esc(_fmtDate(date))}</span>
          <span class="ws-date-count">${items.length} projet${items.length !== 1 ? 's' : ''}</span>
        </div>
        <div class="ws-date-items">${items.map(ws => _buildCard(ws)).join('')}</div>
      </div>`;
    }).join('');
  }
  container.innerHTML = html;
  if (typeof lucide !== 'undefined') lucide.createIcons({ el: container });
}

function _fmtDate(dateStr) {
  try {
    const d = new Date(dateStr);
    const diff = Math.floor((Date.now() - d) / 86400000);
    if (diff === 0) return "Aujourd'hui";
    if (diff === 1) return 'Hier';
    if (diff < 7) return `Il y a ${diff} jours`;
    return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch { return dateStr; }
}

const _TECH_META = {
  'Node.js':    { color: '#339933' },
  'HTML':       { color: '#e34c26' },
  'Python':     { color: '#3776ab' },
  'TypeScript': { color: '#3178c6' },
  'Rust':       { color: '#ce422b' },
  'Go':         { color: '#00acd7' },
  'Docker':     { color: '#2496ed' },
};

function _techBadges(ws) {
  return (ws.tech_stack || []).map(t => {
    const c = (_TECH_META[t] || {}).color || '#888';
    return `<span class="ws-tech" style="--tc:${c}">${esc(t)}</span>`;
  }).join('');
}

function _sizeLabel(kb) {
  if (kb === 0) return '0 KB';
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb} KB`;
}

const _FILE_ICONS = {
  html:'🌐', htm:'🌐', css:'🎨', scss:'🎨', less:'🎨',
  js:'📜', mjs:'📜', jsx:'⚛', ts:'📘', tsx:'⚛',
  py:'🐍', rs:'⚙', go:'◈', rb:'💎', php:'🐘',
  json:'{}', yaml:'📋', yml:'📋', toml:'📋', xml:'📋',
  md:'📝', txt:'📄', csv:'📊',
  png:'🖼', jpg:'🖼', jpeg:'🖼', gif:'🖼', svg:'🖼', webp:'🖼', ico:'🖼',
  mp4:'🎬', webm:'🎬', mp3:'🎵', wav:'🎵',
  zip:'📦', tar:'📦', gz:'📦',
  pdf:'📋', sh:'⬛', bat:'⬛', ps1:'⬛',
  dockerfile:'🐳',
};

function _fileIcon(name) {
  const lower = name.toLowerCase();
  if (lower === 'dockerfile') return '🐳';
  if (lower === 'makefile') return '⚙';
  if (lower === 'readme.md') return '📘';
  const ext = name.split('.').pop().toLowerCase();
  return _FILE_ICONS[ext] || '📄';
}

function _fmtBytes(b) {
  if (!b) return '—';
  if (b >= 1048576) return `${(b / 1048576).toFixed(1)} MB`;
  if (b >= 1024) return `${Math.round(b / 1024)} KB`;
  return `${b} B`;
}

function _buildFileTree(slug, files) {
  if (!files) return `<div class="ws-tree-loading"><i data-lucide="loader-2" style="width:13px;height:13px"></i> Chargement de l'arbre…</div>`;
  if (!files.length) return `<div class="ws-tree-empty">Dossier vide</div>`;

  const tree = {};
  for (const f of files) {
    const parts = f.path.split('/');
    const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
    (tree[dir] || (tree[dir] = [])).push({ name: parts[parts.length - 1], ...f });
  }

  let html = '<div class="ws-tree">';
  const rootFiles = tree[''] || [];
  for (const f of rootFiles) {
    html += `<div class="ws-tree-file">
      <span class="ws-file-icon">${_fileIcon(f.name)}</span>
      <span class="ws-file-name">${esc(f.name)}</span>
      <span class="ws-file-size">${_fmtBytes(f.size_bytes)}</span>
    </div>`;
  }
  const dirs = Object.keys(tree).filter(d => d !== '').sort();
  for (const dir of dirs) {
    html += `<div class="ws-tree-dir">
      <i data-lucide="folder" style="width:13px;height:13px"></i>
      <span class="ws-dir-name">${esc(dir)}/</span>
    </div>`;
    for (const f of tree[dir]) {
      html += `<div class="ws-tree-file ws-tree-file-nested">
        <span class="ws-file-icon">${_fileIcon(f.name)}</span>
        <span class="ws-file-name">${esc(f.name)}</span>
        <span class="ws-file-size">${_fmtBytes(f.size_bytes)}</span>
      </div>`;
    }
  }
  if (files.length >= 100) html += `<div class="ws-tree-more">… + d'autres fichiers (max 100 affichés)</div>`;
  html += '</div>';
  return html;
}

function _buildCard(ws) {
  const isOpen = _wsOpen.has(ws.slug);
  const safeSlug = esc(ws.slug);
  const safePath = esc(ws.path.replace(/\\/g, '/'));

  const liveBadge = ws.is_serving ? `<span class="ws-live-badge">● LIVE</span>` : '';
  const techHtml = _techBadges(ws);

  const icon = ws.has_index_html ? 'globe' : ws.has_package_json ? 'package' : 'folder';

  const openBtn = ws.has_index_html
    ? `<button class="ws-btn ws-btn-primary" onclick="event.stopPropagation();serveAndOpenWorkspace('${safeSlug}')">
        <i data-lucide="play" style="width:12px;height:12px"></i> Ouvrir
      </button>` : '';
  const continueBtn = `<button class="ws-btn" onclick="event.stopPropagation();continueWorkspace('${safeSlug}','${safePath}')">
    <i data-lucide="corner-down-left" style="width:12px;height:12px"></i> Continuer
  </button>`;
  const deleteBtn = `<button class="ws-btn ws-btn-danger" title="Supprimer" onclick="event.stopPropagation();deleteWorkspace('${safeSlug}')">
    <i data-lucide="trash-2" style="width:13px;height:13px"></i>
  </button>`;
  const liveOpenBtn = ws.is_serving && ws.serve_url
    ? `<a href="${esc(ws.serve_url)}" target="_blank" rel="noopener" class="ws-btn ws-btn-ok" onclick="event.stopPropagation()">
        <i data-lucide="external-link" style="width:12px;height:12px"></i> Voir
      </a>` : '';
  const stopBtn = ws.is_serving
    ? `<button class="ws-btn ws-btn-stop" onclick="event.stopPropagation();stopWorkspace('${safeSlug}')">
        <i data-lucide="square" style="width:12px;height:12px"></i> Arrêter
      </button>` : '';

  const treeHtml = isOpen ? `<div class="ws-tree-section">${_buildFileTree(ws.slug, _wsTreeCache[ws.slug] || null)}</div>` : '';

  return `<div class="ws-card${isOpen ? ' ws-card-open' : ''}" id="ws-card-${safeSlug}">
    <div class="ws-card-main" onclick="toggleWsTree('${safeSlug}')">
      <div class="ws-card-icon">
        <i data-lucide="${icon}" style="width:18px;height:18px"></i>
      </div>
      <div class="ws-card-body">
        <div class="ws-card-name">
          <span class="ws-name-text">${esc(ws.slug)}</span>
          ${liveBadge}
        </div>
        <div class="ws-card-meta">
          <span>${ws.files_count} fichier${ws.files_count !== 1 ? 's' : ''}</span>
          <span class="ws-meta-sep">·</span>
          <span>${_sizeLabel(ws.total_size_kb)}</span>
          ${techHtml ? `<span class="ws-meta-sep">·</span>${techHtml}` : ''}
        </div>
      </div>
      <div class="ws-chevron${isOpen ? ' open' : ''}">
        <i data-lucide="chevron-down" style="width:15px;height:15px"></i>
      </div>
    </div>
    ${treeHtml}
    <div class="ws-card-actions">
      ${liveOpenBtn}${stopBtn}${openBtn}${continueBtn}
      <div style="flex:1"></div>
      ${deleteBtn}
    </div>
  </div>`;
}

export async function toggleWsTree(slug) {
  if (_wsOpen.has(slug)) {
    _wsOpen.delete(slug);
    renderWorkspaces();
    return;
  }
  _wsOpen.add(slug);
  renderWorkspaces();
  if (!_wsTreeCache[slug]) {
    try {
      const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}`, {
        headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
      });
      if (r.ok) {
        const data = await r.json();
        _wsTreeCache[slug] = data.files || [];
        const treeEl = document.querySelector(`#ws-card-${CSS.escape(slug)} .ws-tree-section`);
        if (treeEl) {
          treeEl.innerHTML = _buildFileTree(slug, _wsTreeCache[slug]);
          if (typeof lucide !== 'undefined') lucide.createIcons({ el: treeEl });
        }
      }
    } catch (e) { console.warn('ws tree error:', e); }
  }
}

export async function serveAndOpenWorkspace(slug) {
  logC(`▶ Serving workspace: ${slug}`, 'info');
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}/serve`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) { const err = await r.json().catch(() => ({})); throw new Error(err.detail || `HTTP ${r.status}`); }
    const data = await r.json();
    window.open(data.url, '_blank', 'noopener');
    logC(`Workspace servi sur ${data.url}`, 'success');
    loadWorkspaces();
  } catch (e) {
    logC(`Erreur serve: ${e.message}`, 'error');
    alert(`Impossible de servir le workspace: ${e.message}`);
  }
}

export function continueWorkspace(slug, wsPath) {
  switchPanel('chat');
  const input = document.getElementById('message-input');
  if (input) {
    input.value = `Continue le projet "${slug}" situé dans ${wsPath}`;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }
  logC(`↩ Continuer projet: ${slug}`, 'info');
}

export async function stopWorkspace(slug) {
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}/serve`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) { const err = await r.json().catch(() => ({})); throw new Error(err.detail || `HTTP ${r.status}`); }
    logC(`Serveur "${slug}" arrêté`, 'success');
    loadWorkspaces();
  } catch (e) {
    logC(`Erreur arrêt: ${e.message}`, 'error');
    alert(`Impossible d'arrêter: ${e.message}`);
  }
}

export async function deleteWorkspace(slug) {
  if (!confirm(`Supprimer définitivement le workspace "${slug}" et tous ses fichiers ?`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` }
    });
    if (!r.ok) { const err = await r.json().catch(() => ({})); throw new Error(err.detail || `HTTP ${r.status}`); }
    logC(`Workspace "${slug}" supprimé`, 'success');
    if (_wsData) _wsData.workspaces = (_wsData.workspaces || []).filter(w => w.slug !== slug);
    delete _wsTreeCache[slug];
    _wsOpen.delete(slug);
    renderWorkspaces();
  } catch (e) {
    logC(`Erreur suppression: ${e.message}`, 'error');
    alert(`Impossible de supprimer: ${e.message}`);
  }
}
