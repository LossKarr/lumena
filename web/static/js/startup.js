/* ============================================================
   STARTUP & INIT — Lumena Control Panel
   ============================================================ */
// Init logic moved to main.js (module entry point)

/* ============================================================
   STARTUP
   ============================================================ */
export async function loadStartupModels(attempt = 1){
  const MAX = 8;
  try{
    try{const r=await fetch(`${API_BASE}/api/auth/config`);if(r.ok){const d=await r.json();ADMIN_TOKEN=d.admin_token||''}}catch(e){}
    const _mh={};if(ADMIN_TOKEN)_mh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const res=await fetch(`${API_BASE}/api/models`,{headers:_mh});
    if(!res.ok)throw new Error(`HTTP ${res.status}`);
    const data=await res.json();
    allModels=Array.isArray(data.models)?data.models:[];
    allModels.sort((a,b)=>{if(a.is_local&&!b.is_local)return-1;if(!a.is_local&&b.is_local)return 1;return a.provider.localeCompare(b.provider)});
    const c=document.getElementById('startup-models');
    c.innerHTML=allModels.map(m=>`
      <div class="startup-model-item ${!m.available?'disabled':''}"
           onclick="${m.available?`selectStartupModel('${m.name}')`:''}"
           id="smodel-${m.name}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-weight:500;color:var(--text)">${esc(m.display_name)}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:4px">${esc(m.description)} <span style="color:${m.is_free?'var(--ok)':'var(--warn)'}">${m.is_free?'Gratuit':'Payant'}</span> · <span style="color:var(--muted)">${m.is_local?'Local':'Cloud'}</span></div>
          </div>
          ${!m.available?'<span style="font-size:11px;color:var(--warn)">Cle manquante</span>':''}
        </div>
      </div>
    `).join('');
    const pref=allModels.find(m=>m.current&&m.available)||allModels.find(m=>m.available);
    if(pref)selectStartupModel(pref.name);
  }catch(e){
    if(attempt >= MAX){
      document.getElementById('startup-models').innerHTML='<div style="color:var(--danger);padding:20px">Serveur inaccessible — relancez START.bat</div>';
      return;
    }
    document.getElementById('startup-models').innerHTML=`<div style="color:var(--muted);padding:20px">Connexion... (${attempt}/${MAX})</div>`;
    setTimeout(() => loadStartupModels(attempt + 1), 2000);
  }
}

export function selectStartupModel(name){
  document.querySelectorAll('.startup-model-item').forEach(el=>el.classList.remove('selected'));
  const item=document.getElementById('smodel-'+name);
  if(item){item.classList.add('selected');selectedModel=name;document.getElementById('startup-btn').disabled=false}
}

export async function startLumena(){
  if(!selectedModel)return;
  const btn=document.getElementById('startup-btn');
  btn.textContent='Initialisation...';btn.disabled=true;
  try{
    const cur=allModels.find(m=>m.current);
    if(!cur||cur.name!==selectedModel){
      const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
      const r=await fetch(`${API_BASE}/api/model/switch`,{method:'POST',headers:h,body:JSON.stringify({model_name:selectedModel})});
      if(!r.ok){const err=await r.json();throw new Error(err.detail)}
    }
    document.getElementById('startup-screen').classList.add('hidden');
    document.getElementById('app-shell').style.display='grid';
    // Restore saved theme
    const savedTheme=localStorage.getItem('lumena_theme');
    if(savedTheme&&typeof applyTheme==='function')applyTheme(savedTheme);
    setupNavigation();setupTextarea();loadStatus();loadModels();loadTools();initTraceStream();loadTraceRecent();startLiveRefreshLoops();loadChatHistory();
    document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModelPicker()});
  }catch(e){
    alert('Erreur: '+e.message);btn.textContent='Demarrer Lumena';btn.disabled=false;
  }
}

/* ============================================================
   MODEL PICKER (modal)
   ============================================================ */
const _PROVIDER_META = {
  deepseek: { label: 'DeepSeek',  color: '#4f8ef7' },
  openai:   { label: 'OpenAI',    color: '#10a37f' },
  anthropic:{ label: 'Anthropic', color: '#e2812a' },
  google:   { label: 'Google',    color: '#ea4335' },
  nvidia:   { label: 'NVIDIA',    color: '#76b900' },
  xai:      { label: 'xAI',       color: '#a855f7' },
  minimax:  { label: 'MiniMax',   color: '#8b5cf6' },
  moonshot: { label: 'Moonshot',  color: '#0ea5e9' },
  zai:      { label: 'Z.AI',      color: '#00c8a0' },
  ollama:   { label: 'Ollama',    color: '#f59e0b' },
};
const _PROVIDER_ORDER = ['deepseek','openai','anthropic','google','nvidia','xai','minimax','moonshot','zai','ollama'];

let _mpFilter = 'all';
let _mpSearch = '';

export function toggleModelDropdown(){
  const m = document.getElementById('model-picker-modal');
  if(m.classList.contains('open')){ closeModelPicker(); }
  else {
    m.classList.add('open');
    document.getElementById('model-picker-search').value = '';
    _mpSearch = '';
    setTimeout(()=>document.getElementById('model-picker-search').focus(), 50);
    _renderModelPicker();
    if(typeof lucide!=='undefined') lucide.createIcons({el:m});
  }
}

export function closeModelPicker(){
  document.getElementById('model-picker-modal').classList.remove('open');
}

export function setModelFilter(provider){
  _mpFilter = provider;
  document.querySelectorAll('.mpicker-pill').forEach(p=>p.classList.toggle('active', p.dataset.provider===provider));
  _renderModelPicker();
}

export function filterModelSearch(q){
  _mpSearch = q;
  _renderModelPicker();
}

function _ctxLabel(n){
  if(n>=1000000) return (n/1000000).toFixed(1)+'M';
  if(n>=1000) return Math.round(n/1000)+'K';
  return n+'';
}

function _badgeClass(b){
  if(!b) return '';
  const k = b.toLowerCase();
  if(k==='recommandé'||k==='recommande') return 'badge-recommended';
  if(k==='reasoning') return 'badge-reasoning';
  if(k==='legacy') return 'badge-legacy';
  if(k==='fallback') return 'badge-legacy';
  if(k==='gratuit') return 'badge-free';
  if(k==='beta') return 'badge-beta';
  return 'badge-default';
}

function _renderCard(m, color){
  const ctx = _ctxLabel(m.context_window||0);
  const tags = [];
  if(m.supports_vision) tags.push('<span class="mpicker-tag">Vision</span>');
  if(m.is_local) tags.push('<span class="mpicker-tag tag-local">Local</span>');
  tags.push(`<span class="mpicker-tag">${ctx} ctx</span>`);
  const badge = m.badge ? `<span class="mpicker-badge ${_badgeClass(m.badge)}">${esc(m.badge)}</span>` : '';
  const nameParts = m.display_name.split(' (');
  const shortName = nameParts[0];
  const providerSub = nameParts[1] ? nameParts[1].replace(')','') : '';
  return `<div class="mpicker-card${m.current?' is-current':''}${!m.available?' is-unavailable':''}"
     style="--pc:${color}"
     onclick="${m.available?`switchModel('${m.name}')`:''}"
     title="${esc(m.display_name)}">
    <div class="mpicker-card-stripe"></div>
    <div class="mpicker-card-inner">
      <div class="mpicker-card-head">
        <span class="mpicker-card-name">${esc(shortName)}</span>
        <div class="mpicker-card-badges">
          ${m.current?'<span class="mpicker-badge badge-active">✓ Actif</span>':''}
          ${!m.available?'<span class="mpicker-badge badge-nokey">Clé manquante</span>':''}
          ${badge}
        </div>
      </div>
      ${providerSub?`<div class="mpicker-card-provider">${esc(providerSub)}</div>`:''}
      <div class="mpicker-card-desc">${esc(m.description||'')}</div>
      <div class="mpicker-card-tags">${tags.join('')}</div>
    </div>
  </div>`;
}

function _renderModelPicker(){
  const search = _mpSearch.trim().toLowerCase();
  const filter = _mpFilter;
  const byProv = {};
  for(const m of allModels){
    if(filter!=='all' && m.provider!==filter) continue;
    if(search && !m.display_name.toLowerCase().includes(search) && !(m.description||'').toLowerCase().includes(search)) continue;
    (byProv[m.provider]||(byProv[m.provider]=[])).push(m);
  }
  const body = document.getElementById('model-picker-body');
  if(!body) return;
  const keys = Object.keys(byProv);
  if(!keys.length){
    body.innerHTML='<div class="mpicker-empty">Aucun modèle trouvé</div>';
    return;
  }
  const ordered = [..._PROVIDER_ORDER, ...keys.filter(p=>!_PROVIDER_ORDER.includes(p))];
  body.innerHTML = ordered.filter(p=>byProv[p]?.length).map(p=>{
    const meta = _PROVIDER_META[p]||{label:p, color:'#888'};
    const models = byProv[p];
    return `<div class="mpicker-section">
      <div class="mpicker-section-hdr">
        <span class="mpicker-section-dot" style="background:${meta.color}"></span>
        <span class="mpicker-section-label">${meta.label}</span>
        <span class="mpicker-section-count">${models.length}</span>
      </div>
      <div class="mpicker-cards">${models.map(m=>_renderCard(m, meta.color)).join('')}</div>
    </div>`;
  }).join('');
}

export async function loadModels(){
  try{
    const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const res=await fetch(`${API_BASE}/api/models`,{headers:h});
    if(!res.ok)throw new Error(`HTTP ${res.status}`);
    const data=await res.json();
    allModels=Array.isArray(data.models)?data.models:[];
    const cur=allModels.find(m=>m.current);
    if(cur)document.getElementById('current-model-name').textContent=cur.display_name.split(' (')[0];
    // Build provider pills
    const providers=[...new Set(allModels.map(m=>m.provider))];
    const pillsEl=document.getElementById('model-picker-filters');
    if(pillsEl){
      pillsEl.innerHTML=[
        `<button class="mpicker-pill active" data-provider="all" onclick="setModelFilter('all')">Tous <span>${allModels.length}</span></button>`,
        ...[..._PROVIDER_ORDER,...providers.filter(p=>!_PROVIDER_ORDER.includes(p))]
          .filter(p=>providers.includes(p))
          .map(p=>{
            const meta=_PROVIDER_META[p]||{label:p,color:'#888'};
            const cnt=allModels.filter(m=>m.provider===p).length;
            return `<button class="mpicker-pill" data-provider="${p}" onclick="setModelFilter('${p}')"
              style="--pill-color:${meta.color}">${meta.label} <span>${cnt}</span></button>`;
          })
      ].join('');
    }
    if(document.getElementById('model-picker-modal').classList.contains('open')){
      _renderModelPicker();
    }
  }catch(e){document.getElementById('current-model-name').textContent='Erreur'}
}

export async function switchModel(name){
  closeModelPicker();
  document.getElementById('current-model-name').textContent='Changement...';
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/model/switch`,{method:'POST',headers:h,body:JSON.stringify({model_name:name})});
    if(!r.ok){const err=await r.json();throw new Error(err.detail)}
    const d=await r.json();
    document.getElementById('current-model-name').textContent=d.display_name.split(' (')[0];
    logC(d.message,'success');loadStatus();loadModels();
    addMsg('assistant',`**Modele change** : J'utilise maintenant **${esc(d.display_name)}** !`);
  }catch(e){logC(e.message,'error');loadModels()}
}

export function toggleAgent(){
  useAgent=!useAgent;
  localStorage.setItem('lumena_agent_mode', useAgent ? 'true' : 'false');
  const btn=document.getElementById('agent-toggle');
  btn.innerHTML=useAgent?'<i data-lucide="plug"></i> Agent ON':'<i data-lucide="plug"></i> Agent OFF';
  btn.classList.toggle('active',useAgent);
}

/* ============================================================
   LIVE REFRESH
   ============================================================ */
export function startLiveRefreshLoops(){
  if(liveRefreshLoopsStarted){scheduleStatusRefresh(true);return}
  liveRefreshLoopsStarted=true;
  scheduleStatusRefresh(true);
  panelRefreshTimer=setInterval(()=>{
    if(document.hidden)return;
    const active=document.querySelector('.nav-item.active');
    const p=active?active.dataset.panel:'chat';
    // Seuls les panneaux live se rafraîchissent automatiquement
    const livePanels=['overview','trace','emotions','tasks','sessions'];
    if(p&&livePanels.includes(p)){
      // Sauvegarder scroll avant refresh, restaurer après
      const panelEl=document.getElementById('panel-'+p);
      const scrollY=panelEl?panelEl.scrollTop:0;
      loadPanelData(p);
      if(panelEl)requestAnimationFrame(()=>{panelEl.scrollTop=scrollY});
    }
  },4000);
  document.addEventListener('visibilitychange',()=>scheduleStatusRefresh(true));
}

export function scheduleStatusRefresh(immediate=false){
  if(statusRefreshTimer)clearTimeout(statusRefreshTimer);
  statusRefreshTimer=setTimeout(async()=>{await loadStatus();scheduleStatusRefresh(false)},immediate?0:Math.max(3000,statusPollRecommendedMs||3000));
}

/* Cleanup */
window.addEventListener('beforeunload',()=>{
  if(traceEventSource){traceEventSource.close();traceEventSource=null}
  if(statusRefreshTimer){clearTimeout(statusRefreshTimer);statusRefreshTimer=null}
  if(panelRefreshTimer){clearInterval(panelRefreshTimer);panelRefreshTimer=null}
});
