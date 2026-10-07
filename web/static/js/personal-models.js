const PM_BASE='/api/personal-model';
let pmState=null;
let pmLastFocus=null;
let pmConfirmAction=null;

const pmEsc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const pmToken=()=>typeof ADMIN_TOKEN!=='undefined'?ADMIN_TOKEN:'';
const pmApiBase=()=>typeof API_BASE!=='undefined'?API_BASE:'';
const pmHeaders=()=>({'Content-Type':'application/json','Authorization':`Bearer ${pmToken()}`});

async function pmRequest(path,options={}){
  const response=await fetch(`${pmApiBase()}${PM_BASE}${path}`,{...options,headers:{...pmHeaders(),...(options.headers||{})}});
  const payload=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(payload.detail||`HTTP ${response.status}`);
  return payload;
}

function pmToast(message,error=false){
  const node=document.getElementById('pm-toast');if(!node)return;
  node.textContent=message;node.className=`pm-toast${error?' error':''}`;node.hidden=false;
  clearTimeout(node._timer);node._timer=setTimeout(()=>{node.hidden=true},4200);
}

function pmName(value,fallback='Aucun'){return value?pmEsc(value):fallback}
function pmCount(state,key){return Number(state?.experiences?.counts?.[key]||0)}

function renderSummary(state){
  const active=state.personal_model;
  const job=state.active_job;
  document.getElementById('pm-summary').innerHTML=`
    <article class="pm-stat"><span>Modèle principal</span><strong>${pmName(state.principal_model)}</strong><small>Reste disponible à tout moment</small></article>
    <article class="pm-stat"><span>Modèle personnel</span><strong>${active?pmEsc(active.model_name):'Pas encore créé'}</strong><small>${active?`Version ${pmEsc(active.version)}`:'La lignée commencera en 1.0.0'}</small></article>
    <article class="pm-stat"><span>Apprentissage</span><strong class="pm-state ${state.policy.learning_enabled?'ok':'warn'}">${state.policy.learning_enabled?'Activé':'Désactivé'}</strong><small>Collecte ${state.policy.local_capture_enabled?'active':'inactive'}</small></article>
    <article class="pm-stat"><span>Entraînement</span><strong class="pm-state ${job?'warn':'ok'}">${job?pmEsc(job.state):'Au repos'}</strong><small>${job?pmEsc(job.run_id):'Aucun job actif'}</small></article>`;
}

function renderOverview(state,recommendations){
  const rec=(recommendations||[])[0];
  document.getElementById('pm-recommendation').innerHTML=rec?`<div><strong>${pmEsc(rec.expected_benefit)}</strong><p>${pmEsc((rec.reason_codes||[]).join(' · '))}</p></div><button class="btn accent" data-pm-tab="training">Voir</button>`:'<div><strong>Aucune action urgente</strong><p>L’état actuel ne demande pas d’intervention.</p></div>';
  document.getElementById('pm-data-overview').innerHTML=`
    <div class="pm-row"><div class="pm-row-main"><strong>${pmCount(state,'accepted')} expériences acceptées</strong><small>${state.experiences.total||0} expériences uniques stockées</small></div></div>
    <div class="pm-row"><div class="pm-row-main"><strong>${pmCount(state,'quarantined')} en quarantaine</strong><small>${pmCount(state,'rejected')} rejetées, ${pmCount(state,'candidate')} candidates</small></div></div>`;
  const deps=state.readiness?.dependencies||{};
  const missing=Object.entries(deps).filter(([,ok])=>!ok).map(([name])=>name);
  document.getElementById('pm-health-overview').innerHTML=`<div class="pm-row"><div class="pm-row-main"><strong>${missing.length?'Dépendances à compléter':'Pipeline prêt'}</strong><small>${missing.length?pmEsc(missing.join(', ')):'Composants d’entraînement détectés'}</small></div></div><div class="pm-row"><div class="pm-row-main"><strong>${Number(state.readiness?.resources?.free_disk_gb||0).toFixed(1)} Go libres</strong><small>Mesure actuelle, jamais estimée dans le navigateur</small></div></div>`;
}

function renderDataManagement(backups=[],sources=[]){
  const backupSelect=document.getElementById('pm-backup-select');
  const restore=document.getElementById('pm-restore-backup');
  if(backupSelect){backupSelect.innerHTML=`<option value="">${backups.length?'Choisir une sauvegarde':'Aucune sauvegarde'}</option>`+backups.map(item=>`<option value="${pmEsc(item.name)}">${pmEsc(item.name)} · ${(Number(item.size_bytes||0)/1048576).toFixed(1)} Mo</option>`).join('');if(restore)restore.disabled=true}
  const migration=document.getElementById('pm-migration-sources');
  if(migration)migration.innerHTML=sources.length?sources.map(item=>`<label><input type="checkbox" data-pm-migration-source value="${pmEsc(item.path)}"><span><strong>${pmEsc(item.path)}</strong><br><small>${(Number(item.size_bytes||0)/1024).toFixed(1)} Ko</small></span></label>`).join(''):'<div class="pm-empty">Aucune donnée historique compatible détectée.</div>';
  const importButton=document.getElementById('pm-import-history');if(importButton)importButton.disabled=true;
}

function renderJobs(state){
  const el=document.getElementById('pm-jobs');const jobs=state.jobs||[];
  if(!jobs.length){el.innerHTML='<div class="pm-empty"><i data-lucide="activity"></i>Aucun entraînement lancé.</div>';return}
  el.innerHTML=jobs.map(job=>`<div class="pm-row"><div class="pm-row-main"><strong>${pmEsc(job.target_version)} · ${pmEsc(job.state)}</strong><small>${pmEsc(job.run_id)}${job.error_code?` · ${pmEsc(job.error_code)}`:''}</small></div><div class="pm-row-actions">${job.state==='running'?`<button class="btn" data-pm-pause="${pmEsc(job.run_id)}">Pause</button>`:''}${['paused','unknown_interrupted','failed'].includes(job.state)&&job.checkpoint_path?`<button class="btn accent" data-pm-resume="${pmEsc(job.run_id)}">Reprendre</button>`:''}${['queued','waiting_idle','running','paused','unknown_interrupted'].includes(job.state)?`<button class="btn danger" data-pm-cancel="${pmEsc(job.run_id)}">Annuler</button>`:''}</div></div>`).join('');
}

function renderVersions(state){
  const versions=Object.entries(state.lineages?.lineages||{}).flatMap(([lineageId,lineage])=>(lineage.versions||[]).map(version=>({...version,lineage_id:lineageId})));
  const el=document.getElementById('pm-versions');
  if(!versions.length){el.innerHTML='<div class="pm-empty"><i data-lucide="git-branch"></i>Aucune version personnelle. Vos modèles principal et locaux restent disponibles.</div>';return}
  el.innerHTML=versions.map(version=>{const key=`${pmEsc(version.lineage_id)}:${pmEsc(version.version)}`;const exported=Boolean(version.artifact_hashes?.ollama_canary);const canActivate=['evaluated','available','archived'].includes(version.status)&&exported;return `<div class="pm-row"><div class="pm-row-main"><strong>${pmEsc(version.model_name)}</strong><small>${pmEsc(version.status)} · base ${pmEsc(version.base_model_id)}</small></div><div class="pm-row-actions">${version.personal_active?`<span class="pill success">Active</span>${version.global_default?'<span class="pill success">Par défaut</span>':`<button class="btn accent" data-pm-default="${key}">Utiliser par défaut</button>`}`:`${['candidate','evaluated','rejected'].includes(version.status)?`<button class="btn" data-pm-export="${key}">Exporter vers Ollama</button>`:''}${version.status==='candidate'&&exported?`<button class="btn" data-pm-evaluate="${key}">Évaluer</button>`:''}${canActivate?`<button class="btn accent" data-pm-activate="${key}">Activer</button>`:'<span class="pill">Preuves requises</span>'}`}</div></div>`}).join('');
}

function fillSettings(state){
  const settings=state.settings||{};const policy=state.policy||{};
  const set=(id,value)=>{const el=document.getElementById(id);if(!el)return;if(el.type==='checkbox')el.checked=Boolean(value);else el.value=value??''};
  set('pm-learning-enabled',policy.learning_enabled);set('pm-capture-enabled',policy.local_capture_enabled);
  set('pm-training-enabled',settings.enabled);set('pm-auto-enabled',settings.automatic);set('pm-trigger',settings.trigger);set('pm-window-start',settings.window_start);set('pm-window-end',settings.window_end);set('pm-idle',settings.min_idle_minutes);set('pm-min-experiences',settings.min_new_experiences);set('pm-frequency',settings.max_frequency_days);set('pm-duration',settings.max_duration_minutes);set('pm-cpu',settings.max_cpu_percent);set('pm-ram',settings.max_ram_percent);set('pm-vram',settings.max_vram_percent);set('pm-disk',settings.min_free_disk_gb);set('pm-profile',settings.profile);set('pm-battery',settings.allow_on_battery);set('pm-pause-voice',settings.pause_during_voice);set('pm-pause-work',settings.pause_during_work);
  document.querySelectorAll('[data-pm-day]').forEach(input=>{input.checked=(settings.days||[]).includes(Number(input.dataset.pmDay))});
  set('pm-judge-mode',policy.judge_mode);set('pm-judge-model',policy.judge_model);set('pm-judge-threshold',policy.judge_accept_threshold);set('pm-cloud-judge',policy.cloud_judge_enabled);
}

export async function loadPersonalModels(){
  const root=document.getElementById('panel-finetuning');if(!root)return;
  root.setAttribute('aria-busy','true');
  try{
    const [state,recs]=await Promise.all([pmRequest('/status'),pmRequest('/recommendations')]);
    pmState=state;renderSummary(state);renderOverview(state,recs.recommendations);renderJobs(state);renderVersions(state);fillSettings(state);
    const [backups,sources]=await Promise.allSettled([pmRequest('/backups'),pmRequest('/migration/sources')]);
    renderDataManagement(backups.status==='fulfilled'?backups.value.backups:[],sources.status==='fulfilled'?sources.value.sources:[]);
    const advanced=document.getElementById('pm-advanced-status');if(advanced){const deps=state.readiness?.dependencies||{};advanced.textContent=deps.training_backend_ready?'Backend SFT local prêt. Les parcours DPO et from-scratch restent soumis à leurs preuves dédiées.':'Entraînement lourd bloqué : dépendances CUDA, ressources ou espace disque insuffisants.'}
    root.querySelector('[data-pm-tab].active')?.click();
    if(window.lucide)window.lucide.createIcons();
  }catch(error){pmToast(`État indisponible : ${error.message}`,true)}finally{root.removeAttribute('aria-busy')}
}

function selectTab(name){
  document.querySelectorAll('.pm-tab').forEach(tab=>{const active=tab.dataset.pmTab===name;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active))});
  document.querySelectorAll('.pm-page').forEach(page=>page.classList.toggle('active',page.dataset.pmPage===name));
}

async function saveSettings(){
  const checked=id=>Boolean(document.getElementById(id)?.checked);const value=id=>document.getElementById(id)?.value;
  try{
    await pmRequest('/policy',{method:'PATCH',body:JSON.stringify({changes:{learning_enabled:checked('pm-learning-enabled'),local_capture_enabled:checked('pm-capture-enabled')}})});
    const days=[...document.querySelectorAll('[data-pm-day]:checked')].map(input=>Number(input.dataset.pmDay));
    await pmRequest('/settings',{method:'PATCH',body:JSON.stringify({changes:{enabled:checked('pm-training-enabled'),automatic:checked('pm-auto-enabled'),trigger:value('pm-trigger'),days,window_start:value('pm-window-start'),window_end:value('pm-window-end'),min_idle_minutes:Number(value('pm-idle')),min_new_experiences:Number(value('pm-min-experiences')),max_frequency_days:Number(value('pm-frequency')),max_duration_minutes:Number(value('pm-duration')),max_cpu_percent:Number(value('pm-cpu')),max_ram_percent:Number(value('pm-ram')),max_vram_percent:Number(value('pm-vram')),min_free_disk_gb:Number(value('pm-disk')),allow_on_battery:checked('pm-battery'),pause_during_voice:checked('pm-pause-voice'),pause_during_work:checked('pm-pause-work'),profile:value('pm-profile')}})});
    pmToast('Réglages enregistrés et relus.');await loadPersonalModels();
  }catch(error){pmToast(error.message,true)}
}

async function createTraining(){
  const base=document.getElementById('pm-base-model')?.value.trim();if(!base){pmToast('Indiquez le modèle de base.',true);return}
  try{
    const created=await pmRequest('/training',{method:'POST',body:JSON.stringify({base_model_id:base,display_prefix:document.getElementById('pm-model-prefix')?.value.trim()||'lumena',bump:document.getElementById('pm-version-bump').value,finetune:{num_epochs:Number(document.getElementById('pm-epochs').value),learning_rate:Number(document.getElementById('pm-lr').value),batch_size:1,grad_accumulation:8,max_seq_length:2048,load_in_4bit:true,use_unsloth:true,seed:42}})});
    const launched=await pmRequest(`/training/${encodeURIComponent(created.run.run_id)}/launch`,{method:'POST',body:'{}'});
    pmToast(launched.launched===false?'En attente des conditions de ressources.':'Entraînement lancé.');await loadPersonalModels();
  }catch(error){pmToast(error.message,true)}
}

async function saveJudgeSettings(){
  const changes={judge_mode:document.getElementById('pm-judge-mode').value,judge_model:document.getElementById('pm-judge-model').value.trim(),judge_accept_threshold:Number(document.getElementById('pm-judge-threshold').value),cloud_judge_enabled:Boolean(document.getElementById('pm-cloud-judge').checked)};
  const changed=Boolean(pmState?.policy?.cloud_judge_enabled)!==changes.cloud_judge_enabled;
  const apply=async()=>{if(changed){const ticket=await pmRequest('/approvals',{method:'POST',body:JSON.stringify({action:'change_cloud_judge',resource:'policy'})});await pmRequest('/policy',{method:'PATCH',body:JSON.stringify({changes,approval_token:ticket.approval_token})})}else await pmRequest('/policy',{method:'PATCH',body:JSON.stringify({changes})});pmToast('Configuration du juge enregistrée.');await loadPersonalModels()};
  if(changed){openApproval('Modifier l’accès cloud du juge ?',changes.cloud_judge_enabled?'Le juge pourra envoyer uniquement les exemples redacted au fournisseur configuré.':'Le juge sera limité aux modèles locaux.',apply);return}await apply();
}

function openApproval(title,message,action){
  const modal=document.getElementById('pm-modal');pmLastFocus=document.activeElement;pmConfirmAction=action;
  document.getElementById('pm-modal-title').textContent=title;document.getElementById('pm-modal-message').textContent=message;modal.classList.add('open');modal.setAttribute('aria-hidden','false');document.getElementById('pm-modal-confirm').focus();
}
function closeApproval(){const modal=document.getElementById('pm-modal');modal.classList.remove('open');modal.setAttribute('aria-hidden','true');pmConfirmAction=null;if(pmLastFocus?.focus)pmLastFocus.focus()}
async function approvedAction(action,resource,path,body={}){
  const ticket=await pmRequest('/approvals',{method:'POST',body:JSON.stringify({action,resource})});
  return pmRequest(path,{method:'POST',body:JSON.stringify({...body,approval_token:ticket.approval_token})});
}

export function initPersonalModelsPanel(){
  document.addEventListener('change',event=>{
    if(event.target.matches('#pm-backup-select')){document.getElementById('pm-restore-backup').disabled=!event.target.value;return}
    if(event.target.matches('[data-pm-migration-source]'))document.getElementById('pm-import-history').disabled=!document.querySelector('[data-pm-migration-source]:checked');
  });
  document.addEventListener('click',async event=>{
    const tab=event.target.closest('[data-pm-tab]');if(tab){selectTab(tab.dataset.pmTab);return}
    if(event.target.closest('#pm-refresh')){await loadPersonalModels();return}
    if(event.target.closest('#pm-save-settings')){await saveSettings();return}
    if(event.target.closest('#pm-save-judge')){try{await saveJudgeSettings()}catch(e){pmToast(e.message,true)}return}
    if(event.target.closest('#pm-start-training')){await createTraining();return}
    const pause=event.target.closest('[data-pm-pause]');if(pause){try{await pmRequest(`/training/${encodeURIComponent(pause.dataset.pmPause)}/pause`,{method:'POST',body:'{}'});await loadPersonalModels()}catch(e){pmToast(e.message,true)}return}
    const resume=event.target.closest('[data-pm-resume]');if(resume){try{await pmRequest(`/training/${encodeURIComponent(resume.dataset.pmResume)}/resume`,{method:'POST',body:'{}'});await loadPersonalModels()}catch(e){pmToast(e.message,true)}return}
    const cancel=event.target.closest('[data-pm-cancel]');if(cancel){const run=cancel.dataset.pmCancel;openApproval('Annuler cet entraînement ?',`Le job ${run} sera arrêté après sauvegarde de son checkpoint.`,()=>approvedAction('cancel_training',run,`/training/${encodeURIComponent(run)}/cancel`));return}
    const activate=event.target.closest('[data-pm-activate]');if(activate){const [lineage,version]=activate.dataset.pmActivate.split(':');const resource=`${lineage}:${version}`;openApproval('Activer cette version ?',`La version ${version} deviendra le modèle personnel actif. Le modèle principal ne change pas.`,()=>approvedAction('activate_version',resource,'/versions/activate',{lineage_id:lineage,version}));return}
    const makeDefault=event.target.closest('[data-pm-default]');if(makeDefault){const [lineage,version]=makeDefault.dataset.pmDefault.split(':');const resource=`${lineage}:${version}`;openApproval('Utiliser ce modèle par défaut ?',`Lumena utilisera la version ${version} comme cerveau par défaut. Le modèle principal reste disponible.`,()=>approvedAction('set_global_default',resource,'/versions/default',{lineage_id:lineage,version}));return}
    const exportVersion=event.target.closest('[data-pm-export]');if(exportVersion){const [lineage,version]=exportVersion.dataset.pmExport.split(':');const resource=`${lineage}:${version}`;openApproval('Exporter cette version ?',`Lumena va fusionner, quantifier, importer puis tester la version ${version} dans Ollama.`,()=>approvedAction('export_version',resource,'/versions/export',{lineage_id:lineage,version,quant_type:'Q4_K_M'}));return}
    const evaluate=event.target.closest('[data-pm-evaluate]');if(evaluate){const [lineage,version]=evaluate.dataset.pmEvaluate.split(':');try{await pmRequest('/versions/evaluate',{method:'POST',body:JSON.stringify({lineage_id:lineage,version})});pmToast('Évaluation terminée et preuve enregistrée.');await loadPersonalModels()}catch(e){pmToast(e.message,true)}return}
    if(event.target.closest('#pm-create-backup')){openApproval('Créer une sauvegarde ?',`Lumena va créer une archive locale vérifiée de votre modèle personnel.`,async()=>{const result=await approvedAction('export_data','personal-model-backup','/backups');const target=document.getElementById('pm-backup-result');if(target)target.textContent=`Sauvegarde créée : ${result.backup_name} · ${result.file_count} fichiers vérifiés.`;return result});return}
    const backupSelect=event.target.closest('#pm-backup-select');if(backupSelect){document.getElementById('pm-restore-backup').disabled=!backupSelect.value;return}
    if(event.target.closest('#pm-restore-backup')){const name=document.getElementById('pm-backup-select')?.value;if(!name)return;openApproval('Restaurer cette sauvegarde ?',`Lumena vérifiera chaque hash avant de restaurer ${name}. Un conflit existant sera refusé.`,()=>approvedAction('import_data',`personal-model-restore:${name}`,'/backups/restore',{backup_name:name}));return}
    const migrationChoice=event.target.closest('[data-pm-migration-source]');if(migrationChoice){document.getElementById('pm-import-history').disabled=!document.querySelector('[data-pm-migration-source]:checked');return}
    if(event.target.closest('#pm-import-history')){const sources=[...document.querySelectorAll('[data-pm-migration-source]:checked')].map(input=>input.value);if(!sources.length)return;try{const preview=await pmRequest('/migration/preview',{method:'POST',body:JSON.stringify({sources})});const report=preview.report||{};openApproval('Importer les données détectées ?',`${report.valid||0} entrées valides seront examinées. ${report.invalid||0} entrées invalides resteront exclues.`,()=>approvedAction('import_data',preview.approval_resource,'/migration/import',{sources}))}catch(e){pmToast(e.message,true)}return}
    if(event.target.closest('#pm-delete-learning-data')){openApproval('Effacer les données d’apprentissage ?',`Cette action supprime les expériences et datasets locaux. Les modèles déjà entraînés, réglages et sauvegardes restent disponibles.`,()=>approvedAction('delete_data','personal-learning-data','/data/delete'));return}
    if(event.target.closest('#pm-modal-close')){closeApproval();return}
    if(event.target.closest('#pm-modal-confirm')&&pmConfirmAction){try{await pmConfirmAction();pmToast('Action exécutée et vérifiée.');closeApproval();await loadPersonalModels()}catch(e){pmToast(e.message,true)}return}
  });
  document.addEventListener('keydown',event=>{const modal=document.getElementById('pm-modal');if(!modal?.classList.contains('open'))return;if(event.key==='Escape'){closeApproval();return}if(event.key==='Tab'){const focusable=[...modal.querySelectorAll('button:not([disabled])')];if(!focusable.length)return;const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}});
}

window.loadPersonalModels=loadPersonalModels;
