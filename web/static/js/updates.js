const updater = { status:null, releases:[], busy:false, error:'' };

function apiBase(){ return typeof API_BASE!=='undefined' ? API_BASE : ''; }
function authHeaders(json=false){
  const headers={};
  const token=typeof ADMIN_TOKEN!=='undefined' ? ADMIN_TOKEN : '';
  if(token)headers.Authorization=`Bearer ${token}`;
  if(json)headers['Content-Type']='application/json';
  return headers;
}
function escHtml(value){
  return String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
async function request(path, options={}){
  const response=await fetch(`${apiBase()}${path}`,{...options,headers:{...authHeaders(Boolean(options.body)),...(options.headers||{})}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);
  return data;
}
function selectedRelease(){
  const select=document.getElementById('update-version-select');
  const version=select?.value||updater.status?.selected_version||updater.status?.available_version;
  return updater.releases.find(item=>item.version===version)||updater.releases[0]||null;
}
function openUpdateSettings(){
  document.querySelector('.nav-item[data-panel="config"]')?.click();
  setTimeout(()=>window.switchCfgGroup?.('Mises a jour'),80);
}
function dismissBanner(){
  const version=updater.status?.available_version;
  if(version)localStorage.setItem('lumena_update_dismissed',version);
  renderBanner();
}
function renderBanner(){
  const banner=document.getElementById('update-banner');
  if(!banner)return;
  const version=updater.status?.available_version;
  const dismissed=localStorage.getItem('lumena_update_dismissed');
  if(!version||dismissed===version){banner.hidden=true;banner.innerHTML='';return;}
  banner.hidden=false;
  banner.innerHTML=`<i data-lucide="package-open"></i><div class="update-banner-copy"><strong>Lumena ${escHtml(version)} est disponible</strong><span>Release GitHub certifiée. Aucune installation sans ton accord.</span></div><button class="btn primary" id="update-banner-open">Voir</button><button class="btn-icon" id="update-banner-later" title="Plus tard" aria-label="Masquer cette version"><i data-lucide="x"></i></button>`;
  document.getElementById('update-banner-open')?.addEventListener('click',openUpdateSettings);
  document.getElementById('update-banner-later')?.addEventListener('click',dismissBanner);
  window.lucide?.createIcons();
}
function reason(entry){
  if(!entry)return 'Aucune version sélectionnée.';
  if(entry.installable)return entry.direction==='downgrade'?'Rétrogradation certifiée et compatible.':'Paquet léger certifié et compatible.';
  return entry.blocked_reason||'Cette version ne peut pas être installée automatiquement.';
}
function fullInstallerAction(entry){
  if(!entry?.requires_full_installer||!entry?.installer_asset_url)return '';
  return `<a class="btn primary" href="${escHtml(entry.installer_asset_url)}" target="_blank" rel="noopener"><i data-lucide="download"></i> Télécharger l'installateur complet</a>`;
}
function renderCenter(target=document.getElementById('update-center')){
  if(!target)return;
  const state=updater.status?.state||'idle';
  const current=updater.status?.current_version||'—';
  const selected=selectedRelease();
  const options=updater.releases.map(entry=>`<option value="${escHtml(entry.version)}" ${entry.version===selected?.version?'selected':''}>${escHtml(entry.version)} · ${entry.certified?'certifiée':'historique'}${entry.direction==='current'?' · actuelle':''}</option>`).join('');
  const percent=updater.status?.total_bytes?Math.min(100,Math.round((updater.status.progress_bytes||0)*100/updater.status.total_bytes)):0;
  const verified=state==='verified'&&updater.status?.staged_version===selected?.version;
  target.innerHTML=`<section class="update-center-card" aria-label="Centre de mises à jour"><div class="update-center-head"><div><div class="update-center-title"><i data-lucide="shield-check"></i>Version et intégrité</div><div class="update-center-meta">Installée ${escHtml(current)} · mode ${escHtml(updater.status?.installation_type||'—')}</div></div><span class="update-state-pill ${escHtml(state)}">${escHtml(state)}</span></div>${updater.error?`<div class="update-version-detail" style="border-color:var(--danger);color:var(--danger)">${escHtml(updater.error)}</div>`:''}<div class="update-version-row"><select class="input" id="update-version-select" aria-label="Version de Lumena">${options||'<option>Aucune release détectée</option>'}</select><button class="btn" id="update-check" ${updater.busy?'disabled':''}><i data-lucide="refresh-cw"></i> Vérifier</button></div><div class="update-version-detail" id="update-version-detail"><strong>${selected?`Lumena ${escHtml(selected.version)}`:'Catalogue indisponible'}</strong><br>${escHtml(reason(selected))}${selected?.requires_full_installer?'<br>Les mises à jour de dépendances restent hors du paquet léger.':''}</div><div class="update-actions"><button class="btn primary" id="update-download" ${!selected?.installable||updater.busy||verified?'disabled':''}><i data-lucide="download"></i> ${verified?'Paquet vérifié':'Télécharger et vérifier'}</button>${fullInstallerAction(selected)}${verified?'<button class="btn primary" id="update-apply"><i data-lucide="refresh-ccw"></i> Installer et redémarrer</button>':''}${updater.status?.rollback_available?'<button class="btn danger" id="update-rollback"><i data-lucide="undo-2"></i> Restaurer la précédente</button>':''}${selected?.notes_url?`<a class="btn" href="${escHtml(selected.notes_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i> Notes</a>`:''}</div>${state==='downloading'?`<div class="update-progress"><span style="width:${percent}%"></span></div>`:''}</section>`;
  target.querySelector('#update-version-select')?.addEventListener('change',()=>renderCenter(target));
  target.querySelector('#update-check')?.addEventListener('click',()=>refreshUpdates(true));
  target.querySelector('#update-download')?.addEventListener('click',downloadSelected);
  target.querySelector('#update-apply')?.addEventListener('click',applySelected);
  target.querySelector('#update-rollback')?.addEventListener('click',rollbackLatest);
  window.lucide?.createIcons();
}
async function refreshUpdates(force=false){
  updater.busy=true;updater.error='';renderCenter();
  try{
    if(force)await request('/api/updates/check',{method:'POST'});
    const [status,catalog]=await Promise.all([request('/api/updates/status'),request(`/api/updates/releases${force?'?force=true':''}`)]);
    updater.status=status;updater.releases=catalog.releases||[];
  }catch(error){updater.error=error.message;}
  finally{updater.busy=false;renderBanner();renderCenter();}
}
async function downloadSelected(){
  const entry=selectedRelease();if(!entry)return;
  if(entry.direction==='downgrade'&&!confirm(`Revenir à Lumena ${entry.version} ? Les données utilisateur seront conservées.`))return;
  updater.busy=true;updater.error='';renderCenter();
  try{
    await request('/api/updates/select',{method:'POST',body:JSON.stringify({version:entry.version})});
    updater.status=await request('/api/updates/download',{method:'POST'});
  }catch(error){updater.error=error.message;}
  finally{updater.busy=false;renderCenter();renderBanner();}
}
async function applySelected(){
  const entry=selectedRelease();if(!entry)return;
  if(!confirm(`Installer Lumena ${entry.version} et redémarrer maintenant ? Les travaux actifs bloqueront automatiquement l'opération.`))return;
  updater.busy=true;updater.error='';renderCenter();
  try{
    updater.status=await request('/api/updates/apply',{method:'POST'});
    renderCenter();
  }catch(error){updater.error=error.message;updater.busy=false;renderCenter();}
}
async function rollbackLatest(){
  if(!confirm('Restaurer la version précédente et redémarrer Lumena ?'))return;
  updater.busy=true;updater.error='';renderCenter();
  try{updater.status=await request('/api/updates/rollback',{method:'POST'});renderCenter();}
  catch(error){updater.error=error.message;updater.busy=false;renderCenter();}
}

window.renderUpdateCenter=renderCenter;
window.openUpdateSettings=openUpdateSettings;
setTimeout(()=>refreshUpdates(false),2000);
