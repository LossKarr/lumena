/* ============================================================
   API & DATA LOADERS — Lumena Control Panel
   ============================================================ */
export async function loadStatus(){
  if(statusRequestInFlight)return;statusRequestInFlight=true;
  try{
    const _sh={};if(ADMIN_TOKEN)_sh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const res=await fetch(`${API_BASE}/api/status`,{headers:_sh});const d=await res.json();
    if(d.status_poll_recommended_ms>0)statusPollRecommendedMs=Math.round(d.status_poll_recommended_ms);

    if(d.tool_count!=null)setText('stat-tools',d.tool_count);
    setText('stat-memories',d.memory_count||'—');
    setText('stat-symbols',d.symbols_count||'—');
    if(d.tool_count!=null)setText('badge-tools',d.tool_count);

    // Journal badge
    if(d.journal_total!=null){
      const jb=document.getElementById('badge-journal');
      if(jb){jb.textContent=d.journal_total;jb.style.background=d.journal_total>0?'var(--accent)':'var(--muted)'}
    }

    const skills=typeof d.skills_loaded==='number'?d.skills_loaded:0;
    const skillsEl=document.getElementById('stat-skills');
    if(skillsEl){skillsEl.textContent=skills;skillsEl.className='stat-value '+(d.skills_auto_activation?'ok':'warn')}

    setText('mood-value',d.mood||'neutral');

    // Skills actifs
    const activeSkills=Array.isArray(d.skills_last_active)?d.skills_last_active:[];
    const asl=document.getElementById('skills-active-list');
    if(asl)asl.innerHTML=activeSkills.length===0?'<span class="pill">Aucun</span>':activeSkills.slice(0,8).map(s=>`<span class="pill ok">${esc(s)}</span>`).join('');

    // Telegram
    const tgBadge=document.getElementById('stat-telegram');
    const tgDot=document.getElementById('tg-dot');
    const tgText=document.getElementById('tg-status-text');
    if(tgBadge){
      if(d.telegram_running){tgBadge.textContent='ON';tgBadge.style.background='var(--ok)';if(tgDot)tgDot.classList.add('ok');if(tgText)tgText.textContent='Connecte'}
      else if(d.telegram_conflict_seen){tgBadge.textContent='CONFLICT';tgBadge.style.background='var(--danger)';if(tgDot)tgDot.classList.remove('ok');if(tgText)tgText.textContent='Conflit detecte'}
      else{tgBadge.textContent='OFF';tgBadge.style.background='var(--muted)';if(tgDot)tgDot.classList.remove('ok');if(tgText)tgText.textContent='Desactive'}
    }

    // WhatsApp
    const waBadge=document.getElementById('stat-whatsapp');
    const waDot=document.getElementById('wa-dot');
    const waText=document.getElementById('wa-status-text');
    if(waBadge){
      if(d.whatsapp_running){waBadge.textContent='ON';waBadge.style.background='var(--ok)';if(waDot)waDot.classList.add('ok');if(waText)waText.textContent='Connecte'}
      else{waBadge.textContent='OFF';waBadge.style.background='var(--muted)';if(waDot)waDot.classList.remove('ok');if(waText)waText.textContent='Desactive'}
    }

    // Autonomy
    const autoBadge=document.getElementById('stat-autonomy');
    const autoDot=document.getElementById('auto-dot');
    const autoText=document.getElementById('auto-status-text');
    if(autoBadge){
      if(d.autonomy_running&&d.autonomy_action_execution){autoBadge.textContent='ON';autoBadge.style.background='var(--ok)';if(autoDot)autoDot.classList.add('ok');if(autoText)autoText.textContent='Daemon actif'}
      else{autoBadge.textContent='OFF';autoBadge.style.background='var(--muted)';if(autoDot)autoDot.classList.remove('ok');if(autoText)autoText.textContent='Desactive'}
    }

    // Overview cards — text-only updates (layout is handled by loadOverview)
    setText('ov-memories',d.memory_count||'—');
    setText('ov-skills',skills);
    setText('ov-mood',d.mood||'—');
    setText('ov-energy',d.energy?`Energie: ${d.energy}`:'—');

    // Badge files
    if(d.symbols_count)setText('badge-files',d.symbols_count);

    // Tasks badge (scheduler tasks + orchestrator active)
    const tasksBadge=document.getElementById('badge-tasks');
    const schedTasks=d.scheduler_tasks_active||0;
    const orchTasks=(d.tasks_backlog||0)+(d.tasks_waiting_io||0);
    const totalActiveTasks=schedTasks+orchTasks;
    if(tasksBadge){tasksBadge.textContent=totalActiveTasks;tasksBadge.style.background=totalActiveTasks>0?'var(--ok)':'var(--muted)'}

    // Alerts badge
    if(d.alerts_total!=null){
      const ab=document.getElementById('badge-alerts');
      if(ab){ab.textContent=d.alerts_total;ab.style.background=d.alerts_total>0?'var(--danger)':'var(--muted)'}
    }

    lastStatusData=d;
    checkHealth();
    loadVoiceStatus();
  }catch(e){console.error('Status error:',e)}finally{statusRequestInFlight=false}
}

export async function loadRepoMap(){
  try{
    const _rmh={};if(ADMIN_TOKEN)_rmh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/repo-map`,{headers:_rmh});const d=await r.json();
    document.getElementById('repomap-stats').innerHTML=`<div style="display:flex;gap:20px"><div><strong>${d.stats.total_files}</strong> fichiers</div><div><strong>${d.stats.total_symbols}</strong> symboles</div></div>`;
    document.getElementById('repomap-languages').innerHTML=Object.entries(d.stats.languages||{}).map(([k,v])=>`<span class="pill accent">${k}: ${v}</span>`).join(' ');
    document.getElementById('repomap-content').textContent=d.map;
  }catch(e){document.getElementById('repomap-content').textContent='Erreur de chargement'}
}

export async function loadRules(){
  try{
    const _ruh={};if(ADMIN_TOKEN)_ruh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/rules`,{headers:_ruh});const d=await r.json();
    document.getElementById('rules-info').innerHTML=`<strong>Projet:</strong> ${esc(d.project_name)}<br><strong>Langage:</strong> ${esc(d.language)}<br><strong>Style:</strong> ${esc(d.style_guide)}`;
    document.getElementById('rules-conventions').innerHTML=(d.conventions||[]).map(c=>`<li>${esc(c)}</li>`).join('');
    document.getElementById('rules-always').innerHTML=(d.always||[]).map(a=>`<li>${esc(a)}</li>`).join('');
    document.getElementById('rules-never').innerHTML=(d.do_not||[]).map(n=>`<li>${esc(n)}</li>`).join('');
  }catch(e){document.getElementById('rules-info').textContent='Erreur'}
}

export async function loadInstincts(){
  try{
    const _inh={};if(ADMIN_TOKEN)_inh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/instincts`,{headers:_inh});const d=await r.json();
    if(!d.instincts.length){document.getElementById('instincts-list').innerHTML='<div class="card"><div class="card-content" style="color:var(--muted)">Aucun instinct appris.</div></div>';return}
    document.getElementById('instincts-list').innerHTML=d.instincts.map(i=>`
      <div class="instinct-item">
        <div class="instinct-pattern">${esc(i.pattern)}</div>
        <div class="instinct-response">→ ${esc(i.response)}</div>
        <div class="instinct-confidence">Confiance: ${Math.round(i.confidence*100)}%</div>
      </div>`).join('');
  }catch(e){document.getElementById('instincts-list').innerHTML='Erreur'}
}

export async function loadTools(){
  try{
    const _th={};if(ADMIN_TOKEN)_th['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/tools`,{headers:_th});const d=await r.json();
    allTools=d.tools;renderTools(allTools);setText('stat-tools',d.count);setText('badge-tools',d.count);setText('ov-tools',d.count);setText('welcome-tools',d.count);
  }catch(e){document.getElementById('tools-grid').innerHTML='Erreur'}
}

export function renderTools(tools){
  document.getElementById('tools-grid').innerHTML=tools.map(t=>`
    <div class="tool-box"><div class="tool-name">${esc(t.name)}</div><div class="tool-desc">${esc(t.description)}</div></div>
  `).join('');
}

export function filterTools(){
  const q=document.getElementById('tools-search').value.toLowerCase();
  renderTools(allTools.filter(t=>t.name.toLowerCase().includes(q)||t.description.toLowerCase().includes(q)));
}

export async function loadEmotions(){
  const el=document.getElementById('emotions-display');
  try{
    const _eh={};if(ADMIN_TOKEN)_eh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/emotion`,{headers:_eh});
    if(!r.ok){el.innerHTML='<div style="color:var(--muted)">Systeme emotionnel indisponible</div>';return;}
    const d=await r.json();
    const moodLabels={neutral:'Neutre',happy:'Heureuse',curious:'Curieuse',excited:'Excitee',thoughtful:'Pensive',playful:'Joueuse',tired:'Fatiguee',bored:'Ennuyee',proud:'Fiere',touched:'Touchee'};
    const label=moodLabels[d.mood]||d.mood;
    const padBars=[
      {name:'Plaisir',key:'pleasure',color:'var(--ok,#22c55e)'},
      {name:'Activation',key:'arousal',color:'var(--accent,#f59f4a)'},
      {name:'Dominance',key:'dominance',color:'var(--info,#3b82f6)'}
    ];
    el.innerHTML=`
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
        <div style="width:48px;height:48px;border-radius:12px;background:rgba(245,159,74,.12);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="heart" style="width:24px;height:24px;color:var(--accent)"></i></div>
        <div>
          <div style="font-size:20px;font-weight:700;color:var(--text-strong)" id="emotion-mood-label">${esc(label)}</div>
          <div style="font-size:12px;color:var(--muted);margin-top:2px">Energie: ${esc(String(d.energy))}</div>
        </div>
      </div>
      <div style="margin-bottom:16px">
        ${padBars.map(b=>{
          const v=d[b.key]||0;
          const pct=Math.round((v+1)*50);
          return`<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px"><span style="color:var(--muted)">${b.name}</span><span style="font-weight:600;color:var(--text)">${v>=0?'+':''}${v.toFixed(2)}</span></div><div style="height:6px;background:var(--bg-hover);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${b.color};border-radius:3px;transition:width .3s"></div></div></div>`;
        }).join('')}
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
        <div style="text-align:center;padding:8px;background:var(--bg-hover);border-radius:8px"><div style="font-size:18px;font-weight:700;color:var(--text-strong)">${d.compliments_received||0}</div><div style="font-size:10px;color:var(--muted)">Compliments</div></div>
        <div style="text-align:center;padding:8px;background:var(--bg-hover);border-radius:8px"><div style="font-size:18px;font-weight:700;color:var(--text-strong)">${d.tasks_completed||0}</div><div style="font-size:10px;color:var(--muted)">Taches</div></div>
        <div style="text-align:center;padding:8px;background:var(--bg-hover);border-radius:8px"><div style="font-size:18px;font-weight:700;color:var(--accent)">${Math.round(d.happiness||50)}</div><div style="font-size:10px;color:var(--muted)">Bonheur</div></div>
      </div>
      <button class="btn danger" style="width:100%;font-size:12px;padding:6px 0" onclick="window._resetEmotion()">Reinitialiser l'humeur</button>`;
  }catch(e){el.innerHTML='<div style="color:var(--muted)">Erreur chargement emotions</div>'}
}

// Reset emotion handler
window._resetEmotion=function(){
  if(!confirm("Reinitialiser l'humeur a Neutre ?"))return;
  fetch(`${API_BASE}/api/emotion/mood`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${ADMIN_TOKEN}`},body:JSON.stringify({mood:'neutral'})})
    .then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()})
    .then(()=>loadEmotions())
    .catch(e=>alert('Erreur: '+e.message));
};

export async function loadHooks(){
  try{
    const _hh={};if(ADMIN_TOKEN)_hh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/hooks`,{headers:_hh});const d=await r.json();
    if(!d.hooks.length){document.getElementById('hooks-list').innerHTML='<div style="color:var(--muted)">Aucun hook enregistre</div>';return}
    document.getElementById('hooks-list').innerHTML=d.hooks.map(h=>`
      <div class="list-item"><div><div class="list-item-title">${esc(h.name)}</div><div class="list-item-sub">${esc(h.event)} — Priorite: ${h.priority}</div>${h.description?`<div style="font-size:11px;color:var(--muted);margin-top:4px">${esc(h.description)}</div>`:''}</div><span class="pill ${h.enabled?'ok':'warn'}">${h.enabled?'Actif':'Inactif'}</span></div>
    `).join('');
  }catch(e){document.getElementById('hooks-list').innerHTML='Erreur'}
}

export async function loadVoiceStatus(){
  initVoicePanel();
  try{
    const _vs={};if(ADMIN_TOKEN)_vs['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/voice/status`,{headers:_vs});const d=await r.json();updateVoiceUI(d);await Promise.all([loadVoiceSettings(),loadVoicePerformanceProfiles()]);
  }catch(e){}
}

let _lastVoiceStatus={};
function _voiceHeaders(json=false){const h={};if(json)h['Content-Type']='application/json';if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;return h}
function _setVoiceSettingsMessage(text,type='info'){
  const msg=document.getElementById('voice-settings-message');if(!msg)return;
  msg.textContent=text||'';msg.classList.remove('is-info','is-success','is-error');if(text)msg.classList.add(`is-${type}`);
}
function _setVoiceControlMessage(text,type='info'){
  const msg=document.getElementById('voice-control-message');if(!msg)return;
  msg.textContent=text||'';msg.classList.remove('is-info','is-success','is-error');if(text)msg.classList.add(`is-${type}`);
}
async function _voiceResponseJson(response){
  try{return await response.json()}catch(_e){return {detail:`Réponse serveur invalide (HTTP ${response.status})`}}
}
export function initVoicePanel(){
  const root=document.getElementById('panel-voice');if(!root||root.dataset.ready==='1')return;root.dataset.ready='1';
  const tabs=[...root.querySelectorAll('[data-voice-tab]')];
  const activate=(name)=>{tabs.forEach(b=>{const on=b.dataset.voiceTab===name;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on))});root.querySelectorAll('[data-voice-page]').forEach(p=>{const on=p.dataset.voicePage===name;p.classList.toggle('active',on);p.hidden=!on})};
  tabs.forEach((button,index)=>{button.addEventListener('click',()=>activate(button.dataset.voiceTab));button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const next=(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();activate(tabs[next].dataset.voiceTab)})});
  document.getElementById('voice-setting-activation')?.addEventListener('change',event=>{if(event.detail?.source!=='config-load')_setVoiceSettingsMessage('')});
}

export async function loadVoiceSettings(){
  const root=document.getElementById('panel-voice');if(!root)return;
  try{const r=await fetch(`${API_BASE}/api/config`,{headers:_voiceHeaders()});if(!r.ok)return;const d=await r.json();const values={};(d.items||[]).forEach(item=>values[item.key]=item.value);
    const assign=(id,key)=>{const el=document.getElementById(id);if(el&&values[key]!==undefined){el.value=values[key];el.dispatchEvent(new CustomEvent('change',{bubbles:true,detail:{source:'config-load'}}))}};assign('voice-setting-mode','LUMENA_VOICE_V2_MODE');assign('voice-setting-activation','LUMENA_VOICE_ACTIVATION_MODE');assign('voice-setting-language','LUMENA_STT_LANGUAGE');assign('voice-setting-wake','LUMENA_VOICE_WAKE_PHRASE');
  }catch(_e){}
}

async function _restartVoiceRuntimeIfRunning(){
  if(!_lastVoiceStatus.running)return {restarted:false};
  const r=await fetch(`${API_BASE}/api/voice/restart`,{method:'POST',headers:_voiceHeaders()});
  const d=await _voiceResponseJson(r);
  if(!r.ok||!d.running)throw new Error(d.detail||d.message||'Redémarrage de l’écoute impossible');
  updateVoiceUI(d);
  return d;
}

async function _refreshVoiceConfigurationSurfaces(){
  const tasks=[loadVoiceSettings(),loadVoicePerformanceProfiles()];
  if(typeof window.loadConfig==='function')tasks.push(window.loadConfig({force:true,preserveDrafts:true}));
  await Promise.all(tasks);
}

function _voiceProfileDots(value){return `<span class="voice-profile-dots" aria-label="${value} sur 5">${[1,2,3,4,5].map(i=>`<i class="${i<=value?'on':''}"></i>`).join('')}</span>`}
function _voiceProfileError(detail){if(typeof detail==='string')return detail;if(detail&&typeof detail==='object'){const reasons=Array.isArray(detail.reasons)?detail.reasons.join(' · '):'';return reasons||detail.message||detail.code||'Profil incompatible'}return 'Application impossible'}
export async function loadVoicePerformanceProfiles(){
  const root=document.getElementById('voice-performance-profiles');if(!root)return;
  try{
    const r=await fetch(`${API_BASE}/api/voice/performance-profiles`,{headers:_voiceHeaders()});const d=await r.json();if(!r.ok)throw new Error(_voiceProfileError(d.detail));
    const profiles=Array.isArray(d.profiles)?d.profiles:[];
    root.innerHTML=profiles.map(p=>{const reasons=(p.incompatibility_reasons||[]).join(' · ');return `<article class="voice-profile-card ${p.active?'active':''} ${p.compatible?'':'incompatible'}" data-voice-profile="${esc(p.id)}"><div class="voice-profile-head"><div><div class="voice-profile-rank">Niveau ${Number(p.order)||0}</div><h3>${esc(p.label)}</h3></div><div class="voice-profile-badges">${p.active?'<span class="voice-profile-badge active">Actif</span>':''}${p.recommended?'<span class="voice-profile-badge recommended">Recommandé</span>':''}</div></div><p><strong>${esc(p.tagline)}</strong> · ${esc(p.description)}</p><div class="voice-profile-meters"><div class="voice-profile-meter"><span>Précision</span>${_voiceProfileDots(Number(p.quality)||0)}</div><div class="voice-profile-meter"><span>Réactivité</span>${_voiceProfileDots(Number(p.responsiveness)||0)}</div></div><div class="voice-profile-meta"><span>${esc(p.requirement)}</span><span>${esc(p.privacy)}</span>${reasons?`<span class="voice-profile-warning">${esc(reasons)}</span>`:''}</div><button class="btn ${p.recommended?'primary':''}" data-apply-voice-profile="${esc(p.id)}" ${(!p.compatible||p.active)?'disabled':''}>${p.active?'Profil actif':p.compatible?'Appliquer':'Indisponible'}</button></article>`}).join('')||'<p>Aucun profil disponible.</p>';
    root.querySelectorAll('[data-apply-voice-profile]').forEach(button=>button.addEventListener('click',()=>applyVoicePerformanceProfile(button.dataset.applyVoiceProfile)));
  }catch(e){root.innerHTML=`<p class="voice-profile-warning">${esc(e.message)}</p>`}
}

export async function applyVoicePerformanceProfile(profileId){
  const msg=document.getElementById('voice-profile-message');const button=document.querySelector(`[data-apply-voice-profile="${CSS.escape(profileId)}"]`);if(button)button.disabled=true;if(msg)msg.textContent='Enregistrement du profil…';
  try{const r=await fetch(`${API_BASE}/api/voice/performance-profiles/${encodeURIComponent(profileId)}`,{method:'POST',headers:_voiceHeaders()});const d=await r.json();if(!r.ok||!d.success)throw new Error(_voiceProfileError(d.detail||d.error));const restart=await _restartVoiceRuntimeIfRunning();if(msg)msg.textContent=restart.restarted?'Profil appliqué et écoute redémarrée.':(d.note||'Profil enregistré');await _refreshVoiceConfigurationSurfaces()}catch(e){if(msg)msg.textContent=e.message;if(button)button.disabled=false}
}

export async function saveVoiceSettings(){
  const value=id=>document.getElementById(id)?.value||'';const button=document.getElementById('voice-settings-save');
  const updates={LUMENA_VOICE_V2_MODE:value('voice-setting-mode'),LUMENA_VOICE_ACTIVATION_MODE:value('voice-setting-activation'),LUMENA_STT_LANGUAGE:value('voice-setting-language'),LUMENA_VOICE_WAKE_PHRASE:value('voice-setting-wake')};
  if(button){button.disabled=true;button.textContent='Enregistrement…'}_setVoiceSettingsMessage('Enregistrement des réglages…','info');
  try{const r=await fetch(`${API_BASE}/api/config`,{method:'PUT',headers:_voiceHeaders(true),body:JSON.stringify({updates})});const d=await _voiceResponseJson(r);if(!r.ok||!d.success)throw new Error(d.error||d.detail||'Enregistrement impossible');const restart=d.needs_restart?await _restartVoiceRuntimeIfRunning():{restarted:false};_setVoiceSettingsMessage(restart.restarted?'Réglages appliqués et écoute redémarrée.':(d.note||'Réglages enregistrés.'),'success');await _refreshVoiceConfigurationSurfaces()}catch(e){_setVoiceSettingsMessage(e.message||'Enregistrement impossible','error')}finally{if(button){button.disabled=false;button.textContent='Enregistrer'}}
}

export async function armVoicePushToTalk(){
  const selected=document.getElementById('voice-setting-activation')?.value||'';const button=document.getElementById('voice-ptt-button');
  if(selected!=='push_to_talk'){_setVoiceSettingsMessage('Choisis « Appuyer pour parler » dans Activation, puis enregistre.','info');return}
  if(_lastVoiceStatus.activation_mode!=='push_to_talk'){_setVoiceSettingsMessage('Réglage enregistré mais pas encore actif : arrête puis redémarre l’écoute.','info');return}
  if(!_lastVoiceStatus.running){_setVoiceSettingsMessage('Démarre d’abord l’écoute vocale, puis appuie sur ce bouton.','info');return}
  if(button){button.disabled=true;button.textContent='Armement…'}_setVoiceSettingsMessage('Préparation du prochain énoncé…','info');
  try{const r=await fetch(`${API_BASE}/api/voice/push-to-talk`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok||!d.armed)throw new Error(typeof d.detail==='string'?d.detail:'Le mode Appuyer pour parler n’est pas prêt');_setVoiceSettingsMessage('Micro armé : parle maintenant.','success');logC('Prochain énoncé autorisé','success')}catch(e){_setVoiceSettingsMessage(e.message||'Impossible d’armer le micro','error')}finally{if(button){button.disabled=false;button.textContent='Appuyer pour parler'}}
}
export async function testVoiceMicro(){const out=document.getElementById('voice-micro-result');if(out)out.textContent='Test en cours…';try{const r=await fetch(`${API_BASE}/api/voice/test-micro`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok||!d.ok)throw new Error(d.detail||'Micro indisponible');const threshold=d.calibration?.energy_threshold;const suffix=Number.isFinite(Number(threshold))?` · seuil ${threshold}`:'';if(out)out.textContent=`Micro prêt${d.active_runtime?' · écoute active':''}${suffix}`}catch(e){if(out)out.textContent=e.message||'Test micro impossible'}}

function _safeVoiceDiagnostic(d){const h=d.hardware_profile||d.capabilities?.hardware||{};return{schema_version:1,backend:d.backend||null,state:d.state||null,mode:d.mode||null,barge_in_mode:d.barge_in_mode||null,runtime_options:d.runtime_options||{},hardware:h,vad:d.vad||{},wake_word:d.wake_word||{},audio_frontend:d.audio_frontend||{},voice_profile_status:d.voice_profile_status||{},timings:d.timings||{},privacy:d.privacy||{},last_error:d.last_error?String(d.last_error).slice(0,240):null}}
export function exportVoiceDiagnostic(){const payload=_safeVoiceDiagnostic(_lastVoiceStatus);const blob=new Blob([JSON.stringify(payload,null,2)+'\n'],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='lumena-voice-diagnostic.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)}

export async function deleteVoiceData(){
  const out=document.getElementById('voice-delete-message');
  if(!window.confirm('Effacer les packs, profils et données vocales locales gérés par Lumena ?'))return;
  if(out)out.textContent='Suppression en cours…';
  try{const r=await fetch(`${API_BASE}/api/voice/data?confirm=DELETE`,{method:'DELETE',headers:_voiceHeaders()});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Suppression impossible');if(out)out.textContent=`Données vocales effacées · ${d.files||0} fichier(s)`;await loadVoiceStatus()}catch(e){if(out)out.textContent=e.message}
}

export async function toggleVoiceAssistant(){
  const btn=document.getElementById('voice-toggle-btn');if(!btn)return;btn.disabled=true;btn.textContent='...';_setVoiceControlMessage('Changement de l’écoute…','info');
  try{const r=await fetch(`${API_BASE}/api/voice/toggle?backend=v2`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok)throw new Error(d.detail||'Démarrage vocal impossible');updateVoiceUI(d);_setVoiceControlMessage(d.message||'État vocal mis à jour.','success');logC(d.message,d.running?'success':'info')}
  catch(e){_setVoiceControlMessage(e.message||'Commande vocale impossible','error');logC(e.message,'error')}finally{btn.disabled=false}
}

export function updateVoiceUI(status){
  const d=typeof status==='object'&&status?status:{running:!!status};const running=!!d.running;
  _lastVoiceStatus=d;
  const btn=document.getElementById('voice-toggle-btn');
  const dot=document.getElementById('voice-dot');
  const txt=document.getElementById('voice-status-text');
  const badge=document.getElementById('badge-voice');
  if(running){btn.textContent="Arreter l'ecoute";btn.classList.add('active');dot.classList.add('ok');txt.textContent="Lumena ecoute";txt.style.color="var(--ok)";badge.textContent="ON";badge.style.background="var(--ok)"}
  else{btn.textContent="Demarrer l'ecoute";btn.classList.remove('active');dot.classList.remove('ok');txt.textContent="Desactive";txt.style.color="var(--muted)";badge.textContent="OFF";badge.style.background="var(--danger)"}
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=(v===undefined||v===null||v==='')?'—':String(v)};
  set('voice-pipeline',`${d.backend||'—'} / ${d.state||'—'}`);
  set('voice-mode-role',`${d.mode||'—'} / ${d.session_role||'—'}${d.session_trusted?' (appairé)':''}`);
  set('voice-provider',`${d.provider||'—'}${d.degraded?' (dégradé)':''}`);
  set('voice-first-audio',Number.isFinite(d.first_audio_ms)?`${Math.round(d.first_audio_ms)} ms`:'—');
  set('voice-task',d.task_id||'aucune');
  set('voice-privacy',d.cloud_allowed?'cloud autorisé':'local strict');
  set('voice-effective-provider',d.provider||d.voice_profile_status?.active_provider||'—');set('voice-profile',d.voice_profile||d.voice_profile_status?.profile_id||'Lumena');set('voice-tts-language',d.response_language||d.runtime_options?.language||'—');set('voice-locality',d.cloud_allowed?'Réseau autorisé':'Local strict');
  set('voice-micro',d.capabilities?.pyaudio_available?'Disponible':'Indisponible');set('voice-vad',d.vad?.engine||d.capabilities?.vad_engine||'—');set('voice-aec',d.audio_frontend?.aec_active?'Actif':d.barge_in_mode==='guarded_no_aec'?'Absent · interruption surveillée':d.capabilities?.server_aec_available?'Disponible':'Non disponible');set('voice-wake-engine',d.wake_word?.engine||d.activation_engine||'—');
  set('voice-input-language',d.input_language||'—');set('voice-response-language',d.response_language||d.runtime_options?.language||'—');set('voice-language-scope',d.language_scope||'session');set('voice-language-guard',d.language_guard||'—');
  const hw=d.hardware_profile||d.capabilities?.hardware||{};set('voice-hardware-profile',hw.profile||'—');set('voice-stt-effective',`${d.runtime_options?.device||'—'} / ${d.runtime_options?.compute||'—'}`);set('voice-endpoint-p95',Number.isFinite(d.timings?.endpointing_ms?.p95)?`${d.timings.endpointing_ms.p95} ms`:'—');set('voice-gap-p95',Number.isFinite(d.timings?.playback_gap_ms?.p95)?`${d.timings.playback_gap_ms.p95} ms`:'—');
  const diag=document.getElementById('voice-diagnostic-json');if(diag)diag.textContent=JSON.stringify(_safeVoiceDiagnostic(d),null,2);
  const models=document.getElementById('voice-models-list');if(models){const local=d.voice_profile_status?.local||{};const entries=[['Piper',local.piper_model||'Voix locale par défaut'],['Whisper',d.runtime_options?.device?`${d.runtime_options.device} · ${d.runtime_options.compute}`:'Non démarré'],['VAD',d.vad?.engine||d.capabilities?.vad_engine||'energy'],['Wake word',d.wake_word?.available?'Pack local actif':'Phrase STT locale']];models.innerHTML=entries.map(([name,value])=>`<div class="voice-model-chip"><div class="stat-label">${esc(name)}</div><strong>${esc(String(value))}</strong></div>`).join('')}
}

export async function stopVoiceAudio(){
  _setVoiceControlMessage('Coupure en cours…','info');
  try{const r=await fetch(`${API_BASE}/api/voice/stop-audio`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok)throw new Error(d.detail||'Coupure impossible');const text=d.stopped?'Voix coupée. Le travail continue.':'Aucun audio actif à couper.';_setVoiceControlMessage(text,'success');logC(text,'info')}catch(e){_setVoiceControlMessage(e.message||'Coupure impossible','error')}
}

export async function toggleVoiceMute(){
  const btn=document.getElementById('voice-mute-btn');if(!btn)return;const enabled=!btn.classList.contains('active');btn.disabled=true;
  try{const r=await fetch(`${API_BASE}/api/voice/mute?enabled=${enabled}`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok)throw new Error(d.detail||'Mute impossible');btn.classList.toggle('active',!!d.muted);btn.innerHTML=d.muted?'<i data-lucide="mic"></i> Réactiver':'<i data-lucide="mic-off"></i> Mute';_setVoiceControlMessage(d.muted?'Micro et voix rendus silencieux.':'Son vocal réactivé.','success');if(window.lucide)window.lucide.createIcons()}catch(e){_setVoiceControlMessage(e.message||'Mute impossible','error')}finally{btn.disabled=false}
}

export async function testVoiceOutput(){
  try{const r=await fetch(`${API_BASE}/api/voice/test-output`,{method:'POST',headers:_voiceHeaders()});const d=await _voiceResponseJson(r);if(!r.ok||!d.ok)throw new Error(d.detail||'Voice V2 doit être démarrée pour le test audio');_setVoiceControlMessage('Phrase de test envoyée au moteur vocal.','success');logC('Phrase de test envoyée au moteur vocal','success')}catch(e){_setVoiceControlMessage(e.message||'Test audio impossible','error')}
}

/* ============================================================
   SEARCH
   ============================================================ */
export async function searchCode(){
  const q=document.getElementById('code-search-input').value.trim();if(!q)return;
  const c=document.getElementById('search-results');c.innerHTML=loadingDots('Recherche...');
  try{
    const sh={'Content-Type':'application/json'};if(ADMIN_TOKEN)sh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/search/code`,{method:'POST',headers:sh,body:JSON.stringify({query:q,n_results:10})});
    const d=await r.json();
    if(!d.results.length){c.innerHTML='<div style="color:var(--muted)">Aucun resultat</div>';return}
    c.innerHTML=d.results.map(r=>`<div class="list-item"><div><div class="list-item-title">${esc(r.file)}</div><div class="list-item-sub">${esc(r.symbol)}</div></div><span class="pill ok">Score: ${r.score}</span></div>`).join('');
  }catch(e){c.innerHTML='<div style="color:var(--danger)">Erreur de recherche</div>'}
}

export async function loadRecentMemories(){
  const c=document.getElementById('memory-results');c.innerHTML=loadingDots('Chargement...');
  try{
    const mh={'Content-Type':'application/json'};if(ADMIN_TOKEN)mh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/search/memory`,{method:'POST',headers:mh,body:JSON.stringify({query:"projet utilisateur Lumena",limit:15})});
    const d=await r.json();
    if(d.results&&d.results.length){
      c.innerHTML=`<div style="margin-bottom:12px;color:var(--muted);font-size:13px">${d.results.length} souvenirs recents</div>`+
        d.results.map(r=>`<div class="memory-item"><div style="font-size:11px;color:var(--accent);margin-bottom:4px">${esc(r.type)}</div><div style="font-size:13px">${esc(r.content)}</div>${r.timestamp?`<div style="font-size:10px;color:var(--muted);margin-top:4px">${esc(r.timestamp)}</div>`:''}</div>`).join('');
    }else c.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Utilisez la recherche</div>';
  }catch(e){c.innerHTML='<div style="color:var(--danger)">Erreur</div>'}
}

export async function searchMemory(){
  const q=document.getElementById('memory-search-input').value.trim();if(!q)return;
  const c=document.getElementById('memory-results');c.innerHTML=loadingDots('Recherche...');
  try{
    const smh={'Content-Type':'application/json'};if(ADMIN_TOKEN)smh['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/search/memory`,{method:'POST',headers:smh,body:JSON.stringify({query:q,limit:10})});
    const d=await r.json();
    c.innerHTML=d.results.map(r=>`<div class="memory-item"><div style="font-size:11px;color:var(--accent);margin-bottom:4px">${esc(r.type)}</div><div style="font-size:13px">${esc(r.content)}</div></div>`).join('')||'<div style="color:var(--muted)">Aucun souvenir</div>';
  }catch(e){c.innerHTML='<div style="color:var(--danger)">Erreur</div>'}
}

/* ============================================================
   TRACE
   ============================================================ */
let _traceAbort=null;
export function initTraceStream(){
  if(_traceAbort)_traceAbort.abort();
  _traceAbort=new AbortController();
  const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
  fetch(`${API_BASE}/api/trace/stream`,{headers:h,signal:_traceAbort.signal})
    .then(res=>{
      if(!res.ok)throw new Error(res.status);
      traceConnected=true;logC('Trace stream connected','info');
      const td=document.getElementById('trace-dot');if(td)td.classList.add('ok');
      const tt=document.getElementById('trace-conn-text');if(tt){tt.textContent='Connecte';tt.style.color='var(--ok)'}
      const reader=res.body.getReader();
      const decoder=new TextDecoder();
      let buf='';
      function pump(){
        reader.read().then(({done,value})=>{
          if(done){traceConnected=false;return;}
          buf+=decoder.decode(value,{stream:true});
          const lines=buf.split('\n');
          buf=lines.pop();
          let evtType='trace';
          for(const line of lines){
            if(line.startsWith('event:'))evtType=line.slice(6).trim();
            else if(line.startsWith('data:')){
              const raw=line.slice(5).trim();
              if(evtType==='heartbeat'){traceLastEventTs=Date.now();evtType='trace';continue;}
              try{const ev=JSON.parse(raw);renderTraceEvent(ev);traceStats.total++;if(ev.status==='error'||ev.error)traceStats.errors++;if(ev.tool_name)traceStats.tools++;if(ev.duration_ms)traceStats.durations.push(Number(ev.duration_ms));updateTraceStats();pushOverviewTraceEvent(ev);if(ev.tool_name||ev.stage){if(_refreshTasksTimer)clearTimeout(_refreshTasksTimer);_refreshTasksTimer=setTimeout(()=>loadActiveTasks(),800)}}catch(err){}
              evtType='trace';
            }
          }
          pump();
        });
      }
      pump();
    })
    .catch(()=>{traceConnected=false;const td=document.getElementById('trace-dot');if(td)td.classList.remove('ok');const tt=document.getElementById('trace-conn-text');if(tt){tt.textContent='Deconnecte';tt.style.color='var(--danger)'}});
}

export async function loadTraceRecent(){
  const list=document.getElementById('trace-list');if(!list)return;
  try{
    const _trh=ADMIN_TOKEN?{'Authorization':`Bearer ${ADMIN_TOKEN}`}:{};
    const r=await fetch(`${API_BASE}/api/trace/recent?limit=120`,{headers:_trh});const d=await r.json();
    const events=Array.isArray(d.events)?d.events:[];list.innerHTML='';
    for(let i=events.length-1;i>=0;i--)renderTraceEvent(events[i]);
    if(!events.length)list.innerHTML='<div style="color:var(--muted)">Aucun evenement.</div>';
  }catch(e){list.innerHTML='<div style="color:var(--danger)">Erreur</div>'}
}

export function renderTraceEvent(ev){
  const list=document.getElementById('trace-list');if(!list||!ev)return;
  traceLastEventTs=Date.now();
  const tle=document.getElementById('trace-last-event');if(tle)tle.textContent='Dernier: '+new Date().toLocaleTimeString('fr-FR');
  list.insertAdjacentHTML('afterbegin',`
    <div class="list-item"><div>
      <div class="list-item-title">${esc(ev.stage||'?')} <span class="pill">${esc(ev.status||'ok')}</span> <span class="pill">${esc(ev.mode||'chat')}</span></div>
      <div class="list-item-sub">trace=${esc(ev.trace_id||'n/a')} | ${fmtDur(ev.duration_ms)} | ${esc(ev.ts||'')}</div>
      ${ev.provider||ev.model?`<div class="list-item-sub">${esc(ev.provider||'')} / ${esc(ev.model||'')}</div>`:''}
      ${ev.tool_name?`<div class="list-item-sub">tool=${esc(ev.tool_name)}</div>`:''}
      ${ev.summary||ev.error?`<div style="font-size:12px;color:var(--muted);margin-top:4px">${esc(ev.summary||ev.error||'')}</div>`:''}
    </div></div>`);
  while(list.children.length>300)list.removeChild(list.lastElementChild);
}

/* ============================================================
   HEALTH CHECK
   ============================================================ */
export async function checkHealth(){
  try{
    const r=await fetch(`${API_BASE}/api/health`);const d=await r.json();
    healthOk=r.ok;
    const dot=document.getElementById('health-dot');
    const txt=document.getElementById('health-text');
    if(dot)dot.classList.toggle('ok',healthOk);
    if(txt)txt.textContent=healthOk?'En ligne':'Hors ligne';
    if(txt)txt.style.color=healthOk?'var(--ok)':'var(--danger)';
  }catch(e){
    healthOk=false;
    const dot=document.getElementById('health-dot');if(dot)dot.classList.remove('ok');
    const txt=document.getElementById('health-text');if(txt){txt.textContent='Hors ligne';txt.style.color='var(--danger)'}
  }
}
// checkHealth polling is started by startLiveRefreshLoops() inside startLumena()

/* ============================================================
   TRACE FILTERS
   ============================================================ */
export function filterTrace(mode,btn){
  traceFilterMode=mode;
  document.querySelectorAll('#trace-filter-tabs .tab').forEach(t=>t.classList.remove('active'));
  if(btn)btn.classList.add('active');
  const items=document.querySelectorAll('#trace-list .list-item');
  items.forEach(item=>{
    if(mode==='all'){item.style.display='';return}
    const text=item.textContent.toLowerCase();
    if(mode==='error')item.style.display=text.includes('error')||text.includes('fail')?'':'none';
    else if(mode==='tool')item.style.display=text.includes('tool=')?'':'none';
    else if(mode==='llm')item.style.display=text.includes('llm')||text.includes('provider')?'':'none';
  });
}

export function clearTraceList(){
  document.getElementById('trace-list').innerHTML='';
  traceStats={total:0,errors:0,tools:0,durations:[]};
  updateTraceStats();
}

export function updateTraceStats(){
  setText('trace-count-total',traceStats.total);
  setText('trace-count-errors',traceStats.errors);
  setText('trace-count-tools',traceStats.tools);
  if(traceStats.durations.length){
    const avg=Math.round(traceStats.durations.reduce((a,b)=>a+b,0)/traceStats.durations.length);
    const max=Math.round(Math.max(...traceStats.durations));
    setText('trace-avg-ms',avg+'ms');
    setText('trace-max-ms',max+'ms');
  }
}
