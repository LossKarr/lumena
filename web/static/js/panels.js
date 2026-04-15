/* ============================================================
   PANEL LOADERS — Lumena Control Panel
   ============================================================ */

/* ============================================================
   JOURNAL
   ============================================================ */
let _journalData=null;
export async function loadJournal(){
  const list=document.getElementById('journal-list');
  if(list)list.innerHTML=loadingDots('Chargement...');
  const typeVal=document.getElementById('journal-type-filter')?.value||'';
  try{
    const url=typeVal?`${API_BASE}/api/journal?limit=200&type=${encodeURIComponent(typeVal)}`:`${API_BASE}/api/journal?limit=200`;
    const r=await fetch(url,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    _journalData=await r.json();
  }catch(e){if(list)list.innerHTML=`<div style="color:var(--muted);padding:12px">Erreur: ${esc(e.message)}</div>`;return;}
  const badge=document.getElementById('badge-journal');
  if(badge&&_journalData){badge.textContent=_journalData.total||0;badge.style.background='var(--accent)'}
  renderJournal();
}
export function renderJournal(){
  const list=document.getElementById('journal-list');if(!list||!_journalData)return;
  const entries=_journalData.entries||[];
  if(!entries.length){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucune entree dans le journal.</div>';return;}
  const typeColors={action:'accent',thought:'ok',learning:'info',error:'danger',test:'muted'};
  list.innerHTML=entries.map(e=>{
    const ts=(e.timestamp||'').substring(0,19).replace('T',' ');
    const type=e.type||'action';
    const content=(e.content||'').substring(0,200);
    return`<div class="list-item">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
          <span class="pill ${typeColors[type]||'accent'}" style="font-size:10px;padding:1px 7px">${esc(type)}</span>
          <span style="font-size:11px;color:var(--muted)">${esc(ts)}</span>
        </div>
        <div style="font-size:12px;color:var(--text);word-break:break-word">${esc(content)}${(e.content||'').length>200?'…':''}</div>
      </div>
    </div>`;
  }).join('');
}

/* ============================================================
   FACTS / IDENTITE
   ============================================================ */
const _ESSENTIAL_KEYS=['prénom_utilisateur','formality','language','profession','ville'];
const _ESSENTIAL_META={
  'prénom_utilisateur':{label:'Prénom',icon:'user',alts:['user_name','creator']},
  'formality':{label:'Registre de langue',icon:'message-circle',alts:[]},
  'language':{label:'Langue préférée',icon:'globe',alts:[]},
  'profession':{label:'Profession',icon:'briefcase',alts:['travail','métier']},
  'ville':{label:'Ville',icon:'map-pin',alts:['localisation','pays']},
};

function _factValue(facts,key){
  if(facts[key])return facts[key];
  const alts=(_ESSENTIAL_META[key]||{}).alts||[];
  for(const a of alts){if(facts[a])return facts[a];}
  return '';
}

function _safeAttr(s){return String(s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');}

function _reInitIcons(){if(typeof lucide!=='undefined'&&lucide.createIcons)lucide.createIcons();}

export async function loadFacts(){
  const essEl=document.getElementById('facts-essential');
  const learnEl=document.getElementById('facts-content');
  if(essEl)essEl.innerHTML=loadingDots('');
  if(learnEl)learnEl.innerHTML=loadingDots('');
  try{
    const _fh={};if(ADMIN_TOKEN)_fh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/facts`,{headers:_fh});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const facts=d.facts||{};

    // ── Essential facts ──
    const essUsed=new Set();
    let filled=0;
    const essHtml=_ESSENTIAL_KEYS.map(k=>{
      const m=_ESSENTIAL_META[k]||{};
      const val=_factValue(facts,k);
      if(val)filled++;
      essUsed.add(k);
      (m.alts||[]).forEach(a=>essUsed.add(a));
      const hasVal=!!val;
      const dotColor=hasVal?'var(--ok)':'var(--warn)';
      const valTxt=hasVal?esc(String(val).substring(0,60)):'Non renseigné';
      const valColor=hasVal?'var(--text-strong)':'var(--muted)';
      const safeK=_safeAttr(k);
      const safeV=hasVal?_safeAttr(val):'';
      return`<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--bg-hover);border:1px solid var(--border);border-radius:10px;margin-top:8px;transition:border-color .15s" onmouseenter="this.style.borderColor='var(--border-hover)'" onmouseleave="this.style.borderColor='var(--border)'">
        <div style="width:34px;height:34px;border-radius:8px;background:var(--accent-subtle);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="${m.icon||'circle'}" style="width:16px;height:16px;color:var(--accent)"></i></div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:6px"><span style="width:6px;height:6px;border-radius:50%;background:${dotColor};flex-shrink:0"></span><span style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)">${esc(m.label||k)}</span></div>
          <div style="font-size:13px;color:${valColor};margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${valTxt}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">${hasVal
          ?`<button class="btn" style="font-size:11px;padding:4px 10px" onclick="window._editFact('${safeK}','${safeV}')"><i data-lucide="pencil" style="width:12px;height:12px"></i> Modifier</button><button class="btn danger" style="font-size:11px;padding:4px 10px" onclick="window._deleteFact('${safeK}')"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>`
          :`<button class="btn primary" style="font-size:11px;padding:4px 10px" onclick="window._editFact('${safeK}','')"><i data-lucide="plus" style="width:12px;height:12px"></i> Renseigner</button>`
        }</div>
      </div>`;
    }).join('');
    if(essEl)essEl.innerHTML=essHtml;
    const badge=document.getElementById('facts-essential-badge');
    if(badge){
      const complete=filled===_ESSENTIAL_KEYS.length;
      badge.textContent=`${filled} / ${_ESSENTIAL_KEYS.length}`;
      badge.style.background=complete?'var(--ok-subtle)':'var(--warn-subtle)';
      badge.style.color=complete?'var(--ok)':'var(--warn)';
      badge.style.border=`1px solid ${complete?'var(--ok-muted)':'var(--warn-muted)'}`;
    }

    // ── Contacts (telegram) ──
    const _TG_RE=/^telegram_(\d+)_name$/;
    const contactKeys=new Set();
    const ownerId=facts['telegram_owner_id']||'';
    if(ownerId)contactKeys.add('telegram_owner_id');
    if(facts['telegram_known_ids'])contactKeys.add('telegram_known_ids');
    const contacts=[];
    for(const k of Object.keys(facts)){
      const m=k.match(_TG_RE);
      if(m){contactKeys.add(k);contacts.push({id:m[1],name:facts[k],isOwner:m[1]===ownerId});}
    }
    contacts.sort((a,b)=>a.isOwner===b.isOwner?a.name.localeCompare(b.name):a.isOwner?-1:1);
    const ctEl=document.getElementById('facts-contacts');
    if(ctEl)ctEl.innerHTML=contacts.length
      ?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px">${contacts.map(c=>{
        const initials=(c.name||'?').substring(0,2).toUpperCase();
        const ownerBadge=c.isOwner?`<span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:var(--radius-full);background:var(--accent-subtle);color:var(--accent);margin-left:6px">OWNER</span>`:'';
        return`<div style="background:var(--bg-hover);border:1px solid var(--border);border-radius:10px;padding:14px;display:flex;align-items:center;gap:12px;transition:border-color .15s" onmouseenter="this.style.borderColor='var(--border-hover)'" onmouseleave="this.style.borderColor='var(--border)'">
          <div style="width:38px;height:38px;border-radius:50%;background:var(--accent-subtle);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px;font-weight:700;color:var(--accent)">${esc(initials)}</div>
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;flex-wrap:wrap"><span style="font-size:13px;font-weight:600;color:var(--text-strong)">${esc(c.name)}</span>${ownerBadge}</div>
            <div style="display:flex;align-items:center;gap:4px;margin-top:2px"><i data-lucide="send" style="width:11px;height:11px;color:var(--muted)"></i><span style="font-size:11px;color:var(--muted)">Telegram · ${esc(c.id)}</span></div>
          </div>
        </div>`;
      }).join('')}</div>`
      :'<div style="color:var(--muted);font-size:13px;padding:12px 0;text-align:center">Aucun contact connu. Lumena ajoutera les personnes au fil des echanges.</div>';
    const ctBadge=document.getElementById('facts-contacts-badge');
    if(ctBadge){
      ctBadge.textContent=`${contacts.length} contact${contacts.length>1?'s':''}`;
      ctBadge.style.background='var(--accent-subtle)';ctBadge.style.color='var(--accent)';
      ctBadge.style.border='1px solid var(--accent-muted)';
    }

    // ── Learned facts ──
    const learnKeys=Object.keys(facts).filter(k=>!essUsed.has(k)&&!contactKeys.has(k));
    if(learnEl)learnEl.innerHTML=learnKeys.length
      ?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px">${learnKeys.map(k=>{
        const v=String(facts[k]).substring(0,80);
        const safeK=_safeAttr(k);const safeV=_safeAttr(v);
        return`<div style="background:var(--bg-hover);border:1px solid var(--border);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;transition:border-color .15s" onmouseenter="this.style.borderColor='var(--border-hover)'" onmouseleave="this.style.borderColor='var(--border)'">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:65%">${esc(k)}</span>
            <div style="display:flex;gap:4px">
              <button class="btn-icon" style="width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:6px;border:1px solid var(--border);background:transparent;cursor:pointer;color:var(--muted);transition:all .15s" onmouseenter="this.style.color='var(--accent)';this.style.borderColor='var(--accent)'" onmouseleave="this.style.color='var(--muted)';this.style.borderColor='var(--border)'" onclick="window._editFact('${safeK}','${safeV}')"><i data-lucide="pencil" style="width:12px;height:12px"></i></button>
              <button class="btn-icon" style="width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:6px;border:1px solid var(--border);background:transparent;cursor:pointer;color:var(--muted);transition:all .15s" onmouseenter="this.style.color='var(--danger)';this.style.borderColor='var(--danger)'" onmouseleave="this.style.color='var(--muted)';this.style.borderColor='var(--border)'" onclick="window._deleteFact('${safeK}')"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
            </div>
          </div>
          <div style="font-size:13px;color:var(--text-strong);word-break:break-word;line-height:1.4">${esc(v)}</div>
        </div>`;
      }).join('')}</div>`
      :'<div style="color:var(--muted);font-size:13px;padding:12px 0;text-align:center">Aucun fait appris pour le moment. Lumena apprend au fil des conversations.</div>';

    // Heartbeat
    const hb=d.heartbeat||{};
    document.getElementById('heartbeat-content').innerHTML=hb.last_heartbeat
      ?`<div style="display:flex;gap:16px;flex-wrap:wrap"><div class="stat-card"><div class="stat-label">Dernier battement</div><div class="stat-value ok" style="font-size:13px">${esc((hb.last_heartbeat||'').substring(0,19).replace('T',' '))}</div></div><div class="stat-card"><div class="stat-label">Total battements</div><div class="stat-value">${hb.heartbeat_count||0}</div></div></div>`
      :'<div style="color:var(--muted);font-size:13px">Aucune donnee heartbeat.</div>';
    // Insights
    const ins=d.insights||[];
    document.getElementById('insights-content').innerHTML=ins.length
      ?ins.map(i=>`<div style="padding:8px 0;border-bottom:1px solid var(--border)"><div style="font-size:11px;color:var(--muted);margin-bottom:4px">${esc((i.timestamp||'').substring(0,10))}</div>${(i.insights||[]).map(s=>`<div style="font-size:13px;color:var(--text);padding:2px 0">• ${esc(s)}</div>`).join('')}</div>`).join('')
      :'<div style="color:var(--muted);font-size:13px">Aucun insight enregistre.</div>';
    _reInitIcons();
  }catch(e){
    if(essEl)essEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
}

// ── CRUD Facts helpers ──────────────────────────────────────────────────────
window._editFact=function(key,oldVal){
  const val=prompt(`Valeur pour "${key}" :`,oldVal||'');
  if(val===null)return;
  if(!val.trim()){alert('Valeur vide');return;}
  fetch(`${API_BASE}/api/facts`,{method:'PUT',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify({key,value:val.trim()})})
    .then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(()=>loadFacts())
    .catch(e=>alert('Erreur: '+e.message));
};
window._deleteFact=function(key){
  if(!confirm(`Supprimer le fait "${key}" ?`))return;
  fetch(`${API_BASE}/api/facts/${encodeURIComponent(key)}`,{method:'DELETE',headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}})
    .then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(()=>loadFacts())
    .catch(e=>alert('Erreur: '+e.message));
};
window._showAddFact=function(){
  const key=prompt('Clé du fait (ex: hobby, surnom, timezone) :');
  if(!key)return;
  const val=prompt(`Valeur pour "${key}" :`);
  if(!val||!val.trim())return;
  fetch(`${API_BASE}/api/facts`,{method:'PUT',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify({key:key.trim(),value:val.trim()})})
    .then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then(()=>loadFacts())
    .catch(e=>alert('Erreur: '+e.message));
};

/* ============================================================
   PROVIDERS LLM
   ============================================================ */
export async function loadProviders(){
  const list=document.getElementById('providers-list');
  if(list)list.innerHTML=loadingDots('Chargement...');
  try{
    const _ph={};if(ADMIN_TOKEN)_ph['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/providers`,{headers:_ph});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const providers=d.providers||[];
    if(!providers.length){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucune donnee provider.</div>';return;}
    list.innerHTML=providers.map(p=>{
      const st=p.status||'Inactif';
      const statusClass=st==='Sain'?'ok':st==='Dégradé'||st==='Degradé'?'warn':st==='Inactif'||st==='Non configuré'?'muted':'danger';
      const bar=Math.round((p.success_rate||0));
      const inactive=st==='Inactif'||st==='Non configuré';
      return`<div class="list-item"${inactive?' style="opacity:0.55"':''}>
        <div style="flex:1">
          <div class="list-item-title">${esc(p.name.charAt(0).toUpperCase()+p.name.slice(1))}</div>
          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:8px">
            <div class="stat-card" style="flex:1;min-width:100px"><div class="stat-label">Succes</div><div class="stat-value ${statusClass}">${p.probes?p.success_rate+'%':'—'}</div></div>
            <div class="stat-card" style="flex:1;min-width:100px"><div class="stat-label">Sondes</div><div class="stat-value">${p.probes}</div></div>
            <div class="stat-card" style="flex:1;min-width:100px"><div class="stat-label">Echecs</div><div class="stat-value ${p.failures>0?'danger':'ok'}">${p.failures}</div></div>
            <div class="stat-card" style="flex:1;min-width:100px"><div class="stat-label">Latence moy.</div><div class="stat-value">${p.avg_latency!=null?p.avg_latency+'s':'—'}</div></div>
            <div class="stat-card" style="flex:1;min-width:100px"><div class="stat-label">Latence min/max</div><div class="stat-value" style="font-size:12px">${p.min_latency!=null?p.min_latency+'s':'—'} / ${p.max_latency!=null?p.max_latency+'s':'—'}</div></div>
          </div>
          <div style="margin-top:12px;height:6px;background:rgba(0,0,0,0.3);border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${inactive?0:bar}%;background:var(--${statusClass});border-radius:3px;transition:width .4s"></div>
          </div>
        </div>
        <span class="pill ${statusClass}" style="margin-left:12px">${esc(st)}</span>
      </div>`;
    }).join('');
  }catch(e){if(list)list.innerHTML=`<div style="color:var(--danger);padding:12px">Erreur: ${esc(e.message)}</div>`;}
}

/* ============================================================
   ALERTES
   ============================================================ */
export async function loadAlerts(){
  const list=document.getElementById('alerts-list');
  if(list)list.innerHTML=loadingDots('Chargement...');
  try{
    const r=await fetch(`${API_BASE}/api/alerts?limit=50`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const alerts=d.alerts||[];
    const badge=document.getElementById('badge-alerts');
    if(badge){badge.textContent=alerts.length;badge.style.background=alerts.length>0?'var(--danger)':'var(--muted)'}
    if(!alerts.length){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucune alerte enregistree.</div>';return;}
    list.innerHTML=alerts.map(a=>{
      const sev=a.severity||'info';
      const sevClass={critical:'danger',high:'danger',warning:'warn',info:'accent'}[sev]||'accent';
      const ts=(a.ts||'').substring(0,19).replace('T',' ');
      const msg=a.message||a.error||a.reason||'';
      const chan=a.channel||a.source||'system';
      return`<div class="list-item" style="border-left:3px solid var(--${sevClass})">
        <div style="flex:1">
          <div class="list-item-title" style="color:var(--${sevClass})">⚠ ${esc(sev.toUpperCase())} — ${esc(chan)}</div>
          <div class="list-item-sub">${esc(ts)}</div>
          ${msg?`<div style="font-size:12px;color:var(--text);margin-top:4px">${esc(msg.substring(0,200))}</div>`:''}
          ${a.ok===false&&a.error?`<div style="font-size:11px;color:var(--danger);margin-top:2px">Erreur: ${esc(String(a.error).substring(0,120))}</div>`:''}
        </div>
        <span class="pill ${sevClass}">${esc(sev)}</span>
      </div>`;
    }).join('');
  }catch(e){if(list)list.innerHTML=`<div style="color:var(--danger);padding:12px">Erreur: ${esc(e.message)}</div>`;}
}

/* ============================================================
   TRAINING / APPRENTISSAGE
   ============================================================ */
export async function loadTraining(){
  const list=document.getElementById('training-list');
  if(list)list.innerHTML=loadingDots('Chargement...');
  try{
    const _th={};if(ADMIN_TOKEN)_th['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/training`,{headers:_th});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const datasets=d.datasets||[];
    const totalConvs=d.total_conversations||0;
    if(!datasets.length){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucun dataset trouve.</div>';return;}
    const totalEntries=datasets.reduce((a,x)=>a+(x.entries||0),0);
    const totalSize=datasets.reduce((a,x)=>a+(x.size_bytes||0),0);
    const badge=totalConvs>=100?'<span class="pill success">Pret</span>':totalConvs>=50?'<span class="pill accent">Suffisant</span>':'<span class="pill" style="background:var(--warn);color:#000">Insuffisant (&lt;50)</span>';
    const summary=`<div class="list-item"><div style="flex:1"><div class="list-item-title">\uD83D\uDCE6 ${totalConvs.toLocaleString()} conversations disponibles pour fine-tuning ${badge} — ${(totalSize/1024/1024).toFixed(2)} Mo</div></div></div>`;
    const folderNames={"training_pool":"Pool quotidien","training_validated":"Valides (prets)"};
    const folders={};
    for(const ds of datasets){const f=ds.folder||'other';if(!folders[f])folders[f]=[];folders[f].push(ds);}
    let html=summary;
    for(const [folder,items] of Object.entries(folders)){
      const label=folderNames[folder]||folder;
      html+=`<div class="list-item" style="background:rgba(0,0,0,0.15)"><div style="flex:1"><div class="list-item-title" style="color:var(--accent)">\uD83D\uDCC1 ${esc(label)}</div></div></div>`;
      html+=items.map(ds=>{
        const date=ds.modified?new Date(ds.modified*1000).toLocaleDateString('fr-FR'):'?';
        const size=(ds.size_bytes/1024).toFixed(0);
        return`<div class="list-item" style="padding-left:28px">
          <div style="flex:1">
            <div class="list-item-title">${esc(ds.name)}</div>
            <div class="list-item-sub">${(ds.entries||0).toLocaleString()} entrees | ${size} Ko | ${date}</div>
          </div>
          <span class="pill accent">${ds.name.endsWith('.jsonl')?'JSONL':'JSON'}</span>
        </div>`;
      }).join('');
    }
    list.innerHTML=html;
  }catch(e){if(list)list.innerHTML=`<div style="color:var(--danger);padding:12px">Erreur: ${esc(e.message)}</div>`;}
}

/* ============================================================
   FINE-TUNING
   ============================================================ */
const _FT_PHASES=['downloading','preparing','training','merging','converting','quantizing','importing','done'];
let _ftEventSource=null;

export async function loadFinetuning(){
  const _h={'Authorization':`Bearer ${ADMIN_TOKEN}`};
  // Status
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/status`,{headers:_h});
    if(r.ok){const d=await r.json();_renderFtHardware(d);}
  }catch(e){console.warn('ft status',e);}
  // Models
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/models`,{headers:_h});
    if(r.ok){const d=await r.json();_renderFtModels(d.models||[]);}
  }catch(e){console.warn('ft models',e);}
  // Dataset stats
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/dataset-stats`,{headers:_h});
    if(r.ok){const d=await r.json();_renderFtDataset(d);}
  }catch(e){console.warn('ft dataset',e);}
  // Jobs
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/jobs`,{headers:_h});
    if(r.ok){const d=await r.json();_renderFtJobs(d.models||[]);}
  }catch(e){console.warn('ft jobs',e);}
}

function _renderFtHardware(d){
  const gpu=d.gpu||{};
  const deps=d.deps||{};
  const banner=document.getElementById('ft-alert-banner');
  // GPU card
  const gc=document.getElementById('ft-gpu-card');
  if(gc){
    const ok=gpu.available;
    gc.innerHTML=`<div class="card-content"><div style="font-size:13px;font-weight:600;margin-bottom:6px"><span class="pill ${ok?'success':'danger'}">${ok?'GPU OK':'Pas de GPU'}</span></div>`
      +`<p style="font-size:12px;color:var(--text)">${ok?esc(gpu.name||'?'):'Aucun GPU NVIDIA detecte'}</p>`
      +`<p style="font-size:11px;color:var(--muted)">${ok?`${gpu.vram_gb||0} Go VRAM`:gpu.reason||''}</p></div>`;
  }
  // Deps card
  const dc=document.getElementById('ft-deps-card');
  if(dc){
    const ok=deps.all_ok;
    const noGpu=!gpu.available;
    if(noGpu&&!ok){
      // No GPU → deps can't install (they require CUDA) — show clear message
      dc.innerHTML=`<div class="card-content"><div style="font-size:13px;font-weight:600;margin-bottom:6px"><span class="pill accent">Non applicable</span></div>`
        +`<p style="font-size:12px;color:var(--muted)">Les deps fine-tuning necessitent un GPU NVIDIA + CUDA</p></div>`;
    } else {
      dc.innerHTML=`<div class="card-content"><div style="font-size:13px;font-weight:600;margin-bottom:6px"><span class="pill ${ok?'success':'accent'}">${ok?'Pret':'Manquant'}</span></div>`
        +`<p style="font-size:12px;color:var(--text)">${ok?`${(deps.installed||[]).length} packages`:`${(deps.missing||[]).length} manquants`}</p>`
        +(ok?'':`<button class="btn" style="font-size:11px;padding:3px 8px;margin-top:6px" onclick="_installDeps()">Installer</button>`)
        +`</div>`;
    }
  }
  // Dataset card
  const dtc=document.getElementById('ft-data-card');
  if(dtc){
    const cnt=d.dataset_count||0;
    const badge=cnt>=100?'success':cnt>=50?'accent':'danger';
    dtc.innerHTML=`<div class="card-content"><div style="font-size:13px;font-weight:600;margin-bottom:6px"><span class="pill ${badge}">${cnt} convs</span></div>`
      +`<p style="font-size:12px;color:var(--text)">${cnt>=50?'Pret pour fine-tuning':'Donnees insuffisantes (<50)'}</p></div>`;
  }
  // Banner
  if(banner){
    if(!gpu.available){banner.style.display='block';banner.style.background='var(--danger)';banner.style.color='#fff';banner.textContent='GPU non disponible — Un GPU NVIDIA avec CUDA est requis pour le fine-tuning local';}
    else if(!deps.all_ok){banner.style.display='block';banner.style.background='var(--warn)';banner.style.color='#000';banner.textContent=`Dependances manquantes : ${(deps.missing||[]).join(', ')}`;}
    else{banner.style.display='none';}
  }
  // Enable/disable start button + dim config when no GPU
  const btn=document.getElementById('ft-start-btn');
  if(btn)btn.disabled=!gpu.available||!deps.all_ok;
  const cfgSection=document.getElementById('ft-config-section');
  if(cfgSection)cfgSection.style.opacity=gpu.available?'1':'0.5';
  // Show active job progress if any
  if(d.active_job){
    const ps=document.getElementById('ft-progress-section');
    if(ps)ps.style.display='block';
    _connectProgressSSE();
  }
}

function _renderFtModels(models){
  const wrap=document.getElementById('ft-model-select');
  if(!wrap)return;
  const trigger=wrap.querySelector('.ft-select-trigger .ft-select-text');
  const dropdown=wrap.querySelector('.ft-select-dropdown');
  if(!trigger||!dropdown)return;
  dropdown.innerHTML='';
  wrap._ftValue='';wrap._ftOllamaTag='';

  if(!models.length){
    trigger.textContent='Aucun modele disponible';
    return;
  }

  const cats={llm:[],code:[],vision:[]};
  for(const m of models){const c=m.category||'llm';if(!cats[c])cats[c]=[];cats[c].push(m);}

  let firstPicked=false;
  for(const [cat,items] of Object.entries(cats)){
    if(!items.length)continue;
    const lbl=document.createElement('div');lbl.className='ft-select-group-label';lbl.textContent=cat.toUpperCase();
    dropdown.appendChild(lbl);
    for(const m of items){
      const opt=document.createElement('div');opt.className='ft-select-option';
      const val=m.hf_id_4bit||m.hf_id_full||m.ollama_id;
      opt.dataset.value=val;
      opt.dataset.ollamaTag=m.ollama_id;
      const tags=[];
      if(m.already_installed)tags.push('installe');
      if(m.fits_vram===false)tags.push('VRAM insuffisante');
      const tagStr=tags.length?` (${tags.join(', ')})`:'';
      opt.textContent=`${m.ollama_id} — ${m.params} — ${m.vram_ft_min_gb} Go VRAM${tagStr}`;
      if(m.fits_vram===false)opt.classList.add('dimmed');
      opt.addEventListener('click',()=>_ftSelectOption(wrap,opt));
      dropdown.appendChild(opt);
      if(!firstPicked&&(m.fits_vram||m.already_installed)){
        _ftSelectOption(wrap,opt,true);firstPicked=true;
      }
    }
  }
  if(!firstPicked){
    const first=dropdown.querySelector('.ft-select-option');
    if(first)_ftSelectOption(wrap,first,true);
  }

  // Toggle dropdown on trigger click
  wrap.querySelector('.ft-select-trigger').onclick=function(e){
    e.stopPropagation();wrap.classList.toggle('open');
  };
  // Close on outside click
  document.addEventListener('click',function _ftClose(e){
    if(!wrap.contains(e.target))wrap.classList.remove('open');
  });
}

function _ftSelectOption(wrap,opt,silent){
  const prev=wrap.querySelector('.ft-select-option.selected');
  if(prev)prev.classList.remove('selected');
  opt.classList.add('selected');
  wrap._ftValue=opt.dataset.value;
  wrap._ftOllamaTag=opt.dataset.ollamaTag;
  wrap.querySelector('.ft-select-trigger .ft-select-text').textContent=opt.textContent;
  if(!silent)wrap.classList.remove('open');
  _autoFillOutputName();
}

function _autoFillOutputName(){
  const wrap=document.getElementById('ft-model-select');
  const inp=document.getElementById('ft-output-name');
  if(!wrap||!inp||inp.value)return;
  const tag=(wrap._ftOllamaTag||'model').replace(/[^a-z0-9]/g,'-').replace(/-+/g,'-');
  inp.placeholder=`lumena-${tag}-v1`;
}

function _renderFtDataset(d){
  // Already rendered in hardware; can add detail later
}

function _renderFtJobs(models){
  const el=document.getElementById('ft-models-list');
  if(!el)return;
  if(!models.length){el.innerHTML='<p style="color:var(--muted);font-size:12px">Aucun modele fine-tune pour l\'instant.</p>';return;}
  el.innerHTML=models.map(m=>`<div class="list-item" style="margin-bottom:8px">
    <div style="flex:1">
      <div class="list-item-title">${esc(m.model_name)}</div>
      <div class="list-item-sub">Base: ${esc(m.base_model||'?')} | ${esc(m.quant_type||'?')} | ${m.dataset_size||0} convs | ${m.epochs||0} epochs | ${m.created_at?new Date(m.created_at).toLocaleDateString('fr-FR'):'?'}</div>
    </div>
    <div style="display:flex;gap:6px">
      <button class="btn accent" style="font-size:11px;padding:3px 8px" onclick="_selectFinetuned('${esc(m.model_name)}')">Utiliser</button>
      <button class="btn" style="font-size:11px;padding:3px 8px;background:var(--danger);color:#fff" onclick="_deleteFinetuned('${esc(m.model_name)}')">Supprimer</button>
    </div>
  </div>`).join('');
}

/* ============================================================
   FINE-TUNING — ACTIONS
   ============================================================ */

window._startFinetuning=async function(){
  const wrap=document.getElementById('ft-model-select');
  const outputName=document.getElementById('ft-output-name')?.value||document.getElementById('ft-output-name')?.placeholder||'';
  if(!wrap?._ftValue||!outputName){alert('Selectionnez un modele et un nom de sortie');return;}
  const body={
    base_model:wrap._ftValue,
    ollama_tag:wrap._ftOllamaTag||wrap._ftValue,
    output_name:outputName.replace(/^lumena-/,'').length?outputName:'lumena-ft-v1',
    num_epochs:parseInt(document.getElementById('ft-epochs')?.value||'3'),
    quant_type:document.getElementById('ft-quant')?.value||'Q4_K_M',
    system_prompt:document.getElementById('ft-system-prompt')?.value||'',
    hf_token:document.getElementById('ft-hf-token')?.value||'',
    lora_r:parseInt(document.getElementById('ft-lora-r')?.value||'16'),
    lora_alpha:parseInt(document.getElementById('ft-lora-alpha')?.value||'32'),
    learning_rate:parseFloat(document.getElementById('ft-lr')?.value||'0.0002'),
    max_seq_length:parseInt(document.getElementById('ft-seq-len')?.value||'2048'),
    batch_size:parseInt(document.getElementById('ft-batch')?.value||'2'),
    grad_accumulation:parseInt(document.getElementById('ft-grad-acc')?.value||'4'),
    lora_dropout:parseFloat(document.getElementById('ft-lora-dropout')?.value||'0'),
    load_in_4bit:document.getElementById('ft-4bit')?.checked!==false,
    use_unsloth:document.getElementById('ft-unsloth')?.checked!==false,
  };
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/start`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify(body)});
    if(!r.ok){const e=await r.json().catch(()=>({}));alert(e.detail||`Erreur ${r.status}`);return;}
    document.getElementById('ft-progress-section').style.display='block';
    document.getElementById('ft-start-btn').disabled=true;
    _connectProgressSSE();
  }catch(e){alert('Erreur: '+e.message);}
};

let _ftAbort=null;
function _connectProgressSSE(){
  if(_ftAbort){_ftAbort.abort();_ftAbort=null;}
  _ftAbort=new AbortController();
  const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
  const log=document.getElementById('ft-log');
  const bar=document.getElementById('ft-progress-bar');
  const timeline=document.getElementById('ft-phase-timeline');
  const seenPhases=new Set();
  fetch(`${API_BASE}/api/finetuning/progress`,{headers:h,signal:_ftAbort.signal})
    .then(res=>{
      const reader=res.body.getReader();
      const decoder=new TextDecoder();
      let buf='';
      function pump(){
        reader.read().then(({done,value})=>{
          if(done){_ftAbort=null;return;}
          buf+=decoder.decode(value,{stream:true});
          const lines=buf.split('\n');
          buf=lines.pop();
          for(const line of lines){
            if(!line.startsWith('data:'))continue;
            const raw=line.slice(5).trim();
            try{
              const d=JSON.parse(raw);
              if(d.phase)seenPhases.add(d.phase);
              if(d.pct_done!=null&&bar){bar.style.width=d.pct_done+'%';bar.textContent=Math.round(d.pct_done)+'%';}
              if(d.message&&log){log.innerHTML+=`<div>${esc(d.message)}</div>`;log.scrollTop=log.scrollHeight;}
              if(d.loss!=null&&log){log.innerHTML+=`<div style="color:var(--accent)">Step ${d.step}/${d.max_steps} — loss: ${d.loss.toFixed(4)}</div>`;log.scrollTop=log.scrollHeight;}
              if(timeline){
                timeline.innerHTML=_FT_PHASES.map(p=>{
                  const cls=p===d.phase?'phase-active':seenPhases.has(p)?'phase-done':'phase-pending';
                  return`<span class="ft-phase ${cls}">${p}</span>`;
                }).join(' → ');
              }
              if(d.phase==='done'){_ftAbort.abort();_ftAbort=null;document.getElementById('ft-start-btn').disabled=false;loadFinetuning();}
              if(d.phase==='error'){_ftAbort.abort();_ftAbort=null;document.getElementById('ft-start-btn').disabled=false;}
            }catch(err){console.warn('SSE parse',err);}
          }
          pump();
        });
      }
      pump();
    })
    .catch(()=>{_ftAbort=null;});
}

window._cancelFinetuning=async function(){
  try{
    await fetch(`${API_BASE}/api/finetuning/cancel`,{method:'POST',headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
  }catch(e){console.warn(e);}
};

window._selectFinetuned=async function(name){
  try{
    const r=await fetch(`${API_BASE}/api/model/switch`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify({model_name:name})});
    if(r.ok)alert(`Modele brain change pour ${name}`);
    else alert('Erreur lors du changement');
  }catch(e){alert(e.message);}
};

window._deleteFinetuned=async function(name){
  if(!confirm(`Supprimer le modele fine-tune "${name}" ? Cette action est irreversible.`))return;
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/jobs/${encodeURIComponent(name)}`,{method:'DELETE',headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(r.ok)loadFinetuning();
    else{const e=await r.json().catch(()=>({}));alert(e.detail||'Erreur');}
  }catch(e){alert(e.message);}
};

window._installDeps=async function(){
  const dc=document.getElementById('ft-deps-card');
  if(dc)dc.innerHTML='<div class="card-content"><p style="font-size:12px">Installation en cours...</p></div>';
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/install-deps`,{method:'POST',headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const reader=r.body.getReader();const dec=new TextDecoder();
    let buf='';
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      const lines=buf.split('\n');buf=lines.pop()||'';
      for(const line of lines){
        if(!line.startsWith('data: '))continue;
        try{const d=JSON.parse(line.slice(6));
          if(dc)dc.innerHTML=`<div class="card-content"><p style="font-size:11px;font-family:var(--mono)">${esc(d.message||'...')}</p></div>`;
          if(d.phase==='done'||d.phase==='error'){loadFinetuning();return;}
        }catch(e){}
      }
    }
  }catch(e){if(dc)dc.innerHTML=`<div class="card-content"><p style="color:var(--danger);font-size:12px">Erreur: ${esc(e.message)}</p></div>`;}
};

/* ============================================================
   LOGS SYSTEME
   ============================================================ */
let _logsData=null;
export async function loadLogsRecent(){
  document.getElementById('logs-content').textContent='Chargement...';
  document.getElementById('logs-meta').textContent='';
  try{
    const r=await fetch(`${API_BASE}/api/logs/recent?lines=200`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    _logsData=await r.json();
    const meta=document.getElementById('logs-meta');
    if(meta&&_logsData)meta.textContent=`Fichier: ${_logsData.file||'?'}  |  ${_logsData.total_lines||0} lignes total  |  Affichage: dernières ${(_logsData.lines||[]).length}`;
  }catch(e){document.getElementById('logs-content').textContent=`Erreur: ${e.message}`;return;}
  renderLogs();
}
export function renderLogs(){
  if(!_logsData)return;
  const filter=(document.getElementById('logs-filter')?.value||'').toLowerCase();
  let lines=_logsData.lines||[];
  if(filter)lines=lines.filter(l=>l.toLowerCase().includes(filter));
  const el=document.getElementById('logs-content');
  if(!el)return;
  const colored=lines.map(l=>{
    const le=esc(l);
    if(l.includes('ERROR')||l.includes('CRITICAL'))return`<span style="color:var(--danger)">${le}</span>`;
    if(l.includes('WARNING')||l.includes('WARN'))return`<span style="color:var(--warn)">${le}</span>`;
    if(l.includes('INFO'))return`<span style="color:var(--text)">${le}</span>`;
    if(l.includes('DEBUG'))return`<span style="color:var(--muted)">${le}</span>`;
    return`<span style="color:var(--text)">${le}</span>`;
  }).join('\n');
  el.innerHTML=colored||'<span style="color:var(--muted)">Aucune ligne correspondante.</span>';
  el.scrollTop=el.scrollHeight;
}

/* ============================================================
   CONFIGURATION (.env)
   ============================================================ */
let _configData=null;
let _cfgLevel='simple'; // kept for backward compat
const _LEVEL_ORDER=['simple','avancé','expert'];

// P5.1 — Ordre et niveau des groupes
const _GROUP_ORDER=[
  {name:'LLM',               level:'simple'},
  {name:'Cerveaux Spécialisés',level:'simple'},
  {name:'Préférences',       level:'simple'},
  {name:'Voix',              level:'simple'},
  {name:'Autonomie',         level:'simple'},
  {name:'Alertes',           level:'simple'},
  {name:'Clés API',          level:'simple'},
  {name:'Telegram',          level:'simple'},
  {name:'WhatsApp',          level:'simple'},
  {name:'Serveur',           level:'avancé'},
  {name:'Browser',           level:'avancé'},
  {name:'Email',             level:'avancé'},
  {name:'Paiements',         level:'avancé'},
  {name:'Automation (n8n)',  level:'avancé'},
  {name:'Vidéo',             level:'avancé'},
  {name:'IONOS (Hébergement)',level:'avancé'},
  {name:'Apprentissage',     level:'avancé'},
  {name:'Ops',               level:'expert'},
  {name:'SLO',               level:'expert'},
  {name:'Système',           level:'expert'},
  {name:'Instance',          level:'expert'},
];

// P5.3 — Clés cachées dans le groupe Instance
const _INSTANCE_ISOLATED=new Set(['LUMENA_INSTANCE_ID','LUMENA_DATA_DIR','LUMENA_WORKSPACE_DIR','LUMENA_UPLOADS_DIR','LUMENA_LOGS_DIR']);

function _renderCfgRow(it){
  const val=it.value||'';
  let input='';
  if(it.type==='bool'){
    const on=val==='1'||val==='true'||val==='True';
    input=`<label style="display:flex;align-items:center;gap:8px;cursor:pointer"><input type="checkbox" data-cfg="${esc(it.key)}" ${on?'checked':''} style="width:16px;height:16px;accent-color:var(--accent)"><span style="font-size:12px;color:var(--muted)">${on?'Actif':'Inactif'}</span></label>`;
  }else if(it.type==='select'){
    const opts=(it.options||[]).map(o=>`<option value="${esc(o)}" ${o===val?'selected':''}>${esc(o)}</option>`).join('');
    input=`<select data-cfg="${esc(it.key)}" class="input" style="height:32px;font-size:12px;padding:0 8px;min-width:200px">${opts}</select>`;
  }else if(it.type==='number'){
    const minA=it.min!==undefined?` min="${it.min}"`:'';const maxA=it.max!==undefined?` max="${it.max}"`:'';
    input=`<input type="number" data-cfg="${esc(it.key)}" class="input" style="height:32px;font-size:12px;padding:0 8px;width:120px" value="${esc(val)}"${minA}${maxA}>`;
  }else if(it.type==='secret'){
    const uid='sec_'+it.key;
    const sbadge=it.has_value?`<span style="color:var(--ok);font-size:10px;margin-left:6px">&#9679; Configuré</span>`:`<span style="color:var(--danger);font-size:10px;margin-left:6px">&#9679; Absent</span>`;
    input=`<div style="display:flex;align-items:center;gap:6px"><input type="password" id="${uid}" data-cfg="${esc(it.key)}" data-secret="1" class="input" style="height:32px;font-size:12px;padding:0 8px;width:280px;font-family:var(--mono)" value="${esc(val)}" readonly><button type="button" class="btn" style="font-size:14px;padding:4px 8px;min-width:34px" title="Voir / masquer" onclick="toggleSecret('${uid}')">&#128065;&#65039;</button>${sbadge}</div>`;
  }else{
    input=`<input type="text" data-cfg="${esc(it.key)}" class="input" style="height:32px;font-size:12px;padding:0 8px;min-width:240px" value="${esc(val)}">`;
  }
  const restartBadge=it.restart?`<span style="font-size:9px;background:rgba(255,165,0,.12);color:orange;border-radius:3px;padding:1px 5px;margin-left:4px">&#8635; restart</span>`:'';
  return`<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><div style="min-width:200px"><div style="font-size:13px;font-weight:500">${esc(it.label)}${restartBadge}</div><div style="font-size:10px;color:var(--muted);font-family:var(--mono)">${esc(it.key)}</div></div><div>${input}</div></div>`;
}

function _renderGroupCard(name,items){
  if(!items||!items.length)return'';
  let rows='';
  if(name==='Instance'){
    const main=items.filter(it=>!_INSTANCE_ISOLATED.has(it.key));
    const iso=items.filter(it=>_INSTANCE_ISOLATED.has(it.key));
    for(const it of main)rows+=_renderCfgRow(it);
    if(iso.length){
      let isoRows='';for(const it of iso)isoRows+=_renderCfgRow(it);
      rows+=`<details style="margin-top:4px"><summary style="cursor:pointer;font-size:12px;color:var(--muted);user-select:none;padding:6px 0">Isolation avancée &#9658;</summary><div>${isoRows}</div></details>`;
    }
  }else{
    for(const it of items)rows+=_renderCfgRow(it);
  }
  return`<div class="card"><div class="card-title">${esc(name)}</div><div class="card-content" style="padding:4px 0">${rows}</div></div>`;
}

function _renderConfig(){
  const box=document.getElementById('config-groups');if(!box||!_configData)return;
  const groups=_configData.groups||{};
  // P5.1 — groupes triés ; P5.2 — collapsible par niveau
  const simple=[],avance=[],expert=[],seen=new Set();
  for(const{name,level}of _GROUP_ORDER){
    seen.add(name);
    const card=_renderGroupCard(name,groups[name]||[]);
    if(!card)continue;
    if(level==='simple')simple.push(card);
    else if(level==='avancé')avance.push(card);
    else expert.push(card);
  }
  // Groupes inconnus → avancé par défaut
  for(const[name,items]of Object.entries(groups)){
    if(seen.has(name))continue;
    const card=_renderGroupCard(name,items);
    if(card)avance.push(card);
  }
  const ds='border-top:1px solid var(--border);margin-top:8px;padding-top:4px';
  const ss='cursor:pointer;padding:10px 4px;font-size:13px;font-weight:600;color:var(--text);list-style:none;display:flex;align-items:center;gap:8px;user-select:none';
  let html=simple.join('');
  if(avance.length){
    html+=`<details id="cfg-avance" style="${ds}"><summary style="${ss}"><span>&#9881;&#65039; Options avanc\u00e9es</span><span style="font-size:10px;font-weight:400;color:var(--muted)">(${avance.length} groupes)</span></summary><div style="display:flex;flex-direction:column;gap:12px;padding-top:8px">${avance.join('')}</div></details>`;
  }
  if(expert.length){
    html+=`<details id="cfg-expert" style="${ds}"><summary style="${ss}"><span>&#128295; Options expert</span><span style="font-size:10px;font-weight:400;color:var(--muted)">(${expert.length} groupes)</span></summary><div style="display:flex;flex-direction:column;gap:12px;padding-top:8px">${expert.join('')}</div></details>`;
  }
  box.innerHTML=html;
}

export async function loadConfig(){
  const box=document.getElementById('config-groups');if(!box)return;
  box.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Chargement...</div>';
  try{
    const r=await fetch(`${API_BASE}/api/config`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});if(!r.ok)throw new Error(`HTTP ${r.status}`);
    _configData=await r.json();
    _renderConfig();
  }catch(e){box.innerHTML=`<div style="color:var(--danger);padding:20px">Erreur: ${e.message}</div>`;}
}

export function setCfgLevel(lvl){
  _cfgLevel=lvl; // backward compat — P5 utilise des sections collapsibles, plus de filtre barre
}

export async function toggleSecret(uid){
  const el=document.getElementById(uid);if(!el)return;
  if(el.type==='text'){
    // Re-masquer
    el.type='password';el.readOnly=true;
    loadConfig(); // Recharger les valeurs masquées
    return;
  }
  // Demander confirmation
  if(!confirm('Afficher cette clé en clair ? Assurez-vous que personne ne regarde votre écran.'))return;
  // Appeler l'API reveal
  try{
    const key=el.dataset.cfg;
    const r=await fetch(`${API_BASE}/api/config/reveal?key=${encodeURIComponent(key)}`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.success){
      el.value=d.value;el.type='text';el.readOnly=false;
    }else{alert('Erreur: '+d.error);}
  }catch(e){alert('Erreur: '+e.message);}
}

export async function saveConfig(){
  const msg=document.getElementById('config-save-msg');
  const updates={};
  document.querySelectorAll('[data-cfg]').forEach(el=>{
    const key=el.dataset.cfg;
    if(el.type==='checkbox'){
      updates[key]=el.checked?'1':'';
    }else if(el.dataset.secret==='1'){
      // Ne sauvegarder que si démasqué (modifié)
      if(el.type==='text'&&!el.readOnly){
        updates[key]=el.value;
      }
    }else{
      updates[key]=el.value;
    }
  });
  if(!Object.keys(updates).length){showCfgMsg('Rien a sauvegarder','var(--muted)');return;}
  try{
    const r=await fetch(`${API_BASE}/api/config`,{method:'PUT',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify({updates})});
    const d=await r.json();
    if(d.success){
      let note=`Sauvegardé: ${(d.updated||[]).join(', ')}.`;
      if(d.needs_restart){note+=' Redémarrage requis pour certains changements.';}
      else if(d.note){note+=` ${d.note}`;}
      showCfgMsg(note,'var(--ok)');
    }else{showCfgMsg(`Erreur: ${d.error}`,'var(--danger)');}
  }catch(e){showCfgMsg(`Erreur: ${e.message}`,'var(--danger)');}
}

export function showCfgMsg(text,color){
  const el=document.getElementById('config-save-msg');if(!el)return;
  el.style.display='block';el.style.color=color;el.style.background=color.includes('ok')?'rgba(39,174,96,0.1)':color.includes('danger')?'rgba(231,76,60,0.1)':'rgba(255,255,255,0.04)';
  el.textContent=text;
  setTimeout(()=>{el.style.display='none';},5000);
}

/* ============================================================
   SESSIONS
   ============================================================ */
export async function loadSessions(){
  const list=document.getElementById('sessions-list');if(!list)return;
  const d=lastStatusData;
  if(!d){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Donnees status non disponibles</div>';return}
  const statsHtml=`
    <div class="grid-3" style="margin-bottom:16px">
      <div class="card"><div class="card-title">Sessions totales</div><div style="font-size:28px;font-weight:700;color:var(--accent)">${d.sessions_total||0}</div></div>
      <div class="card"><div class="card-title">Sessions actives</div><div style="font-size:28px;font-weight:700;color:var(--ok)">${d.sessions_active||0}</div></div>
      <div class="card"><div class="card-title">Conversations</div><div style="font-size:28px;font-weight:700;color:var(--warn)">${d.tasks_conversations||0}</div></div>
    </div>`;
  list.innerHTML=statsHtml+'<div class="card"><div class="card-title"><i data-lucide="search"></i> Rechercher une session</div><div class="card-content"><p style="color:var(--muted);font-size:13px;margin-bottom:12px">Entrez un ID de conversation pour voir ses taches et son etat.</p><div style="display:flex;gap:8px"><input type="text" class="input" id="session-conv-id" placeholder="Ex: web_1234567890"><button class="btn primary" onclick="loadSessionDetail(document.getElementById(\'session-conv-id\').value.trim())">Charger</button></div></div></div>';
}

export function filterSessions(){}

export async function loadSessionDetail(convId){
  if(!convId)return;
  document.getElementById('session-detail').style.display='block';
  document.getElementById('session-detail-title').textContent=`Session ${convId.length>16?convId.substring(0,16)+'...':convId}`;
  const container=document.getElementById('session-messages');
  container.innerHTML=loadingDots('Chargement...');
  try{
    const _ssh={};if(ADMIN_TOKEN)_ssh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(convId)}`,{headers:_ssh});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const tasks=d.tasks||[];
    const state=d.session_state||{};
    let html=`<div style="margin-bottom:12px;font-size:13px;color:var(--muted)">Conversation: ${esc(d.conversation_id)} — ${tasks.length} tache(s)</div>`;
    if(state.status)html+=`<div class="pill ${state.status==='running'?'ok':'accent'}" style="margin-bottom:12px">${esc(state.status)}</div>`;
    if(!tasks.length){html+='<div style="color:var(--muted)">Aucune tache trouvee pour cette conversation.</div>';}
    else{html+=tasks.map(t=>`
      <div class="list-item" style="margin-bottom:6px">
        <div>
          <div class="list-item-title">${esc(t.task_id||(typeof t==='string'?t:'?'))}</div>
          <div class="list-item-sub">${esc(t.status||'?')} — ${esc(t.channel||'web')} — ${esc(t.message_preview||'')}</div>
        </div>
        <span class="pill ${(t.status||'')==='done'?'ok':(t.status||'')==='failed'?'danger':'accent'}">${esc(t.status||'?')}</span>
      </div>
    `).join('');}
    container.innerHTML=html;
  }catch(e){container.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`}
}

export function closeSessionDetail(){
  document.getElementById('session-detail').style.display='none';
}

/* ============================================================
   OVERVIEW ENHANCED
   ============================================================ */
export function loadOverview(){
  const d=lastStatusData;if(!d)return;
  setText('ov-memories',d.memory_count||'—');
  setText('ov-skills',typeof d.skills_loaded==='number'?d.skills_loaded:'—');
  setText('ov-mood',d.mood||'—');
  // Model info from allModels (loaded separately)
  const curModel=allModels.find(m=>m.current);
  const ovModel=document.getElementById('ov-model');
  if(ovModel)ovModel.innerHTML=curModel?`
    <div style="font-size:16px;font-weight:600;color:var(--accent)">${esc(curModel.display_name)}</div>
    <div style="font-size:12px;color:var(--muted);margin-top:4px">Provider: ${esc(curModel.provider||'?')}</div>
    <div style="font-size:12px;color:var(--muted);margin-top:2px">${curModel.is_local?'💻 Local':'☁️ Cloud'} ${curModel.is_free?'🟢 Gratuit':'💰 Payant'}</div>
  `:'<div style="color:var(--muted)">Modele non charge</div>';
  const ovRes=document.getElementById('ov-resources');
  if(ovRes)ovRes.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between"><span>Energie</span><span style="color:var(--accent)">${d.energy||'?'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Modules actifs</span><span style="color:var(--ok)">${d.active_modules||'?'}/${d.total_modules||'?'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Code index</span><span>${d.symbols_count||'?'} symboles</span></div>
      <div style="display:flex;justify-content:space-between"><span>Instincts</span><span>${d.instincts_count||'?'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Memoires</span><span>${d.memory_count||'?'}</span></div>
    </div>`;
  const ovTasks=document.getElementById('ov-tasks');
  if(ovTasks){
    const bt=d.tasks_backlog||0,wt=d.tasks_waiting_io||0,dt=d.tasks_done||0,ft=d.tasks_failed||0;
    ovTasks.innerHTML=`
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;justify-content:space-between"><span>En attente</span><span class="pill ${bt>0?'warn':''}">${bt}</span></div>
        <div style="display:flex;justify-content:space-between"><span>En cours (IO)</span><span class="pill ${wt>0?'ok':''}">${wt}</span></div>
        <div style="display:flex;justify-content:space-between"><span>Terminees</span><span class="pill ok">${dt}</span></div>
        <div style="display:flex;justify-content:space-between"><span>Echouees</span><span class="pill ${ft>0?'danger':''}">${ft}</span></div>
      </div>`;
  }
  // Pipeline stats
  const ovPipe=document.getElementById('ov-pipeline');
  if(ovPipe)ovPipe.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;justify-content:space-between"><span>Chat total</span><span style="color:var(--accent)">${d.pipeline_chat_requests_total||0}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Chat succes</span><span style="color:var(--ok)">${d.pipeline_chat_success_total||0}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Stream total</span><span>${d.pipeline_stream_requests_total||0}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Erreurs</span><span style="color:var(--danger)">${d.pipeline_errors_total||0}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Timeouts</span><span style="color:var(--warn)">${d.pipeline_timeouts_total||0}</span></div>
    </div>`;
  // SLO
  const ovSlo=document.getElementById('ov-slo');
  if(ovSlo){
    if(!d.slo_enabled)ovSlo.innerHTML='<span style="color:var(--muted)">SLO desactive</span>';
    else ovSlo.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;justify-content:space-between"><span>Samples</span><span>${d.slo_samples||0}/${d.slo_window_size||0}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Succes</span><span style="color:var(--ok)">${((d.slo_success_rate||0)*100).toFixed(1)}%</span></div>
      <div style="display:flex;justify-content:space-between"><span>Latence P50</span><span>${d.slo_latency_median_ms||0}ms</span></div>
      <div style="display:flex;justify-content:space-between"><span>Latence P95</span><span style="color:var(--warn)">${d.slo_latency_p95_ms||0}ms</span></div>
      ${(d.slo_breaches||[]).length?`<div style="color:var(--danger);font-size:12px;margin-top:4px">⚠ ${d.slo_breaches.length} violation(s)</div>`:''}
    </div>`;
  }
  setText('ov-energy',d.energy||'—');
  // Connections
  const ovConn=document.getElementById('ov-connections');
  if(ovConn)ovConn.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Telegram</span><span class="pill ${d.telegram_running?'ok':'danger'}">${d.telegram_running?'ON':'OFF'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>WhatsApp</span><span class="pill ${d.whatsapp_running?'ok':'danger'}">${d.whatsapp_running?'ON':'OFF'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Daemon</span><span class="pill ${d.autonomy_running?'ok':'danger'}">${d.autonomy_running?'ON':'OFF'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Trace SSE</span><span class="pill ${traceConnected?'ok':'danger'}">${traceConnected?'ON':'OFF'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Sessions</span><span style="color:var(--accent)">${d.sessions_active||0}/${d.sessions_total||0}</span></div>
    </div>`;
  // Modules
  const ovMod=document.getElementById('ov-modules');
  if(ovMod){
    const mods=d.modules||{};
    const modEntries=Object.entries(mods);
    ovMod.innerHTML=`<div style="display:flex;flex-direction:column;gap:4px">${modEntries.map(([name,active])=>`<div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(name)}</span><span class="pill ${active?'ok':'warn'}" style="font-size:10px">${active?'ON':'OFF'}</span></div>`).join('')}<div style="margin-top:4px;font-size:11px;color:var(--muted)">${d.active_modules||0}/${d.total_modules||0} actifs</div></div>`;
  }
  // Daemon
  const ovDaemon=document.getElementById('ov-daemon');
  if(ovDaemon)ovDaemon.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Status</span><span class="pill ${d.autonomy_running?'ok':'danger'}">${d.autonomy_running?'ACTIF':'INACTIF'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Uptime</span><span style="color:var(--accent)">${esc(d.autonomy_uptime||'—')}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Actions/h</span><span style="color:var(--ok)">${d.autonomy_actions_last_hour||0}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>Execution</span><span class="pill ${d.autonomy_action_execution?'ok':'warn'}">${d.autonomy_action_execution?'ON':'OFF'}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>User present</span><span class="pill ${d.autonomy_user_present?'ok':'muted'}">${d.autonomy_user_present?'OUI':'NON'}</span></div>
      ${d.autonomy_last_error?`<div style="font-size:11px;color:var(--danger);margin-top:4px">⚠ ${esc(d.autonomy_last_error)}</div>`:''}
    </div>`;
  renderScheduledTasks();
  renderOverviewTraceFeed();
}

/* ============================================================
   TELEGRAM DETAILS
   ============================================================ */
export function loadTelegramDetails(){
  const d=lastStatusData;if(!d)return;
  const det=document.getElementById('tg-details');if(!det)return;
  det.innerHTML=`
    <div class="list-item"><div><div class="list-item-title">Bot actif</div></div><span class="pill ${d.telegram_running?'ok':'danger'}">${d.telegram_running?'OUI':'NON'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Active en config</div></div><span class="pill ${d.telegram_enabled?'ok':'warn'}">${d.telegram_enabled?'OUI':'NON'}</span></div>
    ${d.telegram_conflict_seen?'<div class="list-item"><div><div class="list-item-title">Conflit detecte</div><div class="list-item-sub">Un autre processus utilise le token</div></div><span class="pill danger">CONFLIT</span></div>':''}
    ${d.telegram_transient_error?`<div class="list-item"><div><div class="list-item-title">Erreur transitoire</div><div class="list-item-sub">Backoff: ${d.telegram_transient_backoff_sec||0}s</div></div><span class="pill warn">RETRY</span></div>`:''}
    ${d.telegram_last_error?`<div class="list-item"><div><div class="list-item-title">Derniere erreur</div><div class="list-item-sub">${esc(d.telegram_last_error)}</div></div><span class="pill danger">ERR</span></div>`:''}`;
  const stats=document.getElementById('tg-stats');
  if(stats)stats.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between"><span>Enabled</span><span style="color:${d.telegram_enabled?'var(--ok)':'var(--muted)'}">${d.telegram_enabled?'Oui':'Non'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Running</span><span style="color:${d.telegram_running?'var(--ok)':'var(--danger)'}">${d.telegram_running?'Oui':'Non'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Conflit</span><span style="color:${d.telegram_conflict_seen?'var(--danger)':'var(--ok)'}">${d.telegram_conflict_seen?'Oui':'Non'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Erreur transitoire</span><span>${d.telegram_transient_error?'Oui ('+d.telegram_transient_backoff_sec+'s)':'Non'}</span></div>
    </div>`;
}

/* ============================================================
   WHATSAPP DETAILS
   ============================================================ */
export function loadWhatsAppDetails(){
  const d=lastStatusData;if(!d)return;
  const det=document.getElementById('wa-details');if(!det)return;
  det.innerHTML=`
    <div class="list-item"><div><div class="list-item-title">Canal actif</div></div><span class="pill ${d.whatsapp_running?'ok':'danger'}">${d.whatsapp_running?'OUI':'NON'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Active en config</div></div><span class="pill ${d.whatsapp_enabled?'ok':'warn'}">${d.whatsapp_enabled?'OUI':'NON'}</span></div>
    ${d.whatsapp_state?`<div class="list-item"><div><div class="list-item-title">Etat</div></div><span class="pill">${esc(d.whatsapp_state)}</span></div>`:''}
    ${d.whatsapp_last_error?`<div class="list-item"><div><div class="list-item-title">Derniere erreur</div><div class="list-item-sub">${esc(d.whatsapp_last_error)}</div></div><span class="pill danger">ERR</span></div>`:''}`;
  const stats=document.getElementById('wa-stats');
  if(stats)stats.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between"><span>Enabled</span><span style="color:${d.whatsapp_enabled?'var(--ok)':'var(--muted)'}">${d.whatsapp_enabled?'Oui':'Non'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Running</span><span style="color:${d.whatsapp_running?'var(--ok)':'var(--danger)'}">${d.whatsapp_running?'Oui':'Non'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Etat</span><span>${d.whatsapp_state||'—'}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Cache dedup</span><span>${d.whatsapp_dedup_cache_size||0}</span></div>
    </div>`;
}

/* ============================================================
   AUTONOMY DETAILS
   ============================================================ */
export function loadAutonomyDetails(){
  const d=lastStatusData;if(!d)return;
  const det=document.getElementById('auto-details');if(!det)return;
  det.innerHTML=`
    <div class="list-item"><div><div class="list-item-title">Daemon actif</div></div><span class="pill ${d.autonomy_running?'ok':'danger'}">${d.autonomy_running?'OUI':'NON'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Disponible</div></div><span class="pill ${d.autonomy_available?'ok':'warn'}">${d.autonomy_available?'OUI':'NON'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Execution actions</div></div><span class="pill ${d.autonomy_action_execution?'ok':'warn'}">${d.autonomy_action_execution?'ON':'OFF'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Active sur web</div></div><span class="pill ${d.autonomy_enabled_on_web?'ok':'muted'}">${d.autonomy_enabled_on_web?'OUI':'NON'}</span></div>
    <div class="list-item"><div><div class="list-item-title">Utilisateur present</div></div><span class="pill ${d.autonomy_user_present?'ok':'muted'}">${d.autonomy_user_present?'OUI':'NON'}</span></div>`;
  const config=document.getElementById('auto-config');
  if(config)config.innerHTML=`
    <div style="display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between"><span>Uptime</span><span style="color:var(--accent)">${esc(d.autonomy_uptime||'—')}</span></div>
      <div style="display:flex;justify-content:space-between"><span>Actions derniere heure</span><span style="color:var(--ok)">${d.autonomy_actions_last_hour||0}</span></div>
      ${d.autonomy_last_error?`<div style="margin-top:8px;padding:8px;background:rgba(239,68,68,0.1);border-radius:8px;font-size:12px;color:var(--danger)">Erreur: ${esc(d.autonomy_last_error)}</div>`:''}
    </div>`;
}

/* ============================================================
   DOCS EDITOR (.lumena_rules / README / HEARTBEAT)
   ============================================================ */
let _currentDocKey = 'lumena_rules';

export async function loadDocs(){
  await switchDoc('lumena_rules');
}

export async function switchDoc(key){
  _currentDocKey = key;
  // Highlight onglet actif
  ['lumena_rules','readme','heartbeat'].forEach(k=>{
    const btn=document.getElementById(`doc-tab-${k}`);
    if(btn)btn.classList.toggle('primary', k===key);
  });
  const editor=document.getElementById('doc-editor');
  const fname=document.getElementById('doc-filename');
  const fsize=document.getElementById('doc-size');
  if(editor)editor.value='Chargement...';
  try{
    const r=await fetch(`${API_BASE}/api/docs/${key}`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(editor){editor.value=d.content||'';}
    if(fname)fname.textContent=d.filename||key;
    const bytes=(new TextEncoder().encode(d.content||'')).length;
    if(fsize)fsize.textContent=bytes>1024?`${(bytes/1024).toFixed(1)} ko`:`${bytes} o`;
  }catch(e){
    if(editor)editor.value=`Erreur: ${e.message}`;
  }
}

export async function saveDoc(){
  const editor=document.getElementById('doc-editor');
  const msg=document.getElementById('doc-save-msg');
  if(!editor||!msg)return;
  const content=editor.value;
  msg.style.display='block';
  msg.style.background='rgba(99,102,241,0.15)';
  msg.style.color='var(--accent)';
  msg.textContent='Sauvegarde...';
  try{
    const r=await fetch(`${API_BASE}/api/docs/${_currentDocKey}`,{
      method:'PUT',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({content})
    });
    const d=await r.json();
    if(d.success){
      msg.style.background='rgba(34,197,94,0.15)';
      msg.style.color='var(--ok)';
      msg.textContent=`✓ Sauvegardé (${d.bytes} octets)`;
    }else{
      throw new Error(d.error||'Erreur inconnue');
    }
  }catch(e){
    msg.style.background='rgba(239,68,68,0.15)';
    msg.style.color='var(--danger)';
    msg.textContent=`✗ ${e.message}`;
  }
  setTimeout(()=>{msg.style.display='none';},3000);
}

/* ============================================================
   PRODUCT DOCUMENTATION
   ============================================================ */
let _productDocSections = null;
let _currentDocSection = null;

export async function loadProductDocs() {
  const nav = document.getElementById('doc-nav');
  const main = document.getElementById('doc-main');
  if (!nav || !main) return;

  // Toujours recharger depuis l'API pour avoir les sections à jour
  _productDocSections = null;

  try {
    const r = await fetch(`${API_BASE}/api/product-docs`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (!d.success || !d.sections) throw new Error('Invalid response');
    _productDocSections = d.sections;

    // Build nav
    _renderDocNav(nav);

    // Build sections HTML
    main.innerHTML = d.sections.map(s =>
      `<div class="doc-section" id="doc-sec-${s.id}">
        <h2><i data-lucide="${s.icon}"></i> ${s.title}</h2>
        ${s.content}
      </div>`
    ).join('');

    // Init icons
    if (typeof lucide !== 'undefined') lucide.createIcons();

    // Show first section
    switchDocSection(d.sections[0].id);
  } catch (e) {
    main.innerHTML = `<div class="doc-loading" style="color:var(--danger)">Erreur: ${e.message}</div>`;
  }
}

function _renderDocNav(nav) {
  if (!_productDocSections) return;
  nav.innerHTML = _productDocSections.map(s =>
    `<div class="doc-nav-item${_currentDocSection === s.id ? ' active' : ''}"
          data-doc="${s.id}" onclick="switchDocSection('${s.id}')">
      <i data-lucide="${s.icon}"></i>
      <span>${s.title}</span>
    </div>`
  ).join('');
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export function switchDocSection(id) {
  _currentDocSection = id;
  // Update nav
  document.querySelectorAll('.doc-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.doc === id);
  });
  // Show section
  document.querySelectorAll('.doc-section').forEach(el => {
    el.classList.toggle('active', el.id === `doc-sec-${id}`);
  });
  // Scroll to top of main
  const main = document.getElementById('doc-main');
  if (main) main.scrollTop = 0;
}

/* ============================================================
   IONOS — Gestion des comptes SFTP
   ============================================================ */

let _ionosSites = [];

export async function loadIonosSites() {
  const box = document.getElementById('ionos-sites-list');
  if (!box) return;
  box.innerHTML = '<div style="color:var(--muted);padding:20px;text-align:center">Chargement...</div>';
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites`, { headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    _ionosSites = d.sites || [];
    _renderIonosSites();
    const badge = document.getElementById('badge-ionos');
    if (badge) { badge.textContent = _ionosSites.length; badge.style.background = _ionosSites.length ? 'var(--ok)' : 'var(--muted)'; }
  } catch (e) {
    box.innerHTML = `<div style="color:var(--danger);padding:20px">Erreur: ${e.message}</div>`;
  }
}

function _renderIonosSites() {
  const box = document.getElementById('ionos-sites-list');
  if (!box) return;
  if (!_ionosSites.length) {
    box.innerHTML = '<div style="color:var(--muted);padding:30px;text-align:center;font-size:13px">Aucun compte IONOS configuré.<br>Ajoute-en un ci-dessous.</div>';
    return;
  }
  let html = '';
  for (const s of _ionosSites) {
    const lastDeploy = s.last_deploy ? new Date(s.last_deploy).toLocaleString('fr-FR') : 'Jamais';
    html += `<div style="display:flex;align-items:center;justify-content:space-between;padding:12px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;background:rgba(255,255,255,.02)">
      <div style="flex:1;min-width:0">
        <div style="font-size:14px;font-weight:600;color:var(--text)">${esc(s.label || s.domain)}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:2px">${esc(s.host)}:${s.port} &mdash; ${esc(s.user)} &mdash; root: ${esc(s.root)}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px">Dernier déploiement: ${lastDeploy} &bull; ${s.deploy_count || 0} déploiement(s)</div>
      </div>
      <button class="btn" style="font-size:11px;padding:4px 10px;color:var(--danger);border-color:var(--danger)" onclick="removeIonosSite('${esc(s.domain)}')">
        <i data-lucide="trash-2" style="width:13px;height:13px"></i> Supprimer
      </button>
    </div>`;
  }
  box.innerHTML = html;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function addIonosSite() {
  const get = id => (document.getElementById(id) || {}).value?.trim() || '';
  const domain = get('ionos-domain');
  const host = get('ionos-host');
  const user = get('ionos-user');
  const password = get('ionos-password');
  const port = parseInt(get('ionos-port') || '22', 10);
  const root = get('ionos-root') || '/';
  const label = get('ionos-label') || '';
  const msg = document.getElementById('ionos-msg');

  if (!domain || !host || !user || !password) {
    if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Domaine, host, utilisateur et mot de passe sont obligatoires.'; }
    return;
  }

  if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--muted)'; msg.textContent = 'Test de connexion SFTP en cours...'; }

  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${ADMIN_TOKEN}` },
      body: JSON.stringify({ domain, host, user, password, port, root, label }),
    });
    const d = await r.json();
    if (r.ok) {
      if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--ok)'; msg.textContent = `Site ${domain} ajouté avec succès. Connexion SFTP OK.`; }
      // Clear form
      ['ionos-domain', 'ionos-host', 'ionos-user', 'ionos-password', 'ionos-label'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
      loadIonosSites();
    } else {
      if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = `Erreur: ${d.detail || 'Échec de connexion'}`; }
    }
  } catch (e) {
    if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = `Erreur: ${e.message}`; }
  }
}

export async function removeIonosSite(domain) {
  if (!confirm(`Supprimer le site ${domain} ?`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN}` },
    });
    if (r.ok) {
      loadIonosSites();
    } else {
      const d = await r.json();
      alert(`Erreur: ${d.detail || 'Échec suppression'}`);
    }
  } catch (e) { alert(`Erreur: ${e.message}`); }
}
