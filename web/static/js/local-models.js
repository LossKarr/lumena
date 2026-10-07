let _catalog = [];
let _installed = new Map();
let _jobs = [];
let _recommendations = [];
let _hardware = null;
let _health = {};
let _view = 'discover';
let _refreshTimer = null;
let _pendingDelete = null;
let initialized = false;

const el = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const bytes = value => Number.isFinite(value) && value > 0
  ? `${(value / 1024 / 1024 / 1024).toFixed(value > 10 * 1024 ** 3 ? 0 : 1)} Go`
  : 'Inconnue';
const key = reference => `${reference.source}:${reference.canonical}`;
const terminalStates = new Set(['succeeded', 'failed', 'cancelled', 'unknown_interrupted']);

const labels = {
  capability_matches_intent: 'Adapté à votre usage',
  capability_not_confirmed: 'Capacité à confirmer',
  fits_free_vram: 'Tient dans la VRAM disponible',
  fits_available_ram_cpu_possible: 'Compatible avec la RAM disponible',
  insufficient_disk: 'Espace disque insuffisant',
  memory_pressure_likely: 'Mémoire potentiellement limitée',
  gated_repository: 'Accès Hugging Face requis',
  already_installed: 'Déjà installé',
  download_size: 'taille du téléchargement',
  license: 'licence',
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN) headers.Authorization = `Bearer ${ADMIN_TOKEN}`;
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${typeof API_BASE !== 'undefined' ? API_BASE : ''}/api/local-models${path}`, {
    ...options, headers,
  });
  const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function icon(name) {
  return `<i data-lucide="${safe(name)}"></i>`;
}

function showNotice(message, tone = 'info') {
  const box = el('local-model-error');
  if (!box) return;
  if (!message) {
    box.hidden = true;
    box.textContent = '';
    box.className = 'lm-notice';
    return;
  }
  box.className = `lm-notice${tone === 'error' ? ' is-error' : tone === 'success' ? ' is-success' : ''}`;
  box.innerHTML = `${icon(tone === 'error' ? 'circle-alert' : tone === 'success' ? 'circle-check' : 'info')}<span>${safe(message)}</span>`;
  box.hidden = false;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function combinedModels() {
  const rows = new Map(_catalog.map(model => [key(model.reference), model]));
  for (const installed of _installed.values()) {
    const modelKey = key(installed.reference);
    if (!rows.has(modelKey)) {
      rows.set(modelKey, {
        reference: installed.reference,
        display_name: installed.reference.canonical,
        description: 'Modèle présent dans votre bibliothèque Ollama locale.',
        category: installed.family || 'modèle local',
        size_bytes: installed.size_bytes,
        quantization: installed.quantization,
        capabilities: installed.capabilities || [],
        provenance: ['inventaire Ollama local'],
        installed: true,
        enabled: installed.enabled,
      });
    }
  }
  return [...rows.values()];
}

function installedFor(model) {
  return _installed.get(key(model.reference));
}

function providerLabel(source) {
  return source === 'huggingface' ? 'Hugging Face' : 'Ollama';
}

function modelMark(model) {
  const value = String(model.display_name || model.reference?.canonical || 'M').split('/').pop();
  return safe(value.slice(0, 2));
}

function renderStatus(health = {}) {
  const status = el('local-model-status');
  if (!status) return;
  const active = _jobs.filter(job => !terminalStates.has(job.state)).length;
  const disk = _hardware?.disk_free_bytes;
  status.innerHTML = `
    <div class="lm-stat-card ${health.available ? 'is-online' : 'is-offline'}">
      <span class="lm-stat-icon">${icon(health.available ? 'server' : 'server-off')}</span>
      <div><small>Moteur Ollama</small><strong>${health.available ? 'Opérationnel' : 'Indisponible'}</strong><span>${health.available ? `${health.model_count ?? _installed.size} modèle(s) détecté(s)` : safe(health.error_code || 'Daemon non joignable')}</span></div>
    </div>
    <div class="lm-stat-card"><span class="lm-stat-icon">${icon('package-check')}</span><div><small>Bibliothèque locale</small><strong>${_installed.size}</strong><span>modèle(s) installé(s)</span></div></div>
    <div class="lm-stat-card"><span class="lm-stat-icon">${icon(active ? 'loader-circle' : 'activity')}</span><div><small>Opérations</small><strong>${active || 'Aucune'}</strong><span>${active ? 'en cours ou en attente' : 'file disponible'}</span></div></div>
    <div class="lm-stat-card"><span class="lm-stat-icon">${icon('hard-drive')}</span><div><small>Stockage disponible</small><strong>${disk ? bytes(disk) : 'À analyser'}</strong><span>${_hardware ? 'espace disque libre' : 'calculé avec les recommandations'}</span></div></div>`;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function statusBadges(model, installed) {
  const badges = [];
  if (installed || model.installed) badges.push(`<span class="lm-badge ok">${icon('check')}Installé</span>`);
  if (installed?.verified) badges.push(`<span class="lm-badge ok">${icon('badge-check')}Vérifié</span>`);
  else if (installed) badges.push(`<span class="lm-badge warn">${icon('shield-alert')}À vérifier</span>`);
  if (installed?.enabled || model.enabled) badges.push(`<span class="lm-badge ok">${icon('power')}Activé</span>`);
  if (installed?.assigned_roles?.length) badges.push(`<span class="lm-badge ok">${icon('brain')}Principal</span>`);
  if (model.gated) badges.push(`<span class="lm-badge warn">${icon('lock-keyhole')}Accès requis</span>`);
  for (const capability of (model.capabilities || []).slice(0, 2)) badges.push(`<span class="lm-badge">${safe(capability)}</span>`);
  if (!model.capabilities?.length && model.category) badges.push(`<span class="lm-badge">${safe(model.category)}</span>`);
  return badges.join('');
}

function modelActions(model, installed) {
  if (!installed && !model.installed) {
    return `<button class="lm-button lm-button-primary" data-lm-action="install">${icon('download')}Installer</button><span class="lm-actions-spacer"></span><button class="lm-icon-button" data-lm-action="inspect" title="Copier l'identifiant" aria-label="Copier l'identifiant">${icon('copy')}</button>`;
  }
  const enabled = Boolean(installed?.enabled || model.enabled);
  return `${!installed?.verified ? `<button class="lm-button" data-lm-action="verify">${icon('shield-check')}Vérifier</button>` : ''}
    ${enabled ? `<button class="lm-button" data-lm-action="select">${icon('brain')}Utiliser</button>` : `<button class="lm-button lm-button-primary" data-lm-action="enable">${icon('power')}Activer</button>`}
    <span class="lm-actions-spacer"></span>
    <button class="lm-icon-button" data-lm-action="${enabled ? 'disable' : 'enable'}" title="${enabled ? 'Désactiver' : 'Activer'}" aria-label="${enabled ? 'Désactiver' : 'Activer'}">${icon(enabled ? 'power-off' : 'power')}</button>
    <button class="lm-icon-button" data-lm-action="unload" title="Décharger de la mémoire" aria-label="Décharger de la mémoire">${icon('memory-stick')}</button>
    <button class="lm-icon-button" data-lm-action="delete" title="Supprimer du disque" aria-label="Supprimer">${icon('trash-2')}</button>`;
}

function renderCard(model) {
  const ref = model.reference;
  const installed = installedFor(model);
  const isInstalled = Boolean(installed || model.installed);
  const license = model.license || 'Non publiée';
  const quantization = model.quantization || installed?.quantization || 'Auto';
  const source = providerLabel(ref.source);
  return `<article class="lm-card ${isInstalled ? 'is-installed' : ''}" data-source="${safe(ref.source)}" data-ref="${safe(ref.canonical)}">
    <div class="lm-card-top">
      <div class="lm-model-identity"><span class="lm-model-mark">${modelMark(model)}</span><div class="lm-model-name"><h3 title="${safe(model.display_name)}">${safe(model.display_name)}</h3><p>${safe(model.category || 'Génération de texte')}</p></div></div>
      <span class="lm-provider ${ref.source === 'huggingface' ? 'hf' : ''}"><i></i>${safe(source)}</span>
    </div>
    <div class="lm-badges">${statusBadges(model, installed)}</div>
    <p class="lm-card-desc">${safe(model.description || 'Description non publiée par la source. Consultez les métadonnées avant installation.')}</p>
    <div class="lm-card-facts">
      <div class="lm-card-fact"><small>Taille</small><strong title="${safe(bytes(model.size_bytes || installed?.size_bytes))}">${safe(bytes(model.size_bytes || installed?.size_bytes))}</strong></div>
      <div class="lm-card-fact"><small>Quantification</small><strong title="${safe(quantization)}">${safe(quantization)}</strong></div>
      <div class="lm-card-fact"><small>Licence</small><strong title="${safe(license)}">${safe(license)}</strong></div>
    </div>
    <div class="lm-proof">${icon(model.partial ? 'circle-help' : 'badge-check')} ${safe((model.provenance || []).join(' · ') || 'Source non précisée')}${model.partial ? ' · informations partielles' : ''}</div>
    <div class="lm-actions">${modelActions(model, installed)}</div>
    <div class="lm-job" hidden><div class="lm-progress"><span style="width:0%"></span></div><div class="lm-job-copy"><span data-lm-job-status>Préparation…</span><span data-lm-job-percent>0%</span></div></div>
  </article>`;
}

function emptyState(title, copy, iconName = 'package-open') {
  return `<div class="lm-empty">${icon(iconName)}<strong>${safe(title)}</strong><p>${safe(copy)}</p></div>`;
}

function visibleModels() {
  const query = _view === 'discover'
    ? (el('local-model-search')?.value || '').trim().toLowerCase()
    : '';
  const rows = _view === 'installed' ? combinedModels().filter(model => installedFor(model) || model.installed) : _catalog;
  return rows.filter(model => !query || `${model.display_name} ${model.description || ''} ${model.category || ''} ${(model.capabilities || []).join(' ')}`.toLowerCase().includes(query));
}

function renderGrid() {
  const grid = el('local-model-grid');
  if (!grid) return;
  const rows = visibleModels();
  if (!rows.length) {
    grid.innerHTML = _view === 'installed'
      ? emptyState('Aucun modèle dans votre bibliothèque', 'Installez un modèle depuis le Catalogue pour le rendre disponible dans Lumena.', 'library')
      : emptyState('Aucun résultat', 'Essayez un nom de famille, une capacité ou un identifiant direct.', 'search-x');
  } else {
    grid.innerHTML = rows.map(renderCard).join('');
  }
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function jobLabel(state) {
  return ({ queued: 'En attente', running: 'Téléchargement', cancelling: 'Annulation', succeeded: 'Terminé', failed: 'Échec', cancelled: 'Annulé', unknown_interrupted: 'Interrompu' })[state] || state;
}

function renderJobs() {
  const container = el('local-model-jobs');
  if (!container) return;
  if (!_jobs.length) {
    container.innerHTML = emptyState('Aucune opération', 'Les installations et annulations apparaîtront ici avec leur progression et leur preuve.', 'list-restart');
    return;
  }
  container.innerHTML = _jobs.map(job => {
    const active = !terminalStates.has(job.state);
    const percent = Math.max(0, Math.min(100, Number(job.progress_percent) || 0));
    return `<article class="lm-job-row" data-job-id="${safe(job.job_id)}">
      <span class="lm-job-state">${icon(active ? 'loader-circle' : job.state === 'succeeded' ? 'check' : 'triangle-alert')}</span>
      <div><strong>${safe(job.reference?.canonical || 'Modèle local')}</strong><small>${safe(job.operation || 'installation')} · ${safe(jobLabel(job.state))}</small></div>
      <div class="lm-job-progress"><div><span>${safe(job.status_code || jobLabel(job.state))}</span><b>${percent.toFixed(0)}%</b></div><div class="lm-progress"><span style="width:${percent}%"></span></div></div>
      ${active ? `<button class="lm-button" data-lm-job-action="cancel">${icon('x')}Annuler</button>` : `<span class="lm-badge ${job.state === 'succeeded' ? 'ok' : 'warn'}">${job.verified ? 'Prouvé' : safe(jobLabel(job.state))}</span>`}
    </article>`;
  }).join('');
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function setView(view) {
  _view = ['discover', 'installed', 'jobs'].includes(view) ? view : 'discover';
  document.querySelectorAll('#panel-local-models [data-lm-view]').forEach(button => {
    const selected = button.dataset.lmView === _view;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  document.querySelectorAll('#panel-local-models [data-lm-page-section="discover"]').forEach(section => {
    section.hidden = _view !== 'discover';
  });
  const grid = el('local-model-grid');
  const jobs = el('local-model-jobs');
  const title = el('lm-library-title');
  const note = el('local-model-source-note');
  if (grid) grid.hidden = _view === 'jobs';
  if (jobs) jobs.hidden = _view !== 'jobs';
  if (title) title.textContent = _view === 'installed' ? 'Mes modèles installés' : _view === 'jobs' ? 'Historique des opérations' : 'Explorer les modèles';
  if (_view === 'installed' && note) note.textContent = 'Activez, vérifiez, sélectionnez ou libérez vos modèles sans supprimer leurs poids par erreur.';
  if (_view === 'jobs' && note) note.textContent = 'Chaque opération conserve son état, sa progression et sa preuve après rechargement.';
  if (_view === 'discover') updateSourceNote();
  renderGrid();
  renderJobs();
}

function updateCounts() {
  const counts = {
    'lm-catalog-count': _catalog.length,
    'lm-installed-count': _installed.size,
    'lm-job-count': _jobs.filter(job => !terminalStates.has(job.state)).length || _jobs.length,
  };
  for (const [id, value] of Object.entries(counts)) if (el(id)) el(id).textContent = value;
}

function updateSourceNote() {
  const note = el('local-model-source-note');
  if (!note) return;
  const ollama = window.__lmSources?.ollama;
  const hf = window.__lmSources?.huggingface;
  const live = ollama?.public_search_available;
  note.textContent = live
    ? 'Index public Ollama et catalogue GGUF Hugging Face actualisés. Certaines métadonnées communautaires peuvent rester inconnues.'
    : hf?.available === false
      ? 'Sources distantes momentanément indisponibles. La bibliothèque locale et le catalogue de repli restent accessibles.'
      : 'Catalogue Ollama de repli et modèles GGUF Hugging Face. Un identifiant valide reste installable directement.';
}

function scheduleRefresh() {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  if (_jobs.some(job => !terminalStates.has(job.state))) {
    _refreshTimer = setTimeout(() => loadLocalModels({ quiet: true }), 1200);
  }
}

export async function loadLocalModels(options = {}) {
  const quiet = options?.quiet === true;
  if (!quiet) showNotice('');
  try {
    const source = el('local-model-source')?.value || 'all';
    const query = el('local-model-search')?.value || '';
    const [health, installed, catalogue, jobs] = await Promise.all([
      api('/status'),
      api('/installed'),
      api(`/search?q=${encodeURIComponent(query)}&source=${encodeURIComponent(source)}&limit=60`),
      api('/jobs?limit=50'),
    ]);
    _installed = new Map((installed.models || []).map(model => [key(model.reference), model]));
    _catalog = catalogue.models || [];
    _jobs = jobs.jobs || [];
    _health = health;
    window.__lmSources = catalogue.sources || {};
    renderStatus(health);
    updateCounts();
    setView(_view);
    scheduleRefresh();
    if (!_recommendations.length && _view === 'discover') recommendLocalModels({ quiet: true });
  } catch (exception) {
    _health = { available: false, error_code: exception.message };
    showNotice(exception.message, 'error');
    renderStatus(_health);
  }
}

function translatedReasons(entry) {
  const reasons = (entry.reasons || []).filter(reason => !['capability_not_confirmed'].includes(reason)).map(reason => labels[reason] || reason);
  if (entry.unknowns?.length) reasons.push(`À confirmer : ${entry.unknowns.map(value => labels[value] || value).join(', ')}`);
  return reasons.slice(0, 2).join(' · ') || 'Classement fondé sur les informations disponibles';
}

function renderHardware() {
  const target = el('local-model-hardware');
  if (!target || !_hardware) return;
  const gpu = _hardware.gpu || {};
  target.innerHTML = `<span>${icon('cpu')}${gpu.available ? safe(gpu.name || 'GPU détecté') : 'Exécution CPU'}</span><span>${icon('memory-stick')}${bytes(gpu.vram_free_bytes)} VRAM libre</span><span>${icon('square-stack')}${bytes(_hardware.ram_available_bytes)} RAM disponible</span><span>${icon('hard-drive')}${bytes(_hardware.disk_free_bytes)} disque libre</span>`;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderRecommendations() {
  const target = el('local-model-recommendations');
  if (!target) return;
  if (!_recommendations.length) {
    target.innerHTML = emptyState('Aucune recommandation disponible', 'Actualisez les sources ou choisissez un autre usage.', 'sparkles');
    return;
  }
  target.innerHTML = _recommendations.slice(0, 3).map((entry, index) => {
    const model = entry.model;
    return `<button type="button" class="lm-recommend-card" data-lm-recommend-ref="${safe(model.reference.canonical)}" data-lm-recommend-source="${safe(model.reference.source)}">
      <span class="lm-rank">${index + 1}</span><span class="lm-recommend-main"><strong>${safe(model.display_name)}</strong><p>${safe(translatedReasons(entry))}</p></span><span class="lm-score"><b>${safe(entry.score)}</b>score</span>
    </button>`;
  }).join('');
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function recommendLocalModels(options = {}) {
  const target = el('local-model-recommendations');
  if (!target) return;
  if (!options?.quiet) target.innerHTML = '<div class="lm-skeleton-card"></div><div class="lm-skeleton-card"></div><div class="lm-skeleton-card"></div>';
  try {
    const intent = el('local-model-intent')?.value || 'general';
    const query = el('local-model-search')?.value || '';
    const result = await api(`/recommendations?q=${encodeURIComponent(query)}&intent=${encodeURIComponent(intent)}&limit=5`);
    _recommendations = result.recommendations || [];
    _hardware = result.hardware || null;
    renderHardware();
    renderRecommendations();
    renderStatus(_health);
  } catch (exception) {
    target.innerHTML = emptyState('Analyse indisponible', exception.message, 'triangle-alert');
  }
}

function attachJobToCard(jobId, card) {
  const box = card?.querySelector('.lm-job');
  if (box) box.hidden = false;
  _view = 'jobs';
  loadLocalModels({ quiet: true });
}

async function installReference(source, ref, card = null) {
  const job = await api('/install', {
    method: 'POST',
    body: JSON.stringify({ source, ref, idempotency_key: crypto.randomUUID(), enable_after_install: true }),
  });
  showNotice(`Installation de ${ref} ajoutée à la file.`, 'success');
  attachJobToCard(job.job_id, card);
}

async function installDirect() {
  const ref = (el('local-model-search')?.value || '').trim();
  if (!ref) return showNotice('Saisissez un identifiant exact avant l’installation.', 'error');
  let source = el('local-model-source')?.value || 'all';
  if (source === 'all') source = ref.toLowerCase().startsWith('hf.co/') || ref.split(':')[0].split('/').length === 2 ? 'huggingface' : 'ollama';
  try { await installReference(source, ref); } catch (exception) { showNotice(exception.message, 'error'); }
}

async function prepareDelete(card) {
  const { source, ref } = card.dataset;
  const prepared = await api('/delete-ticket', { method: 'POST', body: JSON.stringify({ source, ref }) });
  _pendingDelete = { source, ref, ticket: prepared.ticket };
  const impact = prepared.impact || {};
  el('local-model-delete-copy').textContent = `${ref} sera retiré du stockage Ollama. Cette action ne peut pas être annulée.`;
  el('local-model-delete-impact').innerHTML = `<span>Taille estimée <b>${safe(bytes(impact.size_bytes))}</b></span><span>Chargé en mémoire <b>${impact.loaded ? 'Oui' : 'Non'}</b></span><span>Affectations actives <b>${safe((impact.assignments || []).join(', ') || 'Aucune')}</b></span>`;
  const dialog = el('local-model-delete-dialog');
  if (dialog?.showModal) dialog.showModal();
  else if (confirm(`Supprimer définitivement ${ref} (${bytes(impact.size_bytes)}) ?`)) await confirmDelete();
}

async function confirmDelete() {
  if (!_pendingDelete) return;
  const target = _pendingDelete;
  _pendingDelete = null;
  try {
    await api('/delete', { method: 'POST', body: JSON.stringify(target) });
    showNotice(`${target.ref} a été supprimé du stockage local.`, 'success');
    await loadLocalModels({ quiet: true });
  } catch (exception) { showNotice(exception.message, 'error'); }
}

async function mutate(card, action) {
  const { source, ref } = card.dataset;
  if (action === 'inspect') {
    await navigator.clipboard?.writeText(ref);
    return showNotice(`Identifiant copié : ${ref}`, 'success');
  }
  if (action === 'delete') {
    try { await prepareDelete(card); } catch (exception) { showNotice(exception.message, 'error'); }
    return;
  }
  card.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    if (action === 'install') return await installReference(source, ref, card);
    if (action === 'select') {
      await api('/select', { method: 'POST', body: JSON.stringify({ source, ref, role: 'primary' }) });
      await window.loadModels?.();
    }
    else await api(`/${action}`, { method: 'POST', body: JSON.stringify({ source, ref }) });
    showNotice(`${ref} : opération ${action} terminée et vérifiée.`, 'success');
    await loadLocalModels({ quiet: true });
  } catch (exception) {
    showNotice(exception.message, 'error');
    card.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
}

async function cancelJob(row) {
  try {
    await api(`/jobs/${encodeURIComponent(row.dataset.jobId)}/cancel`, { method: 'POST' });
    showNotice('Annulation demandée. L’état sera confirmé par le moteur.', 'info');
    await loadLocalModels({ quiet: true });
  } catch (exception) { showNotice(exception.message, 'error'); }
}

export function initLocalModelsPanel() {
  if (initialized) return;
  initialized = true;
  el('local-model-refresh')?.addEventListener('click', () => loadLocalModels());
  el('local-model-search-btn')?.addEventListener('click', () => loadLocalModels());
  el('local-model-direct-install')?.addEventListener('click', installDirect);
  el('local-model-search')?.addEventListener('keydown', event => { if (event.key === 'Enter') loadLocalModels(); });
  el('local-model-source')?.addEventListener('change', () => loadLocalModels());
  el('local-model-recommend-btn')?.addEventListener('click', () => recommendLocalModels());
  el('local-model-hero-recommend')?.addEventListener('click', () => { setView('discover'); el('lm-recommend-title')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); recommendLocalModels(); });
  el('local-model-delete-confirm')?.addEventListener('click', confirmDelete);
  el('local-model-delete-dialog')?.addEventListener('close', event => { if (event.target.returnValue === 'cancel') _pendingDelete = null; });
  el('panel-local-models')?.addEventListener('click', event => {
    const view = event.target.closest('[data-lm-view]');
    if (view) return setView(view.dataset.lmView);
    const recommendation = event.target.closest('[data-lm-recommend-ref]');
    if (recommendation) {
      el('local-model-search').value = recommendation.dataset.lmRecommendRef;
      el('local-model-source').value = recommendation.dataset.lmRecommendSource;
      loadLocalModels();
      return el('lm-library-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    const action = event.target.closest('[data-lm-action]');
    const card = action?.closest('.lm-card');
    if (action && card) return mutate(card, action.dataset.lmAction);
    const jobAction = event.target.closest('[data-lm-job-action]');
    const row = jobAction?.closest('.lm-job-row');
    if (jobAction && row) cancelJob(row);
  });
}
