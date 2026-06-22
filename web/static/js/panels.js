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
  ['lumena_rules','readme','heartbeat','mcp_status','mcp_plan','mcp_category','work_method'].forEach(k=>{
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
  // Pastille de NIVEAU (lecture seule ici ; la config se fait en vue avancée).
  const isMission = p.capability_level === 'mission';
  const levelPill = `<span class="pill" style="font-size:10px;${isMission ? 'color:var(--accent);border-color:var(--accent)' : ''}" title="Niveau de capacité (configurable en mode avancé)">${isMission ? '🚀 mission' : 'chat'}</span>`;
  const scopePills = scopes.length
    ? scopes.map(s => `<span class="pill" style="font-size:10px">${esc(s)}</span>`).join(' ')
    : '<span class="pill muted" style="font-size:10px">scope: aucun</span>';
  return `${levelPill} ${scopePills}`;
}

// Bloc A — définition des 7 scopes (libellés + palier mission).
// missionTier=true → grisé tant que le pair est en niveau « chat ».
const _PEER_SCOPE_DEFS = [
  {scope:'chat',            label:'Discuter (chat)',        missionTier:false},
  {scope:'knowledge.query', label:'Lire la mémoire',        missionTier:false},
  {scope:'knowledge.share', label:'Partager un savoir',     missionTier:false},
  {scope:'task.delegate',   label:'Confier une mission',    missionTier:true},
  {scope:'task.status',     label:'Suivre une mission',     missionTier:true},
  {scope:'task.cancel',     label:'Annuler une mission',    missionTier:true},
  {scope:'artifact.share',  label:'Échanger des fichiers',  missionTier:true},
];

function _peerConfigDrawer(p) {
  const iid = esc(p.instance_id);
  const scopes = Array.isArray(p.allowed_scopes) ? p.allowed_scopes : [];
  const isMission = p.capability_level === 'mission';
  const alias = esc(p.alias || '');
  const origin = esc(p.instance_name || p.instance_id);

  // Cases à cocher pour chaque scope (lecture de l'état au moment du bulk-update).
  const scopeRows = _PEER_SCOPE_DEFS.map(d => {
    const checked = scopes.includes(d.scope) ? ' checked' : '';
    const locked = d.missionTier && !isMission;
    const dis = locked ? ' disabled' : '';
    const op = locked ? 'opacity:.45;' : '';
    const hint = locked ? ' title="Passe ce pair en « Mission » pour accorder ce droit"' : '';
    return `<label style="display:flex;align-items:center;gap:6px;font-size:11px;${op}cursor:${locked?'not-allowed':'pointer'}"${hint}>
      <input type="checkbox" data-scope="${d.scope}"${checked}${dis} onchange="setPeerScopesBulk('${iid}')" style="margin:0">
      ${esc(d.label)} <span style="color:var(--muted);font-size:9px">${d.scope}</span></label>`;
  }).join('');

  return `<details class="peer-cfg" style="width:100%;margin-top:6px">
    <summary style="cursor:pointer;font-size:11px;color:var(--accent);list-style:none;user-select:none"><i data-lucide="settings-2" style="width:11px;height:11px;vertical-align:-1px"></i> Configurer</summary>
    <div id="peer-cfg-${iid}" style="margin-top:8px;display:flex;flex-direction:column;gap:10px;padding:8px;background:var(--surface);border:1px solid var(--border);border-radius:8px">

      <div>
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px">Nom</div>
        <div style="display:flex;gap:6px;align-items:center">
          <input id="peer-alias-${iid}" value="${alias}" placeholder="${origin}" style="flex:1;font-size:11px;padding:3px 6px;background:var(--bg,#111);border:1px solid var(--border);border-radius:6px;color:var(--text)">
          <button class="btn" style="font-size:10px;padding:2px 8px" onclick="setPeerAlias('${iid}')">Renommer</button>
        </div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px">Niveau de capacité</div>
        <div style="display:flex;gap:12px;font-size:11px">
          <label style="display:flex;align-items:center;gap:5px;cursor:pointer"><input type="radio" name="lvl-${iid}" value="chat"${isMission?'':' checked'} onchange="setPeerCapability('${iid}','chat')" style="margin:0">🛡️ Chat (lecture)</label>
          <label style="display:flex;align-items:center;gap:5px;cursor:pointer"><input type="radio" name="lvl-${iid}" value="mission"${isMission?' checked':''} onchange="setPeerCapability('${iid}','mission')" style="margin:0">🚀 Mission (complet)</label>
        </div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px">Droits accordés</div>
        <div style="display:flex;flex-direction:column;gap:4px">${scopeRows}</div>
      </div>

      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <button class="btn" style="font-size:10px;padding:2px 8px" onclick="probePeer('${iid}','${esc(p.host||'')}',${p.port||8080})"><i data-lucide="radar" style="width:10px;height:10px"></i> Sonder</button>
        <button class="btn" style="font-size:10px;padding:2px 8px" onclick="revokePeerToken('${iid}')"><i data-lucide="key-round" style="width:10px;height:10px"></i> Révoquer le token</button>
        <span id="peer-cfg-msg-${iid}" style="font-size:10px;color:var(--muted)"></span>
      </div>

      <div style="display:flex;gap:6px;border-top:1px solid var(--border);padding-top:8px">
        <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="blockPeerSimple('${iid}')">Bloquer</button>
        <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple('${iid}')">Supprimer</button>
      </div>
    </div>
  </details>`;
}

function _peerActionsHtml(p, advanced=false) {
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
    // Vue avancée → tiroir de config complet (alias, niveau, 7 scopes, token, sonde).
    if (advanced) {
      return `<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;width:100%">${testBtn}</div>${_peerConfigDrawer(p)}`;
    }
    // Vue simple → minimal (Tester / Bloquer).
    return `${testBtn}
            <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="blockPeerSimple('${iid}')">Bloquer</button>
            <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple('${iid}')">Supprimer</button>`;
  }
  // trusted sans token, ou unknown → proposer jumelage par code
  return `<button class="btn primary" style="font-size:10px;padding:2px 8px" onclick="showSimplePairingForm('${host}',${port})"><i data-lucide="key-round" style="width:10px;height:10px"></i> Jumeler</button>
          <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="blockPeerSimple('${iid}')">Bloquer</button>
          <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger)" onclick="deletePeerSimple('${iid}')">Supprimer</button>`;
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
  const jobs = [];
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
  // Refonte UI : ces widgets bruts ont été remplacés par l'historique unifié.
  // Si aucun n'est présent, on délègue au nouvel historique et on s'arrête.
  if (!knowledgeEl && !tasksEl && !peerSelect) { try { loadPeerHistory(); } catch(_){} return; }
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

export async function setPeerCapability(instanceId, level) {
  // A3 brique 2 : accorde/retire le mode mission (pleine puissance) à un pair.
  if (level === 'mission' && !confirm(
      "Autoriser ce pair à exécuter une MISSION à pleine puissance (agent complet) ?\n\n" +
      "Le pair pourra agir comme si tu lui parlais directement. À n'accorder qu'à une Lumena de confiance (ta flotte).")) {
    return;
  }
  _showNetworkActionMessage('Mise à jour du niveau du pair…', 'muted');
  try {
    const r = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}/capability`, {
      method:'PUT',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({level}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Mise à jour du niveau impossible');
    _showNetworkActionMessage(level === 'mission' ? 'Mode mission accordé.' : 'Repassé en lecture (chat).', 'ok');
    await _refreshNetworkPanels();
  } catch (e) {
    _showNetworkActionMessage(`Erreur: ${e.message}`, 'danger');
    alert(`Erreur: ${e.message}`);
  }
}

// ── Bloc A — Tiroir de config par pair : alias / scopes bulk / token / sonde ──
function _peerCfgMsg(iid, text, tone='muted') {
  const el = document.getElementById(`peer-cfg-msg-${iid}`);
  if (el) { el.textContent = text; el.style.color = `var(--${tone})`; }
}

export async function setPeerAlias(instanceId) {
  const input = document.getElementById(`peer-alias-${instanceId}`);
  const alias = (input?.value || '').trim();
  _peerCfgMsg(instanceId, 'Renommage…');
  try {
    const r = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}/alias`, {
      method:'PUT',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({alias}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Renommage impossible');
    _peerCfgMsg(instanceId, alias ? `Renommé en « ${alias} ».` : 'Nom d’origine rétabli.', 'ok');
    await _refreshNetworkPanels();
  } catch (e) {
    _peerCfgMsg(instanceId, `Erreur: ${e.message}`, 'danger');
  }
}

export async function setPeerScopesBulk(instanceId) {
  // Lit l'état des cases du tiroir et envoie le SET COMPLET (permet le retrait).
  const box = document.getElementById(`peer-cfg-${instanceId}`);
  if (!box) return;
  const next = Array.from(box.querySelectorAll('input[type="checkbox"][data-scope]'))
    .filter(cb => cb.checked && !cb.disabled)
    .map(cb => cb.getAttribute('data-scope'));
  _peerCfgMsg(instanceId, 'Mise à jour des droits…');
  try {
    const r = await fetch(`${API_BASE}/api/peers/${encodeURIComponent(instanceId)}/scopes`, {
      method:'PUT',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({allowed_scopes: next}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Mise à jour des droits impossible');
    // Pas de refresh complet : on garde le tiroir ouvert.
    _peerCfgMsg(instanceId, `Droits : ${(d.allowed_scopes||next).join(', ') || 'aucun'}.`, 'ok');
  } catch (e) {
    _peerCfgMsg(instanceId, `Erreur: ${e.message}`, 'danger');
  }
}

export async function revokePeerToken(instanceId) {
  if (!confirm('Révoquer le token de ce pair ?\n\nIl ne pourra plus déléguer jusqu’à un nouveau jumelage.')) return;
  _peerCfgMsg(instanceId, 'Révocation…');
  try {
    const r = await fetch(`${API_BASE}/api/peer/revoke-token/${encodeURIComponent(instanceId)}`, {
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Révocation impossible');
    _peerCfgMsg(instanceId, 'Token révoqué — re-jumelage requis.', 'ok');
    await _refreshNetworkPanels();
  } catch (e) {
    _peerCfgMsg(instanceId, `Erreur: ${e.message}`, 'danger');
  }
}

export async function probePeer(instanceId, host, port) {
  if (!host) { _peerCfgMsg(instanceId, 'Adresse du pair inconnue.', 'danger'); return; }
  _peerCfgMsg(instanceId, 'Sonde en cours…');
  const t0 = performance.now();
  try {
    const r = await fetch(`${API_BASE}/api/peer/probe`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body:JSON.stringify({host, port: Number(port)||8080, timeout: 3.0}),
    });
    const ms = Math.round(performance.now() - t0);
    if (r.status === 404) { _peerCfgMsg(instanceId, `Injoignable sur ${host}:${port}.`, 'danger'); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Sonde impossible');
    _peerCfgMsg(instanceId, `En ligne — ${ms}ms (${esc(d.instance_name || d.instance_id || 'Lumena')}).`, 'ok');
  } catch (e) {
    _peerCfgMsg(instanceId, `Erreur: ${e.message}`, 'danger');
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

// ── Onboarding : interrupteur maître du réseau (LUMENA_PEER_ENABLED) ─────────
async function _fetchPeerMasterOn() {
  try {
    const r = await fetch(`${API_BASE}/api/config`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if (!r.ok) return false;
    const d = await r.json();
    const item = (d.items||[]).find(i => i.key === 'LUMENA_PEER_ENABLED');
    return item ? String(item.value) === '1' : false;
  } catch(_) { return false; }
}

function _applyMasterUI(on) {
  const cards   = document.getElementById('net-simple-cards');
  const onboard = document.getElementById('net-onboarding');
  const dot     = document.getElementById('net-master-dot');
  const state   = document.getElementById('net-master-state');
  const btn     = document.getElementById('net-master-btn');
  if (cards)   cards.style.display   = on ? 'block' : 'none';
  if (onboard) onboard.style.display = on ? 'none' : 'block';
  if (dot)     { dot.textContent = '●'; dot.style.color = on ? 'var(--ok)' : 'var(--muted)'; }
  if (state)   { state.textContent = on ? '— activé' : '— désactivé'; state.style.color = on ? 'var(--ok)' : 'var(--muted)'; }
  if (btn)     { btn.textContent = on ? 'Désactiver' : 'Activer le réseau'; btn.className = on ? 'btn' : 'btn primary'; }
}

// ── Kill-switch SOFT (urgence) — LUMENA_PEER_HALT ───────────────────────────
async function _fetchPeerHalt() {
  try {
    const r = await fetch(`${API_BASE}/api/peer/halt`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if (!r.ok) return false;
    const d = await r.json();
    return !!d.halt;
  } catch(_) { return false; }
}

function _applyHaltUI(halted) {
  const banner = document.getElementById('net-halt-banner');
  const btn = document.getElementById('net-halt-btn');
  if (banner) banner.style.display = halted ? 'block' : 'none';
  if (btn) { btn.style.display = halted ? 'none' : 'inline-flex'; }
}

export async function togglePeerHalt() {
  const cur = await _fetchPeerHalt();
  const next = !cur;
  if (next && !confirm('Couper le réseau Lumena (urgence) ?\n\nStoppe TOUTE nouvelle activité réseau (délégations entrantes ET sortantes, découverte).\nLes missions EN COURS ne sont PAS interrompues — elles se terminent normalement.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/peer/halt`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body: JSON.stringify({halt: next}),
    });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error('Échec du kill-switch');
    _applyHaltUI(next);
  } catch(e) {
    alert(`Erreur: ${e.message}`);
  }
}

export async function togglePeerMaster() {
  const msgEl = document.getElementById('net-master-msg');
  const cur = await _fetchPeerMasterOn();
  const next = !cur;
  if (!next && !confirm('Désactiver le réseau Lumena ?\n\nTes instances ne se découvriront plus et ne pourront plus collaborer.')) return;
  if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--muted)'; msgEl.textContent='Mise à jour…'; }
  try {
    const r = await fetch(`${API_BASE}/api/config`, {
      method:'PUT',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body: JSON.stringify({updates: {LUMENA_PEER_ENABLED: next ? '1' : '0'}}),
    });
    const d = await r.json().catch(() => ({}));
    if (!d.success) throw new Error(d.error || 'Échec de la mise à jour');
    _applyMasterUI(next);
    if (msgEl) {
      msgEl.style.color = next ? 'var(--ok)' : 'var(--muted)';
      msgEl.textContent = next
        ? (d.needs_restart
            ? 'Réseau activé — chat & collaboration actifs maintenant. Redémarre Lumena pour finaliser la découverte LAN et l’autonomie.'
            : 'Réseau activé.')
        : 'Réseau désactivé.';
    }
    if (next) loadNetworkSimple();
  } catch(e) {
    if (msgEl) { msgEl.style.display='block'; msgEl.style.color='var(--danger)'; msgEl.textContent=`Erreur: ${e.message}`; }
  }
}

export async function loadNetworkSimple() {
  // Onboarding : si le réseau maître est éteint, on affiche l'accueil et on s'arrête.
  const masterOn = await _fetchPeerMasterOn();
  _applyMasterUI(masterOn);
  _fetchPeerHalt().then(_applyHaltUI);  // reflète le kill-switch
  if (!masterOn) { closePeerEventStream(); return; }

  initPeerEventStream();  // Cran 2 : push temps réel des missions (poll Cran 1 = filet)
  _loadQuarantine();      // C-1.b : pairs en quarantaine auto

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
            <div style="font-size:13px;font-weight:500">${esc(p.alias||p.instance_name||p.instance_id)}</div>
            <div style="font-size:11px;color:${color}">${label} <span id="peer-presence-${iid}" style="margin-left:4px"></span></div>
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
  refreshNetworkLive(true);  // Cran 1 : présence réelle + missions en cours (immédiat)
  // L'historique vit dans sa propre vue (bouton « Historique ») — pas chargé ici.
}

// ── Bascule entre les 3 vues réseau : simple / avancé / historique ───────────
function _setNetView(name){
  const views = {
    simple:   document.getElementById('net-simple-view'),
    advanced: document.getElementById('net-advanced-view'),
    history:  document.getElementById('net-history-view'),
  };
  for (const [k, el] of Object.entries(views)) {
    if (el) el.style.display = (k === name) ? 'block' : 'none';
  }
}

export function toggleNetworkAdvanced() {
  const advanced = document.getElementById('net-advanced-view');
  if (!advanced) return;
  const showAdv = advanced.style.display === 'none';
  _setNetView(showAdv ? 'advanced' : 'simple');
  if (showAdv) loadInstancesNetwork();
}

export function showNetworkHistory() {
  _setNetView('history');
  loadPeerHistory();
}

export function backToNetworkSimple() {
  _setNetView('simple');
  loadNetworkSimple();
}

// ── Cran 1 — Liveness par poll (présence réelle + missions en cours) ─────────
let _lastNetLive = 0;

// Pastille de présence à partir du statut santé (/api/peers/health).
function _presenceHtml(hp) {
  const st = hp?.status || 'unknown';
  const lat = hp?.latency_ms;
  if (st === 'healthy') return `<span class="pill ok" style="font-size:9px" title="Joignable"><span style="color:var(--ok)">●</span> en ligne${lat!=null?` · ${lat}ms`:''}</span>`;
  if (st === 'blocked') return `<span class="pill danger" style="font-size:9px"><span style="color:var(--danger)">●</span> bloqué</span>`;
  if (st === 'invalid_host') return `<span class="pill" style="font-size:9px;color:var(--warning,#f59e0b)" title="${esc(hp?.last_error||'')}"><span style="color:var(--warning,#f59e0b)">●</span> adresse</span>`;
  if (st === 'down' || st === 'mismatch_or_down') return `<span class="pill" style="font-size:9px;color:var(--muted)" title="${esc(hp?.last_error||'')}"><span style="color:var(--muted)">○</span> injoignable</span>`;
  return `<span class="pill" style="font-size:9px;color:var(--muted)"><span style="color:var(--muted)">○</span> inconnu</span>`;
}

function _patchPresence(healthPeers) {
  for (const hp of (healthPeers || [])) {
    const el = document.getElementById(`peer-presence-${hp.instance_id}`);
    if (el) el.innerHTML = _presenceHtml(hp);
  }
}

function _missionElapsed(submittedAt) {
  if (!submittedAt) return '';
  const t = new Date(submittedAt).getTime();
  if (isNaN(t)) return '';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}min`;
  return `${Math.floor(s/3600)}h${String(Math.floor((s%3600)/60)).padStart(2,'0')}`;
}

function _renderActiveMissions(missions) {
  const card = document.getElementById('net-missions-card');
  const listEl = document.getElementById('net-active-missions');
  if (!card || !listEl) return;
  if (!missions || !missions.length) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  listEl.innerHTML = missions.map(m => {
    const color = (m.status === 'running') ? 'accent' : 'muted';
    const tid = esc(m.task_id || '');
    return `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)">
      <i data-lucide="rocket" style="width:14px;height:14px;color:var(--accent);flex-shrink:0"></i>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(m.objective||'(mission)')}</div>
        <div style="font-size:10px;color:var(--muted)">→ ${esc(m.peer_name||'?')} · <span class="pill ${color}" style="font-size:9px">${esc(m.status)}</span> · ${_missionElapsed(m.submitted_at)}</div>
      </div>
      <button class="btn" style="font-size:10px;padding:2px 8px;color:var(--danger);flex-shrink:0" onclick="cancelPeerMission('${tid}')" title="Annule cette mission (acte explicite)">Annuler</button>
    </div>`;
  }).join('');
  if (window.lucide) lucide.createIcons();
}

export async function cancelPeerMission(taskId) {
  if (!taskId) return;
  if (!confirm('Annuler cette mission ?\n\nC\'est la seule action qui stoppe une mission en cours. Le pair est prévenu si joignable.')) return;
  try {
    const r = await fetch(`${API_BASE}/api/peer/missions/${encodeURIComponent(taskId)}`, {
      method:'DELETE', headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error(d.detail || 'Échec de l\'annulation');
    _refreshActiveMissions();  // la mission sort des « en cours »
  } catch(e) { alert(`Erreur: ${e.message}`); }
}

export async function refreshNetworkLive(immediate=false) {
  // Throttle : la santé SONDE le réseau → max ~1 fois / 7 s même si appelé toutes les 4 s.
  const now = Date.now();
  if (!immediate && (now - _lastNetLive) < 7000) return;
  _lastNetLive = now;
  const h = {'Authorization': `Bearer ${ADMIN_TOKEN}`};
  try {
    const [healthRes, missionsRes] = await Promise.allSettled([
      fetch(`${API_BASE}/api/peers/health?timeout=1.5`, {headers:h}),
      fetch(`${API_BASE}/api/peer/missions/active`, {headers:h}),
    ]);
    if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
      const d = await healthRes.value.json();
      _patchPresence(d.peers || []);
    }
    if (missionsRes.status === 'fulfilled' && missionsRes.value.ok) {
      const d = await missionsRes.value.json();
      _renderActiveMissions(d.missions || []);
    }
  } catch(_) {}
}

// Rafraîchit UNIQUEMENT la carte « Missions en cours » (léger — pas de sonde santé).
async function _refreshActiveMissions() {
  try {
    const r = await fetch(`${API_BASE}/api/peer/missions/active`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if (!r.ok) return;
    const d = await r.json();
    _renderActiveMissions(d.missions || []);
  } catch(_) {}
}

// ── C-1.b — Quarantaine auto : carte + levée ────────────────────────────────
async function _loadQuarantine() {
  const card = document.getElementById('net-quarantine-card');
  const listEl = document.getElementById('net-quarantine-list');
  if (!card || !listEl) return;
  try {
    const r = await fetch(`${API_BASE}/api/peer/quarantine`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if (!r.ok) { card.style.display='none'; return; }
    const d = await r.json();
    const items = d.quarantined || [];
    if (!items.length) { card.style.display='none'; return; }
    card.style.display = 'block';
    listEl.innerHTML = items.map(q => `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--danger);font-size:14px">●</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500">${esc(q.peer_name||q.peer_id)}</div>
        <div style="font-size:10px;color:var(--muted)">${esc(q.reason||'')}</div>
      </div>
      <button class="btn" style="font-size:10px;padding:2px 8px" onclick="releasePeerQuarantine('${esc(q.peer_id)}')">Lever</button>
    </div>`).join('');
  } catch(_) { card.style.display='none'; }
}

export async function releasePeerQuarantine(instanceId) {
  try {
    const r = await fetch(`${API_BASE}/api/peer/quarantine/release/${encodeURIComponent(instanceId)}`, {
      method:'POST', headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error('Échec de la levée');
    _loadQuarantine();
  } catch(e) { alert(`Erreur: ${e.message}`); }
}

// ── Cran 2 — Flux SSE temps réel des événements pairs (push, pas de poll) ────
let _peerEvtAbort = null;
let _peerEvtDebounce = null;

export function initPeerEventStream() {
  if (_peerEvtAbort) return;  // déjà connecté
  _peerEvtAbort = new AbortController();
  const h = {}; if (ADMIN_TOKEN) h['Authorization'] = `Bearer ${ADMIN_TOKEN}`;
  fetch(`${API_BASE}/api/peer/events`, {headers:h, signal:_peerEvtAbort.signal})
    .then(res => {
      if (!res.ok) throw new Error(res.status);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '', evtType = 'peer';
      function pump() {
        reader.read().then(({done, value}) => {
          if (done) { _peerEvtAbort = null; return; }
          buf += decoder.decode(value, {stream:true});
          const lines = buf.split('\n'); buf = lines.pop();
          for (const line of lines) {
            if (line.startsWith('event:')) evtType = line.slice(6).trim();
            else if (line.startsWith('data:')) {
              const raw = line.slice(5).trim();
              if (evtType === 'heartbeat') { evtType = 'peer'; continue; }
              try {
                const ev = JSON.parse(raw);
                if (ev && ev.type === 'mission') {
                  // Débounce : un burst d'événements → un seul refresh.
                  if (_peerEvtDebounce) clearTimeout(_peerEvtDebounce);
                  _peerEvtDebounce = setTimeout(() => _refreshActiveMissions(), 250);
                } else if (ev && ev.type === 'halt') {
                  _applyHaltUI(!!ev.halt);  // kill-switch live
                } else if (ev && ev.type === 'quarantine') {
                  _loadQuarantine();        // quarantaine auto live
                }
              } catch(_) {}
              evtType = 'peer';
            }
          }
          pump();
        }).catch(() => { _peerEvtAbort = null; });
      }
      pump();
    })
    .catch(() => { _peerEvtAbort = null; });
}

export function closePeerEventStream() {
  if (_peerEvtAbort) { try { _peerEvtAbort.abort(); } catch(_){} _peerEvtAbort = null; }
}

export function showSimplePairingForm(host='', port=8080) {
  // Le jumelage par code vit désormais en vue avancée → on y bascule puis on
  // met en évidence le formulaire.
  const advanced = document.getElementById('net-advanced-view');
  if (advanced && advanced.style.display === 'none') toggleNetworkAdvanced();
  const form = document.getElementById('net-simple-pairing-form');
  if (!form) return;
  form.style.display = 'block';
  const hostEl = document.getElementById('net-pairing-host');
  const portEl = document.getElementById('net-pairing-port');
  if (host && hostEl) hostEl.value = host;
  if (port && portEl) portEl.value = port;
  setTimeout(() => form.scrollIntoView({behavior:'smooth', block:'nearest'}), 60);
}

// ── Historique des échanges Lumena (vue simple, read-only) ──────────────────
let _peerHistoryCache = [];
let _peerHistorySelected = null;

const _PEER_EX_META = {
  delegation:     {icon:'message-square', label:'Question/réponse'},
  knowledge:      {icon:'brain',          label:'Savoir'},
  knowledge_query:{icon:'search',         label:'Savoir demandé'},
  task:           {icon:'list-checks',    label:'Tâche'},
  mission:        {icon:'package',        label:'Mission'},
};

function _peerExMeta(type){ return _PEER_EX_META[type] || {icon:'activity', label:type||'échange'}; }

function _peerExStatusColor(status){
  if (status === 'completed' || status === 'shared') return 'ok';
  if (['failed','timeout','cancelled','refused','error','interrupted'].includes(status)) return 'danger';
  if (['running','queued'].includes(status)) return 'accent';
  return 'muted';
}

function _peerExTime(ts){
  if (!ts) return '';
  try { const d = new Date(ts); if (!isNaN(d)) return d.toLocaleString(); } catch(_){}
  return ts;
}

export async function loadPeerHistory(){
  const listEl = document.getElementById('net-history-list');
  if (!listEl) return;
  listEl.innerHTML = '<div style="color:var(--muted)">Chargement…</div>';
  try {
    const r = await fetch(`${API_BASE}/api/peer/history?limit=200`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const data = r.ok ? await r.json() : {exchanges:[], stats:{}};
    _peerHistoryCache = Array.isArray(data.exchanges) ? data.exchanges : [];
    _renderPeerHistoryStats(data.stats || {});
  } catch(_) {
    _peerHistoryCache = [];
  }
  filterPeerHistory();
}

function _renderPeerHistoryStats(stats){
  const el = document.getElementById('net-history-stats');
  if (!el) return;
  const card = (label, val) => `<div class="card" style="padding:8px 10px"><div style="font-size:18px;font-weight:700;color:var(--accent)">${val||0}</div><div style="font-size:10px;color:var(--muted)">${label}</div></div>`;
  el.innerHTML = card('Échanges', stats.total) + card('Terminés', stats.completed) + card('Savoirs', stats.knowledge);
}

// Statuts considérés comme « bruit » (propositions non finalisées) → masquables.
const _PEER_EX_NOISE = new Set(['proposed']);

export function filterPeerHistory(){
  const listEl = document.getElementById('net-history-list');
  if (!listEl) return;
  const q = (document.getElementById('net-history-search')?.value || '').toLowerCase().trim();
  const typeF = document.getElementById('net-history-type-filter')?.value || '';
  const hideNoise = !!document.getElementById('net-history-hide-noise')?.checked;
  const rows = _peerHistoryCache.filter(ex => {
    const t = ex.type === 'knowledge_query' ? 'knowledge' : ex.type;
    if (typeF && t !== typeF) return false;
    if (hideNoise && _PEER_EX_NOISE.has(ex.status)) return false;
    if (q) {
      const hay = `${ex.title||''} ${ex.peer_name||''} ${ex.type||''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (!rows.length) {
    listEl.innerHTML = '<div style="color:var(--muted);padding:12px;font-size:12px">Aucun échange pour l\'instant. Quand tes Lumena se parleront, l\'historique s\'affichera ici.</div>';
    const d = document.getElementById('net-history-detail');
    if (d) d.innerHTML = '<div class="sessions-empty-detail"><i data-lucide="message-square-text"></i><div>Aucun échange à afficher.</div></div>';
    if (window.lucide) lucide.createIcons();
    return;
  }

  // Sélection par défaut : si rien de valide n'est sélectionné, on prend le plus récent.
  if (!rows.some(ex => ex.id === _peerHistorySelected)) {
    _peerHistorySelected = rows[0].id;
    _renderExchangeDetail(_peerHistorySelected);
  }

  // Regroupement par Lumena (les rows sont déjà triées du plus récent au plus ancien).
  const groups = new Map();
  for (const ex of rows) {
    const name = ex.peer_name || '?';
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(ex);
  }
  const rowHtml = (ex) => {
    const m = _peerExMeta(ex.type);
    const color = _peerExStatusColor(ex.status);
    const dir = ex.direction === 'outbound' ? '→' : '←';
    const sel = _peerHistorySelected === ex.id ? 'background:var(--surface);' : '';
    return `<div onclick="selectPeerExchange('${esc(ex.id)}')" style="cursor:pointer;padding:8px 10px;border-bottom:1px solid var(--border);${sel}border-radius:6px">
      <div style="display:flex;align-items:center;gap:8px">
        <i data-lucide="${m.icon}" style="width:14px;height:14px;color:var(--accent);flex-shrink:0"></i>
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(ex.title||m.label)}</div>
          <div style="font-size:10px;color:var(--muted)">${dir} ${esc(ex.peer_name||'?')} · <span style="color:var(--${color})">${esc(ex.status||'')}</span></div>
        </div>
      </div>
    </div>`;
  };
  listEl.innerHTML = Array.from(groups.entries()).map(([name, exs]) => {
    const done = exs.filter(e => e.status === 'completed' || e.status === 'shared').length;
    return `<div style="margin-bottom:4px">
      <div style="display:flex;align-items:center;gap:6px;padding:6px 10px;font-size:11px;color:var(--muted);background:var(--surface);border-radius:6px;position:sticky;top:0">
        <i data-lucide="bot" style="width:12px;height:12px;color:var(--accent)"></i>
        <b style="color:var(--text)">${esc(name)}</b> · ${exs.length} échange${exs.length>1?'s':''} · ${done} ✅
      </div>
      ${exs.map(rowHtml).join('')}
    </div>`;
  }).join('');
  if (window.lucide) lucide.createIcons();
}

function _renderExchangeDetail(id){
  const ex = _peerHistoryCache.find(e => e.id === id);
  const detailEl = document.getElementById('net-history-detail');
  if (!detailEl) return;
  if (!ex) { detailEl.innerHTML = '<div class="sessions-empty-detail"><div>Échange introuvable.</div></div>'; return; }
  const m = _peerExMeta(ex.type);
  const dirLabel = ex.direction === 'outbound' ? 'Envoyé à' : 'Reçu de';
  const items = (ex.items||[]).map(it => {
    const color = _peerExStatusColor(it.status);
    return `<div style="padding:6px 0;border-bottom:1px solid var(--border)">
      <div style="font-size:11px;color:var(--muted)">${_peerExTime(it.ts)} · <span style="color:var(--${color})">${esc(it.status||it.event||'')}</span></div>
      ${it.detail ? `<div style="font-size:12px;margin-top:2px">${esc(it.detail)}</div>` : ''}
    </div>`;
  }).join('');
  // Mission sortante → bouton « Voir les livrables » (C2.2a).
  const taskId = (ex.type === 'mission' && typeof ex.id === 'string' && ex.id.indexOf('mission:') === 0)
    ? ex.id.slice('mission:'.length) : '';
  const artifactsHint = (ex.type === 'mission' && ex.direction === 'outbound' && taskId)
    ? `<div style="margin-top:10px">
         <button class="btn" style="font-size:11px" onclick="loadDeliverables('${esc(taskId)}','net-deliv-${esc(taskId)}')"><i data-lucide="package" style="width:12px;height:12px"></i> Voir les livrables</button>
         <div id="net-deliv-${esc(taskId)}" style="margin-top:8px;font-size:11px"></div>
       </div>`
    : '';
  // C2.2c — relancer une mission échouée / refus→approuver.
  let relaunchSection = '';
  if (ex.type === 'mission' && ex.direction === 'outbound' && taskId) {
    if (ex.status === 'refused') {
      relaunchSection = `<div style="margin-top:8px"><button class="btn primary" style="font-size:11px" onclick="relaunchPeerMission('${esc(taskId)}', true)"><i data-lucide="rocket" style="width:12px;height:12px"></i> Passer en mission & relancer</button></div>`;
    } else if (['failed','cancelled','timeout','interrupted'].includes(ex.status)) {
      relaunchSection = `<div style="margin-top:8px"><button class="btn" style="font-size:11px" onclick="relaunchPeerMission('${esc(taskId)}', false)"><i data-lucide="rotate-cw" style="width:12px;height:12px"></i> Relancer</button></div>`;
    }
  }
  detailEl.innerHTML = `<div style="padding:12px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <i data-lucide="${m.icon}" style="width:16px;height:16px;color:var(--accent)"></i>
      <div style="font-size:13px;font-weight:600">${esc(ex.title||m.label)}</div>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:10px">${m.label} · ${dirLabel} <b>${esc(ex.peer_name||'?')}</b> · ${_peerExTime(ex.last_ts)}</div>
    <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Déroulé</div>
    ${items || '<div style="color:var(--muted);font-size:12px">Aucun détail.</div>'}
    ${artifactsHint}
    ${relaunchSection}
  </div>`;
  if (window.lucide) lucide.createIcons();
}

export async function relaunchPeerMission(taskId, escalate) {
  if (!taskId) return;
  const msg = escalate
    ? 'Passer ce pair en MISSION (pleine puissance) et relancer la mission ?\n\nLe pair pourra agir comme si tu lui parlais directement.'
    : 'Relancer cette mission (pleine puissance) ?';
  if (!confirm(msg)) return;
  try {
    const r = await fetch(`${API_BASE}/api/peer/missions/relaunch`, {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},
      body: JSON.stringify({task_id: taskId, escalate_capability: !!escalate}),
    });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) throw new Error(d.error || d.detail || 'Échec de la relance');
    alert('Mission relancée — elle apparaît dans « Missions en cours ».');
    _refreshActiveMissions();
  } catch(e) { alert(`Erreur: ${e.message}`); }
}

function _fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} o`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} Ko`;
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`;
}

export async function loadDeliverables(taskId, containerId) {
  const el = document.getElementById(containerId);
  if (!el || !taskId) return;
  el.innerHTML = '<span style="color:var(--muted)">Chargement…</span>';
  try {
    const r = await fetch(`${API_BASE}/api/peer/deliverables?task_id=${encodeURIComponent(taskId)}`, {headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'Erreur');
    const files = d.files || [];
    if (!files.length) { el.innerHTML = '<span style="color:var(--muted)">Aucun fichier reçu (ou dossier vide).</span>'; return; }
    el.innerHTML = `<div style="color:var(--muted);margin-bottom:4px">${files.length} fichier(s) — <code style="font-size:10px">${esc(d.dir||'')}</code></div>`
      + files.map(f => `<div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px solid var(--border)"><span style="word-break:break-all">${esc(f.name)}</span><span style="color:var(--muted);flex-shrink:0">${_fmtBytes(f.size)}</span></div>`).join('');
  } catch(e) {
    el.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
  }
}

export function selectPeerExchange(id){
  _peerHistorySelected = id;
  _renderExchangeDetail(id);
  filterPeerHistory(); // rafraîchit la surbrillance de la liste (sans re-sélectionner)
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
  if(selfEl)selfEl.innerHTML=loadingDots('Chargement...');
  if(localEl)localEl.innerHTML=loadingDots('Chargement...');
  if(peersEl)peersEl.innerHTML=loadingDots('Chargement...');
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
              <strong>${esc(p.alias||p.instance_name||p.instance_id)}</strong>
              <span style="color:var(--muted)">— ${esc(p.host)}:${p.port}</span>
              <span id="peer-presence-${esc(p.instance_id)}"></span>
            </div>
            <div style="color:var(--muted)">${esc(p.instance_id)}</div>
            <div>${(p.capabilities||[]).map(c=>`<span class="pill" style="font-size:10px">${esc(c)}</span>`).join(' ')||''}</div>
            <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${_peerScopesHtml(p)}</div>
            <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;align-items:center">${_peerActionsHtml(p, true)}</div>
          </div>
        </div>`).join('');
      }
    }
  }catch(e){
    if(peersEl)peersEl.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
  // L'audit inter-instances est désormais agrégé dans la vue « Historique ».
  refreshNetworkLive(true);  // Cran 1 : présence réelle des pairs (immédiat)
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
        resultEl.innerHTML=d.peers.map(p=>`<div style="margin-bottom:4px"><strong>${esc(p.alias||p.instance_name||p.instance_id)}</strong> — ${esc(p.host)}:${p.port} <span class="pill muted" style="font-size:10px">${esc(p.trust)}</span></div>`).join('');
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
              <strong>${esc(p.alias||p.instance_name||p.instance_id)}</strong>
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

/* ============================================================
   MCP — Phase 20A read-only
   ============================================================ */

let _mcpCurrentTab='catalog';

let _mcpLiveMode=false;
window._mcpLiveMode=false;
let _mcpTrustLiveMode=false;
window._mcpTrustLiveMode=false;

export async function loadMcp(){
  const stats=document.getElementById('mcp-header-stats');
  if(stats)stats.innerHTML='<div style="color:var(--muted);font-size:12px">Chargement...</div>';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/health`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    _mcpLiveMode=!!d.live_mode;
    window._mcpLiveMode=_mcpLiveMode;
    _mcpTrustLiveMode=!!d.trust_live_mode;
    window._mcpTrustLiveMode=_mcpTrustLiveMode;
    if(stats){
      const comps=d.components||{};
      const pillFor=(name,info)=>{
        const ok=info&&info.available;
        return `<span class="pill ${ok?'ok':'muted'}" style="margin-right:6px">${name}: ${ok?'OK':'OFF'}</span>`;
      };
      const liveBanner=_mcpLiveMode
        ? `<span class="pill ok" style="margin-right:6px">LIVE</span>`
        : `<span class="pill warn" style="margin-right:6px" title="LUMENA_MCP_LIVE non défini — toutes les actions sont simulées (dry_run forcé)">DRY_RUN forcé</span>`;
      stats.innerHTML=`
        <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:12px">
          ${liveBanner}
          ${pillFor('catalog',comps.catalog)}
          ${pillFor('approval_queue',comps.approval_queue)}
          ${pillFor('watcher',comps.watcher)}
          ${pillFor('discovery',comps.discovery)}
        </div>`;
    }
    _mcpRenderLiveBanner();
    _mcpUpdateBadgeFromCatalog();
  }catch(e){
    if(stats)stats.innerHTML=`<div style="color:var(--danger);font-size:12px">Erreur: ${esc(e.message)}</div>`;
  }
  loadMcpTab(_mcpCurrentTab||'library');
}
window.loadMcp=loadMcp;

function _mcpRenderLiveBanner(){
  const panel=document.getElementById('panel-mcp');
  if(!panel)return;
  let banner=document.getElementById('mcp-live-banner');
  if(_mcpLiveMode){
    if(banner)banner.remove();
    return;
  }
  const html='<div id="mcp-live-banner" style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);color:var(--text);padding:8px 12px;border-radius:6px;margin:8px 0;font-size:12px;display:flex;align-items:center;gap:8px"><i data-lucide="alert-triangle" style="width:14px;height:14px;color:var(--warn,#e0a23a)"></i><span><strong>Mode dry_run forcé</strong> — LUMENA_MCP_LIVE n\'est pas activé. Les actions sont simulées sans effet réel sur la queue MCP.</span></div>';
  if(banner){
    banner.outerHTML=html;
  }else{
    const first=panel.firstElementChild;
    if(first){first.insertAdjacentHTML('beforebegin',html);}
    else{panel.insertAdjacentHTML('afterbegin',html);}
  }
  if(typeof lucide!=='undefined')lucide.createIcons();
}

async function _mcpUpdateBadgeFromCatalog(){
  const badge=document.getElementById('badge-mcp');if(!badge)return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)return;
    const d=await r.json();
    const servers=d.servers||[];
    const active=servers.filter(s=>s.status==='active').length;
    const installed=servers.filter(s=>s.status==='installed').length;
    // Le badge reflète les MCPs PRÉSENTS (actifs + installés), pas
    // seulement les actifs — sinon un MCP installé affichait 0.
    const total=active+installed;
    badge.textContent=total;
    badge.style.background=active>0?'var(--ok)':(total>0?'var(--warn,#e0a23a)':'var(--muted)');
  }catch(_){}
}

export function loadMcpTab(tabKey){
  _mcpCurrentTab=tabKey||'library';
  document.querySelectorAll('#panel-mcp .tab').forEach(t=>{
    t.classList.toggle('active',t.dataset.arg===_mcpCurrentTab);
  });
  switch(_mcpCurrentTab){
    case'library':_loadMcpLibrary();break;
    case'catalog':_loadMcpCatalog();break;
    case'approvals':_loadMcpApprovals();break;
    case'watcher':_loadMcpWatcher();break;
    case'audit':_loadMcpAuditDiscovery();break;
    case'auto_approve':_loadMcpAutoApprove();break;
    case'diagnostics':_loadMcpDiagnostics();break;
  }
}
window.loadMcpTab=loadMcpTab;

// ── Phase G : Bibliothèque MCP (user-facing, click → chat draft) ─────────────
let _mcpLibraryFilter='all';
let _mcpLibrarySearch='';

async function _loadMcpLibrary(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Chargement de la bibliothèque…</div></div>';
  let data=null;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/library`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    data=await r.json();
  }catch(e){
    box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--danger)">Erreur chargement bibliothèque : ${esc(e.message)}</div></div>`;
    return;
  }
  _mcpRenderLibrary(box,data);
}

function _mcpRenderLibrary(box,data){
  const counts=data.counts||{};
  const installed=Array.isArray(data.installed)?data.installed:[];
  const curated=Array.isArray(data.curated)?data.curated:[];
  // Unifie : installed + curated_only (non encore installés)
  const installedSpecs=new Set(installed.map(i=>i.package_spec));
  const curatedOnly=curated.filter(c=>!installedSpecs.has(c.package_spec));
  // Section compteurs
  const countHtml=`
    <div class="card" style="margin-bottom:8px">
      <div class="card-content" style="display:flex;flex-wrap:wrap;gap:10px;justify-content:space-around;font-size:12px">
        ${_mcpCountPill('Actifs',counts.active||0,'ok')}
        ${_mcpCountPill('Installés',counts.installed||0,'')}
        ${_mcpCountPill('Déclarés',counts.declared||0,'muted')}
        ${_mcpCountPill('Quarantine',counts.quarantined||0,'warn')}
        ${_mcpCountPill('Catalogue user',counts.curated||0,'muted')}
      </div>
    </div>`;
  // Filtres + recherche
  const filterHtml=`
    <div class="card" style="margin-bottom:8px">
      <div class="card-content" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <div style="display:flex;gap:4px;flex-wrap:wrap">
          ${_mcpFilterChip('all','Tous')}
          ${_mcpFilterChip('active','Actifs')}
          ${_mcpFilterChip('installed','Installés')}
          ${_mcpFilterChip('available','Disponibles')}
        </div>
        <input id="mcp-library-search" type="text" placeholder="Rechercher un MCP…" value="${esc(_mcpLibrarySearch)}"
          style="flex:1;min-width:140px;padding:5px 10px;border-radius:4px;border:1px solid var(--border);background:var(--card-bg);color:var(--text);font-size:12px"
          oninput="window._mcpLibrarySearchInput(this.value)"/>
      </div>
    </div>`;
  // Cards
  const cards=[];
  for(const e of installed){cards.push(_mcpLibraryCard(e,/*curated*/false));}
  for(const c of curatedOnly){cards.push(_mcpLibraryCard(c,/*curated*/true));}
  const filtered=cards.filter(c=>_mcpLibraryAccepts(c.entry,c.curated));
  const cardsHtml=filtered.length>0
    ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px">${filtered.map(c=>c.html).join('')}</div>`
    : `<div class="card"><div class="card-content" style="color:var(--muted);text-align:center;padding:20px">Aucun MCP ne correspond à votre filtre. Demandez à Lumena d'en chercher un sur le web.</div></div>`;

  box.innerHTML=countHtml+filterHtml+cardsHtml;
  if(typeof lucide!=='undefined')lucide.createIcons();
}

function _mcpCountPill(label,n,kind){
  const cls=kind?`pill ${kind}`:'pill';
  return `<div style="text-align:center"><div style="font-size:20px;font-weight:700">${n}</div><div style="color:var(--muted);font-size:11px">${esc(label)}</div></div>`;
}

function _mcpFilterChip(key,label){
  const active=_mcpLibraryFilter===key;
  return `<button type="button" class="btn ${active?'primary':''}" style="font-size:11px;padding:4px 10px" onclick="window._mcpLibrarySetFilter('${key}')">${esc(label)}</button>`;
}

function _mcpLibraryAccepts(entry,curated){
  const q=_mcpLibrarySearch.trim().toLowerCase();
  if(q){
    const hay=(`${entry.display_name||''} ${entry.package_spec||''} ${entry.semantic_category||''} ${entry.server_id||''}`).toLowerCase();
    if(!hay.includes(q))return false;
  }
  switch(_mcpLibraryFilter){
    case'active':return !curated && entry.status==='active';
    case'installed':return !curated && (entry.status==='installed'||entry.status==='active');
    case'available':return curated || entry.status==='declared';
    case'all':
    default:return true;
  }
}

function _mcpLibraryCard(entry,curated){
  const isActive=entry.status==='active';
  const statusPill=curated
    ? `<span class="pill muted">disponible</span>`
    : isActive
      ? `<span class="pill ok">actif</span>`
      : entry.status==='installed'
        ? `<span class="pill">installé</span>`
        : `<span class="pill muted">${esc(entry.status||'?')}</span>`;
  const cat=entry.semantic_category||'mcp';
  const preferBadge=entry.prefer_over_native
    ? `<span class="pill warn" title="L'utilisateur préfère ce MCP au natif">★ prioritaire</span>`
    : '';
  const display=entry.display_name||entry.server_id||entry.package_spec||'(sans nom)';
  const sub=entry.package_spec||'';
  const chatHint=curated
    ? `Installer ce MCP : « ajoute ${sub} »`
    : isActive
      ? `Désactiver : « désactive le MCP ${entry.server_id||''} »`
      : `Activer : « active le MCP ${entry.server_id||''} »`;
  const html=`
    <div class="card" style="cursor:default">
      <div class="card-content">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px;margin-bottom:6px">
          <div style="font-weight:600;font-size:13px;line-height:1.2">${esc(display)}</div>
          ${statusPill}
        </div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px;word-break:break-all">${esc(sub)}</div>
        <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">
          <span class="pill muted">${esc(cat)}</span>
          ${preferBadge}
        </div>
        <div style="font-size:11px;color:var(--muted);padding:6px;background:var(--bg);border-radius:4px;margin-bottom:6px">
          <i data-lucide="message-circle" style="width:11px;height:11px"></i> ${esc(chatHint)}
        </div>
        ${(!curated && entry.server_id) ? `<button type="button" class="btn" style="font-size:11px;padding:4px 10px;width:100%;margin-bottom:4px"
          onclick="window._mcpOpenConfigModal(${JSON.stringify(entry.server_id).replace(/"/g,'&quot;')})">
          <i data-lucide="key-round" style="width:11px;height:11px"></i> Configurer (clés / config)
        </button>` : ''}
        <button type="button" class="btn" style="font-size:11px;padding:4px 10px;width:100%"
          onclick='window._mcpLibraryPrefillChat(${JSON.stringify(chatHint).replace(/'/g,"&#39;")})'>
          <i data-lucide="send" style="width:11px;height:11px"></i> Demander à Lumena
        </button>
      </div>
    </div>`;
  return {entry,curated,html};
}

window._mcpLibrarySetFilter=function(k){
  _mcpLibraryFilter=k;
  _loadMcpLibrary();
};
window._mcpLibrarySearchInput=function(v){
  _mcpLibrarySearch=String(v||'');
  // Rerender sans refetch
  const box=document.getElementById('mcp-tab-content');
  if(!box)return;
  // On re-render uniquement la grille pour ne pas perdre le focus de l'input.
  // Simple : on garde l'input affiché et on relance _loadMcpLibrary après debounce.
  clearTimeout(window._mcpLibrarySearchTimer);
  window._mcpLibrarySearchTimer=setTimeout(()=>_loadMcpLibrary(),250);
};
window._mcpLibraryPrefillChat=function(text){
  const input=document.getElementById('user-input')||document.querySelector('textarea#chat-input')||document.querySelector('input.chat-input');
  if(input){
    input.value=text;
    input.focus();
    if(typeof input.dispatchEvent==='function'){
      input.dispatchEvent(new Event('input',{bubbles:true}));
    }
  }else{
    // Fallback clipboard
    try{navigator.clipboard.writeText(text);}catch(_){ }
  }
};

// ── Phase I-6 : MCPConfigModal dynamique ─────────────────────────────────
// Composant générique : prend un server_id, fetch schema + status, render
// un formulaire adapté (text/password/select) selon kind/sensitivity, et
// sauvegarde via PUT /api/mcp/library/{sid}/secrets|config/{key}.

window._mcpOpenConfigModal=async function(serverId){
  if(!serverId) return;
  // Crée l'overlay si absent
  let host=document.getElementById('mcp-config-modal-host');
  if(!host){
    host=document.createElement('div');
    host.id='mcp-config-modal-host';
    document.body.appendChild(host);
  }
  // Style harmonisé avec les modals Lumena (cf openIonosDbModal) :
  // card + card-title (icône lucide) + card-content + footer boutons.
  host.innerHTML=`<div id="mcp-config-modal-backdrop" style="position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px">
    <div class="card" style="width:min(520px,92vw);max-height:88vh;overflow-y:auto;overflow-x:hidden;margin:0">
      <div class="card-title"><i data-lucide="key-round"></i> Configuration MCP — ${esc(serverId)}</div>
      <div class="card-content">
        <div id="mcp-config-modal-body" style="font-size:12px;color:var(--muted)">Chargement…</div>
      </div>
    </div>
  </div>`;
  const backdrop=document.getElementById('mcp-config-modal-backdrop');
  if(backdrop)backdrop.addEventListener('click',(e)=>{if(e.target===backdrop)window._mcpCloseConfigModal();});
  if(typeof lucide!=='undefined')lucide.createIcons();
  // ESC pour fermer
  document.addEventListener('keydown',_mcpConfigModalEsc);
  // Fetch schema + status
  try{
    const [sR,statusR]=await Promise.all([
      fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/schema`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
      fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/config-status`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
    ]);
    const sd=await sR.json();
    const stat=statusR.ok?await statusR.json():{status:{},ready:false,missing:[]};
    _mcpRenderConfigModal(serverId,sd,stat);
  }catch(e){
    const body=document.getElementById('mcp-config-modal-body');
    if(body)body.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
};

window._mcpCloseConfigModal=function(){
  const host=document.getElementById('mcp-config-modal-host');
  if(host)host.innerHTML='';
  document.removeEventListener('keydown',_mcpConfigModalEsc);
};

function _mcpConfigModalEsc(ev){
  if(ev&&ev.key==='Escape')window._mcpCloseConfigModal();
}

function _mcpRenderConfigModal(serverId,schemaPayload,statusPayload){
  const body=document.getElementById('mcp-config-modal-body');if(!body)return;
  const schema=schemaPayload&&schemaPayload.schema;
  const source=(schemaPayload&&schemaPayload.source)||'none';
  if(!schema||!Array.isArray(schema.fields)||schema.fields.length===0){
    body.innerHTML=`<div style="padding:16px;text-align:center">
      <div style="font-size:28px;margin-bottom:6px">${schema?'✅':'❓'}</div>
      <div style="font-weight:600;margin-bottom:6px">${schema?'Aucune configuration requise':'Schéma inconnu'}</div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:10px">
        ${schema?'Ce MCP fonctionne sans configuration.':"Aucun schéma curated n'est connu pour ce MCP. Tu peux demander à Lumena de l'auto-détecter."}
      </div>
      ${!schema?`<button class="btn primary" style="font-size:11px;padding:5px 12px" onclick="window._mcpDetectSchema('${esc(serverId)}')">
        <i data-lucide="search"></i> Détecter automatiquement
      </button>`:''}
    </div>`;
    if(typeof lucide!=='undefined')lucide.createIcons();
    return;
  }
  const status=(statusPayload&&statusPayload.status)||{};
  const ready=!!(statusPayload&&statusPayload.ready);
  // Groupage par 'group' (Authentification / Connexion / Configuration / autres)
  const groups={};
  for(const f of schema.fields){
    const g=f.group||'Configuration';
    (groups[g]=groups[g]||[]).push(f);
  }
  const fieldRows=Object.keys(groups).map(g=>{
    const rows=groups[g].map(f=>_mcpRenderConfigField(serverId,f,status[f.name]||'missing')).join('');
    return `<div style="margin-top:12px"><div style="font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:0.5px;margin-bottom:6px">${esc(g)}</div>${rows}</div>`;
  }).join('');
  // Style harmonisé Lumena (cf openIonosDbModal) : intro grise, champs avec
  // labels simples, footer Annuler/Enregistrer global à droite.
  body.innerHTML=`
    <p style="color:var(--muted);font-size:12px;margin-top:0;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      <span class="pill ${ready?'ok':'warn'}">${ready?'Prêt à activer':'Configuration incomplète'}</span>
      <span class="pill muted">détecté : ${esc(source)}</span>
    </p>
    ${fieldRows}
    <p style="font-size:11px;color:var(--muted);margin-top:14px;margin-bottom:0">Les secrets sont chiffrés (Fernet) côté serveur et ne sont jamais réaffichés. Laisse un champ vide pour conserver la valeur actuelle.</p>
    <div id="mcp-config-modal-msg" style="display:none;margin-top:10px;padding:6px 12px;border-radius:6px;font-size:12px"></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="window._mcpCloseConfigModal()">Fermer</button>
      <button class="btn primary" style="font-size:12px;padding:6px 18px" onclick="window._mcpSaveAllFields('${esc(serverId)}')"><i data-lucide="save"></i> Enregistrer</button>
    </div>`;
  if(typeof lucide!=='undefined')lucide.createIcons();
}

function _mcpRenderConfigField(serverId,field,statusVal){
  const isSecret=(field.sensitivity==='secret');
  const isSet=(statusVal==='set');
  const inputType=isSecret?'password':'text';
  const fieldId=`mcp-cfg-${serverId}-${field.name}`;
  const placeholder=isSet?(isSecret?'••••••••  (définie — vide = inchangée)':(field.placeholder||field.default||'')):(field.placeholder||field.default||'');
  const help=field.obtained_from?`<div style="font-size:10px;color:var(--muted);margin-top:3px">${esc(field.obtained_from)}</div>`:'';
  const inS='width:100%;height:32px;font-size:12px;padding:0 8px;box-sizing:border-box';
  return `<div style="margin-bottom:10px">
    <label style="font-size:11px;color:var(--muted);display:flex;align-items:center;justify-content:space-between;margin-bottom:3px">
      <span>${esc(field.label||field.name)}${field.required?' <span style="color:var(--danger)">*</span>':''}</span>
      <span style="display:flex;align-items:center;gap:6px">
        <span class="pill ${isSet?'ok':'muted'}" style="font-size:10px">${isSet?'définie':'vide'}</span>
        ${isSet?`<i data-lucide="trash-2" style="width:13px;height:13px;cursor:pointer;color:var(--muted)" title="Effacer"
          onclick="window._mcpDeleteField('${esc(serverId)}','${esc(field.name)}','${isSecret?'secrets':'config'}')"></i>`:''}
      </span>
    </label>
    <input id="${esc(fieldId)}" class="input" type="${inputType}" placeholder="${esc(placeholder)}"
      data-mcp-key="${esc(field.name)}" data-mcp-kind="${isSecret?'secrets':'config'}" style="${inS}"/>
    <div style="font-size:10px;color:var(--muted);margin-top:3px">${esc(field.description||'')}</div>
    ${help}
  </div>`;
}

// Sauvegarde globale (style modal IONOS) : PUT chaque champ NON VIDE,
// les champs laissés vides conservent leur valeur actuelle.
window._mcpSaveAllFields=async function(serverId){
  const msg=document.getElementById('mcp-config-modal-msg');
  const inputs=Array.from(document.querySelectorAll(`#mcp-config-modal-body input[data-mcp-key]`));
  const toSave=inputs.filter(i=>(i.value||'').trim()!=='');
  if(!toSave.length){
    if(msg){msg.style.display='block';msg.style.color='var(--muted)';msg.textContent='Aucun champ rempli — rien à enregistrer.';}
    return;
  }
  const errors=[];
  for(const input of toSave){
    const key=input.dataset.mcpKey;
    const kind=input.dataset.mcpKind;
    try{
      const r=await fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/${kind}/${encodeURIComponent(key)}`,{
        method:'PUT',
        headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
        body:JSON.stringify({value:input.value}),
      });
      if(!r.ok)throw new Error(`HTTP ${r.status}`);
    }catch(e){errors.push(`${key}: ${e.message}`);}
  }
  if(errors.length){
    if(msg){msg.style.display='block';msg.style.color='var(--danger)';msg.textContent='Erreur — '+errors.join(' · ');}
    return;
  }
  // Re-render : statuts à jour (pills "définie", bandeau Prêt à activer)
  window._mcpOpenConfigModal(serverId);
};

window._mcpSaveField=async function(serverId,key,kind,inputId){
  const input=document.getElementById(inputId);if(!input)return;
  const value=input.value;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/${kind}/${encodeURIComponent(key)}`,{
      method:'PUT',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({value}),
    });
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    // Re-render
    window._mcpOpenConfigModal(serverId);
  }catch(e){
    alert('Erreur sauvegarde : '+e.message);
  }
};

window._mcpDeleteField=async function(serverId,key,kind){
  if(!confirm(`Effacer ${key} ?`))return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/${kind}/${encodeURIComponent(key)}`,{
      method:'DELETE',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`},
    });
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    window._mcpOpenConfigModal(serverId);
  }catch(e){
    alert('Erreur suppression : '+e.message);
  }
};

window._mcpDetectSchema=async function(serverId){
  try{
    const r=await fetch(`${API_BASE}/api/mcp/library/${encodeURIComponent(serverId)}/detect-schema`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({intent:serverId}),
    });
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    window._mcpOpenConfigModal(serverId);
  }catch(e){
    alert('Erreur détection : '+e.message);
  }
};

async function _loadMcpCatalog(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  // Phase 20B-4 : purge sessionStorage cohérente avec autres onglets
  try{ _mcpPurgeExpiredInstallState(); }catch(_){ }
  box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Chargement...</div></div>';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog?include_removed=false`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.available===false){
      box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--muted)">Module MCP Catalog non chargé (${esc(d.reason||'unknown')}).</div></div>`;
      return;
    }
    const servers=d.servers||[];
    if(!servers.length){
      box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted);padding:30px;text-align:center;font-size:13px">Aucun server MCP enregistré dans le Catalog.</div></div>';
      return;
    }
    const statusPill=(s)=>{
      const map={active:'ok',installed:'warn',declared:'muted',quarantined:'danger',removed:'muted'};
      const cls=map[s]||'muted';
      return `<span class="pill ${cls}">${esc((s||'').toUpperCase())}</span>`;
    };
    const drySuffixHdr=window._mcpLiveMode?'':' <span style="color:var(--muted);font-size:10px">(dry_run)</span>';
    let html=`<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
        <button class="btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="openMcpCatalogAddModal()">+ Ajouter server${drySuffixHdr}</button>
      </div>`;
    html+='<div class="list">';
    for(const s of servers){
      const trust=(s.trust_score!=null)?s.trust_score:'—';
      const lastActive=s.last_active_at?esc(s.last_active_at).substring(0,19).replace('T',' '):'jamais';
      const statusLower=(s.status||'').toLowerCase();
      const isDeclared=statusLower==='declared';
      const isInstalled=statusLower==='installed';
      const isActive=statusLower==='active';
      const isQuarantined=statusLower==='quarantined';
      const isRemoved=statusLower==='removed';
      const drySuffix=window._mcpLiveMode?'':' <span style="color:var(--muted);font-size:10px">(dry_run)</span>';
      let actionButtons='';
      // Phase 20B-2/20B-3 : install / activation / deactivation
      if(isDeclared){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--ok);color:#fff" onclick="event.stopPropagation();openMcpInstallProposeModal('${esc(s.server_id)}')">Proposer install${drySuffix}</button>`;
      }else if(isInstalled){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--accent);color:#fff" onclick="event.stopPropagation();openMcpActivationProposeModal('${esc(s.server_id)}')">Proposer activation${drySuffix}</button>`;
      }else if(isActive){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--danger);color:#fff" onclick="event.stopPropagation();openMcpActivationDeactivateModal('${esc(s.server_id)}')">Désactiver${drySuffix}</button>`;
      }
      // Phase 20B-4 : catalog mutations
      if(!isRemoved&&!isQuarantined){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--warn,#e0a23a);color:#fff" onclick="event.stopPropagation();openMcpCatalogQuarantineModal('${esc(s.server_id)}')">Quarantiner${drySuffix}</button>`;
      }
      if(isQuarantined){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--accent);color:#fff" onclick="event.stopPropagation();openMcpCatalogRestoreModal('${esc(s.server_id)}')">Restaurer (→INSTALLED)${drySuffix}</button>`;
      }
      // Remove : tout sauf REMOVED et ACTIVE (force chemin deactivate→remove)
      if(!isRemoved&&!isActive){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--danger);color:#fff" onclick="event.stopPropagation();openMcpCatalogRemoveModal('${esc(s.server_id)}')">Supprimer${drySuffix}</button>`;
      }
      // Phase 20B-6 : trust_score manual update (tous status sauf REMOVED)
      if(!isRemoved){
        actionButtons+=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--accent);color:#fff" onclick="event.stopPropagation();openMcpTrustUpdateModal('${esc(s.server_id)}',${(s.trust_score!=null)?s.trust_score:'null'})">Ajuster trust_score${drySuffix}</button>`;
      }
      const installBtn=actionButtons
        ?`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;justify-content:flex-end" onclick="event.stopPropagation()">${actionButtons}</div>`
        :'';
      html+=`<div class="list-item" style="flex-direction:column;align-items:stretch;cursor:pointer" onclick="openMcpServerDetail('${esc(s.server_id)}')">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <div style="font-weight:600;font-size:14px">${esc(s.display_name||s.server_id)}</div>
          ${statusPill(s.status)}
        </div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:4px">${esc(s.server_id)} · ${esc(s.package_spec||'')}${s.version?` · v${esc(s.version)}`:''}</div>
        <div style="font-size:11px;color:var(--muted);margin-top:2px">owner: ${esc(s.owner_profile||'?')} · trust: ${trust} · last_active: ${lastActive}</div>
        ${installBtn}
      </div>`;
    }
    html+='</div>';
    box.innerHTML=html;
    if(typeof lucide!=='undefined')lucide.createIcons();
  }catch(e){
    box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--danger)">Erreur: ${esc(e.message)}</div></div>`;
  }
}

export async function openMcpServerDetail(serverId){
  document.querySelectorAll('#mcp-detail-modal').forEach(n=>n.remove());
  const modal=document.createElement('div');
  modal.id='mcp-detail-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML='<div class="card" style="width:min(560px,92vw);max-height:88vh;overflow-y:auto;margin:0"><div class="card-title"><i data-lucide="server"></i> Server MCP</div><div class="card-content" id="mcp-detail-body" style="color:var(--muted)">Chargement...</div></div>';
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)closeMcpDetail()});
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/${encodeURIComponent(serverId)}`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const s=d.server||{};
    const body=document.getElementById('mcp-detail-body');
    if(body){
      const rows=[
        ['server_id',s.server_id],
        ['display_name',s.display_name],
        ['status',s.status],
        ['package_spec',s.package_spec],
        ['version',s.version||'—'],
        ['owner_profile',s.owner_profile],
        ['trust_score',(s.trust_score!=null)?s.trust_score:'—'],
        ['added_at',s.added_at],
        ['updated_at',s.updated_at],
        ['last_active_at',s.last_active_at||'jamais'],
      ];
      let html='<div style="display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text)">';
      for(const [k,v] of rows){
        html+=`<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px solid var(--border)"><span style="color:var(--muted);font-size:11px">${esc(k)}</span><span style="font-family:var(--mono);font-size:11px">${esc(String(v||''))}</span></div>`;
      }
      html+='</div>';
      html+='<div style="margin-top:14px;text-align:right"><button class="btn" style="font-size:12px" onclick="closeMcpDetail()">Fermer</button></div>';
      body.innerHTML=html;
    }
  }catch(e){
    const body=document.getElementById('mcp-detail-body');
    if(body)body.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpServerDetail=openMcpServerDetail;

export function closeMcpDetail(){document.querySelectorAll('#mcp-detail-modal').forEach(n=>n.remove())}
window.closeMcpDetail=closeMcpDetail;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-1 — Modals approve / reject ApprovalQueue (UI mutations)

   Garde-fous UI :
     - Modal explicite avant tout appel POST
     - Reject : textarea reason obligatoire (min 3, max 500)
     - Si dry_run forcé (LUMENA_MCP_LIVE off) : bouton suffixé (dry_run)
       et bandeau d'avertissement en haut du panel
     - Rollback UI : si erreur backend, toast + état précédent restauré
     - Pas d'action en chaîne, pas de retry automatique
     - Marker UUID4 affiché discrètement après approve live (utile 20B-2)
   ────────────────────────────────────────────────────────────────────── */

function _mcpCloseApprovalModals(){
  document.querySelectorAll('#mcp-approval-action-modal').forEach(n=>n.remove());
}
window._mcpCloseApprovalModals=_mcpCloseApprovalModals;

function _mcpToast(message,kind){
  const colorMap={ok:'var(--ok)',error:'var(--danger)',info:'var(--accent)'};
  const color=colorMap[kind]||'var(--accent)';
  const t=document.createElement('div');
  t.style.cssText=`position:fixed;bottom:20px;right:20px;background:var(--panel);border:1px solid ${color};color:var(--text);padding:10px 14px;border-radius:6px;font-size:12px;z-index:10000;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:380px`;
  t.innerHTML=esc(message);
  document.body.appendChild(t);
  setTimeout(()=>t.remove(),5500);
}

export function openMcpApprovalApproveModal(actionId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : ApprovalQueue.approve sera appelée pour de vrai. Un marker UUID4 sera émis (TTL 5 min, one-shot).</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE non actif. Aucune mutation queue, aucun marker.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(480px,92vw);margin:0">
    <div class="card-title"><i data-lucide="check-circle"></i> Approuver ticket${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">action_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all">${esc(actionId)}</div>
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-approve-confirm-btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="submitMcpApprovalApprove('${esc(actionId)}')">Confirmer l'approbation${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpApprovalApproveModal=openMcpApprovalApproveModal;

export function openMcpApprovalRejectModal(actionId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : ApprovalQueue.reject sera appelée pour de vrai avec la raison fournie.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE non actif. Aucune mutation queue.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(520px,92vw);margin:0">
    <div class="card-title"><i data-lucide="x-circle"></i> Rejeter ticket${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">action_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(actionId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Raison du rejet (obligatoire, 3-500 caractères) :</label>
      <textarea id="mcp-reject-reason-input" rows="4" maxlength="500" placeholder="Expliquez pourquoi cette action est refusée..." style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:inherit;font-size:12px;resize:vertical" oninput="_mcpUpdateRejectButton()"></textarea>
      <div id="mcp-reject-length-hint" style="font-size:10px;color:var(--muted);margin-top:2px;text-align:right">0 / 500</div>
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-reject-confirm-btn" disabled style="font-size:12px;background:var(--danger);color:#fff;opacity:.5" onclick="submitMcpApprovalReject('${esc(actionId)}')">Confirmer le rejet${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const ta=document.getElementById('mcp-reject-reason-input');if(ta)ta.focus();},50);
}
window.openMcpApprovalRejectModal=openMcpApprovalRejectModal;

function _mcpUpdateRejectButton(){
  const ta=document.getElementById('mcp-reject-reason-input');
  const btn=document.getElementById('mcp-reject-confirm-btn');
  const hint=document.getElementById('mcp-reject-length-hint');
  if(!ta||!btn)return;
  const trimmed=(ta.value||'').trim();
  const len=trimmed.length;
  if(hint)hint.textContent=`${len} / 500`;
  const valid=len>=3&&len<=500;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateRejectButton=_mcpUpdateRejectButton;

export async function submitMcpApprovalApprove(actionId){
  const btn=document.getElementById('mcp-approve-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  let localCreateNextServerId='';
  let localCreateNextTicketId='';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/approvals/${encodeURIComponent(actionId)}/approve`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.marker){
      // Phase 20B-2/20B-3 : capture marker côté UI si mapping ticket→server_id connu.
      // L'ordre est strict : 1) set marker sous server_id 2) clear ticket mapping.
      // Le mapping peut être install (mcp_install_ticket_*) OU activation
      // (mcp_activate_ticket_*) — on essaie les deux dans cet ordre.
      const ticketId=d.action_id||actionId;
      let serverId=_mcpGetServerIdForTicket(ticketId);
      let mappingKind='install';
      if(!serverId){
        serverId=_mcpGetServerIdForActivateTicket(ticketId);
        if(serverId)mappingKind='activate';
      }
      if(!serverId){
        serverId=_mcpGetServerIdForLocalCreateTicket(ticketId);
        if(serverId)mappingKind='local_create';
      }
      if(serverId){
        _mcpSetMarker(serverId,d.marker,d.marker_ttl_s||300);
        if(mappingKind==='install'){
          _mcpClearTicketMapping(ticketId);
        }else if(mappingKind==='activate'){
          _mcpClearActivateTicketMapping(ticketId);
        }else{
          // Local-create approval is not the final action. Keep this mapping
          // until /api/mcp/local-create/execute consumes the one-shot marker.
          localCreateNextServerId=serverId;
          localCreateNextTicketId=ticketId;
        }
      }
      if(out)out.innerHTML=`<span style="color:var(--ok)">Approuvé. Marker émis : <code style="background:var(--bg);padding:2px 4px;border-radius:3px">${esc(d.marker.substring(0,12))}…</code> (TTL ${esc(String(d.marker_ttl_s||300))}s, one-shot)${serverId?` · stocké pour <code>${esc(serverId)}</code>`:''}</span>`;
      _mcpToast('Ticket approuvé (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucune mutation queue, aucun marker)</span>`;
      _mcpToast('Approbation simulée (dry_run)','info');
    }
    if(localCreateNextServerId&&out){
      out.innerHTML+=`<div style="margin-top:10px;display:flex;justify-content:flex-end"><button class="btn" style="font-size:12px;background:var(--accent);color:#fff" onclick="openMcpLocalCreateExecuteModal('${esc(localCreateNextServerId)}','${esc(localCreateNextTicketId)}')">Materialiser local MCP maintenant</button></div>`;
      _loadMcpApprovals();
    }else{
      setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpApprovals();},1200);
    }
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec approve: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpApprovalApprove=submitMcpApprovalApprove;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-2 — Install lifecycle UI mutations
   (propose / execute, marker UUID4 consommation one-shot)

   Helpers sessionStorage (JSON + TTL indépendants) :
     - mcp_install_ticket_<ticket_id> = {server_id, expires_at}  (TTL 30 min)
     - mcp_marker_<server_id>         = {marker, expires_at}     (TTL 5 min)

   Invariant central : un mapping ticket valide reste en sessionStorage tant
   que son expires_at n'est pas dépassé, MÊME SI aucun marker n'a encore été
   émis. Le marker arrive seulement après approve, qui peut survenir
   longtemps après propose. La purge ne dépend JAMAIS de l'existence d'un
   marker.

   sessionStorage UNIQUEMENT : tout est purgé à la fermeture du navigateur
   (jamais persisté entre sessions).
   ────────────────────────────────────────────────────────────────────── */

const _MCP_TICKET_MAPPING_TTL_S=1800;  // 30 min (> TTL marker pour donner le temps d'approuver)
const _MCP_INSTALL_TOOL_PREFIX='mcp_install:';

function _mcpSetMarker(serverId,marker,ttlS){
  const ttl=Number(ttlS)>0?Number(ttlS):300;
  sessionStorage.setItem(`mcp_marker_${serverId}`,JSON.stringify({
    marker:marker,
    expires_at:Date.now()+(ttl*1000),
  }));
}
window._mcpSetMarker=_mcpSetMarker;

function _mcpGetMarker(serverId){
  const raw=sessionStorage.getItem(`mcp_marker_${serverId}`);
  if(!raw)return null;
  try{
    const d=JSON.parse(raw);
    if(!d||typeof d.marker!=='string'||typeof d.expires_at!=='number'){
      sessionStorage.removeItem(`mcp_marker_${serverId}`);
      return null;
    }
    if(Date.now()>d.expires_at){
      sessionStorage.removeItem(`mcp_marker_${serverId}`);
      return null;
    }
    return d.marker;
  }catch(_){
    sessionStorage.removeItem(`mcp_marker_${serverId}`);
    return null;
  }
}
window._mcpGetMarker=_mcpGetMarker;

function _mcpClearMarker(serverId){
  sessionStorage.removeItem(`mcp_marker_${serverId}`);
}
window._mcpClearMarker=_mcpClearMarker;

function _mcpSetTicketMapping(ticketId,serverId){
  sessionStorage.setItem(`mcp_install_ticket_${ticketId}`,JSON.stringify({
    server_id:serverId,
    expires_at:Date.now()+(_MCP_TICKET_MAPPING_TTL_S*1000),
  }));
}
window._mcpSetTicketMapping=_mcpSetTicketMapping;

function _mcpGetServerIdForTicket(ticketId){
  // INDEPENDANT du marker : lit le JSON, vérifie expires_at uniquement.
  // Le marker n'arrive qu'après approve ; le mapping doit survivre dans cette
  // fenêtre, sinon Lumena ne pourra plus associer marker → server_id.
  const raw=sessionStorage.getItem(`mcp_install_ticket_${ticketId}`);
  if(!raw)return null;
  try{
    const d=JSON.parse(raw);
    if(!d||typeof d.server_id!=='string'||typeof d.expires_at!=='number'){
      sessionStorage.removeItem(`mcp_install_ticket_${ticketId}`);
      return null;
    }
    if(Date.now()>d.expires_at){
      sessionStorage.removeItem(`mcp_install_ticket_${ticketId}`);
      return null;
    }
    return d.server_id;
  }catch(_){
    sessionStorage.removeItem(`mcp_install_ticket_${ticketId}`);
    return null;
  }
}
window._mcpGetServerIdForTicket=_mcpGetServerIdForTicket;

function _mcpClearTicketMapping(ticketId){
  sessionStorage.removeItem(`mcp_install_ticket_${ticketId}`);
}
window._mcpClearTicketMapping=_mcpClearTicketMapping;

function _mcpPurgeExpiredInstallState(){
  // Purge UNIQUEMENT les entrées dont expires_at est dépassé OU corrompues.
  // Ne JAMAIS supprimer un mapping ticket valide sous prétexte qu'aucun
  // marker n'existe encore : le marker arrive après approve.
  const now=Date.now();
  const keysToCheck=[];
  for(let i=0;i<sessionStorage.length;i++){
    const k=sessionStorage.key(i);
    if(k&&(k.startsWith('mcp_install_ticket_')
           ||k.startsWith('mcp_activate_ticket_')
           ||k.startsWith('mcp_marker_'))){
      keysToCheck.push(k);
    }
  }
  for(const k of keysToCheck){
    const raw=sessionStorage.getItem(k);
    if(!raw)continue;
    try{
      const d=JSON.parse(raw);
      if(!d||typeof d.expires_at!=='number'){
        sessionStorage.removeItem(k);
        continue;
      }
      if(now>d.expires_at){
        sessionStorage.removeItem(k);
      }
      // Sinon : entrée VALIDE → on ne touche pas, même si l'autre côté manque.
    }catch(_){
      sessionStorage.removeItem(k);
    }
  }
}
window._mcpPurgeExpiredInstallState=_mcpPurgeExpiredInstallState;

/* ── Modal Proposer install (sur Catalog DECLARED) ───────────────────── */

export function openMcpInstallProposeModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : un ticket d\'approbation sera créé via MCPInstallOrchestrator.propose_install.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE non actif. Aucun ticket créé.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(480px,92vw);margin:0">
    <div class="card-title"><i data-lucide="download"></i> Proposer install${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all">${esc(serverId)}</div>
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-install-propose-confirm-btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="submitMcpInstallPropose('${esc(serverId)}')">Confirmer propose${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpInstallProposeModal=openMcpInstallProposeModal;

export async function submitMcpInstallPropose(serverId){
  const btn=document.getElementById('mcp-install-propose-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/install/propose`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,server_id:serverId,caller_kind:'admin_ui'}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.ticket_id&&d.server_id){
      // Stocker le mapping ticket_id → server_id pour pouvoir associer le
      // marker reçu après approve à ce server_id.
      _mcpSetTicketMapping(d.ticket_id,d.server_id);
      if(out)out.innerHTML=`<span style="color:var(--ok)">Ticket créé : <code style="background:var(--bg);padding:2px 4px;border-radius:3px">${esc(d.ticket_id.substring(0,12))}…</code>. Aller dans Approvals pour valider.</span>`;
      _mcpToast('Ticket install créé (live)','ok');
      setTimeout(()=>{_mcpCloseApprovalModals();loadMcpTab('approvals');},1500);
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucun ticket créé)</span>`;
      _mcpToast('Propose simulé (dry_run)','info');
      setTimeout(()=>{_mcpCloseApprovalModals();},1200);
    }
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec propose: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpInstallPropose=submitMcpInstallPropose;

/* ── Modal Exécuter install (niveau 2 : saisie texte) ────────────────── */

export function openMcpInstallExecuteModal(serverId,ticketId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : un subprocess réel npm/pip va être lancé via MCPSandboxRunner. Le marker sera consommé (one-shot) AVANT l\'exécution. En cas d\'échec, une nouvelle approbation sera requise.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucun subprocess lancé, aucun marker consommé.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="play"></i> Exécuter install${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-install-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateInstallExecuteButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-install-execute-confirm-btn" disabled style="font-size:12px;background:var(--danger);color:#fff;opacity:.5" onclick="submitMcpInstallExecute('${esc(serverId)}','${esc(ticketId||'')}')">Exécuter install${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const inp=document.getElementById('mcp-install-phrase-input');if(inp)inp.focus();},50);
}
window.openMcpInstallExecuteModal=openMcpInstallExecuteModal;

function _mcpUpdateInstallExecuteButton(expectedServerId){
  const inp=document.getElementById('mcp-install-phrase-input');
  const btn=document.getElementById('mcp-install-execute-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateInstallExecuteButton=_mcpUpdateInstallExecuteButton;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-3 — Activation lifecycle UI mutations
   (propose / execute / deactivate)

   Réutilise les helpers sessionStorage 20B-2 (mcp_install_ticket_*, mcp_marker_*).
   Nouveau préfixe ticket : mcp_activate_ticket_* (TTL 30 min, JSON + expires_at).
   ────────────────────────────────────────────────────────────────────── */

const _MCP_ACTIVATE_TOOL_PREFIX='mcp_activate:';
const _MCP_ACTIVATE_TICKET_TTL_S=1800;

function _mcpSetActivateTicketMapping(ticketId,serverId){
  sessionStorage.setItem(`mcp_activate_ticket_${ticketId}`,JSON.stringify({
    server_id:serverId,
    expires_at:Date.now()+(_MCP_ACTIVATE_TICKET_TTL_S*1000),
  }));
}
window._mcpSetActivateTicketMapping=_mcpSetActivateTicketMapping;

function _mcpGetServerIdForActivateTicket(ticketId){
  const raw=sessionStorage.getItem(`mcp_activate_ticket_${ticketId}`);
  if(!raw)return null;
  try{
    const d=JSON.parse(raw);
    if(!d||typeof d.server_id!=='string'||typeof d.expires_at!=='number'){
      sessionStorage.removeItem(`mcp_activate_ticket_${ticketId}`);
      return null;
    }
    if(Date.now()>d.expires_at){
      sessionStorage.removeItem(`mcp_activate_ticket_${ticketId}`);
      return null;
    }
    return d.server_id;
  }catch(_){
    sessionStorage.removeItem(`mcp_activate_ticket_${ticketId}`);
    return null;
  }
}
window._mcpGetServerIdForActivateTicket=_mcpGetServerIdForActivateTicket;

function _mcpClearActivateTicketMapping(ticketId){
  sessionStorage.removeItem(`mcp_activate_ticket_${ticketId}`);
}
window._mcpClearActivateTicketMapping=_mcpClearActivateTicketMapping;

const _MCP_LOCAL_CREATE_TOOL_PREFIX='mcp_local_create:';

function _mcpLocalCreateExecuteButton(serverId,ticketId,drySuffix){
  return `<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--accent);color:#fff" onclick="openMcpLocalCreateExecuteModal('${esc(serverId)}','${esc(ticketId||'')}')">Materialiser local MCP${drySuffix||''}</button>`;
}
window._mcpLocalCreateExecuteButton=_mcpLocalCreateExecuteButton;

function _mcpSetLocalCreateTicketMapping(ticketId,serverId){
  sessionStorage.setItem(`mcp_local_create_ticket_${ticketId}`,JSON.stringify({
    server_id:serverId,
    expires_at:Date.now()+(_MCP_ACTIVATE_TICKET_TTL_S*1000),
  }));
}
window._mcpSetLocalCreateTicketMapping=_mcpSetLocalCreateTicketMapping;

function _mcpGetServerIdForLocalCreateTicket(ticketId){
  const raw=sessionStorage.getItem(`mcp_local_create_ticket_${ticketId}`);
  if(!raw)return null;
  try{
    const d=JSON.parse(raw);
    if(!d||typeof d.server_id!=='string'||typeof d.expires_at!=='number'){
      sessionStorage.removeItem(`mcp_local_create_ticket_${ticketId}`);
      return null;
    }
    if(Date.now()>d.expires_at){
      sessionStorage.removeItem(`mcp_local_create_ticket_${ticketId}`);
      return null;
    }
    return d.server_id;
  }catch(_){
    sessionStorage.removeItem(`mcp_local_create_ticket_${ticketId}`);
    return null;
  }
}
window._mcpGetServerIdForLocalCreateTicket=_mcpGetServerIdForLocalCreateTicket;

function _mcpClearLocalCreateTicketMapping(ticketId){
  sessionStorage.removeItem(`mcp_local_create_ticket_${ticketId}`);
}
window._mcpClearLocalCreateTicketMapping=_mcpClearLocalCreateTicketMapping;

/* ── Modal Proposer activation (sur Catalog INSTALLED) ──────────────── */

export function openMcpActivationProposeModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : un ticket d\'approbation sera créé via MCPActivationService.propose_activation.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE non actif. Aucun ticket créé.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(480px,92vw);margin:0">
    <div class="card-title"><i data-lucide="zap"></i> Proposer activation${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all">${esc(serverId)}</div>
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-activation-propose-confirm-btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="submitMcpActivationPropose('${esc(serverId)}')">Confirmer propose${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpActivationProposeModal=openMcpActivationProposeModal;

export async function submitMcpActivationPropose(serverId){
  const btn=document.getElementById('mcp-activation-propose-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/activation/propose`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,server_id:serverId,caller_kind:'admin_ui'}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.ticket_id&&d.server_id){
      _mcpSetActivateTicketMapping(d.ticket_id,d.server_id);
      if(out)out.innerHTML=`<span style="color:var(--ok)">Ticket créé : <code style="background:var(--bg);padding:2px 4px;border-radius:3px">${esc(d.ticket_id.substring(0,12))}…</code>. Aller dans Approvals pour valider.</span>`;
      _mcpToast('Ticket activation créé (live)','ok');
      setTimeout(()=>{_mcpCloseApprovalModals();loadMcpTab('approvals');},1500);
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucun ticket créé)</span>`;
      _mcpToast('Propose activation simulé (dry_run)','info');
      setTimeout(()=>{_mcpCloseApprovalModals();},1200);
    }
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec propose activation: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpActivationPropose=submitMcpActivationPropose;

/* ── Modal Activer (niveau 2 : saisie texte) ───────────────────────── */

export function openMcpActivationExecuteModal(serverId,ticketId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : démarrage subprocess + register handlers dans le ToolRegistry runtime. Le marker sera consommé (one-shot) AVANT l\'activation. En cas d\'échec, une nouvelle approbation sera requise.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucun subprocess lancé, aucun marker consommé.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="play"></i> Activer${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-activate-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateActivateExecuteButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-activate-execute-confirm-btn" disabled style="font-size:12px;background:var(--ok);color:#fff;opacity:.5" onclick="submitMcpActivationExecute('${esc(serverId)}','${esc(ticketId||'')}')">Activer${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const inp=document.getElementById('mcp-activate-phrase-input');if(inp)inp.focus();},50);
}
window.openMcpActivationExecuteModal=openMcpActivationExecuteModal;

function _mcpUpdateActivateExecuteButton(expectedServerId){
  const inp=document.getElementById('mcp-activate-phrase-input');
  const btn=document.getElementById('mcp-activate-execute-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateActivateExecuteButton=_mcpUpdateActivateExecuteButton;

export async function submitMcpActivationExecute(serverId,ticketId){
  const inp=document.getElementById('mcp-activate-phrase-input');
  const btn=document.getElementById('mcp-activate-execute-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  const marker=_mcpGetMarker(serverId);
  if(!marker&&window._mcpLiveMode){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Marker absent ou expiré. Recréer une approbation.</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/activation/execute`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({
        confirmed:true,
        confirmation_phrase:phrase,
        server_id:serverId,
        marker:marker||'00000000000000000000000000000000',
      }),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      _mcpClearMarker(serverId);
      if(ticketId)_mcpClearActivateTicketMapping(ticketId);
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    _mcpClearMarker(serverId);
    if(ticketId)_mcpClearActivateTicketMapping(ticketId);
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Activé. Status : ${esc(d.status||'?')}</span>`;
      _mcpToast('Activation exécutée (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucun subprocess, marker non consommé)</span>`;
      _mcpToast('Activation simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpApprovals();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec activate: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpActivationExecute=submitMcpActivationExecute;

/* ── Modal Désactiver (niveau 2 : saisie texte, action de protection) ─ */

export function openMcpActivationDeactivateModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : stop subprocess + unregister handlers du ToolRegistry runtime. Les outils MCP de ce server ne seront plus disponibles pour le dispatch.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucune mutation runtime.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="square"></i> Désactiver${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-deactivate-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateDeactivateButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-deactivate-confirm-btn" disabled style="font-size:12px;background:var(--danger);color:#fff;opacity:.5" onclick="submitMcpActivationDeactivate('${esc(serverId)}')">Désactiver${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const inp=document.getElementById('mcp-deactivate-phrase-input');if(inp)inp.focus();},50);
}
window.openMcpActivationDeactivateModal=openMcpActivationDeactivateModal;

function _mcpUpdateDeactivateButton(expectedServerId){
  const inp=document.getElementById('mcp-deactivate-phrase-input');
  const btn=document.getElementById('mcp-deactivate-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateDeactivateButton=_mcpUpdateDeactivateButton;

export async function submitMcpActivationDeactivate(serverId){
  const inp=document.getElementById('mcp-deactivate-phrase-input');
  const btn=document.getElementById('mcp-deactivate-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/activation/deactivate`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({
        confirmed:true,
        confirmation_phrase:phrase,
        server_id:serverId,
      }),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Désactivé. Status : ${esc(d.status||'?')}</span>`;
      _mcpToast('Désactivation exécutée (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucune mutation runtime)</span>`;
      _mcpToast('Désactivation simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec deactivate: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpActivationDeactivate=submitMcpActivationDeactivate;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-4 — Catalog mutations UI
   (add / quarantine / restore / remove)

   Add  : modal niveau 1 (formulaire de création, pas de phrase)
   Quarantine / Restore / Remove : modal niveau 2 (saisie texte = server_id)

   Réutilise singleton MCPServerCatalog Phase 20B-2.
   Pas de marker, pas d'approval queue.
   ────────────────────────────────────────────────────────────────────── */

/* ── Modal Ajouter server (niveau 1) ──────────────────────────────── */

export function openMcpCatalogAddModal(){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : un nouveau server sera ajouté au Catalog avec status DECLARED.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucune mutation Catalog.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(560px,92vw);max-height:88vh;overflow-y:auto;margin:0">
    <div class="card-title"><i data-lucide="plus-circle"></i> Ajouter server${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">server_id (lowercase, [a-z0-9][a-z0-9_.-]{0,63})</label>
      <input type="text" id="mcp-add-server-id" autocomplete="off" spellcheck="false" placeholder="alice-mcp" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">display_name (texte libre, max 200)</label>
      <input type="text" id="mcp-add-display-name" autocomplete="off" placeholder="Alice MCP" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">package_spec (npm:&lt;pkg&gt; | pypi:&lt;pkg&gt; | local:&lt;slug&gt;)</label>
      <input type="text" id="mcp-add-package-spec" autocomplete="off" spellcheck="false" placeholder="npm:my-mcp-server" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">owner_profile ([a-z0-9_-]{1,64})</label>
      <input type="text" id="mcp-add-owner-profile" autocomplete="off" spellcheck="false" placeholder="default" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">version (optionnel)</label>
      <input type="text" id="mcp-add-version" autocomplete="off" spellcheck="false" placeholder="1.0.0" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">trust_score (0-100, optionnel)</label>
      <input type="number" id="mcp-add-trust-score" min="0" max="100" placeholder="80" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">notes (optionnel, max 256, [a-zA-Z0-9 _:.-])</label>
      <textarea id="mcp-add-notes" rows="2" maxlength="256" placeholder="" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;resize:vertical"></textarea>
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-add-confirm-btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="submitMcpCatalogAdd()">Confirmer ajout${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-add-server-id');if(i)i.focus();},50);
}
window.openMcpCatalogAddModal=openMcpCatalogAddModal;

export async function submitMcpCatalogAdd(){
  const btn=document.getElementById('mcp-add-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const sid=(document.getElementById('mcp-add-server-id')||{}).value||'';
  const displayName=(document.getElementById('mcp-add-display-name')||{}).value||'';
  const packageSpec=(document.getElementById('mcp-add-package-spec')||{}).value||'';
  const ownerProfile=(document.getElementById('mcp-add-owner-profile')||{}).value||'';
  const version=((document.getElementById('mcp-add-version')||{}).value||'').trim();
  const trustRaw=((document.getElementById('mcp-add-trust-score')||{}).value||'').trim();
  const notes=((document.getElementById('mcp-add-notes')||{}).value||'');
  const body={confirmed:true,server_id:sid,display_name:displayName,package_spec:packageSpec,owner_profile:ownerProfile};
  if(version)body.version=version;
  if(trustRaw)body.trust_score=parseInt(trustRaw,10);
  if(notes)body.notes=notes;
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/add`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify(body),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Server ajouté (status DECLARED).</span>`;
      _mcpToast('Server ajouté au Catalog (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucune mutation Catalog)</span>`;
      _mcpToast('Ajout simulé (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec ajout: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpCatalogAdd=submitMcpCatalogAdd;

/* ── Modal Quarantiner (niveau 2 : saisie texte) ──────────────────── */

export function openMcpCatalogQuarantineModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : status → QUARANTINED. Le server ne pourra plus être proposé en activation jusqu\'à restore.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucune mutation Catalog.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="alert-triangle"></i> Quarantiner server${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-quarantine-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateQuarantineButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-quarantine-confirm-btn" disabled style="font-size:12px;background:var(--warn,#e0a23a);color:#fff;opacity:.5" onclick="submitMcpCatalogQuarantine('${esc(serverId)}')">Quarantiner${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-quarantine-phrase-input');if(i)i.focus();},50);
}
window.openMcpCatalogQuarantineModal=openMcpCatalogQuarantineModal;

function _mcpUpdateQuarantineButton(expectedServerId){
  const inp=document.getElementById('mcp-quarantine-phrase-input');
  const btn=document.getElementById('mcp-quarantine-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateQuarantineButton=_mcpUpdateQuarantineButton;

export async function submitMcpCatalogQuarantine(serverId){
  const inp=document.getElementById('mcp-quarantine-phrase-input');
  const btn=document.getElementById('mcp-quarantine-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/${encodeURIComponent(serverId)}/quarantine`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,confirmation_phrase:phrase,server_id:serverId}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Status → QUARANTINED.</span>`;
      _mcpToast('Quarantine exécutée (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé)</span>`;
      _mcpToast('Quarantine simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec quarantine: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpCatalogQuarantine=submitMcpCatalogQuarantine;

/* ── Modal Restaurer (niveau 2 : saisie texte) ────────────────────── */

export function openMcpCatalogRestoreModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : status QUARANTINED → INSTALLED. Pour revenir ACTIVE, passer ensuite par Activation (20B-3).</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucune mutation Catalog.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="rotate-ccw"></i> Restaurer server${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-restore-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateRestoreButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-restore-confirm-btn" disabled style="font-size:12px;background:var(--accent);color:#fff;opacity:.5" onclick="submitMcpCatalogRestore('${esc(serverId)}')">Restaurer (→INSTALLED)${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-restore-phrase-input');if(i)i.focus();},50);
}
window.openMcpCatalogRestoreModal=openMcpCatalogRestoreModal;

function _mcpUpdateRestoreButton(expectedServerId){
  const inp=document.getElementById('mcp-restore-phrase-input');
  const btn=document.getElementById('mcp-restore-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateRestoreButton=_mcpUpdateRestoreButton;

export async function submitMcpCatalogRestore(serverId){
  const inp=document.getElementById('mcp-restore-phrase-input');
  const btn=document.getElementById('mcp-restore-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/${encodeURIComponent(serverId)}/restore`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,confirmation_phrase:phrase,server_id:serverId,target_status:'installed'}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Status → INSTALLED.</span>`;
      _mcpToast('Restore exécuté (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé)</span>`;
      _mcpToast('Restore simulé (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec restore: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpCatalogRestore=submitMcpCatalogRestore;

/* ── Modal Supprimer (niveau 2 : saisie texte) ────────────────────── */

export function openMcpCatalogRemoveModal(serverId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modeWarning=liveMode
    ?'<div style="background:rgba(220,80,80,.1);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : soft-delete status → REMOVED. État terminal Phase 14 (irréversible, mais le fichier reste sur disque).</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — aucune mutation Catalog.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(540px,92vw);margin:0">
    <div class="card-title"><i data-lucide="trash-2"></i> Supprimer server${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-remove-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateRemoveButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-remove-confirm-btn" disabled style="font-size:12px;background:var(--danger);color:#fff;opacity:.5" onclick="submitMcpCatalogRemove('${esc(serverId)}')">Supprimer${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-remove-phrase-input');if(i)i.focus();},50);
}
window.openMcpCatalogRemoveModal=openMcpCatalogRemoveModal;

function _mcpUpdateRemoveButton(expectedServerId){
  const inp=document.getElementById('mcp-remove-phrase-input');
  const btn=document.getElementById('mcp-remove-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedServerId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateRemoveButton=_mcpUpdateRemoveButton;

export async function submitMcpCatalogRemove(serverId){
  const inp=document.getElementById('mcp-remove-phrase-input');
  const btn=document.getElementById('mcp-remove-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/${encodeURIComponent(serverId)}/remove`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,confirmation_phrase:phrase,server_id:serverId}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Status → REMOVED${d.idempotent?' (idempotent)':''}.</span>`;
      _mcpToast('Suppression exécutée (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé)</span>`;
      _mcpToast('Suppression simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec remove: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpCatalogRemove=submitMcpCatalogRemove;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-5 — AutoApprove patterns CRUD UI
   (mutations de policy future — double opt-in obligatoire)

   Add  : modal niveau 2 (formulaire + phrase fixe CREATE-AUTOAPPROVE-PATTERN)
   Remove : modal niveau 2 (saisie texte = pattern_id complet 32 chars)

   Singleton AutoApproveEngine côté backend (lifespan).
   Double opt-in :
     LUMENA_MCP_LIVE=1
     ET
     LUMENA_MCP_AUTOAPPROVE_LIVE=1
   Sinon dry-run forcé (0 call add_pattern / remove_pattern côté backend).
   ────────────────────────────────────────────────────────────────────── */

const _MCP_AUTOAPPROVE_ADD_PHRASE='CREATE-AUTOAPPROVE-PATTERN';
const _MCP_AUTOAPPROVE_KNOWN_CONSTRAINT_KEYS=[
  'to_allowlist','channel_allowlist','url_allowlist',
  'account_allowlist','recipient_allowlist',
  'subject_max_chars','body_max_chars',
  'amount_max_eur','amount_max_usd',
  'attachments_forbidden',
];
const _MCP_AUTOAPPROVE_CALLER_KINDS=['react','codeagent','autonomy','scheduler','daemon','silent'];
const _MCP_AUTOAPPROVE_POLICIES=[
  'read_only','external_read','external_write_recoverable',
  'local_write','external_write_irreversible','secrets_auth',
];

async function _loadMcpAutoApprove(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Chargement...</div></div>';
  try{
    // Récupère état double opt-in via /health (refresh)
    const hr=await fetch(`${API_BASE}/api/mcp/health`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const hd=hr.ok?await hr.json():{};
    const liveMode=!!hd.live_mode;
    const aaLiveMode=!!hd.autoapprove_live_mode;
    const doubleOptin=liveMode&&aaLiveMode;
    const r=await fetch(`${API_BASE}/api/mcp/autoapprove/patterns`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.available===false){
      box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--muted)">Module AutoApproveEngine non chargé (${esc(d.reason||'unknown')}).</div></div>`;
      return;
    }
    const patterns=d.patterns||[];
    let html='';
    // Bandeau double opt-in
    if(!doubleOptin){
      const missing=[];
      if(!liveMode)missing.push('LUMENA_MCP_LIVE');
      if(!aaLiveMode)missing.push('LUMENA_MCP_AUTOAPPROVE_LIVE');
      html+=`<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);color:var(--text);padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:12px;display:flex;align-items:center;gap:8px">
        <i data-lucide="alert-triangle" style="width:14px;height:14px;color:var(--warn,#e0a23a)"></i>
        <span><strong>Dry-run forcé</strong> — double opt-in requis (${esc(missing.join(' + '))} manquant${missing.length>1?'s':''}). Les mutations seront simulées sans persistence.</span>
      </div>`;
    }else{
      html+=`<div style="background:rgba(220,80,80,.10);border:1px solid var(--danger);color:var(--text);padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:12px;display:flex;align-items:center;gap:8px">
        <i data-lucide="alert-octagon" style="width:14px;height:14px;color:var(--danger)"></i>
        <span><strong>Mode LIVE actif</strong> — créer un pattern AutoApprove = créer une autorisation FUTURE qui peut court-circuiter ApprovalQueue. Chaque pattern signé est immédiatement actif.</span>
      </div>`;
    }
    // Bouton "Créer pattern"
    const drySuffix=doubleOptin?'':' (dry_run)';
    html+=`<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
      <button class="btn" style="font-size:12px;background:var(--ok);color:#fff" onclick="openMcpAutoApproveAddModal()">+ Créer pattern${drySuffix}</button>
    </div>`;
    // Liste
    if(!patterns.length){
      html+='<div class="card"><div class="card-content" style="color:var(--muted);padding:30px;text-align:center;font-size:13px">Aucun pattern AutoApprove enregistré.</div></div>';
    }else{
      html+='<div class="list">';
      for(const p of patterns){
        html+=`<div class="list-item" style="flex-direction:column;align-items:stretch">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">
            <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${esc(p.pattern_id||'')}</span>
            <span class="pill ${p.policy==='read_only'||p.policy==='external_read'?'ok':'warn'}">${esc((p.policy||'').toUpperCase())}</span>
          </div>
          <div style="font-size:12px;margin-top:4px">profile: <code>${esc(p.profile||'?')}</code> · kind: <code>${esc(p.kind||'?')}</code></div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">caller_kinds: ${esc(String(p.caller_kinds_count||0))} · args_constraints_keys: ${esc(String(p.args_constraints_keys_count||0))} · allowlists_entries: ${esc(String(p.args_constraints_allowlists_total_entries||0))} · tool_name_pattern: ${p.tool_name_pattern_present?'<span style="color:var(--ok)">défini</span>':'<span style="color:var(--muted)">non</span>'}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">quota_max_per_day: ${esc(String(p.quota_max_per_day||0))} · expires_at: ${esc((p.expires_at||'').substring(0,19).replace('T',' '))}</div>
          <div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end">
            <button class="btn" style="font-size:11px;padding:4px 10px;background:var(--danger);color:#fff" onclick="openMcpAutoApproveRemoveModal('${esc(p.pattern_id)}')">Supprimer${drySuffix}</button>
          </div>
        </div>`;
      }
      html+='</div>';
    }
    box.innerHTML=html;
    if(typeof lucide!=='undefined')lucide.createIcons();
  }catch(e){
    box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--danger)">Erreur: ${esc(e.message)}</div></div>`;
  }
}
window._loadMcpAutoApprove=_loadMcpAutoApprove;

/* ── Modal Créer pattern (niveau 2 : formulaire + phrase fixe) ─────── */

export function openMcpAutoApproveAddModal(){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const callerOptions=_MCP_AUTOAPPROVE_CALLER_KINDS
    .map(k=>`<label style="display:inline-flex;align-items:center;gap:4px;margin-right:8px;font-size:11px"><input type="checkbox" class="mcp-aa-caller-checkbox" value="${esc(k)}"> ${esc(k)}</label>`)
    .join('');
  const policyOptions=_MCP_AUTOAPPROVE_POLICIES
    .map(p=>`<option value="${esc(p)}">${esc(p)}</option>`)
    .join('');
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(620px,92vw);max-height:88vh;overflow-y:auto;margin:0">
    <div class="card-title"><i data-lucide="shield-check"></i> Créer pattern AutoApprove${drySuffix}</div>
    <div class="card-content">
      <div style="background:rgba(220,80,80,.10);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">
        <strong>Attention :</strong> ce pattern peut court-circuiter ApprovalQueue pour des actions futures correspondant aux contraintes. Double opt-in requis pour mode live.
      </div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">profile (^[a-z0-9_-]{1,64})</label>
      <input type="text" id="mcp-aa-profile" autocomplete="off" spellcheck="false" placeholder="default" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">kind (non-vide, max 64)</label>
      <input type="text" id="mcp-aa-kind" autocomplete="off" placeholder="email_send" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">policy</label>
      <select id="mcp-aa-policy" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">${policyOptions}</select>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">tool_name_pattern (exact mcp__server__tool ou glob mcp__server__* pour read_only/external_read)</label>
      <input type="text" id="mcp-aa-tool-pattern" autocomplete="off" spellcheck="false" placeholder="mcp__alice__send_email" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">caller_kinds_allowed (au moins 1)</label>
      <div>${callerOptions}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">args_constraints (JSON, whitelist 10 clés DSL Phase 11)</label>
      <textarea id="mcp-aa-args-constraints" rows="4" placeholder='{"to_allowlist":["alice@example.com"]}' style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px;resize:vertical"></textarea>
      <div style="font-size:10px;color:var(--muted);margin-top:2px">Clés autorisées : ${esc(_MCP_AUTOAPPROVE_KNOWN_CONSTRAINT_KEYS.join(', '))}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">quota_max_per_day (int 1-1000000)</label>
      <input type="number" id="mcp-aa-quota" min="1" max="1000000" value="10" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">expires_at (ISO 8601 futur, ex 2026-12-31T23:59:59+00:00)</label>
      <input type="text" id="mcp-aa-expires-at" autocomplete="off" spellcheck="false" placeholder="2026-12-31T23:59:59+00:00" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:10px">Pour confirmer, tapez exactement : <code>${esc(_MCP_AUTOAPPROVE_ADD_PHRASE)}</code></label>
      <input type="text" id="mcp-aa-add-phrase" autocomplete="off" spellcheck="false" placeholder="${esc(_MCP_AUTOAPPROVE_ADD_PHRASE)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateAutoApproveAddButton()">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-aa-add-confirm-btn" disabled style="font-size:12px;background:var(--ok);color:#fff;opacity:.5" onclick="submitMcpAutoApproveAdd()">Créer pattern${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-aa-profile');if(i)i.focus();},50);
}
window.openMcpAutoApproveAddModal=openMcpAutoApproveAddModal;

function _mcpUpdateAutoApproveAddButton(){
  const inp=document.getElementById('mcp-aa-add-phrase');
  const btn=document.getElementById('mcp-aa-add-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===_MCP_AUTOAPPROVE_ADD_PHRASE;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateAutoApproveAddButton=_mcpUpdateAutoApproveAddButton;

export async function submitMcpAutoApproveAdd(){
  const btn=document.getElementById('mcp-aa-add-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const profile=(document.getElementById('mcp-aa-profile')||{}).value||'';
  const kind=(document.getElementById('mcp-aa-kind')||{}).value||'';
  const policy=(document.getElementById('mcp-aa-policy')||{}).value||'';
  const toolPattern=(document.getElementById('mcp-aa-tool-pattern')||{}).value||'';
  const quotaRaw=((document.getElementById('mcp-aa-quota')||{}).value||'').trim();
  const expiresAt=(document.getElementById('mcp-aa-expires-at')||{}).value||'';
  const phrase=(document.getElementById('mcp-aa-add-phrase')||{}).value||'';
  if(phrase!==_MCP_AUTOAPPROVE_ADD_PHRASE){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement ${esc(_MCP_AUTOAPPROVE_ADD_PHRASE)}</span>`;
    return;
  }
  // caller_kinds
  const callerCheckboxes=document.querySelectorAll('.mcp-aa-caller-checkbox');
  const callerKinds=[];
  callerCheckboxes.forEach(cb=>{if(cb.checked)callerKinds.push(cb.value);});
  // args_constraints JSON parse côté UI
  const argsRaw=(document.getElementById('mcp-aa-args-constraints')||{}).value||'';
  let argsConstraints=null;
  try{argsConstraints=JSON.parse(argsRaw);}
  catch(_){
    if(out)out.innerHTML=`<span style="color:var(--danger)">args_constraints doit être un JSON valide</span>`;
    return;
  }
  const body={
    confirmed:true,
    confirmation_phrase:phrase,
    profile,kind,policy,
    tool_name_pattern:toolPattern,
    caller_kinds_allowed:callerKinds,
    args_constraints:argsConstraints,
    quota_max_per_day:parseInt(quotaRaw,10)||0,
    expires_at:expiresAt,
  };
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/autoapprove/add`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify(body),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.autoapprove_live_mode&&d.pattern_id){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Pattern créé : <code style="background:var(--bg);padding:2px 4px;border-radius:3px">${esc(d.pattern_id.substring(0,12))}…</code></span>`;
      _mcpToast('Pattern AutoApprove créé (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (double opt-in non actif — aucun pattern créé)</span>`;
      _mcpToast('Création simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpAutoApprove();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec création: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpAutoApproveAdd=submitMcpAutoApproveAdd;

/* ── Modal Supprimer pattern (niveau 2 : pattern_id complet) ─────── */

export function openMcpAutoApproveRemoveModal(patternId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(560px,92vw);margin:0">
    <div class="card-title"><i data-lucide="trash-2"></i> Supprimer pattern AutoApprove${drySuffix}</div>
    <div class="card-content">
      <div style="background:rgba(220,80,80,.10);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">
        <strong>Attention :</strong> retire l'autorisation future associée. Idempotent (Phase 11).
      </div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">pattern_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(patternId)}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Pour confirmer, tapez exactement le pattern_id complet (32 chars hex) :</label>
      <input type="text" id="mcp-aa-remove-phrase" autocomplete="off" spellcheck="false" placeholder="${esc(patternId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:11px" oninput="_mcpUpdateAutoApproveRemoveButton('${esc(patternId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-aa-remove-confirm-btn" disabled style="font-size:12px;background:var(--danger);color:#fff;opacity:.5" onclick="submitMcpAutoApproveRemove('${esc(patternId)}')">Supprimer pattern${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-aa-remove-phrase');if(i)i.focus();},50);
}
window.openMcpAutoApproveRemoveModal=openMcpAutoApproveRemoveModal;

function _mcpUpdateAutoApproveRemoveButton(expectedPatternId){
  const inp=document.getElementById('mcp-aa-remove-phrase');
  const btn=document.getElementById('mcp-aa-remove-confirm-btn');
  if(!inp||!btn)return;
  const valid=inp.value===expectedPatternId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateAutoApproveRemoveButton=_mcpUpdateAutoApproveRemoveButton;

export async function submitMcpAutoApproveRemove(patternId){
  const inp=document.getElementById('mcp-aa-remove-phrase');
  const btn=document.getElementById('mcp-aa-remove-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==patternId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le pattern_id complet</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/autoapprove/${encodeURIComponent(patternId)}/remove`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,confirmation_phrase:phrase,pattern_id:patternId}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.autoapprove_live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Pattern supprimé${d.idempotent?' (idempotent)':''}.</span>`;
      _mcpToast('Pattern supprimé (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (double opt-in non actif)</span>`;
      _mcpToast('Suppression simulée (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpAutoApprove();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec suppression: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpAutoApproveRemove=submitMcpAutoApproveRemove;

/* ──────────────────────────────────────────────────────────────────────
   Phase 20B-6 — Trust score manual update UI
   (mutation de seuil de sécurité — double opt-in obligatoire)

   Modal niveau 2 : input number trust_score + textarea justification
                    (UTF-8 lisible, 10..256 chars trimés) + saisie texte
                    = server_id exact.
   Double opt-in : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1.
   Sinon dry-run forcé côté backend (0 call update_trust_score).
   ────────────────────────────────────────────────────────────────────── */

const _MCP_TRUST_JUSTIFICATION_MIN_LEN=10;
const _MCP_TRUST_JUSTIFICATION_MAX_LEN=256;

export function openMcpTrustUpdateModal(serverId,currentScore){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const trustLiveMode=!!window._mcpTrustLiveMode;
  const trustDoubleOptin=liveMode&&trustLiveMode;
  const drySuffix=trustDoubleOptin?'':' (dry_run)';
  const currentLabel=(currentScore==null||currentScore===undefined)?'<em style="color:var(--muted)">aucun</em>':`<strong>${esc(String(currentScore))}</strong>`;
  let modeWarning;
  if(trustDoubleOptin){
    modeWarning='<div style="background:rgba(220,80,80,.10);border:1px solid var(--danger);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Mode LIVE actif (double opt-in) :</strong> ajuster trust_score peut indirectement débloquer des chaînes d\'autorisation futures (notamment via patterns AutoApprove). LUMENA_MCP_LIVE=1 ET LUMENA_MCP_TRUST_LIVE=1.</div>';
  }else if(!liveMode&&!trustLiveMode){
    modeWarning='<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE ET LUMENA_MCP_TRUST_LIVE manquants. Aucune mutation Catalog.</div>';
  }else if(!trustLiveMode){
    modeWarning='<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_TRUST_LIVE manquant (opt-in trust dédié non actif). Aucune mutation Catalog.</div>';
  }else{
    modeWarning='<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcé</strong> — LUMENA_MCP_LIVE manquant (opt-in MCP global non actif). Aucune mutation Catalog.</div>';
  }
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(560px,92vw);max-height:88vh;overflow-y:auto;margin:0">
    <div class="card-title"><i data-lucide="shield"></i> Ajuster trust_score (manual update)${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all;margin-bottom:12px">${esc(serverId)}</div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">trust_score actuel : ${currentLabel}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">Nouveau trust_score (0-100)</label>
      <input type="number" id="mcp-trust-score-input" min="0" max="100" placeholder="0-100" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">Justification (obligatoire, 10-256 caractères, UTF-8 lisible — accents autorisés)</label>
      <textarea id="mcp-trust-justification-input" rows="3" maxlength="256" placeholder="Ex: Révision sécurité après audit interne" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;resize:vertical" oninput="_mcpUpdateTrustButton('${esc(serverId)}')"></textarea>
      <div id="mcp-trust-length-hint" style="font-size:10px;color:var(--muted);margin-top:2px;text-align:right">0 / 256</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:6px">Pour confirmer, tapez exactement le server_id : <code>${esc(serverId)}</code></label>
      <input type="text" id="mcp-trust-phrase-input" autocomplete="off" spellcheck="false" placeholder="${esc(serverId)}" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px" oninput="_mcpUpdateTrustButton('${esc(serverId)}')">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-trust-confirm-btn" disabled style="font-size:12px;background:var(--accent);color:#fff;opacity:.5" onclick="submitMcpTrustUpdate('${esc(serverId)}')">Confirmer ajustement${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
  setTimeout(()=>{const i=document.getElementById('mcp-trust-score-input');if(i)i.focus();},50);
}
window.openMcpTrustUpdateModal=openMcpTrustUpdateModal;

function _mcpUpdateTrustButton(expectedServerId){
  const phraseInp=document.getElementById('mcp-trust-phrase-input');
  const justifInp=document.getElementById('mcp-trust-justification-input');
  const lenHint=document.getElementById('mcp-trust-length-hint');
  const btn=document.getElementById('mcp-trust-confirm-btn');
  if(!btn)return;
  const justifTrimmed=((justifInp&&justifInp.value)||'').trim();
  const justifLen=justifTrimmed.length;
  if(lenHint)lenHint.textContent=`${justifLen} / 256 (min 10)`;
  const phraseOk=(phraseInp&&phraseInp.value===expectedServerId);
  const justifOk=justifLen>=_MCP_TRUST_JUSTIFICATION_MIN_LEN&&justifLen<=_MCP_TRUST_JUSTIFICATION_MAX_LEN;
  const valid=phraseOk&&justifOk;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateTrustButton=_mcpUpdateTrustButton;

export async function submitMcpTrustUpdate(serverId){
  const scoreInp=document.getElementById('mcp-trust-score-input');
  const justifInp=document.getElementById('mcp-trust-justification-input');
  const phraseInp=document.getElementById('mcp-trust-phrase-input');
  const btn=document.getElementById('mcp-trust-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const scoreRaw=((scoreInp&&scoreInp.value)||'').trim();
  const justification=((justifInp&&justifInp.value)||'').trim();
  const phrase=(phraseInp&&phraseInp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  if(scoreRaw===''){
    if(out)out.innerHTML=`<span style="color:var(--danger)">trust_score obligatoire (0-100)</span>`;
    return;
  }
  // Number() + Number.isInteger pour refuser "50.5" / "abc" silencieusement
  // (parseInt convertirait "50.5" en 50, ce qui masquerait une saisie invalide).
  const trustScore=Number(scoreRaw);
  if(!Number.isInteger(trustScore)||trustScore<0||trustScore>100){
    if(out)out.innerHTML=`<span style="color:var(--danger)">trust_score doit être un entier 0-100 (pas de décimal)</span>`;
    return;
  }
  if(justification.length<_MCP_TRUST_JUSTIFICATION_MIN_LEN||justification.length>_MCP_TRUST_JUSTIFICATION_MAX_LEN){
    if(out)out.innerHTML=`<span style="color:var(--danger)">justification doit faire ${_MCP_TRUST_JUSTIFICATION_MIN_LEN}-${_MCP_TRUST_JUSTIFICATION_MAX_LEN} caractères (trimés)</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/catalog/${encodeURIComponent(serverId)}/trust/update`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({
        confirmed:true,
        confirmation_phrase:phrase,
        server_id:serverId,
        trust_score:trustScore,
        justification:justification,
      }),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode&&d.trust_live_mode){
      if(d.updated===true){
        if(out)out.innerHTML=`<span style="color:var(--ok)">trust_score : ${esc(String(d.trust_score_old))} → ${esc(String(d.trust_score_new))}</span>`;
        _mcpToast('trust_score ajusté (live)','ok');
      }else if(d.idempotent===true){
        if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Valeur identique (idempotent no-op) — aucune mutation</span>`;
        _mcpToast('trust_score inchangé','info');
      }
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (double opt-in non actif — aucune mutation Catalog)</span>`;
      _mcpToast('Ajustement simulé (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpCatalog();},1500);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec ajustement: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpTrustUpdate=submitMcpTrustUpdate;

export async function submitMcpInstallExecute(serverId,ticketId){
  const inp=document.getElementById('mcp-install-phrase-input');
  const btn=document.getElementById('mcp-install-execute-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(inp&&inp.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">La phrase doit être exactement le server_id</span>`;
    return;
  }
  const marker=_mcpGetMarker(serverId);
  if(!marker&&window._mcpLiveMode){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Marker absent ou expiré. Recréer une approbation.</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/install/execute`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({
        confirmed:true,
        confirmation_phrase:phrase,
        server_id:serverId,
        marker:marker||'00000000000000000000000000000000',
      }),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      // En cas d'échec backend, le marker côté serveur peut être consommé
      // irrécouvrable. Nettoyer la copie UI dans tous les cas.
      _mcpClearMarker(serverId);
      if(ticketId)_mcpClearTicketMapping(ticketId);
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    // Success : clear le marker côté UI (one-shot) + clear ticket mapping si présent
    _mcpClearMarker(serverId);
    if(ticketId)_mcpClearTicketMapping(ticketId);
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Install exécutée. Status : ${esc(d.status||'?')}</span>`;
      _mcpToast('Install exécutée (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucun subprocess, marker non consommé)</span>`;
      _mcpToast('Execute simulé (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpApprovals();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec execute: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpInstallExecute=submitMcpInstallExecute;

export function openMcpLocalCreateExecuteModal(serverId,ticketId){
  _mcpCloseApprovalModals();
  const liveMode=!!window._mcpLiveMode;
  const drySuffix=liveMode?'':' (dry_run)';
  const marker=_mcpGetMarker(serverId);
  const markerState=marker
    ?'<span style="color:var(--ok)">Marker prÃªt (one-shot)</span>'
    :'<span style="color:var(--danger)">Marker absent ou expirÃ© : approuvez Ã  nouveau le ticket.</span>';
  const modeWarning=liveMode
    ?'<div style="background:rgba(0,180,120,.1);border:1px solid var(--ok);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px">Mode LIVE : une demande locale sera matÃ©rialisÃ©e et dÃ©clarÃ©e dans le Catalog. Le serveur local ne sera pas dÃ©marrÃ© automatiquement.</div>'
    :'<div style="background:rgba(255,176,46,.12);border:1px solid var(--warn,#e0a23a);padding:8px;border-radius:4px;font-size:11px;margin-bottom:10px"><strong>Dry-run forcÃ©</strong> â€” aucune Ã©criture Catalog, marker non consommÃ©.</div>';
  const modal=document.createElement('div');
  modal.id='mcp-approval-action-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML=`<div class="card" style="width:min(520px,92vw);margin:0">
    <div class="card-title"><i data-lucide="box"></i> MatÃ©rialiser MCP local${drySuffix}</div>
    <div class="card-content">
      ${modeWarning}
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">server_id :</div>
      <div style="font-family:var(--mono);font-size:12px;background:var(--bg);padding:6px 8px;border-radius:4px;word-break:break-all">${esc(serverId)}</div>
      <div style="font-size:11px;margin-top:8px">${markerState}</div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-top:10px;margin-bottom:4px">Confirmation : retapez le server_id exact</label>
      <input id="mcp-local-create-phrase-input" class="input" style="width:100%;height:34px;font-size:12px" oninput="_mcpUpdateLocalCreateExecuteButton('${esc(serverId)}')" placeholder="${esc(serverId)}">
      <div id="mcp-approval-modal-result" style="margin-top:10px;font-size:11px;color:var(--muted)"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="btn" style="font-size:12px" onclick="_mcpCloseApprovalModals()">Annuler</button>
        <button class="btn" id="mcp-local-create-execute-confirm-btn" disabled style="font-size:12px;background:var(--accent);color:#fff;opacity:.5" onclick="submitMcpLocalCreateExecute('${esc(serverId)}','${esc(ticketId||'')}')">Confirmer${drySuffix}</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)_mcpCloseApprovalModals()});
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpLocalCreateExecuteModal=openMcpLocalCreateExecuteModal;

function _mcpUpdateLocalCreateExecuteButton(serverId){
  const input=document.getElementById('mcp-local-create-phrase-input');
  const btn=document.getElementById('mcp-local-create-execute-confirm-btn');
  if(!input||!btn)return;
  const valid=(input.value||'')===serverId;
  btn.disabled=!valid;
  btn.style.opacity=valid?'1':'.5';
}
window._mcpUpdateLocalCreateExecuteButton=_mcpUpdateLocalCreateExecuteButton;

export async function submitMcpLocalCreateExecute(serverId,ticketId){
  const input=document.getElementById('mcp-local-create-phrase-input');
  const btn=document.getElementById('mcp-local-create-execute-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const phrase=(input&&input.value)||'';
  if(phrase!==serverId){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Confirmation invalide</span>`;
    return;
  }
  const marker=_mcpGetMarker(serverId);
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/local-create/execute`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({
        confirmed:true,
        confirmation_phrase:phrase,
        server_id:serverId,
        marker:marker||'00000000000000000000000000000000',
      }),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      _mcpClearMarker(serverId);
      if(ticketId)_mcpClearLocalCreateTicketMapping(ticketId);
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    _mcpClearMarker(serverId);
    if(ticketId)_mcpClearLocalCreateTicketMapping(ticketId);
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Demande locale matÃ©rialisÃ©e. Status : ${esc(d.status||'?')}</span>`;
      _mcpToast('MCP local dÃ©clarÃ©','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcÃ©)</span>`;
      _mcpToast('Local-create simulÃ© (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpApprovals();_loadMcpCatalog();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Ã‰chec local-create: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='RÃ©essayer';}
  }
}
window.submitMcpLocalCreateExecute=submitMcpLocalCreateExecute;

export async function submitMcpApprovalReject(actionId){
  const ta=document.getElementById('mcp-reject-reason-input');
  const btn=document.getElementById('mcp-reject-confirm-btn');
  const out=document.getElementById('mcp-approval-modal-result');
  const reason=((ta&&ta.value)||'').trim();
  if(reason.length<3||reason.length>500){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Raison invalide (3-500 caractères requis)</span>`;
    return;
  }
  if(btn){btn.disabled=true;btn.style.opacity='.5';btn.textContent='En cours...';}
  try{
    const r=await fetch(`${API_BASE}/api/mcp/approvals/${encodeURIComponent(actionId)}/reject`,{
      method:'POST',
      headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`,'Content-Type':'application/json'},
      body:JSON.stringify({confirmed:true,reason}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d&&d.detail&&d.detail.error_code)||(d&&d.error_code)||`http_${r.status}`;
      throw new Error(code);
    }
    if(d.live_mode){
      if(out)out.innerHTML=`<span style="color:var(--ok)">Rejeté (live)</span>`;
      _mcpToast('Ticket rejeté (live)','ok');
    }else{
      if(out)out.innerHTML=`<span style="color:var(--warn,#e0a23a)">Simulation OK (dry_run forcé — aucune mutation queue)</span>`;
      _mcpToast('Rejet simulé (dry_run)','info');
    }
    setTimeout(()=>{_mcpCloseApprovalModals();_loadMcpApprovals();},1200);
  }catch(e){
    if(out)out.innerHTML=`<span style="color:var(--danger)">Erreur: ${esc(e.message)}</span>`;
    _mcpToast(`Échec reject: ${e.message}`,'error');
    if(btn){btn.disabled=false;btn.style.opacity='1';btn.textContent='Réessayer';}
  }
}
window.submitMcpApprovalReject=submitMcpApprovalReject;

async function _loadMcpApprovals(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  // Phase 20B-2 : purge sessionStorage des entrées expirées AVANT le rendu
  // (n'enlève jamais un mapping ticket valide en attente d'approbation).
  try{ _mcpPurgeExpiredInstallState(); }catch(_){ }
  box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Chargement...</div></div>';
  try{
    const [rp,rd]=await Promise.all([
      fetch(`${API_BASE}/api/mcp/approvals/pending`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
      fetch(`${API_BASE}/api/mcp/approvals/decisions`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
    ]);
    if(!rp.ok)throw new Error(`pending HTTP ${rp.status}`);
    if(!rd.ok)throw new Error(`decisions HTTP ${rd.status}`);
    const dp=await rp.json(),dd=await rd.json();
    if(dp.available===false){
      box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--muted)">Module ApprovalQueue non chargé.</div></div>`;
      return;
    }
    const pending=dp.pending||[],decisions=dd.decisions||[];
    let html='';
    html+='<div class="card" style="margin-bottom:8px"><div class="card-title"><i data-lucide="clock"></i> Tickets PENDING ('+pending.length+')</div><div class="card-content">';
    if(!pending.length){html+='<div style="color:var(--muted);font-size:12px">Aucun ticket en attente.</div>';}
    else{
      html+='<div class="list">';
      for(const p of pending){
        const drySuffix=_mcpLiveMode?'':' <span style="color:var(--muted);font-size:10px">(dry_run)</span>';
        // Phase 20B-2/20B-3 : détecte tickets install / activate via patterns
        // réels Phase 18/19 (tool_name = "mcp_install:<sid>" ou "mcp_activate:<sid>").
        // Si un marker est stocké pour ce server_id, on affiche le bouton correspondant.
        let installExecuteBtn='';
        const toolName=p.tool_name||'';
        if(toolName.startsWith(_MCP_INSTALL_TOOL_PREFIX)){
          const installServerId=toolName.substring(_MCP_INSTALL_TOOL_PREFIX.length);
          if(installServerId&&_mcpGetMarker(installServerId)){
            installExecuteBtn=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--accent);color:#fff" onclick="openMcpInstallExecuteModal('${esc(installServerId)}','${esc(p.id)}')">Exécuter install${drySuffix}</button>`;
          }
        }else if(toolName.startsWith(_MCP_ACTIVATE_TOOL_PREFIX)){
          const activateServerId=toolName.substring(_MCP_ACTIVATE_TOOL_PREFIX.length);
          if(activateServerId&&_mcpGetMarker(activateServerId)){
            installExecuteBtn=`<button class="btn" style="font-size:11px;padding:4px 10px;background:var(--ok);color:#fff" onclick="openMcpActivationExecuteModal('${esc(activateServerId)}','${esc(p.id)}')">Activer${drySuffix}</button>`;
          }
        }else if(toolName.startsWith(_MCP_LOCAL_CREATE_TOOL_PREFIX)){
          const localServerId=toolName.substring(_MCP_LOCAL_CREATE_TOOL_PREFIX.length);
          if(localServerId){
            _mcpSetLocalCreateTicketMapping(p.id,localServerId);
            if(_mcpGetMarker(localServerId)){
              installExecuteBtn=_mcpLocalCreateExecuteButton(localServerId,p.id,drySuffix);
            }
          }
        }
        html+=`<div class="list-item" style="flex-direction:column;align-items:stretch">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">
            <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${esc(p.id)}</span>
            <span class="pill warn">PENDING</span>
          </div>
          <div style="font-size:13px;margin-top:4px;font-weight:500">${esc(p.tool_name||'')}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">policy: ${esc(p.policy||'?')} · caller: ${esc(p.caller_kind||'?')} · proposed: ${esc((p.proposed_at||'').substring(0,19).replace('T',' '))}</div>
          <div style="font-size:11px;color:var(--accent);margin-top:2px;font-family:var(--mono)">${esc(p.risk_summary||'')}</div>
          <div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end">
            <button class="btn" style="font-size:11px;padding:4px 10px" onclick="openMcpApprovalApproveModal('${esc(p.id)}')">Approuver${drySuffix}</button>
            <button class="btn" style="font-size:11px;padding:4px 10px;background:var(--danger);color:#fff" onclick="openMcpApprovalRejectModal('${esc(p.id)}')">Rejeter${drySuffix}</button>
            ${installExecuteBtn}
          </div>
        </div>`;
      }
      html+='</div>';
    }
    html+='</div></div>';
    html+='<div class="card"><div class="card-title"><i data-lucide="check-square"></i> Décisions récentes ('+decisions.length+')</div><div class="card-content">';
    if(!decisions.length){html+='<div style="color:var(--muted);font-size:12px">Aucune décision historique.</div>';}
    else{
      html+='<div class="list">';
      for(const d of decisions){
        const out=String(d.outcome||'').toLowerCase();
        const outPill=out==='approved'?'ok':(out==='rejected'?'danger':'muted');
        let decisionActionBtn='';
        const decisionToolName=d.tool_name||'';
        const decisionActionId=d.action_id||'';
        if(out==='approved'&&decisionToolName.startsWith(_MCP_LOCAL_CREATE_TOOL_PREFIX)){
          const localServerId=decisionToolName.substring(_MCP_LOCAL_CREATE_TOOL_PREFIX.length);
          if(localServerId&&_mcpGetMarker(localServerId)){
            _mcpSetLocalCreateTicketMapping(decisionActionId,localServerId);
            decisionActionBtn=`<div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end">${_mcpLocalCreateExecuteButton(localServerId,decisionActionId,'')}</div>`;
          }
        }
        html+=`<div class="list-item" style="flex-direction:column;align-items:stretch">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">
            <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${esc(d.action_id||'')}</span>
            <span class="pill ${outPill}">${esc((d.outcome||'?').toUpperCase())}</span>
          </div>
          <div style="font-size:12px;margin-top:4px">${esc(d.tool_name||'')}</div>
          ${decisionActionBtn}
          <div style="font-size:11px;color:var(--muted);margin-top:2px">policy: ${esc(d.policy||'?')} · caller: ${esc(d.caller_kind||'?')} · ts: ${esc((d.ts||'').substring(0,19).replace('T',' '))}</div>
        </div>`;
      }
      html+='</div>';
    }
    html+='</div></div>';
    box.innerHTML=html;
    if(typeof lucide!=='undefined')lucide.createIcons();
  }catch(e){
    box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--danger)">Erreur: ${esc(e.message)}</div></div>`;
  }
}

async function _loadMcpWatcher(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  box.innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Chargement...</div></div>';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/watcher/snapshots`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.available===false){
      box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--muted)">Module RuntimeWatcher non chargé.</div></div>`;
      return;
    }
    const snaps=d.snapshots||[];
    let html='<div class="card" style="margin-bottom:8px"><div class="card-content" style="font-size:11px;color:var(--muted)"><i data-lucide="info" style="width:11px;height:11px"></i> Source : snapshots disque persistés (source=persisted, live=false). Le watcher live (mémoire + runners actifs) est reporté à Phase 20B/21.</div></div>';
    if(!snaps.length){
      html+='<div class="card"><div class="card-content" style="color:var(--muted);padding:30px;text-align:center;font-size:13px">Aucun snapshot persisté. Le RuntimeWatcher n\'est pas encore branché au runtime.</div></div>';
    }else{
      html+='<div class="list">';
      for(const s of snaps){
        const healthMap={running:'ok',crashed:'danger',stopped:'muted',init:'muted',unknown:'muted'};
        const stateCls=healthMap[(s.process_state||'').toLowerCase()]||'muted';
        const uptime=Math.floor(s.uptime_seconds||0);
        html+=`<div class="list-item" style="flex-direction:column;align-items:stretch">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">
            <div style="font-weight:600;font-size:13px;font-family:var(--mono)">${esc(s.server_id)}</div>
            <span class="pill ${stateCls}">${esc((s.process_state||'?').toUpperCase())}</span>
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">uptime: ${uptime}s · restarts: ${s.restart_count||0} · crash_window: ${s.crash_count_window||0}</div>
          ${s.last_error_code?`<div style="font-size:11px;color:var(--danger);margin-top:2px;font-family:var(--mono)">last_error_code: ${esc(s.last_error_code)}</div>`:''}
          ${s.last_transition_ts?`<div style="font-size:11px;color:var(--muted);margin-top:2px">last_transition: ${esc((s.last_transition_ts||'').substring(0,19).replace('T',' '))}</div>`:''}
        </div>`;
      }
      html+='</div>';
    }
    box.innerHTML=html;
    if(typeof lucide!=='undefined')lucide.createIcons();
  }catch(e){
    box.innerHTML=`<div class="card"><div class="card-content" style="color:var(--danger)">Erreur: ${esc(e.message)}</div></div>`;
  }
}

const _MCP_AUDIT_COMPONENTS=[
  'catalog','approval_queue','runtime_watcher','orchestrator',
  'discovery','install_orchestrator','activation','policy_resolver','policy_attributor'
];

async function _loadMcpAuditDiscovery(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  const opts=_MCP_AUDIT_COMPONENTS.map(c=>`<option value="${c}">${esc(c)}</option>`).join('');
  box.innerHTML=`
    <div class="card" style="margin-bottom:8px">
      <div class="card-title"><i data-lucide="scroll-text"></i> Audit log</div>
      <div class="card-content" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <label style="font-size:11px;color:var(--muted)">Composant</label>
        <select id="mcp-audit-component" class="input" style="width:auto;height:30px;font-size:12px;padding:0 8px">${opts}</select>
        <label style="font-size:11px;color:var(--muted)">Limit</label>
        <input id="mcp-audit-limit" class="input" type="number" value="50" min="1" max="500" style="width:80px;height:30px;font-size:12px;padding:0 8px">
        <button class="btn" style="font-size:11px" onclick="_mcpLoadAudit()"><i data-lucide="refresh-cw" style="width:12px;height:12px"></i> Charger</button>
      </div>
      <div class="card-content" id="mcp-audit-events" style="padding-top:0">
        <div style="color:var(--muted);font-size:12px">Sélectionnez un composant.</div>
      </div>
    </div>
    <div class="card">
      <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <span><i data-lucide="search"></i> Discovery reports persistés</span>
        <button class="btn" style="font-size:11px" onclick="_mcpLoadDiscoveryReports()"><i data-lucide="refresh-cw" style="width:12px;height:12px"></i> Rafraîchir</button>
      </div>
      <div class="card-content" id="mcp-discovery-reports">
        <div style="color:var(--muted);font-size:12px">Chargement...</div>
      </div>
    </div>`;
  if(typeof lucide!=='undefined')lucide.createIcons();
  _mcpLoadAudit();
  _mcpLoadDiscoveryReports();
}

async function _mcpLoadAudit(){
  const comp=document.getElementById('mcp-audit-component')?.value||'catalog';
  const limit=parseInt(document.getElementById('mcp-audit-limit')?.value||'50',10);
  const target=document.getElementById('mcp-audit-events');if(!target)return;
  target.innerHTML='<div style="color:var(--muted);font-size:12px">Chargement...</div>';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/audit/${encodeURIComponent(comp)}?limit=${limit}`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const events=d.events||[];
    if(!events.length){
      target.innerHTML='<div style="color:var(--muted);font-size:12px">Aucun event dans l\'audit log.</div>';
      return;
    }
    const lines=events.map(e=>JSON.stringify(e)).join('\n');
    target.innerHTML=`<div class="code-block" style="max-height:380px">${esc(lines)}</div>`;
  }catch(e){
    target.innerHTML=`<div style="color:var(--danger);font-size:12px">Erreur: ${esc(e.message)}</div>`;
  }
}
window._mcpLoadAudit=_mcpLoadAudit;

async function _mcpLoadDiscoveryReports(){
  const target=document.getElementById('mcp-discovery-reports');if(!target)return;
  target.innerHTML='<div style="color:var(--muted);font-size:12px">Chargement...</div>';
  try{
    const r=await fetch(`${API_BASE}/api/mcp/discovery/reports`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.available===false){
      target.innerHTML='<div style="color:var(--muted);font-size:12px">Module Discovery non chargé.</div>';
      return;
    }
    const reports=d.reports||[];
    if(!reports.length){
      target.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center;font-size:12px">Aucun rapport Discovery persisté.</div>';
      return;
    }
    let html='<div class="list">';
    for(const rep of reports){
      const ts=esc((rep.ts||'').substring(0,19).replace('T',' '));
      const sid=esc(rep.server_id||'');
      const fname=esc(rep.filename||'');
      const tsKey=fname.replace('.json','').replace(sid+'_','');
      html+=`<div class="list-item" style="flex-direction:column;align-items:stretch;cursor:pointer" onclick="openMcpDiscoveryReport('${sid}','${tsKey}')">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-weight:600;font-size:13px;font-family:var(--mono)">${sid}</span>
          <span style="font-size:11px;color:var(--muted)">${ts}</span>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">discovered: ${rep.discovered_count||0} · proposed: ${rep.proposed_count||0} · refused: ${rep.refused_count||0} · invalid: ${rep.invalid_count||0} · errors: ${rep.error_count||0}</div>
      </div>`;
    }
    html+='</div>';
    target.innerHTML=html;
  }catch(e){
    target.innerHTML=`<div style="color:var(--danger);font-size:12px">Erreur: ${esc(e.message)}</div>`;
  }
}
window._mcpLoadDiscoveryReports=_mcpLoadDiscoveryReports;

export async function openMcpDiscoveryReport(serverId,ts){
  document.querySelectorAll('#mcp-discovery-modal').forEach(n=>n.remove());
  const modal=document.createElement('div');
  modal.id='mcp-discovery-modal';
  modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML='<div class="card" style="width:min(720px,94vw);max-height:90vh;overflow-y:auto;margin:0"><div class="card-title"><i data-lucide="file-json"></i> Discovery report</div><div class="card-content" id="mcp-disc-body" style="color:var(--muted)">Chargement...</div></div>';
  document.body.appendChild(modal);
  modal.addEventListener('click',e=>{if(e.target===modal)closeMcpDiscoveryReport()});
  try{
    const r=await fetch(`${API_BASE}/api/mcp/discovery/reports/${encodeURIComponent(serverId)}/${encodeURIComponent(ts)}`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const body=document.getElementById('mcp-disc-body');
    if(body){
      const raw=JSON.stringify(d.report||{},null,2);
      body.innerHTML=`<div class="code-block" style="max-height:60vh">${esc(raw)}</div><div style="margin-top:14px;text-align:right"><button class="btn" style="font-size:12px" onclick="closeMcpDiscoveryReport()">Fermer</button></div>`;
    }
  }catch(e){
    const body=document.getElementById('mcp-disc-body');
    if(body)body.innerHTML=`<div style="color:var(--danger)">Erreur: ${esc(e.message)}</div>`;
  }
  if(typeof lucide!=='undefined')lucide.createIcons();
}
window.openMcpDiscoveryReport=openMcpDiscoveryReport;

export function closeMcpDiscoveryReport(){document.querySelectorAll('#mcp-discovery-modal').forEach(n=>n.remove())}
window.closeMcpDiscoveryReport=closeMcpDiscoveryReport;

// ════════════════════════════════════════════════════════════════════════════
// PHASE 21 — Onglet Diagnostics (lecture seule, refresh manuel, pas de polling)
// ════════════════════════════════════════════════════════════════════════════
async function _loadMcpDiagnostics(){
  const box=document.getElementById('mcp-tab-content');if(!box)return;
  // Phase G/H polish — hero user-facing + dev details collapsible.
  box.innerHTML=`
    <div class="card" id="mcp-health-hero" style="margin-bottom:10px">
      <div class="card-content" style="padding:16px">
        <div id="mcp-health-status-line" style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
          <div id="mcp-health-emoji" style="font-size:36px;line-height:1">⏳</div>
          <div style="flex:1;min-width:0">
            <div id="mcp-health-title" style="font-size:16px;font-weight:600">Diagnostic en cours…</div>
            <div id="mcp-health-subtitle" style="font-size:12px;color:var(--muted);margin-top:2px">Analyse de l'état de tes MCPs.</div>
          </div>
          <button class="btn" style="font-size:11px;padding:5px 12px" data-action="refreshMcpDiagnostics">
            <i data-lucide="refresh-cw" style="width:11px;height:11px"></i> Rafraîchir
          </button>
        </div>
        <div id="mcp-health-counts" style="display:flex;flex-wrap:wrap;gap:14px;justify-content:space-around;padding-top:10px;border-top:1px solid var(--border)">
          <div style="text-align:center"><div id="mcp-health-count-active" style="font-size:20px;font-weight:700">—</div><div style="color:var(--muted);font-size:11px">Actifs</div></div>
          <div style="text-align:center"><div id="mcp-health-count-installed" style="font-size:20px;font-weight:700">—</div><div style="color:var(--muted);font-size:11px">Installés</div></div>
          <div style="text-align:center"><div id="mcp-health-count-pending" style="font-size:20px;font-weight:700">—</div><div style="color:var(--muted);font-size:11px">En attente</div></div>
          <div style="text-align:center"><div id="mcp-health-count-issues" style="font-size:20px;font-weight:700;color:var(--warn)">—</div><div style="color:var(--muted);font-size:11px">À surveiller</div></div>
        </div>
        <div id="mcp-health-issues" style="margin-top:10px"></div>
        <div style="margin-top:12px;padding:8px;background:var(--bg);border-radius:4px;display:flex;align-items:center;gap:8px">
          <i data-lucide="message-circle" style="width:13px;height:13px;color:var(--muted)"></i>
          <span style="font-size:11px;color:var(--muted);flex:1">Besoin d'un état complet ? Demande à Lumena dans le chat.</span>
          <button class="btn" style="font-size:11px;padding:4px 10px" onclick="window._mcpLibraryPrefillChat&amp;&amp;window._mcpLibraryPrefillChat('Fais-moi un diagnostic complet de mes MCPs')">
            <i data-lucide="send" style="width:11px;height:11px"></i> Demander à Lumena
          </button>
        </div>
      </div>
    </div>

    <details id="mcp-diag-dev-details" style="margin-top:6px">
      <summary style="cursor:pointer;font-size:12px;color:var(--muted);padding:6px 4px;user-select:none">
        <i data-lucide="wrench" style="width:11px;height:11px"></i> Détails techniques (développeur)
      </summary>
      <div class="card" style="margin-top:6px"><div class="card-content">
        <div style="color:var(--muted);font-size:11px;margin-bottom:10px">
          Rapports bruts lecture seule (Phase 21). Aucun auto-fix.
        </div>
        <div id="mcp-diag-readiness"          style="margin-bottom:10px">Chargement readiness…</div>
        <div id="mcp-diag-observability"      style="margin-bottom:10px">Chargement observability…</div>
        <div id="mcp-diag-keys"               style="margin-bottom:10px">Chargement keys…</div>
        <div id="mcp-diag-audit-integrity"    style="margin-bottom:10px">Chargement audit integrity…</div>
        <div id="mcp-diag-coherence"          style="margin-bottom:10px">Chargement coherence…</div>
      </div></div>
    </details>`;
  if(typeof lucide!=='undefined')lucide.createIcons();
  await _mcpDiagRefreshAll();
  // Synthèse user-friendly en parallèle (utilise /api/mcp/library + /api/mcp/readiness)
  _mcpHealthRefreshHero().catch(()=>{});
}

async function _mcpHealthRefreshHero(){
  const titleEl=document.getElementById('mcp-health-title');
  const subEl=document.getElementById('mcp-health-subtitle');
  const emojiEl=document.getElementById('mcp-health-emoji');
  const issuesBox=document.getElementById('mcp-health-issues');
  const setCount=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=String(v);};
  if(!titleEl||!subEl||!emojiEl)return;
  let library=null,readiness=null;
  try{
    const [r1,r2]=await Promise.all([
      fetch(`${API_BASE}/api/mcp/library`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
      fetch(`${API_BASE}/api/mcp/readiness`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}}),
    ]);
    library=await r1.json();
    readiness=await r2.json();
  }catch(_){ }
  const counts=(library&&library.counts)||{};
  const active=counts.active||0;
  const installed=counts.installed||0;
  const declared=counts.declared||0;
  const quarantined=counts.quarantined||0;
  setCount('mcp-health-count-active',active);
  setCount('mcp-health-count-installed',installed);
  setCount('mcp-health-count-pending',declared);
  setCount('mcp-health-count-issues',quarantined);
  // Détermination statut user-friendly
  const overall=(readiness&&readiness.overall)||'unknown';
  const coh=(readiness&&readiness.coherence_overall)||'unknown';
  let level='ok';
  if(overall==='not_ready'||overall==='error'||coh==='fail')level='warn';
  if(quarantined>0)level='warn';
  // Affichage
  if(level==='ok'&&(active>0||installed>0)){
    emojiEl.textContent='✅';
    titleEl.textContent='Tout va bien';
    subEl.textContent=`${active} MCP actif${active>1?'s':''}, ${installed} installé${installed>1?'s':''}. Aucune anomalie détectée.`;
  }else if(level==='ok'&&active===0&&installed===0){
    emojiEl.textContent='💤';
    titleEl.textContent='Aucun MCP installé';
    subEl.textContent='Tu peux demander à Lumena d\'en trouver un pour une capacité précise.';
  }else{
    emojiEl.textContent='⚠️';
    titleEl.textContent='Quelques points à surveiller';
    subEl.textContent='Déroule les détails techniques ou demande à Lumena un diagnostic.';
  }
  // Issues humanisées
  const issues=[];
  if(quarantined>0)issues.push(`${quarantined} MCP en quarantaine`);
  if(coh==='fail')issues.push('Vérification de cohérence en échec');
  if(readiness&&readiness.keys_status_ok===false)issues.push('Clés d\'intégrité manquantes ou invalides');
  if(readiness&&readiness.audit_integrity_ok===false)issues.push('Fichier d\'audit altéré');
  if(issues.length>0&&issuesBox){
    issuesBox.innerHTML=`<ul style="margin:8px 0 0 16px;padding:0;font-size:12px;color:var(--text)">${issues.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`;
  }else if(issuesBox){
    issuesBox.innerHTML='';
  }
}
window._mcpHealthRefreshHero=_mcpHealthRefreshHero;
window._loadMcpDiagnostics=_loadMcpDiagnostics;

export async function refreshMcpDiagnostics(){ await _mcpDiagRefreshAll(); }
window.refreshMcpDiagnostics=refreshMcpDiagnostics;

async function _mcpDiagRefreshAll(){
  await Promise.all([
    _mcpDiagLoadReadiness(),
    _mcpDiagLoadObservability(),
    _mcpDiagLoadKeys(),
    _mcpDiagLoadAuditIntegrity(),
    _mcpDiagLoadCoherence(),
  ]);
}

async function _mcpDiagLoadReadiness(){
  const slot=document.getElementById('mcp-diag-readiness');if(!slot)return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/readiness`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json();
    slot.innerHTML=`<div><b>Readiness</b> — overall: <code>${esc(d.overall||'?')}</code>, coherence: <code>${esc(d.coherence_overall||'?')}</code>, keys: <code>${d.keys_status_ok?'ok':'ko'}</code>, audit: <code>${d.audit_integrity_ok?'ok':'ko'}</code>, singletons: <code>${d.singletons_all_loaded?'ok':'partial'}</code></div>`;
  }catch(e){ slot.innerHTML=`<div style="color:var(--danger)">Readiness erreur: ${esc(e.message)}</div>`; }
}

async function _mcpDiagLoadObservability(){
  const slot=document.getElementById('mcp-diag-observability');if(!slot)return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/observability/overview`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json();
    const c=d.catalog_counts||{};
    slot.innerHTML=`<div><b>Observability</b> — catalog: declared=${c.declared||0}, installed=${c.installed||0}, active=${c.active||0}, quarantined=${c.quarantined||0} | approvals pending: ${d.approvals_pending_count||0} | watcher snapshots: ${d.watcher_persisted_snapshots||0} | live=${d.modes&&d.modes.live_mode?'1':'0'} autoapprove=${d.modes&&d.modes.autoapprove_live_mode?'1':'0'} trust=${d.modes&&d.modes.trust_live_mode?'1':'0'}</div>`;
  }catch(e){ slot.innerHTML=`<div style="color:var(--danger)">Observability erreur: ${esc(e.message)}</div>`; }
}

async function _mcpDiagLoadKeys(){
  const slot=document.getElementById('mcp-diag-keys');if(!slot)return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/keys/status`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json();
    const k=d.keys||{};
    const fmt=(o)=>o?`present=${o.present?'1':'0'} fmt=${o.format_valid?'ok':'ko'}`:'?';
    slot.innerHTML=`<div><b>Keys</b> — auto_approve fernet: ${fmt(k.auto_approve_fernet)} | auto_approve hmac: ${fmt(k.auto_approve_hmac)} | approval_queue fernet: ${fmt(k.approval_queue_fernet)} | catalog hmac: ${fmt(k.catalog_hmac)}</div>`;
  }catch(e){ slot.innerHTML=`<div style="color:var(--danger)">Keys erreur: ${esc(e.message)}</div>`; }
}

async function _mcpDiagLoadAuditIntegrity(){
  const slot=document.getElementById('mcp-diag-audit-integrity');if(!slot)return;
  const components=['admin_ui','catalog','approval_queue','runtime_watcher','activation','install_orchestrator','discovery','policy_resolver'];
  const rows=[];
  for(const c of components){
    try{
      const r=await fetch(`${API_BASE}/api/mcp/audit-integrity/`+encodeURIComponent(c),{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
      const d=await r.json();
      rows.push(`<tr><td>${esc(c)}</td><td>${d.file_present?'oui':'non'}</td><td>${d.size_bytes||0}</td><td>${d.line_count||0}</td><td>${d.malformed_lines||0}</td><td>${d.size_warning?'⚠':''}</td></tr>`);
    }catch(_){ rows.push(`<tr><td>${esc(c)}</td><td colspan="5" style="color:var(--danger)">err</td></tr>`); }
  }
  slot.innerHTML=`<div><b>Audit integrity</b><table style="width:100%;font-size:12px;margin-top:4px"><thead><tr><th>Component</th><th>Présent</th><th>Bytes</th><th>Lignes</th><th>Malformed</th><th>Warn</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

async function _mcpDiagLoadCoherence(){
  const slot=document.getElementById('mcp-diag-coherence');if(!slot)return;
  try{
    const r=await fetch(`${API_BASE}/api/mcp/coherence/check`,{headers:{'Authorization':`Bearer ${ADMIN_TOKEN}`}});
    const d=await r.json();
    const checks=(d.checks||[]).map(c=>`<li>${esc(c.name)}: <code>${esc(c.status)}</code> (${c.details_count})</li>`).join('');
    slot.innerHTML=`<div><b>Coherence</b> — overall: <code>${esc(d.overall_status||'?')}</code><ul style="margin:4px 0 0 16px">${checks}</ul></div>`;
  }catch(e){ slot.innerHTML=`<div style="color:var(--danger)">Coherence erreur: ${esc(e.message)}</div>`; }
}
