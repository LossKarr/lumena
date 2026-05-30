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
const _FT_PHASES=['generating','judging','sampling','preparing','training','merging','converting','quantizing','importing','done'];
const _FT_PHASE_LABELS={
  generating:'Personnalité',judging:'Scoring',sampling:'DPO',
  preparing:'Données',training:'Entraînement',merging:'Fusion',
  converting:'GGUF',quantizing:'Quantization',importing:'Ollama',done:'Terminé',
};
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

  // Group: installed first, then by category
  const installed=models.filter(m=>m.already_installed);
  const notInstalled=models.filter(m=>!m.already_installed);

  const sections=[];
  if(installed.length){
    sections.push({label:'INSTALLÉS',items:installed});
  }
  const cats={llm:[],code:[],vision:[]};
  for(const m of notInstalled){const c=m.category||'llm';if(!cats[c])cats[c]=[];cats[c].push(m);}
  for(const [cat,items] of Object.entries(cats)){
    if(items.length)sections.push({label:cat.toUpperCase(),items});
  }

  let firstPicked=false;
  for(const sec of sections){
    const lbl=document.createElement('div');lbl.className='ft-select-group-label';lbl.textContent=sec.label;
    dropdown.appendChild(lbl);
    for(const m of sec.items){
      const opt=document.createElement('div');opt.className='ft-select-option';
      const val=m.hf_id_4bit||m.hf_id_full||m.ollama_id;
      opt.dataset.value=val;
      opt.dataset.ollamaTag=m.ollama_id;
      const tags=[];
      if(m.already_installed)tags.push('\u2705 installé');
      if(m.auto_detected)tags.push('auto-détecté');
      if(m.fits_vram===false)tags.push('\u26a0 VRAM insuffisante');
      const tagStr=tags.length?` [${tags.join(' | ')}]`:'';
      const vramStr=m.vram_ft_min_gb>0?` — ${m.vram_ft_min_gb} Go VRAM`:'';
      const paramsStr=m.params&&m.params!=='?'?` — ${m.params}`:'';
      opt.textContent=`${m.ollama_id}${paramsStr}${vramStr}${tagStr}`;
      if(m.fits_vram===false&&!m.already_installed)opt.classList.add('dimmed');
      opt.addEventListener('click',()=>_ftSelectOption(wrap,opt));
      dropdown.appendChild(opt);
      if(!firstPicked&&m.already_installed){
        _ftSelectOption(wrap,opt,true);firstPicked=true;
      }
    }
  }
  if(!firstPicked){
    // Pick first fitting model
    const first=dropdown.querySelector('.ft-select-option:not(.dimmed)')||dropdown.querySelector('.ft-select-option');
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
      <button class="btn success" style="font-size:11px;padding:3px 8px;background:var(--success,#4caf50);color:#fff" onclick="_activateFinetuned('${esc(m.model_name)}')">Activer par défaut</button>
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
                  return`<span class="ft-phase ${cls}">${_FT_PHASE_LABELS[p]||p}</span>`;
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

window._activateFinetuned=async function(name){
  if(!confirm(`Activer "${name}" comme modèle par défaut de Lumena ?\n\nUn redémarrage sera nécessaire pour prendre effet.`))return;
  try{
    const r=await fetch(`${API_BASE}/api/finetuning/activate/${encodeURIComponent(name)}`,{method:'POST',headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.success){
      alert(`✅ ${d.message||`Modèle "${name}" activé !`}\n\nRedémarrez Lumena pour utiliser votre modèle fine-tuné.`);
    }else{
      alert(`Erreur : ${d.detail||d.message||`HTTP ${r.status}`}`);
    }
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
let _cfgActiveGroup=null; // currently selected group in sidebar
const _LEVEL_ORDER=['simple','avancé','expert'];

// P5.1 — Ordre et niveau des groupes
const _GROUP_ORDER=[
  {name:'LLM',               level:'simple',  icon:'brain'},
  {name:'Cerveaux Spécialisés',level:'simple', icon:'cpu'},
  {name:'Préférences',       level:'simple',   icon:'heart'},
  {name:'Voix',              level:'simple',   icon:'mic'},
  {name:'Autonomie',         level:'simple',   icon:'bot'},
  {name:'Alertes',           level:'simple',   icon:'bell'},
  {name:'Clés API',          level:'simple',   icon:'key'},
  {name:'Telegram',          level:'simple',   icon:'send'},
  {name:'WhatsApp',          level:'simple',   icon:'message-circle'},
  {name:'Serveur',           level:'avancé',   icon:'server'},
  {name:'Browser',           level:'avancé',   icon:'globe'},
  {name:'Email',             level:'avancé',   icon:'mail'},
  {name:'Paiements',         level:'avancé',   icon:'credit-card'},
  {name:'Automation (n8n)',  level:'avancé',   icon:'workflow'},
  {name:'Vidéo',             level:'avancé',   icon:'video'},
  {name:'IONOS (Hébergement)',level:'avancé',  icon:'cloud'},
  {name:'Apprentissage',     level:'avancé',   icon:'graduation-cap'},
  {name:'Ops',               level:'expert',   icon:'activity'},
  {name:'SLO',               level:'expert',   icon:'gauge'},
  {name:'Système',           level:'expert',   icon:'terminal'},
  {name:'Instance',          level:'expert',   icon:'box'},
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
    input=`<div style="display:flex;align-items:center;gap:6px"><input type="password" id="${uid}" data-cfg="${esc(it.key)}" data-secret="1" class="input" style="height:32px;font-size:12px;padding:0 8px;width:280px;font-family:var(--mono)" value="${esc(val)}" readonly><button type="button" class="btn" style="font-size:12px;padding:4px 8px;min-width:34px" title="Voir / masquer" onclick="toggleSecret('${uid}')"><i data-lucide="eye" style="width:14px;height:14px;pointer-events:none"></i></button>${sbadge}</div>`;
  }else{
    input=`<input type="text" data-cfg="${esc(it.key)}" class="input" style="height:32px;font-size:12px;padding:0 8px;min-width:240px" value="${esc(val)}">`;
  }
  const restartBadge=it.restart?`<span style="font-size:9px;background:rgba(255,165,0,.12);color:orange;border-radius:3px;padding:1px 5px;margin-left:4px">restart</span>`:'';
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
  return rows;
}

function _switchCfgGroup(name){
  _cfgActiveGroup=name;
  // Update sidebar active state
  document.querySelectorAll('.cfg-nav-item').forEach(el=>{
    el.classList.toggle('active',el.dataset.group===name);
  });
  // Render group content
  const box=document.getElementById('config-groups');
  const title=document.getElementById('cfg-group-title');
  if(!box||!_configData)return;
  const groups=_configData.groups||{};
  const items=groups[name]||[];
  const grp=_GROUP_ORDER.find(g=>g.name===name);
  const iconName=grp?grp.icon:'settings';
  if(title)title.innerHTML=`<i data-lucide="${iconName}"></i> ${esc(name)}`;
  if(!items.length){
    box.innerHTML=`<div class="cfg-empty">Aucun paramètre dans ce groupe.</div>`;
  }else{
    box.innerHTML=`<div class="cfg-group-content">${_renderGroupCard(name,items)}</div>`;
  }
  // Re-init lucide icons
  if(window.lucide)window.lucide.createIcons();
}

function _renderConfig(){
  const nav=document.getElementById('cfg-nav');
  const box=document.getElementById('config-groups');
  if(!nav||!box||!_configData)return;
  const groups=_configData.groups||{};

  // Build sidebar nav grouped by level
  const simple=[],avance=[],expert=[],seen=new Set();
  for(const{name,level,icon}of _GROUP_ORDER){
    seen.add(name);
    if(!groups[name]||!groups[name].length)continue;
    const count=groups[name].length;
    const entry={name,icon,count};
    if(level==='simple')simple.push(entry);
    else if(level==='avancé')avance.push(entry);
    else expert.push(entry);
  }
  // Unknown groups → avancé
  for(const[name,items]of Object.entries(groups)){
    if(seen.has(name))continue;
    if(items&&items.length)avance.push({name,icon:'folder',count:items.length});
  }

  let navHtml='';
  const renderNavItem=({name,icon,count})=>`<div class="cfg-nav-item${_cfgActiveGroup===name?' active':''}" data-group="${esc(name)}" onclick="switchCfgGroup('${esc(name).replace(/'/g,"\\'")}')" title="${esc(name)}"><i data-lucide="${icon}" style="width:16px;height:16px;flex-shrink:0"></i><span class="cfg-nav-label">${esc(name)}</span><span class="cfg-nav-badge">${count}</span></div>`;

  if(simple.length){
    navHtml+=`<div class="cfg-nav-section">Général</div>`;
    for(const e of simple)navHtml+=renderNavItem(e);
  }
  if(avance.length){
    navHtml+=`<div class="cfg-nav-section" style="margin-top:12px">Avancé</div>`;
    for(const e of avance)navHtml+=renderNavItem(e);
  }
  if(expert.length){
    navHtml+=`<div class="cfg-nav-section" style="margin-top:12px">Expert</div>`;
    for(const e of expert)navHtml+=renderNavItem(e);
  }
  nav.innerHTML=navHtml;

  // Select first group if none active or if active group no longer exists
  const firstGroup=simple[0]||avance[0]||expert[0];
  if(!_cfgActiveGroup||!groups[_cfgActiveGroup]){
    _cfgActiveGroup=firstGroup?firstGroup.name:null;
  }
  if(_cfgActiveGroup)_switchCfgGroup(_cfgActiveGroup);
  if(window.lucide)window.lucide.createIcons();
}

// Expose for inline onclick handlers
window.switchCfgGroup=function(name){_switchCfgGroup(name);};
window.toggleSecret=function(uid){toggleSecret(uid);};

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
async function loadSessionsLegacy(){
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

function filterSessionsLegacy(){}

async function loadSessionDetailLegacy(convId){
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

function closeSessionDetailLegacy(){
  document.getElementById('session-detail').style.display='none';
}

let _selectedSessionId=null;

function _sessionStatusClass(status){
  const s=(status||'').toLowerCase();
  if(s==='done')return'ok';
  if(s==='error'||s==='failed')return'danger';
  if(s==='running'||s==='waiting_io'||s==='checkpointed')return'accent';
  if(s==='cancelled'||s==='archived')return'muted';
  return'warn';
}

function _sessionStatusLabel(status){
  const s=(status||'').toLowerCase();
  return({running:'active',waiting_io:'attente',checkpointed:'checkpoint',done:'terminee',error:'erreur',failed:'erreur',cancelled:'annulee'}[s]||s||'inconnue');
}

function _fmtSessionDate(value){
  if(!value)return'--';
  try{return new Date(value).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}catch(e){return value}
}

function _sessionHeaders(){
  const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;return h;
}

function _sessionFilters(){
  return {
    q:(document.getElementById('session-search-input')?.value||'').trim(),
    status:(document.getElementById('session-status-filter')?.value||'').trim(),
    channel:(document.getElementById('session-channel-filter')?.value||'').trim()
  };
}

export async function loadSessions(){
  const list=document.getElementById('sessions-list');if(!list)return;
  const stats=document.getElementById('sessions-stats');
  if(!list.dataset.rendered)list.innerHTML=loadingDots('Chargement des conversations...');
  try{
    const f=_sessionFilters();
    const params=new URLSearchParams({limit:'120'});
    if(f.q)params.set('q',f.q);
    if(f.status)params.set('status',f.status);
    if(f.channel)params.set('channel',f.channel);
    const r=await fetch(`${API_BASE}/api/sessions?${params.toString()}`,{headers:_sessionHeaders()});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    allSessions=Array.isArray(d.sessions)?d.sessions:[];
    const s=d.stats||{};
    const statsHtml=`
      <div class="card"><div class="card-title">Conversations</div><div style="font-size:28px;font-weight:700;color:var(--accent)">${s.total??d.total??0}</div><div class="list-item-sub">Historique persistant</div></div>
      <div class="card"><div class="card-title">Actives</div><div style="font-size:28px;font-weight:700;color:var(--ok)">${s.active||0}</div><div class="list-item-sub">En cours ou en attente</div></div>
      <div class="card"><div class="card-title">Archivees</div><div style="font-size:28px;font-weight:700;color:var(--warn)">${s.archived||0}</div><div class="list-item-sub">Masquees par defaut</div></div>
    `;
    if(stats&&stats.dataset.snapshot!==statsHtml){
      stats.innerHTML=statsHtml;
      stats.dataset.snapshot=statsHtml;
    }
    renderSessionsList(allSessions);
    if(_selectedSessionId&&!allSessions.some(x=>x.conversation_id===_selectedSessionId))closeSessionDetail();
    else if(_selectedSessionId&&!document.querySelector('#session-detail .session-detail-head'))loadSessionDetail(_selectedSessionId);
  }catch(e){
    if(stats)stats.innerHTML='';
    list.innerHTML=`<div class="card" style="color:var(--danger);font-size:13px">Impossible de charger les sessions: ${esc(e.message)}</div>`;
  }
}

export function filterSessions(){
  clearTimeout(window._sessionFilterTimer);
  window._sessionFilterTimer=setTimeout(()=>loadSessions(),180);
}

export function renderSessionsList(sessions){
  const list=document.getElementById('sessions-list');if(!list)return;
  if(!sessions.length){
    const emptyHtml='<div class="card" style="color:var(--muted);font-size:13px;text-align:center;padding:28px">Aucune conversation enregistree pour le moment. Les prochaines discussions apparaitront ici automatiquement.</div>';
    if(list.dataset.snapshot!==emptyHtml){
      list.innerHTML=emptyHtml;
      list.dataset.snapshot=emptyHtml;
      list.dataset.rendered='1';
    }
    const detail=document.getElementById('session-detail');
    if(detail&&!_selectedSessionId){
      const detailEmpty='<div class="sessions-empty-detail"><i data-lucide="message-square-text"></i><div>Demarre une conversation pour alimenter cet historique.</div></div>';
      if(detail.dataset.snapshot!==detailEmpty){
        detail.innerHTML=detailEmpty;
        detail.dataset.snapshot=detailEmpty;
      }
    }
    if(window.lucide)window.lucide.createIcons();
    return;
  }
  const html=sessions.map(s=>{
    const active=s.conversation_id===_selectedSessionId?'active':'';
    const title=s.title||s.last_message_preview||s.conversation_id;
    const preview=s.last_response_preview||s.last_message_preview||'Aucun message resume';
    return`
      <div class="list-item session-row ${active}" data-action="loadSessionDetail" data-arg="${esc(String(s.conversation_id||''))}">
        <div class="session-row-main">
          <div class="session-row-title">
            <i data-lucide="${(s.status||'')==='done'?'check-circle-2':'message-square'}" style="width:15px;height:15px;color:var(--accent);flex-shrink:0"></i>
            <span title="${esc(title)}">${esc(title)}</span>
          </div>
          <div class="session-row-preview">${esc(preview)}</div>
          <div class="session-row-meta">
            <span class="pill ${_sessionStatusClass(s.status)}">${esc(_sessionStatusLabel(s.status))}</span>
            <span class="pill muted">${esc(s.channel||'web')}</span>
            <span class="pill muted">${Number(s.message_count||0)} msg</span>
            <span class="pill muted">${esc(_fmtSessionDate(s.updated_at))}</span>
          </div>
        </div>
      </div>`;
  }).join('');
  if(list.dataset.snapshot!==html){
    list.innerHTML=html;
    list.dataset.snapshot=html;
    list.dataset.rendered='1';
    if(window.lucide)window.lucide.createIcons();
  }
}

export async function loadSessionDetail(convId, opts){
  if(!convId)return;
  const options=(opts&&opts.constructor===Object)?opts:{};
  const detail=document.getElementById('session-detail');
  _selectedSessionId=convId;
  renderSessionsList(allSessions||[]);
  if(detail&&!options.silent){
    detail.innerHTML=loadingDots('Chargement du detail...');
    delete detail.dataset.snapshot;
  }
  try{
    const r=await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(convId)}?limit=250`,{headers:_sessionHeaders()});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const tasks=d.tasks||[];
    const state=d.session_state||{};
    const session=d.session||state||{conversation_id:d.conversation_id,status:state.status};
    const messages=d.messages||[];
    const events=d.events||state.events||[];
    const title=session.title||session.last_message_preview||`Session ${convId}`;
    const status=session.status||state.status||'unknown';
    const messageHtml=messages.length?messages.map(m=>`
      <div class="session-message ${esc(m.role||'')}">
        <div class="session-message-head">
          <span class="pill ${m.role==='user'?'accent':'ok'}">${esc(m.role==='user'?'Utilisateur':'Lumena')}</span>
          <span>${esc(_fmtSessionDate(m.ts))}${m.model_used?` - ${esc(m.provider_used||'?')}/${esc(m.model_used)}`:''}</span>
        </div>
        <div class="session-message-body">${esc(m.content||'')}</div>
      </div>`).join(''):'<div class="list-item-sub">Aucun message persistant pour cette session.</div>';
    const taskHtml=tasks.length?tasks.map(t=>`
      <div class="list-item" style="margin-bottom:6px">
        <div style="min-width:0">
          <div class="list-item-title">${esc(t.message_preview||t.task_id||'?')}</div>
          <div class="list-item-sub">ID ${esc((t.task_id||'').substring(0,16))} - ${esc(t.channel||'web')} - ${esc(_fmtSessionDate(t.updated_at||t.created_at))}</div>
          ${t.last_error?`<div style="font-size:12px;color:var(--danger);margin-top:4px">${esc(t.last_error)}</div>`:''}
        </div>
        <span class="pill ${_sessionStatusClass(t.state||t.status)}">${esc(t.state||t.status||'?')}</span>
      </div>`).join(''):'<div class="list-item-sub">Aucune tache liee a cette conversation.</div>';
    const eventHtml=events.length?events.slice(-80).reverse().map(ev=>`
      <div class="session-timeline-item">
        <div class="session-timeline-time">${esc(_fmtSessionDate(ev.ts))}</div>
        <div class="session-timeline-body">
          <strong>${esc(ev.type||ev.status||'event')}</strong>
          <div title="${esc(ev.summary||ev.error||'')}">${esc(ev.summary||ev.error||ev.status||'')}</div>
        </div>
      </div>`).join(''):'<div class="list-item-sub">Aucun evenement detaille.</div>';
    const detailHtml=`
      <div class="session-detail-head">
        <div style="min-width:0">
          <div class="session-detail-title">${esc(title)}</div>
          <div class="session-detail-sub">
            <span class="pill ${_sessionStatusClass(status)}">${esc(_sessionStatusLabel(status))}</span>
            <span class="pill muted">${esc(session.channel||state.last_channel||'web')}</span>
            <span class="pill muted">${Number(session.message_count||messages.length||0)} messages</span>
            <span class="pill muted">Maj ${esc(_fmtSessionDate(session.updated_at||state.updated_at))}</span>
          </div>
          <div class="list-item-sub" style="margin-top:8px">ID: ${esc(convId)}</div>
        </div>
        <div class="session-detail-actions">
          <button class="btn primary" data-action="resumeSessionInChat" data-arg="${esc(convId)}"><i data-lucide="play"></i> Reprendre</button>
          <button class="btn" data-action="exportSessionMarkdown" data-arg="${esc(convId)}"><i data-lucide="download"></i> Exporter</button>
          <button class="btn" data-action="archiveSession" data-arg="${esc(convId)}"><i data-lucide="archive"></i> Archiver</button>
        </div>
      </div>
      <div class="session-section">
        <div class="session-section-title">Messages</div>
        ${messageHtml}
      </div>
      <div class="session-section">
        <div class="session-section-title">Taches liees</div>
        ${taskHtml}
      </div>
      <div class="session-section">
        <div class="session-section-title">Timeline</div>
        ${eventHtml}
      </div>`;
    if(detail&&detail.dataset.snapshot!==detailHtml){
      detail.innerHTML=detailHtml;
      detail.dataset.snapshot=detailHtml;
      if(window.lucide)window.lucide.createIcons();
    }
  }catch(e){
    if(detail)detail.innerHTML=`<div class="card" style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
}

export function closeSessionDetail(){
  _selectedSessionId=null;
  renderSessionsList(allSessions||[]);
  const detail=document.getElementById('session-detail');
  if(detail){
    detail.innerHTML='<div class="sessions-empty-detail"><i data-lucide="message-square-text"></i><div>Selectionne une conversation pour voir son detail.</div></div>';
    delete detail.dataset.snapshot;
  }
  if(window.lucide)window.lucide.createIcons();
}

export async function archiveSession(convId){
  if(!convId)return;
  try{
    const r=await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(convId)}/archive`,{method:'POST',headers:_sessionHeaders()});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    _selectedSessionId=null;
    closeSessionDetail();
    loadSessions();
  }catch(e){logC(`Archive session: ${e.message}`,'error')}
}

export async function exportSessionMarkdown(convId){
  if(!convId)return;
  try{
    const r=await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(convId)}?limit=1000`,{headers:_sessionHeaders()});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const session=d.session||{};
    const messages=d.messages||[];
    const title=session.title||convId;
    const lines=[`# ${title}`,'',`Conversation: ${convId}`,`Date: ${_fmtSessionDate(session.created_at)}`,''];
    for(const m of messages){
      lines.push(`## ${m.role==='user'?'Utilisateur':'Lumena'}`,'',m.content||'','');
    }
    const blob=new Blob([lines.join('\n')],{type:'text/markdown'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`lumena-session-${convId.substring(0,12)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }catch(e){logC(`Export session: ${e.message}`,'error')}
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
    <div style="font-size:12px;color:var(--muted);margin-top:2px">${curModel.is_local?'Local':'Cloud'} · <span style="color:${curModel.is_free?'var(--ok)':'var(--warn)'}">${curModel.is_free?'Gratuit':'Payant'}</span></div>
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
    main.innerHTML = d.sections.map(s => {
      const iconHtml = s.icon === 'lumena-logo'
        ? `<img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:24px;height:24px;object-fit:contain;vertical-align:middle">`
        : `<i data-lucide="${s.icon}"></i>`;
      return `<div class="doc-section" id="doc-sec-${s.id}">
        <h2>${iconHtml} ${s.title}</h2>
        ${s.content}
      </div>`;
    }).join('');

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
  nav.innerHTML = _productDocSections.map(s => {
    const iconHtml = s.icon === 'lumena-logo'
      ? `<img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:16px;height:16px;object-fit:contain">`
      : `<i data-lucide="${s.icon}"></i>`;
    return `<div class="doc-nav-item${_currentDocSection === s.id ? ' active' : ''}"
          data-doc="${s.id}" onclick="switchDocSection('${s.id}')">
      ${iconHtml}
      <span>${s.title}</span>
    </div>`;
  }).join('');
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
    const dom = esc(s.domain);
    html += `<div style="padding:12px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;background:rgba(255,255,255,.02)">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="flex:1;min-width:0">
          <div style="font-size:14px;font-weight:600;color:var(--text)">${esc(s.label || s.domain)}</div>
          <div style="font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:2px">${esc(s.host)}:${s.port} &mdash; ${esc(s.user)} &mdash; root: ${esc(s.root)}</div>
          <div style="font-size:10px;color:var(--muted);margin-top:2px">Dernier déploiement: ${lastDeploy} &bull; ${s.deploy_count || 0} déploiement(s)</div>
        </div>
        <button class="btn" style="font-size:11px;padding:4px 10px;color:var(--danger);border-color:var(--danger)" onclick="removeIonosSite('${dom}')">
          <i data-lucide="trash-2" style="width:13px;height:13px"></i> Supprimer
        </button>
      </div>
      ${_renderIonosDbBlock(s)}
    </div>`;
  }
  box.innerHTML = html;
  if (typeof lucide !== 'undefined') lucide.createIcons();
  // Rafraîchit le statut bridge (async, par site configuré).
  for (const s of _ionosSites) { if (s.database_configured) refreshIonosBridgeStatus(s.domain); }
}

function _renderIonosDbBlock(s) {
  const dom = esc(s.domain);
  let status, actions;
  if (s.database_configured) {
    let badge;
    if (s.database_last_check_ok === true) badge = '<span style="color:var(--ok)">● Connexion OK</span>';
    else if (s.database_last_check_ok === false) badge = '<span style="color:var(--danger)">● Erreur connexion</span>';
    else badge = '<span style="color:var(--muted)">● Non testée</span>';
    status = `<span style="font-size:11px">BDD associée &mdash; ${badge}</span>`;
    actions = `
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDbModal('${dom}')"><i data-lucide="settings" style="width:12px;height:12px"></i> Modifier</button>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="testIonosDb('${dom}')"><i data-lucide="plug" style="width:12px;height:12px"></i> Tester</button>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDbExplorer('${dom}')"><i data-lucide="table" style="width:12px;height:12px"></i> Explorer la BDD</button>
      <button class="btn" style="font-size:10px;padding:3px 8px;color:var(--danger);border-color:var(--danger)" onclick="clearIonosDb('${dom}')"><i data-lucide="x" style="width:12px;height:12px"></i> Retirer</button>`;
  } else {
    status = '<span style="font-size:11px;color:var(--muted)">Aucune BDD associée</span>';
    actions = `<button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDbModal('${dom}')"><i data-lucide="database" style="width:12px;height:12px"></i> Associer BDD</button>`;
  }
  const bridgeRow = s.database_configured ? `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px">
      <div style="display:flex;align-items:center;gap:6px;font-size:11px"><i data-lucide="shield" style="width:13px;height:13px;color:var(--muted)"></i>
        <span id="ionos-bridge-status-${dom}" style="color:var(--muted)">Accès sécurisé : …</span></div>
      <div style="display:flex;gap:6px;flex-wrap:wrap" id="ionos-bridge-actions-${dom}">
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="installIonosBridge('${dom}')"><i data-lucide="shield-check" style="width:12px;height:12px"></i> <span id="ionos-bridge-btn-${dom}">Activer l'accès BDD sécurisé</span></button>
        <button class="btn" style="font-size:10px;padding:3px 8px;color:var(--danger);border-color:var(--danger);display:none" id="ionos-bridge-rm-${dom}" onclick="removeIonosBridge('${dom}')"><i data-lucide="shield-off" style="width:12px;height:12px"></i> Supprimer le bridge</button>
      </div>
    </div>` : '';
  return `<div style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--border)">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
      <div style="display:flex;align-items:center;gap:6px"><i data-lucide="database" style="width:13px;height:13px;color:var(--muted)"></i> ${status}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap" id="ionos-db-status-${dom}">${actions}</div>
    </div>${bridgeRow}
  </div>`;
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

/* ============================================================
   IONOS — Base de données par site (Étape 2.5)
   Mot de passe jamais réaffiché : champ vide = conserver l'existant.
   ============================================================ */

const _ionosAuthHeaders = (json) => {
  const h = { 'Authorization': `Bearer ${ADMIN_TOKEN}` };
  if (json) h['Content-Type'] = 'application/json';
  return h;
};

export async function openIonosDbModal(domain) {
  // Récupère la config non sensible existante (sans mot de passe).
  let cfg = { configured: false };
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database`, { headers: _ionosAuthHeaders(false) });
    if (r.ok) cfg = await r.json();
  } catch (e) { /* affiche un formulaire vide */ }

  const v = (x) => esc(x || '');
  const existing = cfg.configured;
  const inS = 'width:100%;height:32px;font-size:12px;padding:0 8px;box-sizing:border-box';
  const lbl = (t) => `<label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">${t}</label>`;
  document.querySelectorAll('#ionos-db-modal').forEach(n => n.remove());
  const modal = document.createElement('div');
  modal.id = 'ionos-db-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML = `
    <div class="card" style="width:min(460px,92vw);max-height:88vh;overflow-y:auto;overflow-x:hidden;margin:0">
      <div class="card-title"><i data-lucide="database"></i> ${existing ? 'Modifier' : 'Associer'} la BDD — ${esc(domain)}</div>
      <div class="card-content">
        <p style="color:var(--muted);font-size:12px;margin-top:0">Le mot de passe n'est jamais réaffiché.${existing ? ' Laisse-le vide pour conserver l\'actuel.' : ''}</p>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <div style="grid-column:span 2">${lbl('Hôte *')}<input id="db-f-host" class="input" placeholder="dbXXXX.hosting-data.io" value="${v(cfg.host)}" style="${inS}"></div>
          <div>${lbl('Nom de la base *')}<input id="db-f-name" class="input" placeholder="dbsXXXXX" value="${v(cfg.name)}" style="${inS}"></div>
          <div>${lbl('Port')}<input id="db-f-port" class="input" type="number" value="${cfg.port || 3306}" style="${inS}"></div>
          <div>${lbl('Utilisateur *')}<input id="db-f-user" class="input" placeholder="dbuXXXXX" value="${v(cfg.user)}" style="${inS}"></div>
          <div>${lbl('Mot de passe' + (existing ? ' (vide = inchangé)' : ' *'))}<input id="db-f-password" class="input" type="password" placeholder="••••••••" value="" style="${inS}"></div>
          <div>${lbl('Moteur')}<input id="db-f-engine" class="input" placeholder="mariadb" value="${v(cfg.engine) || 'mariadb'}" style="${inS}"></div>
          <div>${lbl('Version (optionnel)')}<input id="db-f-version" class="input" placeholder="10.11" value="${v(cfg.version)}" style="${inS}"></div>
          <div style="grid-column:span 2">${lbl('Libellé (optionnel)')}<input id="db-f-label" class="input" placeholder="BDD principale" value="${v(cfg.label)}" style="${inS}"></div>
        </div>
        <div id="ionos-db-modal-msg" style="display:none;margin-top:10px;padding:6px 12px;border-radius:6px;font-size:12px"></div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
          <button class="btn" style="font-size:12px;padding:6px 16px" onclick="closeIonosDbModal()">Annuler</button>
          <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="saveIonosDb('${esc(domain)}')"><i data-lucide="save"></i> Enregistrer</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeIonosDbModal(); });
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export function closeIonosDbModal() {
  document.querySelectorAll('#ionos-db-modal').forEach(n => n.remove());
}

export async function saveIonosDb(domain) {
  const get = id => (document.getElementById(id) || {}).value?.trim() || '';
  const msg = document.getElementById('ionos-db-modal-msg');
  const body = {
    host: get('db-f-host'), name: get('db-f-name'), user: get('db-f-user'),
    password: (document.getElementById('db-f-password') || {}).value || '',
    port: parseInt(get('db-f-port') || '3306', 10),
    engine: get('db-f-engine') || 'mariadb', version: get('db-f-version'),
    label: get('db-f-label'), description: '',
  };
  if (!body.host || !body.name || !body.user) {
    if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Hôte, nom de base et utilisateur sont obligatoires.'; }
    return;
  }
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify(body),
    });
    const d = await r.json();
    if (r.ok) { closeIonosDbModal(); loadIonosSites(); }
    else if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = `Erreur: ${d.detail || 'échec'}`; }
  } catch (e) {
    if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = `Erreur: ${e.message}`; }
  }
}

export async function testIonosDb(domain) {
  const slot = document.getElementById(`ionos-db-status-${domain}`);
  if (slot) slot.insertAdjacentHTML('afterbegin', '<span id="db-testing" style="font-size:10px;color:var(--muted)">Test…</span>');
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/test`, {
      method: 'POST', headers: _ionosAuthHeaders(false),
    });
    const d = await r.json();
    if (r.ok && d.ok) alert(`Connexion BDD OK (${d.latency_ms} ms).`);
    else alert(`Connexion BDD échouée.\n\n${d.message || 'Vérifie les identifiants et l\'hôte.'}`);
  } catch (e) { alert(`Erreur: ${e.message}`); }
  finally { loadIonosSites(); }
}

export async function clearIonosDb(domain) {
  if (!confirm(`Retirer la BDD du site ${domain} ? (le site SFTP reste intact)`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database`, {
      method: 'DELETE', headers: _ionosAuthHeaders(false),
    });
    if (r.ok) loadIonosSites();
    else { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); }
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

/* ============================================================
   IONOS — Bridge BDD sécurisé : statut / install / suppression
   ============================================================ */

export async function refreshIonosBridgeStatus(domain) {
  const statusEl = document.getElementById(`ionos-bridge-status-${domain}`);
  const btnEl = document.getElementById(`ionos-bridge-btn-${domain}`);
  const rmEl = document.getElementById(`ionos-bridge-rm-${domain}`);
  if (!statusEl) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/bridge`, { headers: _ionosAuthHeaders(false) });
    const d = await r.json();
    if (!r.ok) { statusEl.textContent = 'Accès sécurisé : statut indisponible'; return; }
    if (d.installed) {
      const lc = d.last_check;
      const chk = lc && lc.ok ? ' · testé OK' : (lc && lc.ok === false ? ' · dernier test KO' : ' · non testé');
      const orph = d.orphan ? ` ⚠️ ${d.orphan}` : '';
      statusEl.innerHTML = `Accès sécurisé : <span style="color:var(--ok)">installé</span> (v${esc(String(d.version||'?'))})${esc(chk)}${esc(orph)}`;
      if (btnEl) btnEl.textContent = "Réinstaller l'accès";
      if (rmEl) rmEl.style.display = '';
    } else {
      statusEl.innerHTML = 'Accès sécurisé : <span style="color:var(--muted)">non installé</span>';
      if (btnEl) btnEl.textContent = "Activer l'accès BDD sécurisé";
      if (rmEl) rmEl.style.display = 'none';
    }
  } catch (e) { statusEl.textContent = 'Accès sécurisé : statut indisponible'; }
}

export async function installIonosBridge(domain) {
  const btn = document.getElementById(`ionos-bridge-btn-${domain}`);
  const label = btn ? btn.textContent : '';
  if (btn) btn.textContent = 'Installation…';
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/bridge`, {
      method: 'POST', headers: _ionosAuthHeaders(false),
    });
    const d = await r.json();
    if (r.ok && d.ok) { alert("Accès BDD sécurisé installé."); }
    else { alert(`Installation impossible.\n\n${d.error || d.detail || 'échec'}`); }  // message neutre
  } catch (e) { alert('Erreur réseau.'); }
  finally { if (btn) btn.textContent = label; refreshIonosBridgeStatus(domain); }
}

export async function removeIonosBridge(domain) {
  if (!confirm(`Supprimer le bridge BDD de ${domain} ? (la config BDD et le site SFTP restent intacts)`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/bridge`, {
      method: 'DELETE', headers: _ionosAuthHeaders(false),
    });
    const d = await r.json();
    if (!r.ok) alert(`Suppression impossible.\n\n${d.detail || 'échec'}`);
  } catch (e) { alert('Erreur réseau.'); }
  finally { refreshIonosBridgeStatus(domain); }
}

/* ============================================================
   IONOS — Explorateur BDD read-only (Étape 3E)
   Lecture seule : tables / schéma / aperçu borné. Aucune écriture.
   Tables sensibles : avertissement + confirmation avant aperçu.
   ============================================================ */

const _IONOS_SENSITIVE = new Set(['users', 'sessions', 'verification_codes']);

function _ionosDbModalShell(title, inner) {
  document.querySelectorAll('#ionos-dbx-modal').forEach(n => n.remove());
  const m = document.createElement('div');
  m.id = 'ionos-dbx-modal';
  m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  m.innerHTML = `<div class="card" style="width:min(720px,94vw);max-height:88vh;overflow:auto;margin:0">
    <div class="card-title"><i data-lucide="table"></i> ${esc(title)}
      <button class="btn" style="margin-left:auto;font-size:11px;padding:3px 8px" onclick="closeIonosDbExplorer()">Fermer</button>
    </div>
    <div class="card-content" id="ionos-dbx-body">${inner}</div>
  </div>`;
  m.addEventListener('click', e => { if (e.target === m) closeIonosDbExplorer(); });
  document.body.appendChild(m);
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export function closeIonosDbExplorer() {
  document.querySelectorAll('#ionos-dbx-modal').forEach(n => n.remove());
}

// Cache de session de l'explorateur (tables + config écriture).
let _ionosDbx = { domain: null, tables: [], write: { enabled: false, tables: [] } };

export async function openIonosDbExplorer(domain) {
  _ionosDbModalShell(`Explorateur BDD — ${domain}`, '<div style="color:var(--muted);font-size:12px">Chargement des tables…</div>');
  try {
    const [rt, rw, rs, rd] = await Promise.all([
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/write-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/delete-config`, { headers: _ionosAuthHeaders(false) }),
    ]);
    const d = await rt.json();
    const body = document.getElementById('ionos-dbx-body');
    if (!rt.ok || !d.ok) {
      body.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(d.message || 'Lecture impossible.')}</div>`;
      return;
    }
    const tables = d.tables || [];
    let wc = { enabled: false, tables: [] };
    try { if (rw.ok) wc = await rw.json(); } catch (e) {}
    let sc = { enabled: false };
    try { if (rs.ok) sc = await rs.json(); } catch (e) {}
    let dc = { enabled: false, tables: [] };
    try { if (rd.ok) dc = await rd.json(); } catch (e) {}
    _ionosDbx = { domain, tables, write: wc, sandbox: sc, delete: dc };
    if (!tables.length && !sc.enabled) { body.innerHTML = '<div style="color:var(--muted);font-size:12px">Aucune table.</div>'; return; }

    const writeBadge = wc.enabled
      ? `<span style="color:var(--warn,#e0a030)">écriture ACTIVE</span> (${(wc.tables||[]).length} table(s))`
      : '<span style="color:var(--muted)">écriture désactivée</span>';
    const sbBadge = sc.enabled
      ? `<button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosSandboxCreate('${esc(domain)}')"><i data-lucide="plus-square" style="width:12px;height:12px"></i> Créer table sandbox</button>`
      : '';
    let html = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-size:11px;gap:8px;flex-wrap:wrap">
      <span><i data-lucide="pencil" style="width:12px;height:12px"></i> ${writeBadge}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">
        ${sbBadge}
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosSandbox('${esc(domain)}', ${sc.enabled ? 'false' : 'true'})">${sc.enabled ? 'Désactiver' : 'Activer'} sandbox</button>
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosWriteConfig('${esc(domain)}')">Configurer l'écriture</button>
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDeleteConfig('${esc(domain)}')">Configurer la suppression</button>
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosSnapshots('${esc(domain)}')"><i data-lucide="history" style="width:12px;height:12px"></i> Snapshots</button>
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosPendingActions('${esc(domain)}')"><i data-lucide="bot" style="width:12px;height:12px"></i> Actions IA</button>
      </span>
    </div><div style="display:grid;gap:6px">`;
    const allow = new Set(wc.tables || []);
    const delAllow = new Set(dc.tables || []);
    for (const t of tables) {
      const sens = _IONOS_SENSITIVE.has(t.toLowerCase());
      const warn = sens ? '<span style="color:var(--warn,#e0a030);font-size:10px"> ⚠️ sensible</span>' : '';
      const canWrite = wc.enabled && allow.has(t);
      const canDelete = dc.enabled && delAllow.has(t);
      const writeBtns = (canWrite ? `
          <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDbWriteModal('${esc(domain)}','${esc(t)}','insert')"><i data-lucide="plus" style="width:11px;height:11px"></i> Ajouter</button>
          <button class="btn" style="font-size:10px;padding:3px 8px" onclick="openIonosDbWriteModal('${esc(domain)}','${esc(t)}','update')"><i data-lucide="pencil" style="width:11px;height:11px"></i> Modifier</button>` : '')
        + (canDelete ? `
          <button class="btn" style="font-size:10px;padding:3px 8px;color:var(--danger)" onclick="openIonosDbDeleteModal('${esc(domain)}','${esc(t)}')"><i data-lucide="trash-2" style="width:11px;height:11px"></i> Supprimer</button>` : '');
      html += `<div style="display:flex;align-items:center;justify-content:space-between;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
        <span style="font-family:var(--mono);font-size:12px">${esc(t)}${warn}</span>
        <span style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn" style="font-size:10px;padding:3px 8px" onclick="ionosDbSchema('${esc(domain)}','${esc(t)}')">Schéma</button>
          <button class="btn" style="font-size:10px;padding:3px 8px" onclick="ionosDbPreview('${esc(domain)}','${esc(t)}',${sens})">Aperçu</button>${writeBtns}
        </span></div>`;
    }
    html += '</div><div id="ionos-dbx-detail" style="margin-top:12px"></div>';
    body.innerHTML = html;
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) {
    const body = document.getElementById('ionos-dbx-body');
    if (body) body.innerHTML = `<div style="color:var(--danger);font-size:12px">Erreur réseau.</div>`;
  }
}

// ── Config écriture (toggle + allowlist) ──
export function openIonosWriteConfig(domain) {
  const tables = _ionosDbx.tables || [];
  const wc = _ionosDbx.write || { enabled: false, tables: [] };
  const allow = new Set(wc.tables || []);
  let rows = tables.map(t => {
    const sens = _IONOS_SENSITIVE.has(t.toLowerCase());
    return `<label style="display:flex;align-items:center;gap:6px;font-size:12px;font-family:var(--mono)">
      <input type="checkbox" class="ionos-wt" value="${esc(t)}" ${allow.has(t) ? 'checked' : ''}> ${esc(t)}${sens ? ' <span style="color:var(--warn,#e0a030);font-size:10px">⚠️ sensible</span>' : ''}</label>`;
  }).join('');
  const inner = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">L'écriture (INSERT/UPDATE) est désactivée par défaut. Active-la et coche uniquement les tables autorisées. DELETE n'est pas disponible.</div>
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:10px">
      <input type="checkbox" id="ionos-write-enabled" ${wc.enabled ? 'checked' : ''}> Activer l'écriture pour ce site</label>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Tables autorisées :</div>
    <div style="display:grid;gap:4px;max-height:240px;overflow:auto">${rows}</div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Annuler</button>
      <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="saveIonosWriteConfig('${esc(domain)}')">Enregistrer</button>
    </div>`;
  _ionosDbModalShell(`Écriture BDD — ${domain}`, inner);
}

export async function saveIonosWriteConfig(domain) {
  const enabled = !!document.getElementById('ionos-write-enabled')?.checked;
  const tables = Array.from(document.querySelectorAll('.ionos-wt')).filter(c => c.checked).map(c => c.value);
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/write-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled, tables }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosDbExplorer(domain);
}

// ── Modale write INSERT/UPDATE (confirm obligatoire) ──
export async function openIonosDbWriteModal(domain, table, op) {
  _ionosDbModalShell(`${op === 'insert' ? 'Ajouter une ligne' : 'Modifier des lignes'} — ${table}`, '<div style="color:var(--muted);font-size:12px">Chargement du schéma…</div>');
  let cols = [];
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/schema`, { headers: _ionosAuthHeaders(false) });
    const d = await r.json();
    if (r.ok && d.ok) cols = (d.columns || []).map(c => c.field);
  } catch (e) {}
  const body = document.getElementById('ionos-dbx-body');
  if (!cols.length) { body.innerHTML = '<div style="color:var(--danger);font-size:12px">Schéma indisponible.</div>'; return; }
  const valInputs = cols.map(c => `<div><label style="font-size:11px;color:var(--muted)">${esc(c)}</label><input class="input ionos-wv" data-col="${esc(c)}" style="width:100%;height:30px;font-size:12px;padding:0 8px;box-sizing:border-box" placeholder="(laisser vide = ignorer)"></div>`).join('');
  const whereBlock = op === 'update' ? `
    <div style="font-size:11px;color:var(--muted);margin:10px 0 4px">WHERE (égalité simple — obligatoire) :</div>
    <div style="display:flex;gap:8px"><select id="ionos-ww-col" class="input" style="flex:1;height:30px;font-size:12px">${cols.map(c => `<option>${esc(c)}</option>`).join('')}</select>
      <input id="ionos-ww-val" class="input" style="flex:2;height:30px;font-size:12px;padding:0 8px;box-sizing:border-box" placeholder="valeur"></div>` : '';
  body.innerHTML = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">${op === 'insert' ? 'Renseigne les colonnes à insérer.' : 'Renseigne les colonnes à modifier + le filtre WHERE.'} Confirmation obligatoire avant exécution.</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">${valInputs}</div>
    ${whereBlock}
    <div id="ionos-write-msg" style="display:none;margin-top:10px;font-size:12px"></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Annuler</button>
      <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="submitIonosDbWrite('${esc(domain)}','${esc(table)}','${esc(op)}')"><i data-lucide="save"></i> Vérifier & exécuter</button>
    </div>`;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function submitIonosDbWrite(domain, table, op) {
  const msg = document.getElementById('ionos-write-msg');
  const values = {};
  document.querySelectorAll('.ionos-wv').forEach(i => { const v = i.value; if (v !== '') values[i.dataset.col] = v; });
  if (Object.keys(values).length === 0) { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Renseigne au moins une colonne.'; } return; }
  let where = null;
  if (op === 'update') {
    const wc = document.getElementById('ionos-ww-col')?.value;
    const wv = document.getElementById('ionos-ww-val')?.value;
    if (!wc || wv === undefined || wv === '') { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'WHERE obligatoire pour un UPDATE.'; } return; }
    where = { [wc]: wv };
  }
  // Récap + confirmation explicite.
  const recap = `${op.toUpperCase()} sur "${table}"\nColonnes: ${Object.keys(values).join(', ')}` + (where ? `\nWHERE: ${Object.keys(where)[0]} = ${Object.values(where)[0]}` : '');
  if (!confirm(`Confirmer cette écriture ?\n\n${recap}\n\n(transaction + rollback automatique, max 50 lignes)`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/write`, {
      method: 'POST', headers: _ionosAuthHeaders(true),
      body: JSON.stringify({ op, values, where, confirm: true }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      const warn = d.warning === 'no_rows_modified' ? ' (aucune ligne modifiée)' : '';
      alert(`Écriture OK — ${d.affected} ligne(s) affectée(s)${warn}.`);
      openIonosDbExplorer(domain);
    } else {
      if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = d.message || d.detail || 'Écriture refusée.'; }
    }
  } catch (e) { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Erreur réseau.'; } }
}

// ── Sandbox : activer/désactiver + créer une table sandbox (4.2) ──
const _IONOS_SANDBOX_PREFIX = 'lumena_sandbox_';
const _IONOS_SANDBOX_TYPES = ['INT', 'BIGINT', 'VARCHAR', 'TEXT', 'DATETIME', 'DATE', 'BOOLEAN'];

export async function toggleIonosSandbox(domain, enable) {
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosDbExplorer(domain);
}

function _ionosSandboxColRow() {
  return `<div class="ionos-sb-col" style="display:flex;gap:6px;margin-bottom:4px">
    <input class="input sb-name" placeholder="colonne" style="flex:2;height:28px;font-size:11px;padding:0 6px;box-sizing:border-box">
    <select class="input sb-type" style="flex:1;height:28px;font-size:11px">${_IONOS_SANDBOX_TYPES.map(t => `<option>${t}</option>`).join('')}</select>
    <input class="input sb-len" placeholder="len" style="width:54px;height:28px;font-size:11px;padding:0 6px;box-sizing:border-box">
    <label style="font-size:10px;display:flex;align-items:center;gap:3px"><input type="checkbox" class="sb-null" checked> null</label>
  </div>`;
}

export function openIonosSandboxCreate(domain) {
  const inner = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Crée une table <b>sandbox</b> (préfixe imposé). Types : ${_IONOS_SANDBOX_TYPES.join(', ')}. Une colonne <code>id</code> (PK auto) est ajoutée. Max 30 colonnes. Aucun DROP/ALTER.</div>
    <div style="display:flex;align-items:center;gap:4px;margin-bottom:10px">
      <span style="font-family:var(--mono);font-size:12px;color:var(--muted)">${_IONOS_SANDBOX_PREFIX}</span>
      <input id="ionos-sb-suffix" class="input" placeholder="ma_table" style="flex:1;height:30px;font-size:12px;padding:0 8px;box-sizing:border-box">
    </div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Colonnes (VARCHAR exige une longueur 1..255) :</div>
    <div id="ionos-sb-cols">${_ionosSandboxColRow()}</div>
    <button class="btn" style="font-size:10px;padding:3px 8px;margin-top:4px" onclick="document.getElementById('ionos-sb-cols').insertAdjacentHTML('beforeend', window.__ionosSbRow())">+ colonne</button>
    <div id="ionos-sb-msg" style="display:none;margin-top:10px;font-size:12px"></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Annuler</button>
      <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="submitIonosSandboxCreate('${esc(domain)}')"><i data-lucide="plus-square"></i> Créer</button>
    </div>`;
  _ionosDbModalShell(`Créer une table sandbox — ${domain}`, inner);
  window.__ionosSbRow = _ionosSandboxColRow;  // pour le bouton "+ colonne"
}

export async function submitIonosSandboxCreate(domain) {
  const msg = document.getElementById('ionos-sb-msg');
  const suffix = (document.getElementById('ionos-sb-suffix')?.value || '').trim();
  const name = _IONOS_SANDBOX_PREFIX + suffix;
  if (!suffix || !/^[a-z0-9_]+$/.test(suffix)) {
    if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Suffixe invalide (a-z, 0-9, _).'; } return;
  }
  const columns = [];
  document.querySelectorAll('.ionos-sb-col').forEach(row => {
    const nm = row.querySelector('.sb-name').value.trim();
    if (!nm) return;
    const col = { name: nm, type: row.querySelector('.sb-type').value, nullable: row.querySelector('.sb-null').checked };
    const len = row.querySelector('.sb-len').value.trim();
    if (col.type === 'VARCHAR') col.length = parseInt(len || '0', 10);
    columns.push(col);
  });
  if (!columns.length) { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Ajoute au moins une colonne.'; } return; }
  if (!confirm(`Créer la table "${name}" avec ${columns.length} colonne(s) ?`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-tables`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ name, columns, confirm: true }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      alert(d.created ? `Table "${name}" créée.` : `Table "${name}" déjà existante.`);
      openIonosDbExplorer(domain);
    } else {
      if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = d.message || d.detail || 'Création refusée.'; }
    }
  } catch (e) { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = 'Erreur réseau.'; } }
}

// ── Snapshots chiffrés / rollback (4.3) ──
// Aucune valeur en clair n'est jamais affichée : seules les métadonnées
// (table, noms de colonnes, compteurs, horodatages) sont rendues.
export async function openIonosSnapshots(domain) {
  _ionosDbModalShell(`Snapshots BDD — ${domain}`, '<div style="color:var(--muted);font-size:12px">Chargement…</div>');
  let rc = { enabled: false };
  let snaps = [];
  try {
    const [rr, rs] = await Promise.all([
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/restore-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/snapshots`, { headers: _ionosAuthHeaders(false) }),
    ]);
    try { if (rr.ok) rc = await rr.json(); } catch (e) {}
    const ds = await rs.json();
    if (rs.ok && ds.ok) snaps = ds.snapshots || [];
  } catch (e) {
    const body = document.getElementById('ionos-dbx-body');
    if (body) body.innerHTML = '<div style="color:var(--danger);font-size:12px">Erreur réseau.</div>';
    return;
  }
  const rbadge = rc.enabled
    ? '<span style="color:var(--warn,#e0a030)">restauration ACTIVE</span>'
    : '<span style="color:var(--muted)">restauration désactivée</span>';
  let rows = snaps.map(s => `
    <div style="display:flex;align-items:center;justify-content:space-between;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
      <span style="font-size:11px">
        <span style="font-family:var(--mono)">${esc(s.table || '?')}</span>
        · ${s.row_count || 0} ligne(s) · ${(s.columns || []).length} col.
        <span style="color:var(--muted)"> · ${esc(s.created_at || '')} · exp. ${esc(s.expires_at || '')}</span>
      </span>
      <span style="display:flex;gap:6px">
        ${rc.enabled ? `<button class="btn" style="font-size:10px;padding:3px 8px" onclick="restoreIonosSnapshot('${esc(domain)}','${esc(s.id)}')"><i data-lucide="rotate-ccw" style="width:11px;height:11px"></i> Restaurer</button>` : ''}
        <button class="btn" style="font-size:10px;padding:3px 8px" onclick="deleteIonosSnapshot('${esc(domain)}','${esc(s.id)}')"><i data-lucide="trash-2" style="width:11px;height:11px"></i></button>
      </span></div>`).join('');
  if (!snaps.length) rows = '<div style="color:var(--muted);font-size:12px">Aucun snapshot.</div>';
  const inner = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Un snapshot (image-avant chiffrée) est capturé automatiquement avant chaque UPDATE. La restauration ré-applique les valeurs via les garde-fous d'écriture (transaction, confirmation). Aucune valeur n'est jamais affichée ici.</div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-size:11px">
      <span><i data-lucide="shield" style="width:12px;height:12px"></i> ${rbadge}</span>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosRestore('${esc(domain)}', ${rc.enabled ? 'false' : 'true'})">${rc.enabled ? 'Désactiver' : 'Activer'} la restauration</button>
    </div>
    <div style="display:grid;gap:6px;max-height:340px;overflow:auto">${rows}</div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Retour</button>
    </div>`;
  const body = document.getElementById('ionos-dbx-body');
  if (body) body.innerHTML = inner;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function toggleIonosRestore(domain, enable) {
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/restore-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosSnapshots(domain);
}

export async function restoreIonosSnapshot(domain, snapshotId) {
  if (!confirm('Restaurer ce snapshot ?\n\nLes valeurs-avant seront ré-appliquées via les garde-fous d\'écriture (transaction + rollback). Action irréversible.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/snapshots/${encodeURIComponent(snapshotId)}/restore`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ confirm: true }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      alert(`Restauration OK — ${d.restored} ligne(s)${d.errors ? `, ${d.errors} erreur(s)` : ''}.`);
    } else {
      alert(d.message || d.detail || 'Restauration refusée.');
    }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosSnapshots(domain);
}

export async function deleteIonosSnapshot(domain, snapshotId) {
  if (!confirm('Supprimer ce snapshot ? (le fichier chiffré sera effacé)')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/snapshots/${encodeURIComponent(snapshotId)}`, {
      method: 'DELETE', headers: _ionosAuthHeaders(true),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosSnapshots(domain);
}

// ── DELETE contrôlé (4.4) : config + modale suppression ──
// Flag + allowlist DÉDIÉS (séparés du write). WHERE obligatoire ;
// double confirmation : retaper le nom exact de la table.
export function openIonosDeleteConfig(domain) {
  const tables = _ionosDbx.tables || [];
  const dc = _ionosDbx.delete || { enabled: false, tables: [] };
  const allow = new Set(dc.tables || []);
  const rows = tables.map(t => {
    const sens = _IONOS_SENSITIVE.has(t.toLowerCase());
    return `<label style="display:flex;align-items:center;gap:6px;font-size:12px;font-family:var(--mono)">
      <input type="checkbox" class="ionos-dt" value="${esc(t)}" ${allow.has(t) ? 'checked' : ''}> ${esc(t)}${sens ? ' <span style="color:var(--warn,#e0a030);font-size:10px">⚠️ sensible</span>' : ''}</label>`;
  }).join('');
  const inner = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">La suppression (DELETE) est désactivée par défaut et possède une <b>allowlist séparée</b> de l'écriture. WHERE obligatoire (pas de suppression totale). Un snapshot chiffré est capturé avant chaque suppression ; la ligne reste restaurable.</div>
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:10px">
      <input type="checkbox" id="ionos-delete-enabled" ${dc.enabled ? 'checked' : ''}> Activer la suppression pour ce site</label>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Tables supprimables :</div>
    <div style="display:grid;gap:4px;max-height:240px;overflow:auto">${rows}</div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Annuler</button>
      <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="saveIonosDeleteConfig('${esc(domain)}')">Enregistrer</button>
    </div>`;
  _ionosDbModalShell(`Suppression BDD — ${domain}`, inner);
}

export async function saveIonosDeleteConfig(domain) {
  const enabled = !!document.getElementById('ionos-delete-enabled')?.checked;
  const tables = Array.from(document.querySelectorAll('.ionos-dt')).filter(c => c.checked).map(c => c.value);
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/delete-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled, tables }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosDbExplorer(domain);
}

export async function openIonosDbDeleteModal(domain, table) {
  _ionosDbModalShell(`Supprimer des lignes — ${table}`, '<div style="color:var(--muted);font-size:12px">Chargement du schéma…</div>');
  let cols = [];
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/schema`, { headers: _ionosAuthHeaders(false) });
    const d = await r.json();
    if (r.ok && d.ok) cols = (d.columns || []).map(c => c.field);
  } catch (e) {}
  const body = document.getElementById('ionos-dbx-body');
  if (!cols.length) { body.innerHTML = '<div style="color:var(--danger);font-size:12px">Schéma indisponible.</div>'; return; }
  body.innerHTML = `
    <div style="font-size:12px;color:var(--danger);margin-bottom:8px">⚠️ Suppression définitive de lignes. WHERE obligatoire (pas de suppression totale). Un snapshot chiffré est capturé avant ; restauration possible ensuite.</div>
    <div style="font-size:11px;color:var(--muted);margin:0 0 4px">WHERE (égalité simple — obligatoire) :</div>
    <div style="display:flex;gap:8px;margin-bottom:10px"><select id="ionos-dw-col" class="input" style="flex:1;height:30px;font-size:12px">${cols.map(c => `<option>${esc(c)}</option>`).join('')}</select>
      <input id="ionos-dw-val" class="input" style="flex:2;height:30px;font-size:12px;padding:0 8px;box-sizing:border-box" placeholder="valeur"></div>
    <div style="font-size:11px;color:var(--muted);margin:0 0 4px">Pour confirmer, retape le nom exact de la table :</div>
    <input id="ionos-dw-confirm" class="input" style="width:100%;height:30px;font-size:12px;padding:0 8px;box-sizing:border-box;font-family:var(--mono)" placeholder="${esc(table)}">
    <div id="ionos-delete-msg" style="display:none;margin-top:10px;font-size:12px"></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Annuler</button>
      <button class="btn" style="font-size:12px;padding:6px 18px;color:var(--danger)" onclick="submitIonosDbDelete('${esc(domain)}','${esc(table)}')"><i data-lucide="trash-2"></i> Vérifier & supprimer</button>
    </div>`;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function submitIonosDbDelete(domain, table) {
  const msg = document.getElementById('ionos-delete-msg');
  const showErr = (m) => { if (msg) { msg.style.display = 'block'; msg.style.color = 'var(--danger)'; msg.textContent = m; } };
  const wc = document.getElementById('ionos-dw-col')?.value;
  const wv = document.getElementById('ionos-dw-val')?.value;
  const ct = (document.getElementById('ionos-dw-confirm')?.value || '').trim();
  if (!wc || wv === undefined || wv === '') { showErr('WHERE obligatoire.'); return; }
  if (ct !== table) { showErr('Le nom de table retapé ne correspond pas.'); return; }
  const where = { [wc]: wv };
  if (!confirm(`Supprimer définitivement les lignes de "${table}" où ${wc} = ${wv} ?\n\n(snapshot capturé avant, max 25 lignes, transaction + rollback)`)) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/delete`, {
      method: 'POST', headers: _ionosAuthHeaders(true),
      body: JSON.stringify({ where, confirm: true, confirm_table: ct }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      const warn = d.warning === 'no_rows_deleted' ? ' (aucune ligne supprimée)' : '';
      alert(`Suppression OK — ${d.affected} ligne(s)${warn}. Snapshot: ${d.snapshot_id ? 'oui' : 'non'}.`);
      openIonosDbExplorer(domain);
    } else {
      showErr(d.message || d.detail || 'Suppression refusée.');
    }
  } catch (e) { showErr('Erreur réseau.'); }
}

// ── Propositions ReAct INSERT/UPDATE (4.5A) : revue + approbation humaine ──
// L'agent PROPOSE, l'humain EXÉCUTE. Aucune valeur n'est affichée (clés seulement).
export async function openIonosPendingActions(domain) {
  _ionosDbModalShell(`Actions IA en attente — ${domain}`, '<div style="color:var(--muted);font-size:12px">Chargement…</div>');
  let rc = { enabled: false };
  let rdc = { enabled: false };
  let sdc = { enabled: false };
  let scc = { enabled: false };
  let actions = [];
  try {
    const [rr, rrd, rsd, rsc, ra] = await Promise.all([
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/react-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/react-delete-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-drop-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-clear-config`, { headers: _ionosAuthHeaders(false) }),
      fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/pending-actions`, { headers: _ionosAuthHeaders(false) }),
    ]);
    try { if (rr.ok) rc = await rr.json(); } catch (e) {}
    try { if (rrd.ok) rdc = await rrd.json(); } catch (e) {}
    try { if (rsd.ok) sdc = await rsd.json(); } catch (e) {}
    try { if (rsc.ok) scc = await rsc.json(); } catch (e) {}
    const da = await ra.json();
    if (ra.ok && da.ok) actions = da.actions || [];
  } catch (e) {
    const b = document.getElementById('ionos-dbx-body');
    if (b) b.innerHTML = '<div style="color:var(--danger);font-size:12px">Erreur réseau.</div>';
    return;
  }
  const rbadge = rc.enabled
    ? '<span style="color:var(--warn,#e0a030)">propositions ReAct ACTIVES</span>'
    : '<span style="color:var(--muted)">propositions ReAct désactivées</span>';
  const rdbadge = rdc.enabled
    ? '<span style="color:var(--danger)">DELETE IA ACTIF</span>'
    : '<span style="color:var(--muted)">DELETE IA désactivé</span>';
  const sdbadge = sdc.enabled
    ? '<span style="color:var(--danger)">DROP sandbox IA ACTIF</span>'
    : '<span style="color:var(--muted)">DROP sandbox IA désactivé</span>';
  const scbadge = scc.enabled
    ? '<span style="color:var(--warn,#e0a030)">VIDAGE sandbox IA ACTIF</span>'
    : '<span style="color:var(--muted)">VIDAGE sandbox IA désactivé</span>';
  let rows = actions.map(a => {
    const est = (a.estimated_count !== null && a.estimated_count !== undefined) ? ` · ~${a.estimated_count} ligne(s)` : '';
    const wk = (a.where_keys || []).join(', ') || '—';
    const vk = (a.value_keys || []).join(', ');
    return `<div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px">
      <div style="font-size:11px"><b>${esc((a.op || '').toUpperCase())}</b> sur <span style="font-family:var(--mono)">${esc(a.table || '?')}</span>${est}
        <span style="color:var(--muted)"> · ${esc(a.created_at || '')}</span></div>
      <div style="font-size:11px;color:var(--muted);margin-top:2px">Colonnes : ${esc(vk)} · WHERE : ${esc(wk)}</div>
      <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px">
        <button class="btn primary" style="font-size:10px;padding:3px 10px" onclick="approveIonosAction('${esc(domain)}','${esc(a.id)}','${esc(a.op || '')}','${esc(a.table || '')}')"><i data-lucide="check" style="width:11px;height:11px"></i> Approuver & exécuter</button>
        <button class="btn" style="font-size:10px;padding:3px 10px" onclick="rejectIonosAction('${esc(domain)}','${esc(a.id)}')"><i data-lucide="x" style="width:11px;height:11px"></i> Rejeter</button>
      </div></div>`;
  }).join('');
  if (!actions.length) rows = '<div style="color:var(--muted);font-size:12px">Aucune action en attente.</div>';
  const inner = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px">L'assistant peut <b>proposer</b> des écritures INSERT/UPDATE, mais ne peut jamais les exécuter seul. Vous validez ou rejetez ici. L'exécution passe par les garde-fous d'écriture (allowlist, transaction, snapshot). Aucune valeur n'est affichée — seulement les colonnes ciblées.</div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;font-size:11px">
      <span><i data-lucide="bot" style="width:12px;height:12px"></i> ${rbadge} (INSERT/UPDATE)</span>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosReact('${esc(domain)}', ${rc.enabled ? 'false' : 'true'})">${rc.enabled ? 'Désactiver' : 'Activer'} les propositions IA</button>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-size:11px">
      <span><i data-lucide="trash-2" style="width:12px;height:12px"></i> ${rdbadge}</span>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosReactDelete('${esc(domain)}', ${rdc.enabled ? 'false' : 'true'})">${rdc.enabled ? 'Désactiver' : 'Activer'} les propositions DELETE IA</button>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;font-size:11px">
      <span><i data-lucide="trash" style="width:12px;height:12px"></i> ${sdbadge}</span>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosSandboxDrop('${esc(domain)}', ${sdc.enabled ? 'false' : 'true'})">${sdc.enabled ? 'Désactiver' : 'Activer'} le DROP sandbox IA</button>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-size:11px">
      <span><i data-lucide="eraser" style="width:12px;height:12px"></i> ${scbadge}</span>
      <button class="btn" style="font-size:10px;padding:3px 8px" onclick="toggleIonosSandboxClear('${esc(domain)}', ${scc.enabled ? 'false' : 'true'})">${scc.enabled ? 'Désactiver' : 'Activer'} le VIDAGE sandbox IA</button>
    </div>
    <div style="font-size:10px;color:var(--muted);margin-bottom:8px">DELETE et DROP exigent aussi leur kill-switch global (Configuration → IONOS : <code>LUMENA_IONOS_REACT_DELETE_ENABLED=1</code> / <code>LUMENA_IONOS_SANDBOX_DROP_ENABLED=1</code>). Le DROP ne vise que les tables <code>lumena_sandbox_*</code> VIDES et exige de retaper le nom.</div>
    <div style="display:grid;gap:6px;max-height:340px;overflow:auto">${rows}</div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="openIonosDbExplorer('${esc(domain)}')">Retour</button>
    </div>`;
  const b = document.getElementById('ionos-dbx-body');
  if (b) b.innerHTML = inner;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

export async function toggleIonosReact(domain, enable) {
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/react-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

export async function toggleIonosReactDelete(domain, enable) {
  if (enable && !confirm('Activer les propositions DELETE par l\'IA pour ce site ?\n\nL\'assistant pourra PROPOSER des suppressions (jamais les exécuter). Nécessite aussi le kill-switch global et la suppression activée pour la table.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/react-delete-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

export async function toggleIonosSandboxDrop(domain, enable) {
  if (enable && !confirm('Activer le DROP de tables sandbox par l\'IA pour ce site ?\n\nL\'assistant pourra PROPOSER la suppression de tables lumena_sandbox_* VIDES (jamais les exécuter). Nécessite aussi le kill-switch global.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-drop-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

export async function toggleIonosSandboxClear(domain, enable) {
  if (enable && !confirm('Activer le VIDAGE de tables sandbox par l\'IA pour ce site ?\n\nL\'assistant pourra PROPOSER de vider des tables lumena_sandbox_* (jamais les exécuter). Un snapshot est capturé avant chaque vidage (restaurable).')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/sandbox-clear-config`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ enabled: !!enable }),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

export async function approveIonosAction(domain, proposalId, op, table) {
  // DROP/CLEAR sandbox : double confirmation — retaper le nom exact de la table.
  if (op === 'drop_sandbox' || op === 'clear_sandbox') {
    const what = op === 'drop_sandbox'
      ? `Suppression DÉFINITIVE de la table sandbox "${table}" (doit être vide).`
      : `VIDAGE (toutes les lignes) de la table sandbox "${table}" — un snapshot sera capturé avant.`;
    const typed = prompt(`${what}\n\nRetape le nom EXACT de la table pour confirmer :`);
    if (typed === null) return;
    if (typed.trim() !== table) { alert('Le nom retapé ne correspond pas — annulé.'); return; }
  } else if (!confirm('Approuver et exécuter cette proposition ?\n\nL\'écriture passera par les garde-fous (allowlist, transaction, snapshot pour UPDATE).')) {
    return;
  }
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/pending-actions/${encodeURIComponent(proposalId)}/approve`, {
      method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ confirm: true }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      if (d.op === 'drop_sandbox' || op === 'drop_sandbox') {
        alert(`Table sandbox \`${d.table || table}\` supprimée.`);
      } else if (d.op === 'clear_sandbox' || op === 'clear_sandbox') {
        alert(`Table sandbox \`${d.table || table}\` vidée — ${d.affected ?? 0} ligne(s) supprimée(s) (snapshot capturé).`);
      } else {
        const warn = d.warning === 'no_rows_modified' ? ' (aucune ligne modifiée)' : '';
        alert(`Exécution OK — ${d.affected} ligne(s)${warn}.`);
      }
    } else {
      alert(d.message || d.detail || 'Exécution refusée.');
    }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

export async function rejectIonosAction(domain, proposalId) {
  if (!confirm('Rejeter cette proposition ? Elle ne sera pas exécutée.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/pending-actions/${encodeURIComponent(proposalId)}/reject`, {
      method: 'POST', headers: _ionosAuthHeaders(true),
    });
    if (!r.ok) { const d = await r.json(); alert(`Erreur: ${d.detail || 'échec'}`); return; }
  } catch (e) { alert('Erreur réseau.'); return; }
  openIonosPendingActions(domain);
}

async function _ionosDbDetail(domain, table, url, opts, render) {
  const slot = document.getElementById('ionos-dbx-detail');
  if (slot) slot.innerHTML = '<div style="color:var(--muted);font-size:12px">Chargement…</div>';
  try {
    const r = await fetch(url, opts);
    const d = await r.json();
    if (!r.ok || !d.ok) { if (slot) slot.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(d.message || 'Indisponible.')}</div>`; return; }
    if (slot) slot.innerHTML = render(d);
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) {
    if (slot) slot.innerHTML = '<div style="color:var(--danger);font-size:12px">Erreur réseau.</div>';
  }
}

export async function ionosDbSchema(domain, table) {
  await _ionosDbDetail(domain, table,
    `${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/schema`,
    { headers: _ionosAuthHeaders(false) },
    (d) => {
      let h = `<div style="font-size:12px;font-weight:600;margin-bottom:6px">Schéma de ${esc(table)}</div>`;
      h += '<table style="width:100%;border-collapse:collapse;font-size:11px"><tr style="color:var(--muted)"><th style="text-align:left;padding:3px">Colonne</th><th style="text-align:left;padding:3px">Type</th><th style="text-align:left;padding:3px">Null</th><th style="text-align:left;padding:3px">Clé</th></tr>';
      for (const c of (d.columns || [])) {
        h += `<tr><td style="padding:3px;font-family:var(--mono)">${esc(c.field)}</td><td style="padding:3px">${esc(c.type)}</td><td style="padding:3px">${esc(c.null)}</td><td style="padding:3px">${esc(c.key)}</td></tr>`;
      }
      return h + '</table>';
    });
}

export async function ionosDbPreview(domain, table, sensitive) {
  if (sensitive && !confirm(`La table « ${table} » est marquée sensible. Afficher un aperçu (lecture seule, limité) ?`)) return;
  await _ionosDbDetail(domain, table,
    `${API_BASE}/api/ionos/sites/${encodeURIComponent(domain)}/database/tables/${encodeURIComponent(table)}/preview`,
    { method: 'POST', headers: _ionosAuthHeaders(true), body: JSON.stringify({ limit: 20 }) },
    (d) => {
      const cols = d.columns || [], rows = d.rows || [];
      let h = `<div style="font-size:12px;font-weight:600;margin-bottom:6px">Aperçu ${esc(table)} — ${d.count || 0} ligne(s)${d.truncated ? ' (tronqué)' : ''}</div>`;
      if (!cols.length) return h + '<div style="color:var(--muted);font-size:12px">Aucune colonne.</div>';
      h += '<div style="overflow:auto;max-height:300px"><table style="border-collapse:collapse;font-size:11px"><tr style="color:var(--muted)">';
      for (const c of cols) h += `<th style="text-align:left;padding:3px 8px;border-bottom:1px solid var(--border)">${esc(c)}</th>`;
      h += '</tr>';
      for (const row of rows) {
        h += '<tr>';
        for (const v of row) h += `<td style="padding:3px 8px;border-bottom:1px solid var(--border);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${v === null ? '<span style="color:var(--muted)">NULL</span>' : esc(String(v))}</td>`;
        h += '</tr>';
      }
      return h + '</table></div>';
    });
}

/* ============================================================
   INSTANCES & RÉSEAU — Phase 8.8 : Vue simplifiée
   ============================================================ */

function _netStatusInfo(diag, own) {
  if (!diag && !own) return {label:'Chargement…', color:'var(--muted)', dot:'○', action:null};
  if (diag) {
    if (diag.ok) {
      const ip = diag.lan_ips?.[0] ? `${diag.lan_ips[0]}:${diag.port}` : `port ${diag.port}`;
      return {label:`Réseau OK · ${ip}`, color:'var(--ok)', dot:'●', action:null};
    }
    const errs = (diag.issues||[]).filter(i=>i.severity==='error');
    const warns = (diag.issues||[]).filter(i=>i.severity==='warning');
    if (errs.length) {
      const msg = errs[0]?.message?.slice(0,60)||'Erreur réseau';
      return {label:`Non accessible · ${msg}`, color:'var(--danger)', dot:'●',
              action:{label:'Diagnostiquer', fn:'loadNetworkDiagnostic()'}};
    }
    if (warns.length) {
      return {label:'Pare-feu requis · Port potentiellement bloqué', color:'var(--warning,#f59e0b)', dot:'◉',
              action:{label:'Réparer', fn:'loadFirewallCommand();toggleNetworkAdvanced()'}};
    }
  }
  if (own) {
    return {label:`Instance active · port ${own.port}`, color:'var(--ok)', dot:'●', action:null};
  }
  return {label:'Statut inconnu', color:'var(--muted)', dot:'○', action:null};
}

function _peerStatusInfo(p) {
  if (p.trust === 'trusted' && p.has_peer_token)
    return {label:'Connecté', color:'var(--ok)', dot:'●'};
  if (p.trust === 'trusted' && !p.has_peer_token)
    return {label:'Token requis', color:'var(--warning,#f59e0b)', dot:'◉'};
  if (p.trust === 'blocked')
    return {label:'Bloqué', color:'var(--danger)', dot:'●'};
  return {label:'Non connecté', color:'var(--muted)', dot:'○'};
}

function _peerScopesHtml(p) {
  const scopes = Array.isArray(p.allowed_scopes) ? p.allowed_scopes : [];
  if (!scopes.length) {
    return '<span class="pill muted" style="font-size:10px">scope: aucun</span>';
  }
  return scopes.map(s => `<span class="pill" style="font-size:10px">${esc(s)}</span>`).join(' ');
}

function _peerActionsHtml(p) {
  const iid = esc(p.instance_id);
  const host = esc(p.host||'');
  const port = p.port||8080;
  const scopes = Array.isArray(p.allowed_scopes) ? p.allowed_scopes : [];
  const canChat = scopes.includes('chat');
  if (p.trust === 'blocked') {
    return `<button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple(${_jsArg(p.instance_id)})">Supprimer</button>`;
  }
  if (p.trust === 'trusted' && p.has_peer_token) {
    const testBtn = canChat
      ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="testDelegation('${iid}','ns-test-${iid}')"><i data-lucide="zap" style="width:10px;height:10px"></i> Tester</button>`
      : '';
    const shareBtn = !scopes.includes('knowledge.share')
      ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="setPeerScope(${_jsArg(p.instance_id)},'knowledge.share',true)">+ savoir</button>`
      : '';
    const taskBtn = !scopes.includes('task.delegate')
      ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="setPeerScope(${_jsArg(p.instance_id)},'task.delegate',true)">+ tâches</button>`
      : '';
    return `${testBtn}
            ${shareBtn}
            ${taskBtn}
            <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="blockPeerSimple(${_jsArg(p.instance_id)})">Bloquer</button>
            <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple(${_jsArg(p.instance_id)})">Supprimer</button>`;
  }
  // trusted sans token, ou unknown → proposer jumelage par code
  return `<button class="btn primary" style="font-size:10px;padding:2px 8px" onclick="showSimplePairingForm('${host}',${port})"><i data-lucide="key-round" style="width:10px;height:10px"></i> Jumeler</button>
          <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="blockPeerSimple(${_jsArg(p.instance_id)})">Bloquer</button>
          <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple(${_jsArg(p.instance_id)})">Supprimer</button>`;
}

function _jsArg(value) {
  return JSON.stringify(String(value || ''));
}

function _showNetworkActionMessage(message, tone = 'muted') {
  const candidates = [
    document.getElementById('net-maintenance-msg'),
    document.getElementById('net-action-msg'),
    document.getElementById('net-team-msg'),
  ].filter(Boolean);
  const el = candidates.find(node => node.offsetParent !== null) || candidates[0];
  if (!el) return;
  el.style.display = 'block';
  el.style.color = `var(--${tone})`;
  el.textContent = message;
}

function _refreshNetworkPanels() {
  const simple = document.getElementById('net-simple-view');
  const advanced = document.getElementById('net-advanced-view');
  const isAdvanced = advanced && advanced.style.display !== 'none';
  const jobs = [loadCollaborationPanel()];
  if (isAdvanced) jobs.push(loadInstancesNetwork());
  else if (simple) jobs.push(loadNetworkSimple());
  return Promise.allSettled(jobs);
}

function _hasPeerScope(peer, scope) {
  const scopes = Array.isArray(peer?.allowed_scopes) ? peer.allowed_scopes : [];
  return scopes.includes(scope);
}

function _taskStatusColor(status) {
  if (['completed'].includes(status)) return 'ok';
  if (['failed','timeout','cancelled','interrupted'].includes(status)) return 'danger';
  if (['queued','running'].includes(status)) return 'accent';
  return 'muted';
}

function _knowledgeVisibilityLabel(k, peersById) {
  if (k.visibility === 'shared_with_peer') {
    const peer = peersById.get(k.shared_with_peer_id);
    return `partagé avec ${peer ? (peer.instance_name || peer.instance_id) : (k.shared_with_peer_id || 'pair')}`;
  }
  return 'privé';
}

function _networkOverallLabel(overall) {
  if (overall === 'healthy') return {label:'sain', cls:'ok', color:'var(--ok)'};
  if (overall === 'degraded') return {label:'dégradé', cls:'accent', color:'var(--accent)'};
  if (overall === 'down') return {label:'hors ligne', cls:'danger', color:'var(--danger)'};
  return {label:'aucun pair trusted', cls:'muted', color:'var(--muted)'};
}

export async function loadNetworkObservability() {
  const el = document.getElementById('net-observability-content');
  const h = {'Authorization': `Bearer ${ADMIN_TOKEN}`};
  if (el) el.innerHTML = loadingDots('Chargement...');
  try {
    const [metricsRes, healthRes] = await Promise.allSettled([
      fetch(`${API_BASE}/api/peer/metrics`, {headers:h}),
      fetch(`${API_BASE}/api/peers/health`, {headers:h}),
    ]);
    const metrics = metricsRes.status === 'fulfilled' && metricsRes.value.ok ? await metricsRes.value.json() : null;
    const health = healthRes.status === 'fulfilled' && healthRes.value.ok ? await healthRes.value.json() : null;
    if (!el) return;
    if (!metrics && !health) {
      el.innerHTML = '<div style="color:var(--danger);font-size:12px">Observabilité indisponible.</div>';
      return;
    }
    const overall = _networkOverallLabel(health?.overall || 'empty');
    const downPeers = (health?.peers || []).filter(p => p.trust === 'trusted' && !p.reachable);
    const issues = metrics?.user_issues || [];
    el.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px;font-size:12px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="pill ${overall.cls}" style="font-size:11px">réseau ${overall.label}</span>
        <span style="color:var(--muted)">trusted: ${metrics?.peers?.trusted ?? health?.trusted_count ?? 0}</span>
        <span style="color:var(--muted)">accessibles: ${health?.reachable_trusted ?? 0}</span>
        <span style="color:var(--muted)">latence moy.: ${health?.avg_latency_ms ?? metrics?.latency?.avg_ms ?? '—'}ms</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:6px">
        <div class="pill" style="justify-content:center">délégations: ${metrics?.delegations?.total_events ?? 0}</div>
        <div class="pill" style="justify-content:center">succès: ${metrics?.delegations?.success_rate_percent ?? 100}%</div>
        <div class="pill" style="justify-content:center">tâches actives: ${metrics?.tasks?.active ?? 0}</div>
        <div class="pill" style="justify-content:center">rate-limit: ${metrics?.errors?.rate_limited ?? 0}</div>
      </div>
      ${downPeers.length ? `<div style="color:var(--danger)">Pairs à vérifier : ${downPeers.map(p => esc(p.instance_name || p.instance_id)).join(', ')}</div>` : ''}
      ${issues.length ? `<div style="color:var(--muted)">Derniers problèmes : ${issues.map(esc).join(' · ')}</div>` : ''}
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn" style="font-size:11px" onclick="cleanupPeerRuntime(true)">Dry-run nettoyage</button>
        <button class="btn" style="font-size:11px;color:var(--danger)" onclick="cleanupPeerRuntime(false)">Nettoyer anciens logs</button>
      </div>
    </div>`;
  } catch (e) {
    if (el) el.innerHTML = `<div style="color:var(--danger);font-size:12px">Erreur observabilité: ${esc(e.message)}</div>`;
  }
}

export async function cleanupPeerRuntime(dryRun=true) {
  const msgEl = document.getElementById('net-maintenance-msg');
  if (!dryRun && !confirm('Nettoyer les anciens logs inter-Lumena ? Le registre, les tokens et les connaissances ne seront pas supprimés.')) return;
  if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--muted)'; msgEl.textContent = dryRun ? 'Simulation nettoyage…' : 'Nettoyage en cours…'; }
  try {
    const r = await fetch(`${API_BASE}/api/peer/maintenance/cleanup`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({
        dry_run: !!dryRun,
        keep_audit_lines: 1000,
        keep_task_event_lines: 1000,
        cleanup_memory_tasks: true,
        clear_terminal_task_events: true,
        cleanup_terminal_memory_tasks: true,
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Nettoyage impossible');
    if (msgEl) {
      msgEl.style.color = 'var(--ok)';
      msgEl.textContent = `${dryRun ? 'Simulation' : 'Nettoyage'} OK — audit retirables: ${d.audit?.removed ?? 0}, events taches retirables: ${d.task_events?.removed ?? 0}, memoire: ${d.memory_tasks_removed ?? 0}`;
    }
    loadNetworkObservability();
    loadCollaborationPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

export async function loadCollaborationPanel() {
  const knowledgeEl = document.getElementById('net-knowledge-list');
  const tasksEl = document.getElementById('net-task-list');
  const peerSelect = document.getElementById('net-knowledge-peer');
  const h = {'Authorization': `Bearer ${ADMIN_TOKEN}`};

  if (knowledgeEl) knowledgeEl.innerHTML = loadingDots('Chargement...');
  if (tasksEl) tasksEl.innerHTML = loadingDots('Chargement...');

  const [peersRes, knowledgeRes, tasksRes] = await Promise.allSettled([
    fetch(`${API_BASE}/api/peers`, {headers: h}),
    fetch(`${API_BASE}/api/shared-knowledge`, {headers: h}),
    fetch(`${API_BASE}/api/peer/local-tasks?limit=20`, {headers: h}),
  ]);

  let peers = [], knowledge = [], tasks = [];
  try {
    const d = peersRes.status === 'fulfilled' && peersRes.value.ok ? await peersRes.value.json() : {peers: []};
    peers = d.peers || [];
  } catch (_) {}
  try {
    const d = knowledgeRes.status === 'fulfilled' && knowledgeRes.value.ok ? await knowledgeRes.value.json() : {items: []};
    knowledge = d.items || [];
  } catch (_) {}
  try {
    const d = tasksRes.status === 'fulfilled' && tasksRes.value.ok ? await tasksRes.value.json() : {items: []};
    tasks = d.items || [];
  } catch (_) {}

  const peersById = new Map(peers.map(p => [p.instance_id, p]));
  const sharePeers = peers.filter(p => p.trust === 'trusted' && p.has_peer_token && _hasPeerScope(p, 'knowledge.share'));

  if (peerSelect) {
    const current = peerSelect.value;
    peerSelect.innerHTML = '<option value="">Garder privé</option>' + sharePeers.map(p =>
      `<option value="${esc(p.instance_id)}">${esc(p.instance_name || p.instance_id)} — ${esc(p.host || '')}:${p.port || 8080}</option>`
    ).join('');
    if (current && sharePeers.some(p => p.instance_id === current)) peerSelect.value = current;
  }

  if (knowledgeEl) {
    if (!knowledge.length) {
      knowledgeEl.innerHTML = '<div style="color:var(--muted);font-size:12px">Aucune connaissance contrôlée. Créez un résumé court puis partagez-le avec un pair trusted.</div>';
    } else {
      knowledgeEl.innerHTML = knowledge.slice(0, 8).map(k => {
        const id = k.knowledge_id;
        const vis = _knowledgeVisibilityLabel(k, peersById);
        const tags = (k.tags || []).slice(0, 4).map(t => `<span class="pill" style="font-size:10px">${esc(t)}</span>`).join(' ');
        const shared = k.visibility === 'shared_with_peer';
        const imported = !!k.imported_memory_id;
        const shareBtn = sharePeers.length
          ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="shareKnowledgeFromUi(${_jsArg(id)})"><i data-lucide="share-2" style="width:10px;height:10px"></i> Partager</button>`
          : '';
        const revokeBtn = shared
          ? `<button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="revokeKnowledgeFromUi(${_jsArg(id)})">Révoquer</button>`
          : '';
        const importBtn = imported
          ? `<span class="pill ok" style="font-size:10px">importé</span>`
          : `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="importKnowledgeFromUi(${_jsArg(id)})">Importer</button>`;
        return `<div class="list-item" style="padding:8px 0">
          <div style="flex:1;min-width:0;font-size:12px">
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
              <strong>${esc(k.title || id)}</strong>
              <span class="pill ${shared ? 'ok' : 'muted'}" style="font-size:10px">${esc(vis)}</span>
              ${tags}
            </div>
            <div style="color:var(--muted);margin-top:4px">${esc((k.summary || '').slice(0, 220))}${(k.summary || '').length > 220 ? '…' : ''}</div>
          </div>
          <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">${shareBtn}${revokeBtn}${importBtn}</div>
        </div>`;
      }).join('');
    }
  }

  if (tasksEl) {
    if (!tasks.length) {
      tasksEl.innerHTML = '<div style="color:var(--muted);font-size:12px">Aucune tâche inter-Lumena récente.</div>';
    } else {
      tasksEl.innerHTML = tasks.slice(0, 8).map(t => {
        const peer = peersById.get(t.from_instance_id);
        const who = peer ? (peer.instance_name || peer.instance_id) : (t.from_instance_id || 'pair');
        const latest = t.latest_event || {};
        const result = t.result ? `<div style="color:var(--muted);margin-top:3px">${esc(String(t.result).slice(0, 180))}${String(t.result).length > 180 ? '…' : ''}</div>` : '';
        return `<div class="list-item" style="padding:8px 0">
          <div style="flex:1;min-width:0;font-size:12px">
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
              <span class="pill ${_taskStatusColor(t.status)}" style="font-size:10px">${esc(t.status || 'unknown')}</span>
              <strong>${esc(who)}</strong>
              <code style="font-size:10px;color:var(--muted)">${esc(t.task_id || '')}</code>
            </div>
            <div style="font-size:11px;color:var(--muted);margin-top:3px">${esc(latest.event || 'event')} ${latest.ts ? '— ' + esc(latest.ts.substring(0,19).replace('T',' ')) : ''}</div>
            ${result}
          </div>
        </div>`;
      }).join('');
    }
  }

  if (typeof lucide !== 'undefined') lucide.createIcons({attrs:{class:'lucide'}});
}

export async function createSharedKnowledgeFromUi() {
  const titleEl = document.getElementById('net-knowledge-title');
  const summaryEl = document.getElementById('net-knowledge-summary');
  const tagsEl = document.getElementById('net-knowledge-tags');
  const peerEl = document.getElementById('net-knowledge-peer');
  const msgEl = document.getElementById('net-knowledge-msg');
  const title = (titleEl?.value || '').trim();
  const summary = (summaryEl?.value || '').trim();
  const tags = (tagsEl?.value || '').split(',').map(x => x.trim()).filter(Boolean);
  const peerId = (peerEl?.value || '').trim();
  if (!title || !summary) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent='Titre et résumé sont obligatoires.'; }
    return;
  }
  if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--muted)'; msgEl.textContent='Création en cours…'; }
  try {
    const r = await fetch(`${API_BASE}/api/shared-knowledge`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({title, summary, tags, origin_user_id:'local:owner'}),
    });
    const k = await r.json();
    if (!r.ok) throw new Error(k.detail || 'Création impossible');
    if (peerId) await _shareKnowledge(k.knowledge_id, peerId);
    if (titleEl) titleEl.value = '';
    if (summaryEl) summaryEl.value = '';
    if (tagsEl) tagsEl.value = '';
    if (msgEl) { msgEl.style.color='var(--ok)'; msgEl.textContent = peerId ? 'Connaissance créée et partagée.' : 'Connaissance créée en privé.'; }
    loadCollaborationPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

async function _shareKnowledge(knowledgeId, peerId) {
  const r = await fetch(`${API_BASE}/api/shared-knowledge/${encodeURIComponent(knowledgeId)}/share`, {
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
    body:JSON.stringify({peer_id: peerId}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Partage impossible');
  return d;
}

export async function shareKnowledgeFromUi(knowledgeId) {
  const peerId = (document.getElementById('net-knowledge-peer')?.value || '').trim();
  const msgEl = document.getElementById('net-knowledge-msg');
  if (!peerId) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent='Choisissez un pair avec le scope knowledge.share.'; }
    return;
  }
  try {
    await _shareKnowledge(knowledgeId, peerId);
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--ok)'; msgEl.textContent='Connaissance partagée.'; }
    loadCollaborationPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

export async function revokeKnowledgeFromUi(knowledgeId) {
  const msgEl = document.getElementById('net-knowledge-msg');
  try {
    const r = await fetch(`${API_BASE}/api/shared-knowledge/${encodeURIComponent(knowledgeId)}/revoke`, {
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Révocation impossible');
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--ok)'; msgEl.textContent='Partage révoqué.'; }
    loadCollaborationPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

export async function importKnowledgeFromUi(knowledgeId) {
  const msgEl = document.getElementById('net-knowledge-msg');
  try {
    const r = await fetch(`${API_BASE}/api/shared-knowledge/${encodeURIComponent(knowledgeId)}/import`, {
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Import impossible');
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--ok)'; msgEl.textContent='Connaissance importée en mémoire locale.'; }
    loadCollaborationPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

export async function setPeerScope(instanceId, scope, enabled) {
  _showNetworkActionMessage('Mise a jour des droits du pair...', 'muted');
  try {
    const h = {'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`};
    const r = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}/scopes`, {headers:h});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Lecture scopes impossible');
    const current = new Set(d.allowed_scopes || ['chat']);
    if (enabled) current.add(scope); else current.delete(scope);
    const next = Array.from(current);
    const wr = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}/scopes`, {
      method:'PUT',
      headers:h,
      body:JSON.stringify({allowed_scopes: next}),
    });
    const wd = await wr.json().catch(() => ({}));
    if (!wr.ok) throw new Error(wd.detail || 'Mise a jour scopes impossible');
    _showNetworkActionMessage('Droits du pair mis a jour.', 'ok');
    await _refreshNetworkPanels();
  } catch (e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

export function sendTeamPromptFromUi() {
  const input = document.getElementById('net-team-prompt');
  const msgEl = document.getElementById('net-team-msg');
  const prompt = (input?.value || '').trim();
  if (!prompt) {
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'Ecris une demande pour l equipe Lumena.';
    }
    return;
  }
  if (msgEl) {
    msgEl.style.display = 'block';
    msgEl.style.color = 'var(--muted)';
    msgEl.textContent = 'Envoi vers le chat Lumena...';
  }
  if (typeof window.quickSend === 'function') {
    window.quickSend(`Travaille avec l equipe Lumena si utile : ${prompt}`);
    if (input) input.value = '';
    return;
  }
  const chatInput = document.getElementById('message-input');
  if (chatInput) {
    chatInput.value = `Travaille avec l equipe Lumena si utile : ${prompt}`;
    chatInput.focus();
    if (msgEl) {
      msgEl.style.color = 'var(--ok)';
      msgEl.textContent = 'Demande prete dans le chat.';
    }
  }
}

export async function loadNetworkSimple() {
  const statusEl = document.getElementById('net-simple-status');
  const peersEl  = document.getElementById('net-simple-peers');
  const h = {'Authorization': `Bearer ${ADMIN_TOKEN}`};

  if (statusEl) statusEl.innerHTML = '<div style="color:var(--muted)">Chargement…</div>';
  if (peersEl)  peersEl.innerHTML  = '<div style="color:var(--muted)">Chargement…</div>';

  // Chargement parallèle : instances locales + diagnostic + pairs
  const [localRes, diagRes, peersRes] = await Promise.allSettled([
    fetch(`${API_BASE}/api/instances/local`, {headers: h}),
    fetch(`${API_BASE}/api/instance/network-diagnostic`, {headers: h}),
    fetch(`${API_BASE}/api/peers`, {headers: h}),
  ]);

  let own = null, diag = null, peers = [];
  try {
    const d = localRes.status==='fulfilled'&&localRes.value.ok ? await localRes.value.json() : {instances:[]};
    own = (d.instances||[]).find(i=>i.is_self)||null;
  } catch(_) {}
  try {
    if (diagRes.status==='fulfilled'&&diagRes.value.ok) diag = await diagRes.value.json();
  } catch(_) {}
  try {
    const d = peersRes.status==='fulfilled'&&peersRes.value.ok ? await peersRes.value.json() : {peers:[]};
    peers = d.peers||[];
  } catch(_) {}

  // Rendu statut réseau
  if (statusEl) {
    const {label, color, dot, action} = _netStatusInfo(diag, own);
    const actionBtn = action
      ? `<button class="btn" style="font-size:11px;margin-left:8px" onclick="${action.fn}">${action.label}</button>`
      : '';
    statusEl.innerHTML = `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:18px;color:${color}">${dot}</span>
      <span style="font-size:13px;color:${color};font-weight:500">${esc(label)}</span>
      ${actionBtn}
      <button class="btn" style="font-size:11px;margin-left:auto" onclick="loadNetworkDiagnostic()"><i data-lucide="activity" style="width:11px;height:11px"></i> Détails</button>
    </div>`;
  }

  // Rendu pairs
  if (peersEl) {
    if (!peers.length) {
      peersEl.innerHTML = `<div style="color:var(--muted);font-size:12px">Aucune instance connue. Scannez le réseau ou jumelez par code.</div>`;
    } else {
      peersEl.innerHTML = peers.map(p => {
        const {label, color, dot} = _peerStatusInfo(p);
        const iid = esc(p.instance_id);
        return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
          <span style="font-size:16px;color:${color};flex-shrink:0">${dot}</span>
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:500">${esc(p.instance_name||p.instance_id)}</div>
            <div style="font-size:11px;color:${color}">${label}</div>
            <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${_peerScopesHtml(p)}</div>
            <div id="ns-test-${iid}" style="font-size:10px;margin-top:2px"></div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0;flex-wrap:wrap">${_peerActionsHtml(p)}</div>
        </div>`;
      }).join('');
    }
  }

  // Charge le sélecteur d'interface dans le mode avancé (8.10)
  _loadNetworkInterfaces();
  loadNetworkObservability();
  loadCollaborationPanel();
}

export function toggleNetworkAdvanced() {
  const simple   = document.getElementById('net-simple-view');
  const advanced = document.getElementById('net-advanced-view');
  if (!simple || !advanced) return;
  const showAdv = advanced.style.display === 'none';
  simple.style.display   = showAdv ? 'none' : 'block';
  advanced.style.display = showAdv ? 'block' : 'none';
  if (showAdv) loadInstancesNetwork();
}

export function showSimplePairingForm(host='', port=8080) {
  const form = document.getElementById('net-simple-pairing-form');
  if (!form) return;
  form.style.display = 'block';
  const hostEl = document.getElementById('net-pairing-host');
  const portEl = document.getElementById('net-pairing-port');
  if (host && hostEl) hostEl.value = host;
  if (port && portEl) portEl.value = port;
  form.scrollIntoView({behavior:'smooth', block:'nearest'});
}

export async function blockPeerSimple(instanceId) {
  if (!confirm(`Bloquer ${instanceId} ?`)) return;
  _showNetworkActionMessage('Blocage du pair en cours...', 'muted');
  try {
    const r = await fetch(`${API_BASE}/api/peers/block`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({instance_id:instanceId}),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || 'Blocage impossible');
    }
    _showNetworkActionMessage('Pair bloque.', 'ok');
    await _refreshNetworkPanels();
  } catch(e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

export async function deletePeerSimple(instanceId) {
  if (!confirm(`Supprimer ${instanceId} du registre local ? Les tokens de ce pair seront oublies.`)) return;
  _showNetworkActionMessage('Suppression du pair en cours...', 'muted');
  try {
    const r = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}`, {
      method:'DELETE',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || 'Suppression impossible');
    }
    _showNetworkActionMessage('Pair supprime du registre local.', 'ok');
    await _refreshNetworkPanels();
  } catch(e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

export async function deleteLocalInstance(instanceId) {
  if (!confirm(`Supprimer l'entree locale ${instanceId} ?`)) return;
  _showNetworkActionMessage('Suppression de l entree locale en cours...', 'muted');
  try {
    const r = await fetch(`${API_BASE}/api/instances/local/${encodeURIComponent(instanceId)}`, {
      method:'DELETE',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || 'Suppression impossible');
    }
    _showNetworkActionMessage('Entree locale supprimee.', 'ok');
    await loadInstancesNetwork();
  } catch(e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

export async function cleanupLocalInstances() {
  _showNetworkActionMessage('Nettoyage du registre local en cours...', 'muted');
  try {
    const r = await fetch(`${API_BASE}/api/instances/local/cleanup`, {
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Nettoyage impossible');
    _showNetworkActionMessage(`${d.removed || 0} entree(s) locale(s) supprimee(s).`, 'ok');
    await loadInstancesNetwork();
  } catch(e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

// ── Phase 8.10 — Sélecteur multi-réseaux ─────────────────────────────────────
async function _loadNetworkInterfaces() {
  const sel = document.getElementById('net-discover-iface');
  if (!sel) return;
  try {
    const r = await fetch(`${API_BASE}/api/instance/network-interfaces`, {
      headers: {'Authorization': `Bearer ${ADMIN_TOKEN}`},
    });
    if (!r.ok) return;
    const d = await r.json();
    const ifaces = d.interfaces||[];
    // Réinitialise sans toucher à l'option "Auto"
    while (sel.options.length > 1) sel.remove(1);
    ifaces.forEach(iface => {
      const opt = document.createElement('option');
      opt.value = iface.network;
      opt.textContent = iface.label;
      sel.appendChild(opt);
    });
  } catch(_) {}
}

/* ============================================================
   INSTANCES & RÉSEAU (Phase 4-6) — Vue avancée
   ============================================================ */
export async function loadInstancesNetwork(){
  const selfEl=document.getElementById('net-self-content');
  const localEl=document.getElementById('net-local-list');
  const peersEl=document.getElementById('net-peers-list');
  const auditEl=document.getElementById('net-audit-list');
  if(selfEl)selfEl.innerHTML=loadingDots('Chargement...');
  if(localEl)localEl.innerHTML=loadingDots('Chargement...');
  if(peersEl)peersEl.innerHTML=loadingDots('Chargement...');
  if(auditEl)auditEl.innerHTML=loadingDots('Chargement...');
  const h={'Authorization':`Bearer ${ADMIN_TOKEN}`};

  // Instance courante + instances locales + diagnostic réseau (en parallèle)
  const [localResp, diagResp] = await Promise.allSettled([
    fetch(`${API_BASE}/api/instances/local`,{headers:h}),
    fetch(`${API_BASE}/api/instance/network-diagnostic`,{headers:h}),
  ]);
  try{
    const d=localResp.status==='fulfilled'&&localResp.value.ok?await localResp.value.json():{instances:[]};
    let diag=null;
    if(diagResp.status==='fulfilled'&&diagResp.value.ok){try{diag=await diagResp.value.json();}catch(_){}}
    const own=d.instances?.find(i=>i.is_self)||null;
    if(selfEl){
      if(own){
        // Badge réseau
        let netBadge='';
        if(diag){
          if(diag.ok){
            netBadge=`<span class="pill ok" style="font-size:10px">réseau OK</span>`;
          }else{
            const errs=(diag.issues||[]).filter(i=>i.severity==='error');
            const warns=(diag.issues||[]).filter(i=>i.severity==='warning');
            if(errs.length) netBadge=`<span class="pill danger" style="font-size:10px">réseau ✗</span>`;
            else if(warns.length) netBadge=`<span class="pill" style="font-size:10px;background:var(--warning,#f59e0b);color:#000">pare-feu ?</span>`;
          }
        }
        // Issues réseau
        let issuesHtml='';
        if(diag&&diag.issues&&diag.issues.length){
          issuesHtml=`<div style="margin-top:8px;display:flex;flex-direction:column;gap:4px">`
            +diag.issues.map(i=>`<div style="font-size:11px;color:var(--${i.severity==='error'?'danger':'muted'})">${esc(i.message)}</div>`).join('')
            +`</div>`;
        }
        // LAN IPs
        const lanIps=diag?.lan_ips?.length?diag.lan_ips.map(ip=>`<code style="font-size:10px">${esc(ip)}:${diag.port}</code>`).join(' '):'—';
        selfEl.innerHTML=`<div style="display:flex;flex-direction:column;gap:6px;font-size:12px">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            <strong>${esc(own.instance_name)}</strong>
            <span class="pill ${own.role==='worker'?'muted':'accent'}">${esc(own.role)}</span>
            ${netBadge}
          </div>
          <div><span style="color:var(--muted)">ID :</span> <code style="font-size:11px">${esc(own.instance_id)}</code></div>
          <div><span style="color:var(--muted)">Adresse LAN :</span> ${lanIps}</div>
          <div><span style="color:var(--muted)">Port :</span> ${own.port} &nbsp;|&nbsp; <span style="color:var(--muted)">PID :</span> ${own.pid} &nbsp;|&nbsp; <span style="color:var(--muted)">v</span>${esc(own.version||'?')}</div>
          <div><span style="color:var(--muted)">Capacités :</span> ${(own.capabilities||[]).map(c=>`<span class="pill">${esc(c)}</span>`).join(' ')||'—'}</div>
          ${issuesHtml}
          <div style="margin-top:4px"><button class="btn" style="font-size:11px" onclick="loadNetworkDiagnostic()"><i data-lucide="activity" style="width:12px;height:12px"></i> Diagnostiquer</button></div>
        </div>`;
      }else{
        selfEl.innerHTML='<div style="color:var(--muted)">Chargement du registre…</div>';
      }
    }
    const badge=document.getElementById('badge-network');
    if(badge){badge.textContent=d.count||0;badge.style.background='var(--accent)';}
    if(localEl){
      const others=(d.instances||[]).filter(i=>!i.is_self);
      if(!others.length){
        localEl.innerHTML='<div style="color:var(--muted)">Aucune autre instance Lumena active sur ce PC.</div>';
      }else{
        localEl.innerHTML=others.map(inst=>`<div class="list-item" style="cursor:default">
          <div style="flex:1;min-width:0;font-size:12px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
              <span class="pill ${inst.role==='worker'?'muted':'accent'}" style="font-size:10px">${esc(inst.role)}</span>
              <strong>${esc(inst.instance_name)}</strong>
              <span style="color:var(--muted)">— port ${inst.port}</span>
            </div>
            <div style="color:var(--muted)">${esc(inst.instance_id)}</div>
            <div style="color:var(--muted)">PID ${inst.pid} · v${esc(inst.version||'?')}</div>
          </div>
          <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deleteLocalInstance(${_jsArg(inst.instance_id)})">Supprimer</button>
        </div>`).join('');
      }
    }
  }catch(e){
    if(selfEl)selfEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
    if(localEl)localEl.innerHTML='';
  }

  // Pairs LAN connus
  try{
    const r=await fetch(`${API_BASE}/api/peers`,{headers:h});
    const d=await r.json();
    const peers=d.peers||[];
    if(peersEl){
      if(!peers.length){
        peersEl.innerHTML='<div style="color:var(--muted)">Aucun pair connu. Scannez le LAN ou jumelez manuellement.</div>';
      }else{
        const tc={trusted:'ok',unknown:'muted',blocked:'danger'};
        peersEl.innerHTML=peers.map(p=>`<div class="list-item">
          <div style="flex:1;min-width:0;font-size:12px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
              <span class="pill ${tc[p.trust]||'muted'}" style="font-size:10px">${esc(p.trust)}</span>
              <strong>${esc(p.instance_name||p.instance_id)}</strong>
              <span style="color:var(--muted)">— ${esc(p.host)}:${p.port}</span>
            </div>
            <div style="color:var(--muted)">${esc(p.instance_id)}</div>
            <div>${(p.capabilities||[]).map(c=>`<span class="pill" style="font-size:10px">${esc(c)}</span>`).join(' ')||''}</div>
            <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${_peerScopesHtml(p)}</div>
            <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">${_peerActionsHtml(p)}</div>
          </div>
        </div>`).join('');
      }
    }
  }catch(e){
    if(peersEl)peersEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }

  // Audit inter-instances (20 derniers)
  try{
    const r=await fetch(`${API_BASE}/api/peer/audit?limit=20`,{headers:h});
    const d=await r.json();
    const entries=d.entries||[];
    if(auditEl){
      if(!entries.length){
        auditEl.innerHTML='<div style="color:var(--muted)">Aucun événement inter-instances enregistré.</div>';
      }else{
        const ec={delegate_accepted:'ok',delegate_refused:'danger',delegate_completed:'accent'};
        auditEl.innerHTML=entries.slice().reverse().map(e=>`<div class="list-item" style="padding:6px 0">
          <div style="flex:1;min-width:0;font-size:11px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
              <span class="pill ${ec[e.event]||'muted'}" style="font-size:10px">${esc(e.event)}</span>
              <span style="color:var(--muted)">${esc((e.ts||'').substring(0,19).replace('T',' '))}</span>
            </div>
            <div style="color:var(--text)">${esc(e.from_instance_id)} → scope:${esc(e.scope)} [${esc(e.status)}]</div>
            ${e.detail?`<div style="color:var(--danger);font-size:10px">${esc(e.detail)}</div>`:''}
          </div>
        </div>`).join('');
      }
    }
  }catch(e){
    if(auditEl)auditEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
}

export async function discoverLanPeers(){
  const msgEl=document.getElementById('net-discover-msg');
  const resultEl=document.getElementById('net-discover-result');
  // Indique le scan uniquement dans la carte Découverte — les autres cartes restent intactes
  if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--muted)';msgEl.textContent='Scan en cours… (jusqu\'à 12s)';}
  if(resultEl)resultEl.textContent='';
  const ifaceEl=document.getElementById('net-discover-iface');
  const body=ifaceEl&&ifaceEl.value?{network:ifaceEl.value}:{};
  try{
    const r=await fetch(`${API_BASE}/api/peer/discover`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify(body),
    });
    const d=await r.json();
    if(!r.ok){
      if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${d.detail||'Échec du scan'}`;}
      return;
    }
    if(msgEl){msgEl.style.color='var(--ok)';msgEl.textContent=`${d.discovered} instance(s) découverte(s).`;}
    if(resultEl){
      if(!d.peers?.length){
        resultEl.textContent='Aucune instance Lumena trouvée sur le réseau local.';
      }else{
        resultEl.innerHTML=d.peers.map(p=>`<div style="margin-bottom:4px"><strong>${esc(p.instance_name||p.instance_id)}</strong> — ${esc(p.host)}:${p.port} <span class="pill muted" style="font-size:10px">${esc(p.trust)}</span></div>`).join('');
      }
    }
    // Recharge uniquement la liste des pairs (pas toutes les cartes)
    _reloadPeersList();
  }catch(e){
    if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${e.message}`;}
  }
}

async function _reloadPeersList(){
  const peersEl=document.getElementById('net-peers-list');
  if(!peersEl)return;
  try{
    const r=await fetch(`${API_BASE}/api/peers`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json();
    const peers=d.peers||[];
    if(!peers.length){
      peersEl.innerHTML='<div style="color:var(--muted)">Aucun pair connu. Scannez le LAN ou jumelez manuellement.</div>';
    }else{
      const tc={trusted:'ok',unknown:'muted',blocked:'danger'};
      peersEl.innerHTML=peers.map(p=>{
        const iid=esc(p.instance_id);
        const scopes=Array.isArray(p.allowed_scopes)?p.allowed_scopes:[];
        const testBtn=p.trust==='trusted'&&scopes.includes('chat')
          ?`<button class="btn" style="font-size:10px;padding:2px 8px" onclick="testDelegation('${iid}','net-test-${iid}')"><i data-lucide="zap" style="width:10px;height:10px"></i> Tester</button>`
          :'';
        return`<div class="list-item">
          <div style="flex:1;min-width:0;font-size:12px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;flex-wrap:wrap">
              <span class="pill ${tc[p.trust]||'muted'}" style="font-size:10px">${esc(p.trust)}</span>
              <strong>${esc(p.instance_name||p.instance_id)}</strong>
              <span style="color:var(--muted)">— ${esc(p.host)}:${p.port}</span>
              ${testBtn}
            </div>
            <div style="color:var(--muted)">${esc(p.instance_id)}</div>
            <div>${(p.capabilities||[]).map(c=>`<span class="pill" style="font-size:10px">${esc(c)}</span>`).join(' ')||''}</div>
            <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${_peerScopesHtml(p)}</div>
            <div id="net-test-${iid}" style="font-size:11px;margin-top:4px"></div>
          </div>
        </div>`;
      }).join('');
      if(typeof lucide!=='undefined')lucide.createIcons({attrs:{class:'lucide'}});
    }
  }catch(e){
    if(peersEl)peersEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
}

export async function testDelegation(instanceId, resultElId){
  const el=resultElId?document.getElementById(resultElId):null;
  if(el){el.style.color='var(--muted)';el.textContent='Test en cours…';}
  try{
    const r=await fetch(`${API_BASE}/api/peer/test-delegation`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({instance_id:instanceId}),
    });
    const d=await r.json();
    if(el){
      if(d.ok){
        el.style.color='var(--ok)';
        el.textContent=`✓ Délégation OK — ${d.latency_ms}ms`;
      }else{
        el.style.color='var(--danger)';
        el.textContent=`✗ Échec — ${d.error||d.status}`;
      }
    }
  }catch(e){
    if(el){el.style.color='var(--danger)';el.textContent=`✗ Erreur: ${e.message}`;}
  }
}

export async function loadNetworkDiagnostic(){
  // Détecte la vue active pour cibler le bon conteneur
  const simpleView=document.getElementById('net-simple-view');
  const isSimple=simpleView&&simpleView.style.display!=='none';

  let targetEl, backFn;
  if(isSimple){
    // Vue simple : expansion inline sous le statut, sans remplacer la ligne de statut
    targetEl=document.getElementById('net-diag-detail');
    backFn='hideNetworkDiagnostic()';
    if(targetEl){targetEl.style.display='block';targetEl.innerHTML=loadingDots('Diagnostic en cours…');}
  }else{
    // Vue avancée : remplace le contenu de la carte Instance courante
    targetEl=document.getElementById('net-self-content');
    backFn='loadInstancesNetwork()';
    if(targetEl){targetEl.innerHTML=loadingDots('Diagnostic en cours…');}
  }
  if(!targetEl)return;

  try{
    const r=await fetch(`${API_BASE}/api/instance/network-diagnostic`,{
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d=await r.json();
    const statusColor=d.ok?'var(--ok)':'var(--danger)';
    const statusText=d.ok?'Réseau OK':'Problème détecté';
    const lanIps=(d.lan_ips||[]).map(ip=>`<code style="font-size:10px">${esc(ip)}:${d.port}</code>`).join(' ')||'—';
    let issuesHtml='';
    if(d.issues&&d.issues.length){
      issuesHtml='<div style="margin-top:8px;display:flex;flex-direction:column;gap:4px">'
        +d.issues.map(i=>`<div style="font-size:11px;color:var(--${i.severity==='error'?'danger':'muted'})">${esc(i.message)}</div>`).join('')
        +'</div>';
    }
    let actionsHtml='';
    if((d.suggested_actions||[]).includes('open_windows_firewall_port')){
      const cmd=`netsh advfirewall firewall add rule name="Lumena HTTP ${d.port}" protocol=TCP dir=in localport=${d.port} action=allow`;
      actionsHtml=`<div style="margin-top:8px;font-size:11px;color:var(--muted)">Commande pare-feu :</div>
        <code style="font-size:10px;word-break:break-all;display:block;margin-top:2px;padding:4px;background:var(--surface);border-radius:4px">${esc(cmd)}</code>`;
    }
    const backLabel=isSimple
      ?'<i data-lucide="x" style="width:12px;height:12px"></i> Masquer'
      :'<i data-lucide="arrow-left" style="width:12px;height:12px"></i> Retour';
    targetEl.innerHTML=`<div style="display:flex;flex-direction:column;gap:6px">
      <div style="font-weight:600;color:${statusColor}">${statusText}</div>
      <div><span style="color:var(--muted)">Bind :</span> ${esc(d.host)} &nbsp;|&nbsp; <span style="color:var(--muted)">Port :</span> ${d.port}</div>
      <div><span style="color:var(--muted)">Adresses LAN :</span> ${lanIps}</div>
      <div><span style="color:var(--muted)">Écoute locale :</span> <span style="color:var(--${d.listening?'ok':'danger'})">${d.listening?'oui':'non'}</span></div>
      <div><span style="color:var(--muted)">Accessible réseau :</span> <span style="color:var(--${d.network_accessible?'ok':'danger'})">${d.network_accessible?'oui':'non'}</span></div>
      <div><span style="color:var(--muted)">Pare-feu :</span> ${esc(d.firewall_check||'—')}</div>
      ${issuesHtml}${actionsHtml}
      <div style="margin-top:4px"><button class="btn" style="font-size:11px" onclick="${backFn}">${backLabel}</button></div>
    </div>`;
  }catch(e){
    targetEl.innerHTML=`<div style="color:var(--danger)">Erreur diagnostic: ${esc(e.message)}</div>`;
  }
}

export function hideNetworkDiagnostic(){
  const el=document.getElementById('net-diag-detail');
  if(el){el.style.display='none';el.innerHTML='';}
}

export async function generatePairingCode(){
  const displayEl=document.getElementById('net-pairing-code-display');
  const codeEl=document.getElementById('net-pairing-code-value');
  const h={'Authorization':`Bearer ${ADMIN_TOKEN}`};
  try{
    const r=await fetch(`${API_BASE}/api/peer/pairing-code`,{method:'POST',headers:h});
    const d=await r.json();
    if(!r.ok){alert(`Erreur: ${d.detail||'Échec'}`);return;}
    if(codeEl)codeEl.textContent=d.code;
    if(displayEl)displayEl.style.display='block';
    // Masquer automatiquement après 5 min
    setTimeout(()=>{
      if(displayEl)displayEl.style.display='none';
      if(codeEl)codeEl.textContent='——';
    }, (d.expires_in||300)*1000);
  }catch(e){alert(`Erreur: ${e.message}`);}
}

export async function acceptPairing(){
  const hostEl=document.getElementById('net-pairing-host');
  const portEl=document.getElementById('net-pairing-port');
  const codeEl=document.getElementById('net-pairing-code-input');
  const msgEl=document.getElementById('net-pairing-msg');
  const host=(hostEl?.value||'').trim();
  const port=parseInt(portEl?.value||'8080',10);
  const code=(codeEl?.value||'').trim().toUpperCase();
  if(!host||!code){
    if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--danger)';msgEl.textContent='Remplissez host, port et code.';}
    return;
  }
  if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--muted)';msgEl.textContent='Jumelage en cours…';}
  try{
    const r=await fetch(`${API_BASE}/api/peer/accept-pairing`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({host,port,code}),
    });
    const d=await r.json();
    if(r.ok){
      if(msgEl){msgEl.style.color='var(--ok)';msgEl.textContent=`✓ Jumelé avec ${d.instance_name||d.instance_id} (${host}:${port})`;}
      if(codeEl)codeEl.value='';
      _reloadPeersList();
    }else{
      if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${d.detail||'Échec'}`;}}
  }catch(e){if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${e.message}`;}}
}

export async function loadFirewallCommand(){
  const cmdEl=document.getElementById('net-firewall-cmd');
  const applyBtn=document.getElementById('net-firewall-apply-btn');
  const msgEl=document.getElementById('net-firewall-msg');
  try{
    const r=await fetch(`${API_BASE}/api/instance/firewall-command`,{
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d=await r.json();
    if(!r.ok){if(cmdEl){cmdEl.style.display='block';cmdEl.innerHTML=`<span style="color:var(--danger)">${esc(d.detail||'Erreur')}</span>`;}return;}
    if(cmdEl){
      cmdEl.style.display='block';
      cmdEl.innerHTML=`<div style="margin-bottom:4px;font-size:11px;color:var(--muted)">Commande (${esc(d.platform)}, port ${d.port}) :</div>
        <code style="font-size:10px;word-break:break-all;display:block;padding:6px 8px;background:var(--surface);border-radius:4px;color:var(--text)">${esc(d.command)}</code>`;
    }
    if(applyBtn&&d.platform==='Windows')applyBtn.style.display='block';
  }catch(e){if(cmdEl){cmdEl.style.display='block';cmdEl.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;}}
}

export async function applyFirewallRule(){
  const msgEl=document.getElementById('net-firewall-msg');
  if(!confirm('Appliquer la règle pare-feu Windows pour Lumena ? Cette action modifie les paramètres système.'))return;
  if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--muted)';msgEl.textContent='Application en cours…';}
  try{
    const r=await fetch(`${API_BASE}/api/instance/firewall-apply`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({confirmed:true}),
    });
    const d=await r.json();
    if(r.ok&&d.ok){
      if(msgEl){msgEl.style.color='var(--ok)';msgEl.textContent=`✓ Règle "${d.rule_name}" appliquée (port ${d.port}).`;}
    }else{
      if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${d.detail||d.command_output||'Échec'}`;}
    }
  }catch(e){if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${e.message}`;}}
}

export async function pairSelectedPeer(){
  const idEl=document.getElementById('net-action-id');
  const msgEl=document.getElementById('net-action-msg');
  const val=(idEl?.value||'').trim();
  if(!val){if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--danger)';msgEl.textContent='Entrez un instance_id ou host:port.';}return;}
  if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--muted)';msgEl.textContent='Jumelage en cours...';}
  try{
    let body;
    if(val.includes(':')){
      const[host,portStr]=val.split(':');
      const probe=await fetch(`${API_BASE}/api/peer/probe`,{
        method:'POST',
        headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
        body:JSON.stringify({host,port:parseInt(portStr,10),timeout:3}),
      });
      if(!probe.ok){
        const pd=await probe.json();
        if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=pd.detail||'Instance non trouvée à cette adresse.';}
        return;
      }
      const found=await probe.json();
      body={instance_id:found.instance_id,instance_name:found.instance_name,host:found.host,port:found.port,version:found.version,role:found.role,capabilities:found.capabilities};
    }else{
      const pr=await fetch(`${API_BASE}/api/peers`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
      const pd=await pr.json();
      const peer=(pd.peers||[]).find(p=>p.instance_id===val);
      if(!peer){if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent='Pair inconnu. Utilisez host:port pour un nouveau pair.';}return;}
      body={instance_id:peer.instance_id,instance_name:peer.instance_name||'',host:peer.host,port:peer.port,version:peer.version||'',role:peer.role||'standalone',capabilities:peer.capabilities||[]};
    }
    const r=await fetch(`${API_BASE}/api/peers/pair`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify(body),
    });
    const d=await r.json();
    if(r.ok){if(msgEl){msgEl.style.color='var(--ok)';msgEl.textContent=`Pair ${d.instance_id} jumelé (trust=${d.trust}).`;}}
    else{if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${d.detail||'Échec'}.`;}}
    _reloadPeersList();
  }catch(e){if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${e.message}`;}}
}

export async function blockSelectedPeer(){
  const idEl=document.getElementById('net-action-id');
  const msgEl=document.getElementById('net-action-msg');
  const val=(idEl?.value||'').trim();
  if(!val){if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--danger)';msgEl.textContent='Entrez un instance_id.';}return;}
  if(!confirm(`Bloquer le pair ${val} ?`))return;
  if(msgEl){msgEl.style.display='block';msgEl.style.color='var(--muted)';msgEl.textContent='Blocage en cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/peers/block`,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({instance_id:val}),
    });
    const d=await r.json();
    if(r.ok){if(msgEl){msgEl.style.color='var(--ok)';msgEl.textContent=`Pair ${val} bloqué.`;}}
    else{if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${d.detail||'Échec'}.`;}}
    _reloadPeersList();
  }catch(e){if(msgEl){msgEl.style.color='var(--danger)';msgEl.textContent=`Erreur: ${e.message}`;}}
}
