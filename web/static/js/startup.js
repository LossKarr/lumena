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
    allModels=(Array.isArray(data.models)?data.models:[]).filter(m=>!m.supports_image_generation);
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
  codex:    { label: 'Codex · Abonnement ChatGPT', color: '#f59f4a' },
  anthropic:{ label: 'Anthropic', color: '#e2812a' },
  google:   { label: 'Google',    color: '#ea4335' },
  nvidia:   { label: 'NVIDIA',    color: '#76b900' },
  xai:      { label: 'xAI',       color: '#a855f7' },
  minimax:  { label: 'MiniMax',   color: '#8b5cf6' },
  moonshot: { label: 'Moonshot',  color: '#0ea5e9' },
  zai:      { label: 'Z.AI',      color: '#00c8a0' },
  ollama:   { label: 'Ollama',    color: '#f59e0b' },
  gemini:   { label: 'Gemini',    color: '#ea4335' },
  imagen:   { label: 'Imagen',    color: '#34a853' },
  flux:     { label: 'FLUX',      color: '#f97316' },
  stability:{ label: 'Stability', color: '#38bdf8' },
  ideogram: { label: 'Ideogram',  color: '#ec4899' },
  recraft:  { label: 'Recraft',   color: '#14b8a6' },
  replicate:{ label: 'Replicate', color: '#64748b' },
  huggingface:{ label: 'HF',      color: '#f59e0b' },
};
const _PROVIDER_ORDER = ['deepseek','openai','codex','anthropic','google','nvidia','xai','minimax','moonshot','zai','ollama','gemini','imagen','flux','stability','ideogram','recraft','replicate','huggingface'];

let _mpFilter = 'all';
let _mpSearch = '';
let _mpPanel = 'text';
let _mpSource = 'api';
let _mpAccessMode = '';
let _mpApiModelId = '';
let _mpCodexModelId = '';
let _mpSourceSwitching = false;

export function toggleModelDropdown(){
  const m = document.getElementById('model-picker-modal');
  if(m.classList.contains('open')){ closeModelPicker(); }
  else {
    m.classList.add('open');
    document.getElementById('model-picker-search').value = '';
    _mpSearch = '';
    loadImageModels();
    setTimeout(()=>document.getElementById('model-picker-search').focus(), 50);
    _renderModelSourceControl();
    _renderModelFilters();
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

export function setModelPanel(panel){
  _mpPanel = ['text','vision','image'].includes(panel) ? panel : 'text';
  if(_mpPanel==='image')_mpSource='api';
  _mpFilter = 'all';
  document.querySelectorAll('.mpicker-tab').forEach(t=>t.classList.toggle('active', t.dataset.panel===_mpPanel));
  if(_mpPanel==='image') loadImageModels();
  _renderModelSourceControl();
  _renderModelFilters();
  _renderModelPicker();
}

function _preferredSelectionForSource(source){
  if(source==='codex'){
    if(_mpCodexModelId)return `codex:${_mpCodexModelId}`;
    return allModels.find(model=>model.provider==='codex'&&model.available)?.name||'';
  }
  if(_mpApiModelId)return _mpApiModelId;
  return allModels.find(model=>model.provider!=='codex'&&model.current)?.name
    ||allModels.find(model=>model.provider!=='codex'&&model.available)?.name||'';
}

function _setModelSourceBusy(busy){
  _mpSourceSwitching=busy;
  const control=document.getElementById('model-picker-source');
  if(!control)return;
  control.classList.toggle('is-switching',busy);
  control.querySelectorAll('.mpicker-source-btn').forEach(button=>{button.disabled=busy});
}

export async function setModelSource(source){
  if(_mpPanel==='image')return;
  if(_mpSourceSwitching)return;
  const nextSource=source==='codex'?'codex':'api';
  const previousSource=_mpSource;
  _mpSource=nextSource;
  _mpFilter='all';
  _renderModelSourceControl();
  _renderModelFilters();
  _renderModelPicker();
  const expectedMode=nextSource==='codex'?'chatgpt_codex':'api';
  if(_mpAccessMode===expectedMode)return;
  const selectionId=_preferredSelectionForSource(nextSource);
  if(!selectionId){
    logC(nextSource==='codex'
      ?'Aucun modele Codex disponible pour cet abonnement'
      :'Aucun modele API disponible','error');
    _mpSource=previousSource;
    _renderModelSourceControl();
    _renderModelFilters();
    _renderModelPicker();
    return;
  }
  _setModelSourceBusy(true);
  const switched=await _selectCatalogModel(selectionId,{closePicker:false,announce:false});
  _setModelSourceBusy(false);
  if(!switched){
    _mpSource=previousSource;
    _renderModelSourceControl();
    _renderModelFilters();
    _renderModelPicker();
  }
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
  if(m.provider==='codex') tags.push('<span class="mpicker-tag tag-local">Abonnement ChatGPT</span>');
  else tags.push(`<span class="mpicker-tag">${ctx} ctx</span>`);
  const badge = m.badge ? `<span class="mpicker-badge ${_badgeClass(m.badge)}">${esc(m.badge)}</span>` : '';
  const nameParts = m.display_name.split(' (');
  const shortName = nameParts[0];
  const providerSub = m.source_label || (nameParts[1] ? nameParts[1].replace(')','') : '');
  return `<div class="mpicker-card${m.current?' is-current':''}${!m.available?' is-unavailable':''}"
     style="--pc:${color}"
     onclick="${m.available?`switchCatalogModel('${m.name}')`:''}"
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

function _renderImageCard(m, color){
  const tags = [];
  if(m.free) tags.push('<span class="mpicker-tag tag-local">Gratuit</span>');
  else tags.push('<span class="mpicker-tag">Payant</span>');
  if(m.max_resolution) tags.push(`<span class="mpicker-tag">${esc(m.max_resolution)}</span>`);
  if(m.quality) tags.push(`<span class="mpicker-tag">Q${esc(String(m.quality))}</span>`);
  if(m.speed) tags.push(`<span class="mpicker-tag">V${esc(String(m.speed))}</span>`);
  const caps = Array.isArray(m.capabilities) ? m.capabilities.slice(0,3).join(', ') : '';
  const desc = m.best_for || m.strengths || caps || '';
  const title = m.display_name || m.name;
  return `<div class="mpicker-card${!m.available?' is-unavailable':''}" style="--pc:${color}" title="${esc(title)}">
    <div class="mpicker-card-stripe"></div>
    <div class="mpicker-card-inner">
      <div class="mpicker-card-head">
        <span class="mpicker-card-name">${esc(title)}</span>
        <div class="mpicker-card-badges">
          ${!m.available?'<span class="mpicker-badge badge-nokey">Cle manquante</span>':''}
          <span class="mpicker-badge ${m.free?'badge-free':'badge-default'}">${m.free?'Gratuit':'Image'}</span>
        </div>
      </div>
      <div class="mpicker-card-provider">${esc(m.provider||'image')}</div>
      <div class="mpicker-card-desc">${esc(desc)}</div>
      <div class="mpicker-card-tags">${tags.join('')}</div>
    </div>
  </div>`;
}

function _modelPickerItems(){
  if(_mpPanel==='vision') return allModels.filter(m=>m.supports_vision && !m.supports_image_generation).filter(_sourceModelMatch);
  if(_mpPanel==='image') return allImageModels;
  return allModels.filter(m=>!m.supports_image_generation).filter(_sourceModelMatch);
}

function _sourceModelMatch(model){
  return _mpSource==='codex' ? model.provider==='codex' : model.provider!=='codex';
}

function _renderModelSourceControl(){
  const control=document.getElementById('model-picker-source');
  if(!control)return;
  control.hidden=_mpPanel==='image';
  control.querySelectorAll('.mpicker-source-btn').forEach(button=>{
    button.classList.toggle('active',button.dataset.source===_mpSource);
  });
  const apiCount=allModels.filter(model=>model.provider!=='codex'&&!model.supports_image_generation).length;
  const codexCount=allModels.filter(model=>model.provider==='codex'&&!model.supports_image_generation).length;
  const apiEl=document.getElementById('model-source-api-count');
  const codexEl=document.getElementById('model-source-codex-count');
  if(apiEl)apiEl.textContent=String(apiCount);
  if(codexEl)codexEl.textContent=String(codexCount);
}

function _searchModelMatch(m, search){
  if(!search) return true;
  const hay = [
    m.name, m.display_name, m.provider, m.description,
    m.strengths, m.best_for,
    Array.isArray(m.capabilities)?m.capabilities.join(' '):''
  ].join(' ').toLowerCase();
  return hay.includes(search);
}

function _renderModelFilters(){
  const items = _modelPickerItems();
  const providers=[...new Set(items.map(m=>m.provider||'image'))];
  const pillsEl=document.getElementById('model-picker-filters');
  if(!pillsEl) return;
  const allLabel = _mpPanel==='image' ? 'Tous' : (_mpPanel==='vision' ? 'Vision' : 'Tous');
  pillsEl.innerHTML=[
    `<button class="mpicker-pill active" data-provider="all" onclick="setModelFilter('all')">${allLabel} <span>${items.length}</span></button>`,
    ...[..._PROVIDER_ORDER,...providers.filter(p=>!_PROVIDER_ORDER.includes(p))]
      .filter(p=>providers.includes(p))
      .map(p=>{
        const meta=_PROVIDER_META[p]||{label:p,color:'#888'};
        const cnt=items.filter(m=>(m.provider||'image')===p).length;
        return `<button class="mpicker-pill" data-provider="${p}" onclick="setModelFilter('${p}')"
          style="--pill-color:${meta.color}">${meta.label} <span>${cnt}</span></button>`;
      })
  ].join('');
}

function _renderModelPicker(){
  const search = _mpSearch.trim().toLowerCase();
  const filter = _mpFilter;
  const byProv = {};
  for(const m of _modelPickerItems()){
    const provider = m.provider || 'image';
    if(filter!=='all' && provider!==filter) continue;
    if(!_searchModelMatch(m, search)) continue;
    (byProv[provider]||(byProv[provider]=[])).push(m);
  }
  const body = document.getElementById('model-picker-body');
  if(!body) return;
  const keys = Object.keys(byProv);
  if(!keys.length){
    body.innerHTML=_mpSource==='codex'&&_mpPanel!=='image'
      ?'<div class="mpicker-empty">Aucun modele Codex disponible. Connectez une premiere fois votre abonnement ChatGPT dans Configuration.</div>'
      :'<div class="mpicker-empty">Aucun modèle trouvé</div>';
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
      <div class="mpicker-cards">${models.map(m=>_mpPanel==='image'?_renderImageCard(m, meta.color):_renderCard(m, meta.color)).join('')}</div>
    </div>`;
  }).join('');
}

async function _loadCodexPickerModels(headers, apiModels){
  try{
    const response=await fetch(`${API_BASE}/api/codex-subscription/models`,{headers});
    if(!response.ok)return apiModels;
    const data=await response.json();
    _mpAccessMode=data.access_mode==='chatgpt_codex'?'chatgpt_codex':'api';
    _mpCodexModelId=typeof data.selected_model==='string'?data.selected_model:'';
    const codexModels=Array.isArray(data.models)?data.models.map(model=>({
      name:`codex:${model.model_id}`,
      display_name:model.display_name||model.model_id,
      provider:'codex',
      description:model.description||'Modele disponible avec le quota de votre abonnement ChatGPT.',
      badge:model.is_default?'Recommande':'Abonnement',
      is_local:false,
      is_free:false,
      supports_vision:Array.isArray(model.input_modalities)&&model.input_modalities.includes('image'),
      supports_image_generation:false,
      context_window:0,
      available:true,
      current:data.access_mode==='chatgpt_codex'&&data.selected_model===model.model_id,
      source_label:'Quota abonnement ChatGPT · pas de facturation API',
    })):[];
    if(data.access_mode==='chatgpt_codex')apiModels.forEach(model=>{model.current=false});
    return [...apiModels,...codexModels];
  }catch(_error){
    return apiModels;
  }
}

export async function loadModels(){
  try{
    const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const res=await fetch(`${API_BASE}/api/models`,{headers:h});
    if(!res.ok)throw new Error(`HTTP ${res.status}`);
    const data=await res.json();
    const apiModels=Array.isArray(data.models)?data.models:[];
    const apiCurrent=apiModels.find(model=>model.current);
    if(apiCurrent)_mpApiModelId=apiCurrent.name;
    allModels=await _loadCodexPickerModels(h,apiModels);
    const cur=allModels.find(m=>m.current);
    if(_mpPanel!=='image'){
      if(_mpAccessMode==='chatgpt_codex')_mpSource='codex';
      else if(_mpAccessMode==='api')_mpSource='api';
    }
    if(cur)document.getElementById('current-model-name').textContent=cur.display_name.split(' (')[0];
    _renderModelSourceControl();
    _renderModelFilters();
    if(document.getElementById('model-picker-modal').classList.contains('open')){
      _renderModelPicker();
    }
  }catch(e){document.getElementById('current-model-name').textContent='Erreur'}
}

export async function loadImageModels(){
  try{
    const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const res=await fetch(`${API_BASE}/api/images/models`,{headers:h});
    if(!res.ok)throw new Error(`HTTP ${res.status}`);
    const data=await res.json();
    allImageModels=Array.isArray(data.models)?data.models:[];
    if(document.getElementById('model-picker-modal').classList.contains('open')){
      _renderModelFilters();
      _renderModelPicker();
    }
  }catch(e){
    allImageModels=[];
  }
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

function _selectionError(detail, fallback){
  if(typeof detail==='string')return detail;
  if(detail&&typeof detail.message==='string')return detail.message;
  return fallback;
}

async function _selectCatalogModel(selectionId,{closePicker=true,announce=true}={}){
  if(closePicker)closeModelPicker();
  document.getElementById('current-model-name').textContent='Changement...';
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const response=await fetch(`${API_BASE}/api/codex-subscription/model/select`,{
      method:'POST',headers:h,body:JSON.stringify({selection_id:selectionId}),
    });
    const data=await response.json();
    if(!response.ok)throw new Error(_selectionError(data.detail,`HTTP ${response.status}`));
    _mpAccessMode=data.access_mode==='chatgpt_codex'?'chatgpt_codex':'api';
    if(data.engine==='codex')_mpCodexModelId=String(data.model||selectionId).replace(/^codex:/,'');
    else _mpApiModelId=data.model||selectionId;
    document.getElementById('current-model-name').textContent=(data.display_name||selectionId).split(' (')[0];
    logC(data.message||'Modele change','success');
    loadStatus();await loadModels();
    const source=data.engine==='codex'?'abonnement ChatGPT':'API';
    if(announce)addMsg('assistant',`**Modele change** : J'utilise maintenant **${esc(data.display_name||selectionId)}** via ${source}.`);
    return true;
  }catch(error){
    logC(error.message,'error');
    await loadModels();
    return false;
  }
}

export async function switchCatalogModel(selectionId){
  return _selectCatalogModel(selectionId);
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
    // Overview owns its wider, cancellable source batch and schedules its own
    // backend-recommended refresh. Reloading it here every 4 s aborts that batch.
    const livePanels=['trace','emotions','tasks','sessions'];
    if(p&&livePanels.includes(p)){
      // Sauvegarder scroll avant refresh, restaurer après
      const panelEl=document.getElementById('panel-'+p);
      const scrollY=panelEl?panelEl.scrollTop:0;
      loadPanelData(p);
      if(panelEl)requestAnimationFrame(()=>{panelEl.scrollTop=scrollY});
    }else if(p==='infra-network'){
      // Cran 1 : rafraîchissement CIBLÉ (présence + missions) — ne re-render PAS
      // les cartes de pairs (préserve les tiroirs de config ouverts). Auto-throttlé.
      if(typeof window.refreshNetworkLive==='function')window.refreshNetworkLive();
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
