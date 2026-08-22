/* ============================================================
   OVERVIEW — read-only synthesis of existing Lumena systems.
   No status is inferred as healthy when its source is unavailable.
   ============================================================ */

const OVERVIEW_LAYOUT_KEY = 'lumena_overview_layout_v1';
const OVERVIEW_DENSITY_KEY = 'lumena_overview_density_v1';
const OVERVIEW_WIDGETS = [
  ['work', 'Travail en cours'],
  ['attention', 'À traiter'],
  ['activity', 'Activité'],
  ['systems', 'Systèmes'],
  ['deliverables', 'Livrables'],
  ['health', 'Santé'],
  ['operations', 'Opérations'],
  ['capabilities', 'Capacités'],
  ['sources', 'Sources'],
];
const ACTIVE_MISSION_STATES = new Set(['queued', 'running', 'waiting_io', 'checkpointed']);
const THREE_URL = '/static/vendor/three.module.min.js';
const SVG_LOADER_URL = '/static/vendor/SVGLoader.js';
const LOGO_SVG_URL = '/static/branding/lumena-logo-3d.svg';
const SOURCE_DEFS = Object.freeze({
  status:{label:'Runtime',path:'/api/status',panel:'overview',critical:true,fast:true},
  models:{label:'Modèles',path:'/api/models',panel:'providers',critical:true},
  missions:{label:'Missions',path:'/api/missions?limit=40',panel:'missions',critical:true,fast:true},
  tasks:{label:'Planifications',path:'/api/tasks?limit=30',panel:'tasks',fast:true},
  alerts:{label:'Alertes',path:'/api/alerts?limit=20',panel:'alerts',critical:true,fast:true},
  documents:{label:'Documents',path:'/api/document-studio/library?limit=8',panel:'document-studio'},
  trace:{label:'Trace',path:'/api/trace/recent?limit=40',panel:'trace',fast:true},
  providers:{label:'Providers',path:'/api/providers',panel:'providers',critical:true},
  voice:{label:'Voix',path:'/api/voice/status',panel:'voice'},
  mcp:{label:'MCP',path:'/api/mcp/health',panel:'mcp'},
  mcpOps:{label:'MCP observabilité',path:'/api/mcp/observability/overview',panel:'mcp'},
  peers:{label:'P2P',path:'/api/peers',panel:'infra-network'},
  serving:{label:'Previews web',path:'/api/workspaces/serving',panel:'workspaces',fast:true},
  reliability:{label:'Fiabilité',path:'/api/system/reliability',panel:'providers'},
  audit:{label:'Audit runtime',path:'/api/runtime/audit?format=summary',panel:'tools'},
  daemon:{label:'Daemon',path:'/api/daemon/activity',panel:'infra-autonomy'},
  journal:{label:'Journal',path:'/api/journal?limit=8',panel:'journal'},
  sessions:{label:'Sessions',path:'/api/sessions?limit=8',panel:'sessions'},
  hooks:{label:'Hooks',path:'/api/hooks',panel:'hooks'},
  training:{label:'Apprentissage',path:'/api/training',panel:'training'},
  finetuning:{label:'Fine-tuning',path:'/api/finetuning/status',panel:'finetuning'},
});
const FAST_SOURCE_KEYS = Object.keys(SOURCE_DEFS).filter(key => SOURCE_DEFS[key].fast);
const SOURCE_STALE_MS = 90000;

let overviewAbort = null;
let overviewGeneration = 0;
let coreRuntime = null;
let overviewPaused = false;
let overviewBound = false;
let overviewTimer = 0;
let sourceCache = Object.create(null);

const safe = value => {
  const text = String(value ?? '');
  if (typeof window.esc === 'function') return window.esc(text);
  return text.replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
};
const arr = value => Array.isArray(value) ? value : [];
const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const first = (...values) => values.find(value => value !== undefined && value !== null && value !== '');
const authHeaders = () => {
  const headers = {};
  if (typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN) headers.Authorization = `Bearer ${ADMIN_TOKEN}`;
  return headers;
};

function panel(name){
  if (typeof window.switchPanel === 'function') window.switchPanel(name);
}

async function getJson(path, signal, timeoutMs = 8000){
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, {once:true});
  const timer = setTimeout(abort, timeoutMs);
  const started = performance.now();
  try {
    const response = await fetch(`${typeof API_BASE !== 'undefined' ? API_BASE : ''}${path}`, {
      headers: authHeaders(), signal:controller.signal,
    });
    if (!response.ok) {
      const error = new Error(`${response.status} ${response.statusText}`.trim());
      error.httpStatus = response.status;
      throw error;
    }
    return {data:await response.json(),httpStatus:response.status,latencyMs:Math.round(performance.now()-started)};
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

export function sourceFreshness(record, now = Date.now()){
  if (!record?.lastSuccessAt) return 'unknown';
  if (record.status !== 'ok') return 'stale';
  return now - record.lastSuccessAt > SOURCE_STALE_MS ? 'stale' : 'fresh';
}

async function fetchSource(key, signal){
  const def = SOURCE_DEFS[key];
  const fetchedAt = Date.now();
  try {
    const result = await getJson(def.path, signal);
    return {key,...def,status:'ok',data:result.data,httpStatus:result.httpStatus,latencyMs:result.latencyMs,fetchedAt,lastSuccessAt:fetchedAt,error:''};
  } catch (error) {
    if (signal?.aborted) throw error;
    const previous = sourceCache[key];
    return {key,...def,status:'error',data:previous?.data ?? null,httpStatus:error.httpStatus || 0,latencyMs:null,fetchedAt,lastSuccessAt:previous?.lastSuccessAt || 0,error:String(error?.message || error || 'Erreur inconnue')};
  }
}

function formatTime(value){
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 8);
  return new Intl.DateTimeFormat('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'}).format(date);
}

function formatAge(value){
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}j`;
}

function filenameIcon(format){
  const ext = String(format || '').toLowerCase().replace('.', '');
  if (ext === 'pdf') return 'file-text';
  if (['xlsx','xls','csv','ods'].includes(ext)) return 'sheet';
  if (['pptx','ppt'].includes(ext)) return 'presentation';
  if (['zip','7z'].includes(ext)) return 'archive';
  return 'file';
}

function sourceResult(results, key){
  const result = results[key];
  return result && result.status === 'fulfilled' ? result.value : null;
}

function normalizedAlertKey(alert){
  return [first(alert.source,alert.channel,'lumena'),first(alert.severity,alert.status,'warning'),first(alert.message,alert.summary,'alerte')]
    .map(value => String(value).toLowerCase().replace(/\d+(?:[.,]\d+)?\s*%/g,'#%').replace(/\d+/g,'#').replace(/\s+/g,' ').trim()).join('|');
}

export function dedupeAlerts(alerts){
  const grouped = new Map();
  arr(alerts).forEach(alert => {
    if (alert?.ok === true) return;
    const key = normalizedAlertKey(alert);
    const existing = grouped.get(key);
    if (existing) existing.repeats += 1;
    else grouped.set(key,{...alert,repeats:1});
  });
  return [...grouped.values()];
}

function isRecentFailure(mission, now = Date.now()){
  const stamp = first(mission.updated_at,mission.finished_at,mission.created_at,obj(mission.metadata).updated_at);
  if (!stamp) return true;
  const parsed = new Date(stamp).getTime();
  return !Number.isFinite(parsed) || now - parsed <= 86400000;
}

export function buildOverviewViewModel(sources){
  const status = obj(sources.status);
  const modelsPayload = obj(sources.models);
  const missions = arr(obj(sources.missions).missions);
  const tasks = arr(obj(sources.tasks).tasks);
  const alerts = arr(obj(sources.alerts).alerts);
  const documents = arr(obj(sources.documents).documents);
  const events = arr(obj(sources.trace).events);
  const providers = arr(obj(sources.providers).providers);
  const voice = obj(sources.voice);
  const mcp = obj(sources.mcp);
  const peers = arr(obj(sources.peers).peers);
  const serving = arr(obj(sources.serving).serving);
  const reliability = obj(sources.reliability);
  const audit = obj(sources.audit);
  const daemon = obj(sources.daemon);
  const journal = arr(obj(sources.journal).entries);
  const sessions = arr(first(obj(sources.sessions).sessions,obj(sources.sessions).items,[]));
  const hooks = arr(obj(sources.hooks).hooks);
  const training = obj(sources.training);
  const finetuning = obj(sources.finetuning);
  const mcpOps = obj(sources.mcpOps);
  const models = arr(modelsPayload.models);
  const currentModel = models.find(model => model.current) || models.find(model => model.name === modelsPayload.current_model) || null;
  const activeMissions = missions.filter(mission => ACTIVE_MISSION_STATES.has(String(mission.state || '').toLowerCase()));
  const rootMissions = activeMissions.filter(mission => {
    const meta = obj(mission.metadata);
    return !first(meta.parent_task_id, meta.parent_id, mission.parent_task_id);
  });
  const failedMissions = missions.filter(mission => String(mission.state || '').toLowerCase() === 'failed' && isRecentFailure(mission));
  const unhealthyProviders = providers.filter(provider => provider.api_configured !== false && /critique|erreur|dégrad|degrad|error|down|unhealthy/i.test(String(provider.status || '')));
  const activePeers = peers.filter(peer => ['trusted','online','connected'].includes(String(first(peer.status, peer.state, '')).toLowerCase()));
  const activeModules = number(status.active_modules, Object.values(obj(status.modules)).filter(Boolean).length);
  const totalModules = number(status.total_modules, Object.keys(obj(status.modules)).length);
  const errors = number(status.pipeline_errors_total);
  const timeouts = number(status.pipeline_timeouts_total);
  const alertItems = dedupeAlerts(alerts).slice(0, 6);
  const sortedDocuments = [...documents].sort((a,b) => new Date(first(b.imported_at,obj(b.metadata).created_at,0)) - new Date(first(a.imported_at,obj(a.metadata).created_at,0)));

  return {
    status, currentModel, models, missions, tasks, alerts, documents:sortedDocuments, events, providers,
    voice, mcp, peers, serving, activeMissions, rootMissions, failedMissions,
    unhealthyProviders, activePeers, activeModules, totalModules, errors, timeouts,
    alertItems,reliability,audit,daemon,journal,sessions,hooks,training,finetuning,mcpOps,
  };
}

function renderSummary(vm, errors){
  const el = document.getElementById('ov-summary');
  if (!el) return;
  const modelName = first(vm.currentModel?.display_name, vm.currentModel?.name, vm.status.model, 'Indisponible');
  const modelProvider = first(vm.currentModel?.provider, vm.status.provider, '—');
  const agentOn = localStorage.getItem('lumena_agent_mode') === 'true';
  const autonomyKnown = 'autonomy_running' in vm.status;
  const autonomyOn = !!vm.status.autonomy_running;
  const attention = vm.alertItems.reduce((sum,item) => sum + number(item.repeats,1),0) + vm.unhealthyProviders.length + vm.failedMissions.length;
  const rail = /codex/i.test(String(modelProvider)) ? 'Abonnement Codex' : 'API / local';
  el.innerHTML = `
    <div class="overview-summary-item"><i data-lucide="box"></i><span>Modèle <strong>${safe(modelName)}</strong> · ${safe(modelProvider)} · ${safe(rail)}</span></div>
    <div class="overview-summary-item" title="État du sélecteur Agent de cette session navigateur"><i data-lucide="bot"></i><span>Mode UI <strong class="${agentOn?'is-ok':'is-warn'}">${agentOn?'AGENT':'CHAT'}</strong></span></div>
    <div class="overview-summary-item"><i data-lucide="zap"></i><span>Autonomie <strong class="${autonomyKnown?(autonomyOn?'is-ok':'is-warn'):'is-warn'}">${autonomyKnown?(autonomyOn?'ACTIVE':'INACTIVE'):'INCONNUE'}</strong></span></div>
    <div class="overview-summary-item"><i data-lucide="bell"></i><span>Alertes <strong class="${attention?'is-warn':'is-ok'}">${attention}</strong></span></div>`;

  const global = document.getElementById('ov-global-state');
  if (!global) return;
  const criticalFailures = Object.values(sourceCache).filter(record => record.critical && record.status !== 'ok');
  const statusUnavailable = errors.has('status') || criticalFailures.length > 0;
  const degraded = vm.status.status_source === 'degraded' || errors.size > 0 || attention > 0;
  global.className = `overview-live ${statusUnavailable?'is-danger':degraded?'is-warn':'is-ok'}`;
  global.innerHTML = `<span></span> ${statusUnavailable?'Sources critiques indisponibles':degraded?'État dégradé':'En ligne · sources prouvées'}`;
}

function missionWorkers(mission, missions){
  const id = mission.task_id;
  return missions.filter(item => first(obj(item.metadata).parent_task_id, obj(item.metadata).parent_id, item.parent_task_id) === id);
}

function renderWork(vm, errors){
  const el = document.getElementById('ov-work');
  if (!el) return;
  if (errors.has('missions')) {
    el.innerHTML = empty('Missions indisponibles pour le moment.');
    return;
  }
  const rows = (vm.rootMissions.length ? vm.rootMissions : vm.activeMissions).slice(0, 4);
  if (!rows.length) {
    el.innerHTML = empty('Aucune mission en cours. Lumena est disponible.');
    return;
  }
  el.innerHTML = rows.map(mission => {
    const meta = obj(mission.metadata);
    const workers = missionWorkers(mission, vm.missions);
    const workersDone = workers.filter(worker => String(worker.state).toLowerCase() === 'done').length;
    const title = first(meta.objective, mission.message_preview, 'Mission');
    const phase = first(meta.phase, obj(mission.last_checkpoint).phase, String(mission.state || 'en cours'));
    return `<div class="overview-row">
      <div class="overview-row-main"><span class="overview-row-icon"><i data-lucide="folder-kanban"></i></span><span class="overview-row-text">${safe(title)}<small>${safe(first(meta.mission_workspace, mission.task_id, ''))}</small></span></div>
      <div class="overview-row-meta"><span class="overview-status ${mission.state==='waiting_io'?'is-warn':'is-ok'}">${safe(phase)}</span>${workers.length?` · ${workersDone}/${workers.length} workers`:''}</div>
      <button class="overview-row-action" data-ov-panel="missions">Ouvrir <i data-lucide="chevron-right"></i></button>
    </div>`;
  }).join('');
}

function severityClass(item){
  const severity = String(first(item.severity, item.status, '')).toLowerCase();
  return severity.includes('critical') || severity.includes('error') || severity.includes('fail') ? 'is-danger' : 'is-warn';
}

function renderAttention(vm, errors){
  const el = document.getElementById('ov-attention');
  if (!el) return;
  const items = [];
  if (!errors.has('alerts')) {
    vm.alertItems.forEach(alert => items.push({
      icon:'triangle-alert', text:first(alert.message, 'Alerte système'), meta:first(alert.channel, alert.source, 'Lumena'),
      state:severityClass(alert), panel:'alerts',
    }));
  }
  vm.unhealthyProviders.forEach(provider => items.push({
    icon:'refresh-cw', text:`Provider ${provider.name}`, meta:provider.status, state:provider.status==='Dégradé'?'is-warn':'is-danger', panel:'providers',
  }));
  vm.failedMissions.slice(0, 2).forEach(mission => items.push({
    icon:'circle-x', text:first(obj(mission.metadata).objective, mission.message_preview, 'Mission en échec'), meta:'Mission', state:'is-danger', panel:'missions',
  }));
  if (!items.length) {
    el.innerHTML = errors.has('alerts') ? empty('Alertes indisponibles. Aucun état n’est supposé sain.') : empty('Aucune intervention requise.');
    return;
  }
  el.innerHTML = items.slice(0, 4).map(item => `<div class="overview-row">
    <div class="overview-row-main"><span class="overview-row-icon ${item.state}"><i data-lucide="${item.icon}"></i></span><span class="overview-row-text">${safe(item.text)}<small>${safe(item.meta)}</small></span></div>
    <div class="overview-row-meta"><span class="overview-status ${item.state}">Attention</span></div>
    <button class="overview-row-action" data-ov-panel="${item.panel}">Ouvrir <i data-lucide="chevron-right"></i></button>
  </div>`).join('');
}

function eventText(event){
  return first(event.message, event.summary, event.tool_name, event.stage, event.type, 'Événement');
}

function renderActivity(vm, errors){
  const el = document.getElementById('ov-activity');
  if (!el) return;
  const daemonHandlers = arr(vm.daemon.handlers);
  const daemonEvents = daemonHandlers.length
    ? daemonHandlers.map(event => ({...obj(event),message:first(event.summary,`Daemon · ${first(event.handler,'activité')}`),type:'daemon',channel:'autonomie'}))
    : Object.entries(obj(first(vm.daemon.results,vm.daemon.activity,{}))).map(([name,event]) => ({...obj(event),message:first(obj(event).message,`Daemon · ${name}`),type:'daemon',channel:'autonomie'}));
  const journalEvents = vm.journal.map(event => ({...obj(event),message:first(event.summary,event.content,event.message,event.title,'Journal'),type:first(event.type,'journal'),channel:'journal'}));
  const merged = [...vm.events,...daemonEvents,...journalEvents].sort((a,b) => new Date(first(a.ts,a.timestamp,a.created_at,0)) - new Date(first(b.ts,b.timestamp,b.created_at,0)));
  if (!merged.length) { el.innerHTML = errors.has('trace') ? empty('Trace indisponible et aucune autre activité prouvée.') : empty('Aucune activité récente enregistrée.'); return; }
  el.innerHTML = merged.slice(-8).reverse().map(event => {
    const hasError = event.error || String(event.status || '').toLowerCase() === 'error';
    const icon = event.tool_name ? 'wrench' : event.type === 'checkpoint' ? 'save' : 'activity';
    return `<div class="overview-row">
      <div class="overview-row-main"><span class="overview-row-icon"><i data-lucide="${icon}"></i></span><span class="overview-row-text">${safe(eventText(event))}<small>${safe(first(event.task_id, event.channel, event.type, 'trace'))}</small></span></div>
      <div class="overview-row-meta">${safe(formatTime(first(event.ts,event.timestamp)))}${event.duration_ms?` · ${number(event.duration_ms)} ms`:''}</div>
      <span class="overview-status ${hasError?'is-danger':'is-ok'}">${hasError?'Erreur':'Prouvé'}</span>
    </div>`;
  }).join('');
}

function systemState(known, active, inactiveLabel = 'INACTIF', inactiveClass = 'is-warn', activeLabel = 'ACTIF'){
  if (!known) return {label:'INDISPONIBLE', cls:'is-danger'};
  return active ? {label:activeLabel, cls:'is-ok'} : {label:inactiveLabel, cls:inactiveClass};
}

function renderSystems(vm, errors){
  const el = document.getElementById('ov-systems');
  if (!el) return;
  const mcpComponents = Object.values(obj(vm.mcp.components));
  const mcpHealthy = vm.mcp.available === true && mcpComponents.every(component => obj(component).available !== false);
  const providerHealthy = vm.currentModel && vm.currentModel.available !== false;
  const activeChannels = [vm.status.telegram_running, vm.status.whatsapp_running, vm.status.twitter_running].filter(Boolean).length;
  const activeChannelsLabel = `${activeChannels} ACTIF${activeChannels>1?'S':''} · 1 NON TRACÉ`;
  const systems = [
    ['box','Modèles',systemState(!errors.has('models'),providerHealthy), 'providers'],
    ['database','Mémoire',systemState(!errors.has('status'),Number.isFinite(Number(vm.status.memory_count))), 'memory'],
    ['wrench','Outils & Skills',systemState(!errors.has('status'),number(vm.status.tool_count)>0 || number(vm.status.skills_loaded)>0), 'tools'],
    ['target','Missions',systemState(!errors.has('missions'),true,'INDISPONIBLE','is-danger','DISPONIBLE'), 'missions'],
    ['files','Documents',systemState(!errors.has('documents'),true,'INDISPONIBLE','is-danger','DISPONIBLE'), 'document-studio'],
    ['globe','Browser / Web',systemState(!errors.has('serving'),vm.serving.length>0,'AUCUNE PREVIEW',''), 'workspaces'],
    ['mic','Voix',systemState(!errors.has('voice'),vm.voice.running === true,'INACTIF',''), 'voice'],
    ['plug','MCP',systemState(!errors.has('mcp'),mcpHealthy), 'mcp'],
    ['users','P2P',systemState(!errors.has('peers'),vm.activePeers.length>0,'AUCUN PAIR',''), 'infra-network'],
    ['zap','Autonomie',systemState(!errors.has('status'),'autonomy_running' in vm.status && vm.status.autonomy_running), 'infra-autonomy'],
    ['radio','Canaux',systemState(!errors.has('status'),activeChannels>0,'0 ACTIF · 1 NON TRACÉ','',activeChannelsLabel), 'infra-telegram'],
    ['layers','Sessions',systemState(!errors.has('status'),'sessions_total' in vm.status, 'INCONNU',''), 'sessions'],
  ];
  el.innerHTML = systems.map(([icon,name,state,target]) => `<button class="overview-system" data-ov-panel="${target}">
    <i data-lucide="${icon}"></i><span class="overview-system-name">${name}</span><span class="overview-system-state ${state.cls}">${state.label}</span><i class="overview-chevron" data-lucide="chevron-right"></i>
  </button>`).join('');
}

function renderDeliverables(vm, errors){
  const el = document.getElementById('ov-deliverables');
  if (!el) return;
  if (errors.has('documents')) { el.innerHTML = empty('Bibliothèque documentaire indisponible.'); return; }
  if (!vm.documents.length) { el.innerHTML = empty('Aucun document récent dans Document Studio.'); return; }
  el.innerHTML = vm.documents.slice(0, 5).map(document => {
    const meta = obj(document.metadata);
    const verified = first(meta.render_verified, meta.verified, meta.validation_status === 'verified');
    return `<div class="overview-row">
      <div class="overview-row-main"><span class="overview-row-icon"><i data-lucide="${filenameIcon(document.format)}"></i></span><span class="overview-row-text">${safe(first(document.title, document.filename, 'Document'))}<small>${safe(first(document.source_kind, document.template_id, 'Document Studio'))}</small></span></div>
      <div class="overview-row-meta">${safe(String(document.format || '').toUpperCase())} · ${safe(formatTime(first(document.imported_at, meta.created_at)))}</div>
      <span class="overview-status ${verified===true?'is-ok':verified===false?'is-warn':''}">${verified===true?'Vérifié':verified===false?'Non certifié':'Enregistré'}</span>
    </div>`;
  }).join('');
}

function renderHealth(vm, errors){
  const el = document.getElementById('ov-health');
  if (!el) return;
  const known = !errors.has('status');
  const sloSamples = number(first(vm.status.slo_samples, vm.status.slo_samples_total));
  const hasSloSamples = sloSamples > 0 || number(vm.status.pipeline_chat_requests_total) > 0 || number(vm.status.slo_success_rate) > 0;
  const successRate = vm.status.slo_enabled ? (hasSloSamples ? `${(number(vm.status.slo_success_rate) * 100).toFixed(1)} %` : 'En attente') : 'Désactivé';
  const modules = known && vm.totalModules ? `${vm.activeModules}/${vm.totalModules}` : '—';
  const auditKnown = !errors.has('audit') && Number.isFinite(Number(vm.audit.total_tools));
  const auditedTools = auditKnown ? number(vm.audit.total_tools) : null;
  const callableTools = auditKnown ? number(vm.audit.contract_callable_any_context) : null;
  const toolDomains = auditKnown ? number(vm.audit.categories) : null;
  const toolDrift = auditKnown ? number(vm.audit.drift_count) : null;
  const brokenTools = auditKnown ? number(vm.audit.broken_count) : null;
  const rows = [
    ['Modules actifs',modules,known && vm.activeModules===vm.totalModules?'is-ok':known?'is-warn':''],
    ['Succès SLO',known?successRate:'—',vm.status.slo_enabled && hasSloSamples && number(vm.status.slo_success_rate)>=.95?'is-ok':vm.status.slo_enabled && hasSloSamples?'is-warn':''],
    ['Erreurs',known?String(vm.errors):'—',vm.errors?'is-danger':'is-ok'],
    ['Timeouts',known?String(vm.timeouts):'—',vm.timeouts?'is-warn':'is-ok'],
    ['Latence P50',known&&vm.status.slo_enabled?`${number(vm.status.slo_latency_median_ms)} ms`:'—',''],
    ['Outils réussis',errors.has('reliability')?'—':String(number(obj(vm.reliability.tools).success_count)),'is-ok'],
    ['Erreurs outils',errors.has('reliability')?'—':String(number(obj(vm.reliability.tools).error_total)),number(obj(vm.reliability.tools).error_total)?'is-danger':'is-ok'],
    ['Refus policy',errors.has('reliability')?'—':String(number(obj(vm.reliability.policy).refuse_count)),number(obj(vm.reliability.policy).refuse_count)?'is-warn':''],
    ['Outils audités',auditKnown?String(auditedTools):'—',auditKnown&&auditedTools>0?'is-ok':''],
    ['Domaines d’outils',auditKnown?String(toolDomains):'—',auditKnown&&toolDomains>0?'is-ok':''],
    ['Outils appelables',auditKnown?`${callableTools}/${auditedTools}`:'—',auditKnown&&callableTools===auditedTools?'is-ok':auditKnown?'is-warn':''],
    ['Drift contractuel',auditKnown?String(toolDrift):'—',auditKnown&&toolDrift?'is-danger':auditKnown?'is-ok':''],
    ['Défauts structurels',auditKnown?String(brokenTools):'—',auditKnown&&brokenTools?'is-danger':auditKnown?'is-ok':''],
  ];
  el.innerHTML = rows.map(([label,value,cls]) => `<div class="overview-health-item"><div class="overview-health-label">${label}</div><div class="overview-health-value ${cls}">${safe(value)}</div></div>`).join('');
}

function renderOperations(vm, errors){
  const el = document.getElementById('ov-operations');
  if (!el) return;
  const incidents = arr(first(vm.daemon.incidents_today,obj(vm.daemon.ops).incidents_today));
  const rows = [
    ['Missions actives',errors.has('missions')?'—':String(vm.rootMissions.length || vm.activeMissions.length),vm.activeMissions.length?'is-ok':''],
    ['Planifications',errors.has('tasks')?'—':String(vm.tasks.length),vm.tasks.length?'is-ok':''],
    ['Backlog runtime',errors.has('status')?'—':String(number(vm.status.tasks_backlog)),number(vm.status.tasks_backlog)?'is-warn':'is-ok'],
    ['Attente I/O',errors.has('status')?'—':String(number(vm.status.tasks_waiting_io)),number(vm.status.tasks_waiting_io)?'is-warn':'is-ok'],
    ['Conversations actives',errors.has('status')?'—':String(number(vm.status.sessions_active)),number(vm.status.sessions_active)?'is-ok':''],
    ['Incidents daemon',errors.has('daemon')?'—':String(incidents.length),incidents.length?'is-danger':'is-ok'],
  ];
  el.innerHTML = rows.map(([label,value,cls]) => `<div class="overview-health-item"><div class="overview-health-label">${safe(label)}</div><div class="overview-health-value ${cls}">${safe(value)}</div></div>`).join('');
}

function capabilityState(known, active = null, labels = {}){
  if (!known) return {label:'INCONNU',cls:'is-muted'};
  if (active === true) return {label:labels.active || 'ACTIF',cls:'is-ok'};
  if (active === false) return {label:labels.inactive || 'INACTIF',cls:'is-warn'};
  return {label:labels.available || 'DISPONIBLE',cls:'is-neutral'};
}

function panelState(){
  return {label:'OUVRIR',cls:'is-neutral'};
}

function renderCapabilities(vm, errors){
  const el = document.getElementById('ov-capabilities');
  if (!el) return;
  const modules = obj(vm.status.modules);
  const anyCodex = vm.models.some(model => /codex/i.test(String(first(model.provider,model.name,''))));
  const auditKnown = !errors.has('audit') && Number.isFinite(Number(vm.audit.total_tools));
  const toolAuditLabel = auditKnown
    ? `${number(vm.audit.total_tools)} OUTILS · ${number(vm.audit.categories)} DOMAINES`
    : 'INCONNU';
  const groups = [
    ['IA & runtime',[
      ['message-square','Chat','chat',panelState()],
      ['cpu','Modèles','providers',capabilityState(!errors.has('models'),vm.currentModel?.available !== false)],
      ['wrench','Outils','tools',auditKnown?{label:toolAuditLabel,cls:'is-ok'}:capabilityState(false)],
      ['sparkles','Skills','instincts',capabilityState(!errors.has('status'),number(vm.status.skills_loaded)>0)],
      ['badge-check','Codex','config',capabilityState(!errors.has('models'),anyCodex,{inactive:'NON CONFIGURÉ'})],
      ['layers','Sessions','sessions',capabilityState(!errors.has('sessions'),vm.sessions.length>0,{inactive:'AUCUNE ACTIVE'})],
    ]],
    ['Connaissance',[
      ['database','Mémoire','memory',capabilityState(!errors.has('status'),modules.memory === true)],
      ['book-open','Journal','journal',capabilityState(!errors.has('journal'),vm.journal.length>0,{inactive:'VIDE'})],
      ['id-card','Identité','facts',panelState()],
      ['shield-check','Règles','rules',capabilityState(!errors.has('status'),modules.rules_loader === true)],
      ['zap','Instincts','instincts',capabilityState(!errors.has('status'),modules.instinct_system === true)],
      ['map','Repo Map','repomap',capabilityState(!errors.has('status'),modules.repo_map === true)],
      ['search','Code Search','search',capabilityState(!errors.has('status'),modules.code_index === true)],
    ]],
    ['Production',[
      ['target','Missions','missions',capabilityState(!errors.has('missions'),null)],
      ['calendar-clock','Planifications','tasks',capabilityState(!errors.has('tasks'),null)],
      ['files','Documents','document-studio',capabilityState(!errors.has('documents'),null)],
      ['folder-open','Fichiers','docs',panelState()],
      ['folder-kanban','Workspaces','workspaces',capabilityState(!errors.has('serving'),null)],
      ['globe','Previews web','workspaces',capabilityState(!errors.has('serving'),vm.serving.length>0,{inactive:'ARRÊTÉ'})],
    ]],
    ['Autonomie & apprentissage',[
      ['bot','Autonomie','infra-autonomy',capabilityState(!errors.has('status'),vm.status.autonomy_running === true)],
      ['webhook','Hooks','hooks',capabilityState(!errors.has('hooks'),vm.hooks.some(hook => hook.enabled),{inactive:'AUCUN ACTIF'})],
      ['graduation-cap','Apprentissage','training',capabilityState(!errors.has('training'),number(vm.training.total_conversations)>0,{inactive:'VIDE'})],
      ['brain-circuit','Fine-tuning','finetuning',capabilityState(!errors.has('finetuning'),Boolean(first(vm.finetuning.active_job,vm.finetuning.running,vm.finetuning.active,false)),{inactive:'INACTIF'})],
    ]],
    ['Connexions',[
      ['plug','MCP','mcp',capabilityState(!errors.has('mcp'),vm.mcp.available === true)],
      ['users','P2P','infra-network',capabilityState(!errors.has('peers'),vm.activePeers.length>0,{inactive:'AUCUN PAIR'})],
      ['send','Telegram','infra-telegram',capabilityState(!errors.has('status'),vm.status.telegram_running === true,{inactive:'INACTIF'})],
      ['message-circle','WhatsApp','infra-whatsapp',capabilityState(!errors.has('status'),vm.status.whatsapp_running === true,{inactive:'INACTIF'})],
      ['at-sign','Twitter / X','infra-autonomy',capabilityState(!errors.has('status'),vm.status.twitter_running === true,{inactive:vm.status.twitter_enabled?'ARRÊTÉ':'NON CONFIGURÉ'})],
      ['message-square-more','Discord','tools',{label:'NON TRACÉ',cls:'is-muted'}],
      ['cloud','IONOS','ionos',panelState()],
    ]],
    ['Commerce',[
      ['layout-dashboard','Vue Stripe','stripe-overview',panelState()],
      ['credit-card','Paiements','stripe-payments',panelState()],
      ['repeat-2','Abonnements','stripe-subscriptions',panelState()],
      ['package','Produits','stripe-products',panelState()],
    ]],
    ['Système',[
      ['mic','Voix','voice',capabilityState(!errors.has('voice'),vm.voice.running === true)],
      ['heart-pulse','Émotions','emotions',capabilityState(!errors.has('status'),modules.emotion_manager === true)],
      ['bell','Alertes','alerts',capabilityState(!errors.has('alerts'),vm.alertItems.length>0,{inactive:'AUCUNE'})],
      ['scroll-text','Logs','logs',panelState()],
      ['radio-tower','Trace','trace',capabilityState(!errors.has('trace'),vm.status.trace_enabled === true)],
      ['terminal','Console','console',panelState()],
      ['settings','Configuration','config',panelState()],
      ['book-marked','Documentation','product-docs',panelState()],
    ]],
  ];
  el.innerHTML = groups.map(([title,items]) => `<section class="overview-capability-group"><h3>${safe(title)}</h3><div>${items.map(([icon,label,panelName,state]) => `<button class="overview-capability" data-ov-panel="${panelName}"><i data-lucide="${icon}"></i><span>${safe(label)}</span><small class="${state.cls}">${safe(state.label)}</small></button>`).join('')}</div></section>`).join('');
}

function bindSourceActions(root){
  root?.querySelectorAll('[data-ov-retry]').forEach(button => {
    if (button.dataset.ovRetryBound) return;
    button.dataset.ovRetryBound='1';
    button.addEventListener('click',event => { event.stopPropagation(); loadOverview({keys:[button.dataset.ovRetry],force:true}); });
  });
}

function renderSources(){
  const summary = document.getElementById('ov-source-summary');
  const el = document.getElementById('ov-sources');
  if (!summary || !el) return;
  const records = Object.keys(SOURCE_DEFS).map(key => sourceCache[key] || {key,...SOURCE_DEFS[key],status:'pending',fetchedAt:0,lastSuccessAt:0,error:''});
  const healthy = records.filter(record => record.status === 'ok' && sourceFreshness(record)==='fresh').length;
  const stale = records.filter(record => sourceFreshness(record)==='stale').length;
  const failed = records.filter(record => record.status === 'error').length;
  summary.innerHTML = `<span><strong>${healthy}/${records.length}</strong> fraîches</span><span class="${stale?'is-warn':''}">${stale} périmées</span><span class="${failed?'is-danger':''}">${failed} indisponibles</span>`;
  el.innerHTML = records.map(record => {
    const freshness = sourceFreshness(record);
    const state = record.status === 'error' ? 'is-danger' : freshness === 'stale' ? 'is-warn' : record.status === 'ok' ? 'is-ok' : 'is-muted';
    const label = record.status === 'error' ? `ERREUR${record.httpStatus?` ${record.httpStatus}`:''}` : record.status === 'pending' ? 'EN ATTENTE' : freshness === 'stale' ? 'PÉRIMÉE' : 'OK';
    const meta = record.status === 'error' ? record.error : `${record.latencyMs ?? '—'} ms · ${record.lastSuccessAt?formatAge(record.lastSuccessAt):'jamais'}`;
    return `<div class="overview-source-row"><button data-ov-panel="${record.panel}" title="Ouvrir ${safe(record.label)}"><span><i data-lucide="database"></i>${safe(record.label)}</span><small>${safe(record.path)}</small></button><span class="overview-status ${state}">${label}</span><span class="overview-source-meta" title="${safe(meta)}">${safe(meta)}</span><button class="overview-source-retry" data-ov-retry="${record.key}" title="Réessayer ${safe(record.label)}" aria-label="Réessayer ${safe(record.label)}"><i data-lucide="rotate-cw"></i></button></div>`;
  }).join('');
  bindPanelActions(el);
  bindSourceActions(el);
}

function empty(text){ return `<div class="overview-empty">${safe(text)}</div>`; }

function nodeState(vm, errors, key){
  if (errors.has(key)) return 'danger';
  if (key === 'voice') return vm.voice.running ? 'ok' : 'muted';
  if (key === 'mcp') return vm.mcp.available ? 'ok' : 'warn';
  if (key === 'peers') return vm.activePeers.length ? 'ok' : 'muted';
  if (key === 'status') return vm.status.autonomy_running ? 'ok' : 'muted';
  return 'ok';
}

function renderCoreNodes(vm, errors){
  const el = document.getElementById('ov-core-nodes');
  if (!el) return;
  const nodes = [
    ['target','Missions','missions','missions','29%','22%'],
    ['database','Mémoire','status','memory','23%','49%'],
    ['files','Documents','documents','document-studio','30%','77%'],
    ['plug','MCP','mcp','mcp','71%','22%'],
    ['users','P2P','peers','infra-network','78%','49%'],
    ['mic','Voix','voice','voice','70%','77%'],
    ['zap','Autonomie','status','infra-autonomy','50%','88%'],
  ];
  el.innerHTML = nodes.map(([icon,label,source,target,left,top]) => `<button class="overview-node" data-state="${nodeState(vm,errors,source)}" data-ov-panel="${target}" style="left:${left};top:${top}" title="Ouvrir ${label}"><span class="overview-node-icon"><i data-lucide="${icon}"></i></span><span>${label}</span></button>`).join('');
}

function bindPanelActions(root = document){
  root.querySelectorAll('[data-ov-panel]').forEach(button => {
    if (button.dataset.ovBound) return;
    button.dataset.ovBound = '1';
    button.addEventListener('click', () => panel(button.dataset.ovPanel));
  });
}

function readLayout(){
  try {
    const value = JSON.parse(localStorage.getItem(OVERVIEW_LAYOUT_KEY) || '{}');
    return {order:arr(value.order), hidden:arr(value.hidden)};
  } catch (_) { return {order:[], hidden:[]}; }
}

function applyLayout(){
  const grid = document.getElementById('ov-widget-grid');
  if (!grid) return;
  const layout = readLayout();
  const known = new Set(OVERVIEW_WIDGETS.map(([id]) => id));
  const order = [...layout.order.filter(id => known.has(id)), ...OVERVIEW_WIDGETS.map(([id]) => id).filter(id => !layout.order.includes(id))];
  order.forEach(id => {
    const widget = grid.querySelector(`[data-ov-widget="${id}"]`);
    if (widget) { widget.hidden = layout.hidden.includes(id); grid.appendChild(widget); }
  });
  const shell = document.querySelector('.overview-shell');
  const density = localStorage.getItem(OVERVIEW_DENSITY_KEY) || 'standard';
  shell?.classList.toggle('is-compact', density === 'compact');
  const densityLabel = document.querySelector('#ov-density span');
  if (densityLabel) densityLabel.textContent = `Densité : ${density === 'compact' ? 'compacte' : 'standard'}`;
}

function saveLayout(){
  const grid = document.getElementById('ov-widget-grid');
  if (!grid) return;
  const widgets = [...grid.querySelectorAll('[data-ov-widget]')];
  localStorage.setItem(OVERVIEW_LAYOUT_KEY, JSON.stringify({
    order:widgets.map(widget => widget.dataset.ovWidget),
    hidden:widgets.filter(widget => widget.hidden).map(widget => widget.dataset.ovWidget),
  }));
}

function renderCustomizer(){
  const box = document.getElementById('ov-widget-toggles');
  if (!box) return;
  const layout = readLayout();
  box.innerHTML = OVERVIEW_WIDGETS.map(([id,label]) => `<label class="overview-toggle"><input type="checkbox" data-ov-toggle="${id}" ${layout.hidden.includes(id)?'':'checked'}><span>${safe(label)}</span></label>`).join('');
  box.querySelectorAll('[data-ov-toggle]').forEach(input => input.addEventListener('change', () => {
    const widget = document.querySelector(`[data-ov-widget="${input.dataset.ovToggle}"]`);
    if (widget) widget.hidden = !input.checked;
    saveLayout();
  }));
}

function setupDragAndDrop(){
  const grid = document.getElementById('ov-widget-grid');
  if (!grid || grid.dataset.ovDragBound) return;
  grid.dataset.ovDragBound = '1';
  let dragged = null;
  grid.addEventListener('dragstart', event => {
    const widget = event.target.closest('[data-ov-widget]');
    if (!widget) return;
    dragged = widget;
    widget.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
  });
  grid.addEventListener('dragover', event => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest('[data-ov-widget]');
    grid.querySelectorAll('.is-drag-target').forEach(item => item.classList.remove('is-drag-target'));
    if (target && target !== dragged) target.classList.add('is-drag-target');
  });
  grid.addEventListener('drop', event => {
    event.preventDefault();
    const target = event.target.closest('[data-ov-widget]');
    if (dragged && target && target !== dragged) {
      const rect = target.getBoundingClientRect();
      grid.insertBefore(dragged, event.clientY < rect.top + rect.height / 2 ? target : target.nextSibling);
      saveLayout();
    }
  });
  grid.addEventListener('dragend', () => {
    grid.querySelectorAll('.is-dragging,.is-drag-target').forEach(item => item.classList.remove('is-dragging','is-drag-target'));
    dragged = null;
  });
  grid.addEventListener('keydown', event => {
    const handle = event.target.closest('.overview-drag');
    if (!handle || !event.altKey || !['ArrowUp','ArrowDown'].includes(event.key)) return;
    const widget = handle.closest('[data-ov-widget]');
    if (!widget) return;
    event.preventDefault();
    const sibling = event.key === 'ArrowUp' ? widget.previousElementSibling : widget.nextElementSibling;
    if (!sibling) return;
    if (event.key === 'ArrowUp') grid.insertBefore(widget,sibling);
    else grid.insertBefore(sibling,widget);
    saveLayout();
    handle.focus();
  });
}

function setupControls(){
  if (overviewBound) return;
  overviewBound = true;
  document.getElementById('ov-refresh')?.addEventListener('click', () => loadOverview({force:true}));
  const customize = document.getElementById('ov-customize');
  const customizer = document.getElementById('ov-customizer');
  customize?.addEventListener('click', event => {
    event.stopPropagation();
    const open = !!customizer?.hidden;
    if (customizer) customizer.hidden = !open;
    customize.setAttribute('aria-expanded', String(open));
  });
  customizer?.addEventListener('click', event => event.stopPropagation());
  document.addEventListener('click', () => {
    if (customizer) customizer.hidden = true;
    customize?.setAttribute('aria-expanded','false');
  });
  document.getElementById('ov-layout-reset')?.addEventListener('click', () => {
    localStorage.removeItem(OVERVIEW_LAYOUT_KEY);
    localStorage.removeItem(OVERVIEW_DENSITY_KEY);
    renderCustomizer(); applyLayout();
  });
  document.getElementById('ov-density')?.addEventListener('click', () => {
    const next = localStorage.getItem(OVERVIEW_DENSITY_KEY) === 'compact' ? 'standard' : 'compact';
    localStorage.setItem(OVERVIEW_DENSITY_KEY,next); applyLayout();
  });
  document.getElementById('ov-core-pause')?.addEventListener('click', toggleCorePause);
  bindPanelActions(document.getElementById('panel-overview') || document);
  setupDragAndDrop();
}

function toggleCorePause(){
  overviewPaused = !overviewPaused;
  const button = document.getElementById('ov-core-pause');
  if (button) {
    button.setAttribute('aria-pressed',String(overviewPaused));
    button.title = overviewPaused ? 'Reprendre l’animation' : 'Suspendre l’animation';
    button.innerHTML = `<i data-lucide="${overviewPaused?'play':'pause'}"></i><span class="sr-only">${overviewPaused?'Reprendre':'Suspendre'} l’animation</span>`;
  }
  if (coreRuntime) {
    coreRuntime.paused = overviewPaused;
    if (overviewPaused) coreRuntime.stop();
    else coreRuntime.start();
  }
  if (window.lucide) window.lucide.createIcons();
}

async function ensureCore3D(){
  if (coreRuntime) { coreRuntime.start(); return coreRuntime; }
  const stage = document.getElementById('ov-core-stage');
  const canvas = document.getElementById('ov-core-canvas');
  const section = document.querySelector('.overview-core');
  if (!stage || !canvas || !section) return null;
  if (!window.WebGLRenderingContext) { section.classList.add('is-fallback'); return null; }
  try {
    const [THREE,loaderModule] = await Promise.all([
      import(/* @vite-ignore */ THREE_URL),
      import(/* @vite-ignore */ SVG_LOADER_URL),
    ]);
    const {SVGLoader} = loaderModule;
    const logoResponse = await fetch(LOGO_SVG_URL,{headers:authHeaders()});
    if (!logoResponse.ok) throw new Error(`Logo SVG indisponible (${logoResponse.status})`);
    const logoData = new SVGLoader().parse(await logoResponse.text());
    const renderer = new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x000000,0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34,1,.1,100);
    camera.position.set(0,0,5.4);

    const material = new THREE.MeshStandardMaterial({
      color:0xe67e22,roughness:.5,metalness:.06,side:THREE.DoubleSide,
    });
    const mark = new THREE.Group();
    logoData.paths.forEach(path => {
      path.toShapes().forEach(shape => {
        const geometry = new THREE.ExtrudeGeometry(shape,{
          depth:90,bevelEnabled:true,bevelThickness:2.5,bevelSize:2.2,
          bevelSegments:3,curveSegments:24,
        });
        mark.add(new THREE.Mesh(geometry,material));
      });
    });
    if (!mark.children.length) throw new Error('Le logo SVG ne contient aucune forme exploitable');
    const bounds = new THREE.Box3().setFromObject(mark);
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    mark.position.set(-center.x,-center.y,-center.z);
    const scale = 2.15 / Math.max(size.x,size.y);
    const logo = new THREE.Group();
    logo.add(mark);
    logo.scale.set(scale,-scale,scale);
    logo.rotation.y=-.42;
    const pivot = new THREE.Group();
    pivot.add(logo);
    pivot.rotation.x=-.12;
    scene.add(pivot);

    scene.add(new THREE.HemisphereLight(0xffd9b0,0x3a1808,.42));
    const key = new THREE.DirectionalLight(0xfff0d8,1.35); key.position.set(-4,5,5); scene.add(key);
    const rim = new THREE.DirectionalLight(0xff6a18,.85); rim.position.set(-1.5,1.5,-6); scene.add(rim);
    const fill = new THREE.DirectionalLight(0x88a6ff,.2); fill.position.set(5,-1.5,3); scene.add(fill);
    const front = new THREE.DirectionalLight(0xffd8b0,.28); front.position.set(1,.5,8); scene.add(front);

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
    const runtime = {renderer,scene,camera,logo,pivot,material,paused:overviewPaused || reducedMotion,viewportVisible:true,running:false,raf:0,resize:null,observer:null,start:null,stop:null,dispose:null};
    const resize = () => {
      const width = Math.max(1,stage.clientWidth), height = Math.max(1,stage.clientHeight);
      renderer.setSize(width,height,false); camera.aspect=width/height; camera.updateProjectionMatrix();
    };
    runtime.resize = new ResizeObserver(resize); runtime.resize.observe(stage); resize();
    let last = performance.now(),elapsed=0,rotY=logo.rotation.y,dragging=false,lastX=0;
    const animate = now => {
      if (!runtime.running) return;
      if (runtime.paused || !runtime.viewportVisible || !document.getElementById('panel-overview')?.classList.contains('active')) { runtime.stop(); return; }
      const delta = Math.min(.04,(now-last)/1000); last=now;
      elapsed+=delta;
      if (!dragging) rotY+=delta*.55;
      logo.rotation.y=rotY;
      pivot.position.y=Math.sin(elapsed*.8)*.04;
      renderer.render(scene,camera);
      runtime.raf = requestAnimationFrame(animate);
    };
    runtime.stop = () => {
      runtime.running=false;
      if (runtime.raf) cancelAnimationFrame(runtime.raf);
      runtime.raf=0;
    };
    runtime.start = () => {
      if (runtime.running || runtime.paused || document.hidden || !runtime.viewportVisible || !document.getElementById('panel-overview')?.classList.contains('active')) return;
      runtime.running=true;
      last=performance.now();
      runtime.raf=requestAnimationFrame(animate);
    };
    runtime.observer = new IntersectionObserver(entries => {
      runtime.viewportVisible=!!entries[0]?.isIntersecting;
      if (runtime.viewportVisible) runtime.start();
      else runtime.stop();
    },{threshold:.02});
    runtime.observer.observe(stage);
    const onVisibility = () => document.hidden ? runtime.stop() : runtime.start();
    const onPointerDown = event => {
      dragging=true; lastX=event.clientX; stage.classList.add('is-dragging');
      stage.setPointerCapture?.(event.pointerId);
    };
    const onPointerMove = event => {
      if (!dragging) return;
      rotY+=(event.clientX-lastX)*.01; lastX=event.clientX;
      if (!runtime.running) renderer.render(scene,camera);
    };
    const onPointerEnd = event => {
      dragging=false; stage.classList.remove('is-dragging');
      if (stage.hasPointerCapture?.(event.pointerId)) stage.releasePointerCapture(event.pointerId);
    };
    document.addEventListener('visibilitychange',onVisibility);
    stage.addEventListener('pointerdown',onPointerDown);
    stage.addEventListener('pointermove',onPointerMove);
    stage.addEventListener('pointerup',onPointerEnd);
    stage.addEventListener('pointercancel',onPointerEnd);
    runtime.dispose = () => {
      runtime.stop(); runtime.observer?.disconnect(); runtime.resize?.disconnect();
      document.removeEventListener('visibilitychange',onVisibility);
      stage.removeEventListener('pointerdown',onPointerDown);
      stage.removeEventListener('pointermove',onPointerMove);
      stage.removeEventListener('pointerup',onPointerEnd);
      stage.removeEventListener('pointercancel',onPointerEnd);
      mark.traverse(child => child.geometry?.dispose?.());
      material.dispose(); renderer.dispose();
    };
    coreRuntime=runtime;
    if (reducedMotion) renderer.render(scene,camera);
    else runtime.start();
    section.classList.remove('is-fallback');
    return runtime;
  } catch (error) {
    section.classList.add('is-fallback');
    console.warn('[overview] Three.js indisponible, repli statique:',error);
    return null;
  }
}

export function stopOverview(){
  if (overviewAbort) overviewAbort.abort();
  overviewAbort=null;
  if (overviewTimer) clearTimeout(overviewTimer);
  overviewTimer=0;
  if (coreRuntime) coreRuntime.stop();
}

function scheduleOverviewRefresh(vm){
  if (overviewTimer) clearTimeout(overviewTimer);
  overviewTimer=0;
  if (!document.getElementById('panel-overview')?.classList.contains('active')) return;
  const recommended = number(vm?.status?.status_poll_recommended_ms,12000);
  const delay = Math.max(5000,Math.min(30000,recommended));
  overviewTimer=setTimeout(() => loadOverview({keys:FAST_SOURCE_KEYS,background:true}),delay);
}

export async function loadOverview(options = {}){
  const panelEl = document.getElementById('panel-overview');
  if (!panelEl) return;
  setupControls(); renderCustomizer(); applyLayout();
  bindPanelActions(panelEl);
  ensureCore3D();

  if (overviewTimer) clearTimeout(overviewTimer);
  overviewTimer=0;
  if (overviewAbort) overviewAbort.abort();
  const controller = new AbortController();
  overviewAbort = controller;
  const generation = ++overviewGeneration;
  const refresh = document.getElementById('ov-refresh');
  if (!options.background) refresh?.classList.add('is-loading');
  const keys = arr(options.keys).length ? arr(options.keys).filter(key => SOURCE_DEFS[key]) : Object.keys(SOURCE_DEFS);
  const settled = await Promise.allSettled(keys.map(key => fetchSource(key,controller.signal)));
  if (generation !== overviewGeneration || controller.signal.aborted) return;
  settled.forEach((result,index) => {
    if (result.status === 'fulfilled') sourceCache[keys[index]]=result.value;
  });
  const errors = new Set(Object.entries(sourceCache).filter(([,record]) => record.status === 'error').map(([key]) => key));
  const sources = Object.fromEntries(Object.keys(SOURCE_DEFS).map(key => [key,sourceCache[key]?.data ?? null]));
  const vm = buildOverviewViewModel(sources);

  renderSummary(vm,errors);
  renderWork(vm,errors);
  renderAttention(vm,errors);
  renderActivity(vm,errors);
  renderSystems(vm,errors);
  renderDeliverables(vm,errors);
  renderHealth(vm,errors);
  renderOperations(vm,errors);
  renderCapabilities(vm,errors);
  renderSources();
  renderCoreNodes(vm,errors);
  bindPanelActions(panelEl);
  applyLayout();

  const updated = document.getElementById('ov-updated');
  const stamp = first(vm.status.server_time,new Date().toISOString());
  if (updated) updated.textContent = `Actualisé ${formatTime(stamp)} · ${errors.size ? `${errors.size} source(s) indisponible(s)` : `${Object.keys(sourceCache).length} sources tracées`}`;
  refresh?.classList.remove('is-loading');
  if (window.lucide) window.lucide.createIcons();
  scheduleOverviewRefresh(vm);
}
