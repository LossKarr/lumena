const BASE='/api/document-studio';
let models=[];
let activeTemplate=null;
let initialized=false;
let previewTimer=null;
let editHistory=[];
let editHistoryIndex=-1;
let logos=[];
let visualSample={};
let visualDesign={};
let editorMode='visual';
let templateDraft=null;
const protectedObjectUrls=new Map();
const DEFAULT_DESIGN={accent:'#D97706',text:'#1C2430',muted:'#667085',surface:'#F5F7FA',font:'modern',density:'standard',page_margin_mm:18,logo_enabled:true,logo_position:'left',logo_width_px:128,logo_layout:'flow',logo_x_pct:0,logo_y_mm:0};

const el=id=>document.getElementById(id);
const escapeHtml=value=>String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const currentAdminToken=()=>typeof ADMIN_TOKEN!=='undefined'&&ADMIN_TOKEN?ADMIN_TOKEN:(window.ADMIN_TOKEN||'');
const headers=()=>{const h={'Content-Type':'application/json'};const token=currentAdminToken();if(token)h.Authorization=`Bearer ${token}`;return h};
async function api(path,options={}){const response=await fetch(`${BASE}${path}`,{...options,headers:{...headers(),...(options.headers||{})}});if(!response.ok){let msg=`HTTP ${response.status}`;try{const d=await response.json();msg=d.detail||msg}catch(_){}throw new Error(msg)}return response.json()}
async function protectedObjectUrl(path){const url=String(path).startsWith('/')?String(path):`${BASE}${path}`;if(protectedObjectUrls.has(url))return protectedObjectUrls.get(url);const response=await fetch(url,{headers:headers()});if(!response.ok)throw new Error(`HTTP ${response.status}`);const objectUrl=URL.createObjectURL(await response.blob());protectedObjectUrls.set(url,objectUrl);return objectUrl}
function toast(message,error=false){const box=el('ds-toast');if(!box)return;box.textContent=message;box.style.borderColor=error?'var(--danger)':'var(--ok)';box.classList.add('show');clearTimeout(box._timer);box._timer=setTimeout(()=>box.classList.remove('show'),3200)}

export async function loadDocumentStudio(){
  bindOnce();
  await Promise.all([loadModels(),loadLibrary(),loadLogos()]);
}

function bindOnce(){
  if(initialized)return;initialized=true;
  ensureHistoryButtons();
  ensureLogoPositionControls();
  ensureLogoChoiceButtons();
  document.querySelectorAll('.ds-tab').forEach(tab=>tab.addEventListener('click',()=>switchView(tab.dataset.dsView)));
  el('ds-refresh')?.addEventListener('click',loadDocumentStudio);
  el('ds-model-search')?.addEventListener('input',renderModels);
  el('ds-model-category')?.addEventListener('change',renderModels);
  el('ds-library-search')?.addEventListener('input',debounceLibrary);
  el('ds-library-format')?.addEventListener('change',loadLibrary);
  el('ds-import-open')?.addEventListener('click',()=>el('ds-import-input')?.click());
  el('ds-import-input')?.addEventListener('change',importFile);
  el('ds-template-import-open')?.addEventListener('click',()=>el('ds-template-import-input')?.click());
  el('ds-template-import-input')?.addEventListener('change',createTemplateDraft);
  el('ds-template-import-close')?.addEventListener('click',closeTemplateImport);
  el('ds-template-import-save')?.addEventListener('click',saveTemplateDraft);
  el('ds-template-import-publish')?.addEventListener('click',publishTemplateDraft);
  el('ds-template-import-fields')?.addEventListener('input',handleTemplateDraftField);
  el('ds-logo-upload-open')?.addEventListener('click',()=>el('ds-logo-upload')?.click());
  el('ds-logo-upload')?.addEventListener('change',uploadLogo);
  el('ds-logo-grid')?.addEventListener('click',handleLogoAction);
  el('ds-editor-close')?.addEventListener('click',closeEditor);
  el('ds-clone')?.addEventListener('click',cloneActive);
  el('ds-save')?.addEventListener('click',saveActive);
  el('ds-set-default')?.addEventListener('click',setDefaultActive);
  el('ds-restore-version')?.addEventListener('click',restoreActive);
  el('ds-undo')?.addEventListener('click',()=>moveEditHistory(-1));
  el('ds-redo')?.addEventListener('click',()=>moveEditHistory(1));
  el('ds-edit-name')?.addEventListener('input',recordEditState);
  ['ds-edit-source','ds-edit-sample'].forEach(id=>el(id)?.addEventListener('input',()=>{recordEditState();scheduleDraftPreview()}));
  document.querySelectorAll('[data-ds-editor-mode]').forEach(button=>button.addEventListener('click',()=>setEditorMode(button.dataset.dsEditorMode)));
  ['ds-design-accent','ds-design-text','ds-design-font','ds-design-density','ds-design-margin','ds-design-logo-position','ds-design-logo-width','ds-design-logo-enabled','ds-design-logo-layout','ds-design-logo-x','ds-design-logo-y'].forEach(id=>{
    const control=el(id);if(!control)return;
    control.addEventListener('input',handleDesignInput);
    control.addEventListener('change',handleDesignInput);
  });
  el('ds-logo-position-pad')?.addEventListener('pointerdown',setLogoPositionFromPointer);
  el('ds-logo-position-pad')?.addEventListener('pointermove',event=>{if(event.buttons===1)setLogoPositionFromPointer(event)});
  el('ds-visual-fields')?.addEventListener('input',handleVisualFieldInput);
  el('ds-visual-fields')?.addEventListener('click',handleVisualFieldAction);
  el('ds-web-form')?.addEventListener('submit',searchWeb);
  ['ds-model-grid','ds-custom-grid'].forEach(id=>el(id)?.addEventListener('click',async event=>{const card=event.target.closest('[data-template-id]');if(card){await openEditor(card.dataset.templateId);resetEditHistory()}}));
  el('ds-library-grid')?.addEventListener('click',event=>{const download=event.target.closest('[data-doc-download]');if(download){event.stopPropagation();downloadProtected(download.dataset.docDownload,download.dataset.filename);return}const card=event.target.closest('[data-document-id]');if(card)openDocument(card.dataset.documentId)});
  el('ds-library-grid')?.addEventListener('error',event=>{if(event.target.tagName!=='IMG')return;const card=event.target.closest('[data-document-id]');const type=card?.querySelector('.ds-doc-name')?.textContent?.split('.').pop()||'doc';const fallback=document.createElement('div');fallback.className='ds-doc-icon';fallback.textContent=type;event.target.replaceWith(fallback)},true);
  el('ds-web-results')?.addEventListener('click',handleWebAction);
}

function ensureHistoryButtons(){
  const save=el('ds-save');if(!save||el('ds-undo'))return;
  save.insertAdjacentHTML('beforebegin','<button class="ds-icon-btn" id="ds-undo" title="Annuler" aria-label="Annuler"><i data-lucide="undo-2"></i></button><button class="ds-icon-btn" id="ds-redo" title="Rétablir" aria-label="Rétablir"><i data-lucide="redo-2"></i></button>');
  if(window.lucide)window.lucide.createIcons();
}

function ensureLogoPositionControls(){
  const grid=el('ds-design-logo-position')?.closest('.ds-design-grid');if(!grid||el('ds-design-logo-layout'))return;
  grid.insertAdjacentHTML('beforeend','<label>Placement du logo<select id="ds-design-logo-layout"><option value="flow">En-tête</option><option value="free">Placement libre</option></select></label><label id="ds-logo-x-field" hidden>Position horizontale<input id="ds-design-logo-x" type="range" min="0" max="100" step="1"><output id="ds-design-logo-x-value"></output></label><label id="ds-logo-y-field" hidden>Position verticale<input id="ds-design-logo-y" type="range" min="0" max="240" step="1"><output id="ds-design-logo-y-value"></output></label>');
  grid.insertAdjacentHTML('afterend','<div id="ds-logo-position-pad" class="ds-logo-position-pad" hidden role="application" aria-label="Position libre du logo"><span>Glissez le repère à la position voulue sur la page</span><button id="ds-logo-position-dot" type="button" aria-label="Position du logo"></button></div>');
}

function ensureLogoChoiceButtons(){
  enhanceLogoChoice('ds-design-font','Typographie',[
    ['modern','type','Moderne'],['inter','monitor','Interface'],['classic','book-open','Classique'],['technical','braces','Technique'],
  ]);
  enhanceLogoChoice('ds-design-density','Densité',[
    ['compact','minimize-2','Compacte'],['standard','equal','Standard'],['airy','maximize-2','Aérée'],
  ]);
  enhanceLogoChoice('ds-design-logo-position','Alignement du logo',[
    ['left','align-left','Gauche'],['center','align-center','Centre'],['right','align-right','Droite'],
  ]);
  enhanceLogoChoice('ds-design-logo-layout','Placement du logo',[
    ['flow','panel-top','En-tête'],['free','move','Libre'],
  ]);
  syncLogoChoiceButtons();
  if(window.lucide)window.lucide.createIcons();
}

function enhanceLogoChoice(selectId,label,choices){
  const nativeSelect=el(selectId);if(!nativeSelect||document.querySelector(`[data-ds-logo-choice-for="${selectId}"]`))return;
  const select=document.createElement('input');select.type='hidden';select.id=selectId;select.value=nativeSelect.value;select.dataset.dsImmediate='true';const controlRoot=nativeSelect.closest('.dark-select')||nativeSelect;controlRoot.replaceWith(select);
  const group=document.createElement('div');group.className='ds-choice-group';group.dataset.dsLogoChoiceFor=selectId;group.dataset.choiceCount=String(choices.length);group.setAttribute('role','group');group.setAttribute('aria-label',label);
  group.innerHTML=choices.map(([value,icon,text])=>`<button type="button" class="ds-choice-button" data-ds-logo-choice="${value}" title="${text}" aria-label="${text}" aria-pressed="false"><i data-lucide="${icon}"></i><span>${text}</span></button>`).join('');
  group.addEventListener('click',event=>{const button=event.target.closest('[data-ds-logo-choice]');if(!button||button.disabled)return;select.value=button.dataset.dsLogoChoice;handleDesignInput({type:'change',target:select})});
  select.insertAdjacentElement('afterend',group);
}

function syncLogoChoiceButtons(){
  document.querySelectorAll('[data-ds-logo-choice-for]').forEach(group=>{const select=el(group.dataset.dsLogoChoiceFor);if(!select)return;const locked=select.id==='ds-design-logo-position'&&(visualDesign.logo_layout||'flow')==='free';group.querySelectorAll('[data-ds-logo-choice]').forEach(button=>{const active=button.dataset.dsLogoChoice===select.value;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));button.disabled=locked})});
}

function switchView(view){document.querySelectorAll('.ds-tab').forEach(t=>t.classList.toggle('active',t.dataset.dsView===view));document.querySelectorAll('.ds-view').forEach(s=>s.classList.toggle('active',s.dataset.dsSection===view));if(view==='library')loadLibrary();if(view==='logos')loadLogos()}

async function loadLogos(){
  const grid=el('ds-logo-grid');if(!grid)return;
  grid.innerHTML='<div class="ds-empty">Chargement des logos…</div>';
  try{const data=await api('/logos');logos=data.logos||[];renderLogos();updateActiveLogoNote()}catch(error){grid.innerHTML=`<div class="ds-empty">${escapeHtml(error.message)}</div>`}
}
function renderLogos(){
  const grid=el('ds-logo-grid');if(!grid)return;
  grid.innerHTML=logos.map(item=>`<article class="ds-logo-item ${item.active?'active':''}" data-logo-id="${escapeHtml(item.id)}"><div class="ds-logo-image" data-logo-image="${escapeHtml(item.id)}"><i data-lucide="image"></i></div><div class="ds-logo-meta"><strong>${escapeHtml(item.name)}</strong><span>${item.width} × ${item.height} · ${formatBytes(item.size)}</span></div><div class="ds-logo-actions"><button class="ds-icon-btn" data-logo-action="activate" title="Utiliser ce logo" aria-label="Utiliser ${escapeHtml(item.name)}" ${item.active?'disabled':''}><i data-lucide="${item.active?'circle-check':'circle'}"></i></button><button class="ds-icon-btn" data-logo-action="delete" title="Supprimer" aria-label="Supprimer ${escapeHtml(item.name)}"><i data-lucide="trash-2"></i></button></div>${item.active?'<span class="ds-logo-active">ACTIF</span>':''}</article>`).join('')||'<div class="ds-empty"><strong>Aucun logo enregistré.</strong><span>Ajoutez un PNG, JPEG ou WebP. Le premier logo devient actif automatiquement.</span></div>';
  if(window.lucide)window.lucide.createIcons();
  logos.forEach(async item=>{const target=grid.querySelector(`[data-logo-image="${CSS.escape(item.id)}"]`);if(!target)return;try{const src=await protectedObjectUrl(item.content_url);target.innerHTML=`<img src="${src}" alt="${escapeHtml(item.name)}">`}catch(_){}});
}
async function uploadLogo(event){
  const file=event.target.files?.[0];if(!file)return;
  const body=new FormData();body.append('file',file);const token=currentAdminToken();
  try{const response=await fetch(`${BASE}/logos?name=${encodeURIComponent(file.name.replace(/\.[^.]+$/,''))}`,{method:'POST',headers:token?{Authorization:`Bearer ${token}`}:{},body});if(!response.ok){const data=await response.json();throw new Error(data.detail||`HTTP ${response.status}`)}toast('Logo ajouté');await loadLogos();if(activeTemplate)renderDraftPreview()}catch(error){toast(error.message,true)}finally{event.target.value=''}
}
async function handleLogoAction(event){
  const button=event.target.closest('[data-logo-action]');if(!button)return;
  const item=button.closest('[data-logo-id]');if(!item)return;
  button.disabled=true;
  try{if(button.dataset.logoAction==='activate'){await api(`/logos/${encodeURIComponent(item.dataset.logoId)}/active`,{method:'PUT'});toast('Logo actif mis à jour')}else if(confirm('Supprimer ce logo du Document Studio ?')){await api(`/logos/${encodeURIComponent(item.dataset.logoId)}`,{method:'DELETE'});toast('Logo supprimé')}await loadLogos();if(activeTemplate)renderDraftPreview()}catch(error){toast(error.message,true);button.disabled=false}
}
function updateActiveLogoNote(){
  const target=el('ds-active-logo-note');if(!target)return;
  const active=logos.find(item=>item.active);
  target.innerHTML=active?`<i data-lucide="badge-check"></i><span>Logo actif : <strong>${escapeHtml(active.name)}</strong></span>`:'<i data-lucide="badge"></i><span>Aucun logo actif. Le document reste sans logo.</span>';
  if(window.lucide)window.lucide.createIcons();
}

async function loadModels(){
  const grid=el('ds-model-grid');const customGrid=el('ds-custom-grid');if(grid)grid.innerHTML='<div class="ds-empty">Chargement des modèles…</div>';if(customGrid)customGrid.innerHTML='<div class="ds-empty">Chargement des modèles personnalisés…</div>';
  try{const data=await api('/templates');models=data.templates||[];populateCategories();renderModels();queuePreviews()}catch(error){if(grid)grid.innerHTML=`<div class="ds-empty">${escapeHtml(error.message)}</div>`;if(customGrid)customGrid.innerHTML=`<div class="ds-empty">${escapeHtml(error.message)}</div>`}
}

async function createTemplateDraft(event){
  const file=event.target.files?.[0];if(!file)return;
  const body=new FormData();body.append('file',file);const token=currentAdminToken();
  try{
    const response=await fetch(`${BASE}/template-imports`,{method:'POST',headers:token?{Authorization:`Bearer ${token}`}:{},body});
    if(!response.ok){const data=await response.json();throw new Error(data.detail||`HTTP ${response.status}`)}
    templateDraft=(await response.json()).draft;openTemplateImport();
  }catch(error){toast(error.message,true)}finally{event.target.value=''}
}
function openTemplateImport(){
  if(!templateDraft)return;
  el('ds-template-import-name').value=templateDraft.name||'';
  el('ds-template-import-kind').value=templateDraft.kind||'';
  el('ds-template-import-category').value=templateDraft.category||'custom';
  el('ds-template-import-aliases').value=(templateDraft.aliases||[]).join(', ');
  el('ds-template-import-status').innerHTML=`<span><strong>${escapeHtml(String(templateDraft.source_format||'').toUpperCase())}</strong> · ${escapeHtml(templateDraft.fidelity||'')}</span><span>${escapeHtml(templateDraft.source_filename||'')}</span>`;
  renderTemplateDraftFields();renderTemplateDraftWarnings();
  const wizard=el('ds-template-import-wizard');wizard.classList.add('open');wizard.setAttribute('aria-hidden','false');
  previewTemplateDraft();if(window.lucide)window.lucide.createIcons();
}
function closeTemplateImport(){const wizard=el('ds-template-import-wizard');wizard?.classList.remove('open');wizard?.setAttribute('aria-hidden','true');templateDraft=null}
function renderTemplateDraftFields(){
  const target=el('ds-template-import-fields');if(!target||!templateDraft)return;
  const fields=templateDraft.detected_fields||[];
  target.innerHTML=fields.map((field,index)=>`<div class="ds-import-field" data-draft-field="${index}"><div><strong>${escapeHtml(field.label||field.id)}</strong><span>{{ ${escapeHtml(field.id)} }}</span></div><label>Libellé<input class="ds-input" data-draft-label value="${escapeHtml(field.label||field.id)}"></label><label>Exemple<input class="ds-input" data-draft-sample value="${escapeHtml(templateDraft.sample_data?.[field.id]??'')}"></label></div>`).join('')||'<div class="ds-empty"><strong>Aucun champ variable détecté.</strong><span>Le modèle reste publiable comme structure fixe.</span></div>';
}
function renderTemplateDraftWarnings(){
  const target=el('ds-template-import-warnings');if(!target||!templateDraft)return;
  const labels={no_placeholders_detected:'Aucun champ variable détecté : ce modèle produira une structure fixe.',external_relationships:'Relations externes interdites.'};
  target.innerHTML=(templateDraft.warnings||[]).map(item=>`<div><i data-lucide="triangle-alert"></i><span>${escapeHtml(labels[item]||item)}</span></div>`).join('');
}
function handleTemplateDraftField(event){
  const row=event.target.closest('[data-draft-field]');if(!row||!templateDraft)return;
  const index=Number(row.dataset.draftField);const field=templateDraft.detected_fields[index];if(!field)return;
  if(event.target.matches('[data-draft-label]'))field.label=event.target.value;
  if(event.target.matches('[data-draft-sample]'))templateDraft.sample_data[field.id]=event.target.value;
  clearTimeout(previewTimer);previewTimer=setTimeout(previewTemplateDraft,350);
}
function collectTemplateDraft(){
  if(!templateDraft)return null;
  templateDraft.name=el('ds-template-import-name').value.trim();
  templateDraft.kind=el('ds-template-import-kind').value.trim();
  templateDraft.category=el('ds-template-import-category').value.trim()||'custom';
  templateDraft.aliases=el('ds-template-import-aliases').value.split(',').map(value=>value.trim()).filter(Boolean);
  return templateDraft;
}
async function persistTemplateDraft(){
  const draft=collectTemplateDraft();if(!draft)return null;
  const payload={name:draft.name,kind:draft.kind,category:draft.category,aliases:draft.aliases,detected_fields:draft.detected_fields,sample_data:draft.sample_data};
  const data=await api(`/template-imports/${encodeURIComponent(draft.id)}`,{method:'PUT',body:JSON.stringify(payload)});templateDraft=data.draft;return templateDraft;
}
async function saveTemplateDraft(){try{await persistTemplateDraft();toast('Brouillon enregistré');openTemplateImport()}catch(error){toast(error.message,true)}}
async function previewTemplateDraft(){
  if(!templateDraft)return;el('ds-template-import-preview-state').textContent='Rendu…';
  try{await persistTemplateDraft();const result=await api(`/template-imports/${encodeURIComponent(templateDraft.id)}/preview`,{method:'POST'});el('ds-template-import-preview').srcdoc=result.html;el('ds-template-import-preview-state').textContent=`${String(templateDraft.source_format).toUpperCase()} · aperçu ${templateDraft.fidelity}`}catch(error){el('ds-template-import-preview-state').textContent=error.message}
}
async function publishTemplateDraft(){
  if(!templateDraft)return;const button=el('ds-template-import-publish');button.disabled=true;
  try{await persistTemplateDraft();const result=await api(`/template-imports/${encodeURIComponent(templateDraft.id)}/publish`,{method:'POST',body:JSON.stringify({template_id:templateDraft.kind})});toast(`Modèle ${result.template.name} publié`);closeTemplateImport();await loadModels();switchView('custom');queuePreviews()}catch(error){toast(error.message,true)}finally{button.disabled=false}
}
function populateCategories(){const select=el('ds-model-category');if(!select)return;const current=select.value;const cats=[...new Set(models.filter(m=>m.read_only).map(m=>m.category).filter(Boolean))].sort();select.innerHTML='<option value="">Toutes les catégories</option>'+cats.map(c=>`<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');select.value=current}
function modelCards(items){return items.map(m=>`<article class="ds-model-card" data-template-id="${escapeHtml(m.id)}"><div class="ds-model-preview" id="ds-thumb-${escapeHtml(m.id)}"><span class="ds-preview-loading">Aperçu en attente</span><span class="ds-origin">${m.read_only?'intégré':'personnalisé'}</span></div><div class="ds-model-meta"><strong>${escapeHtml(m.name)}</strong><div class="ds-model-sub"><span>${escapeHtml(m.kind)} · ${escapeHtml(m.format.toUpperCase())}</span><span>${m.is_default?'<span class="ds-default-mark">★ défaut</span>':`v${m.version}`}</span></div></div></article>`).join('')}
function renderModels(){const q=(el('ds-model-search')?.value||'').toLowerCase();const category=el('ds-model-category')?.value||'';const integrated=models.filter(m=>m.read_only&&(!q||`${m.name} ${m.kind} ${m.description}`.toLowerCase().includes(q))&&(!category||m.category===category));const custom=models.filter(m=>!m.read_only);el('ds-model-count').textContent=`${integrated.length} modèle${integrated.length>1?'s':''}`;el('ds-model-grid').innerHTML=modelCards(integrated)||'<div class="ds-empty">Aucun modèle intégré correspondant.</div>';el('ds-custom-count').textContent=`${custom.length} personnalisé${custom.length>1?'s':''}`;el('ds-custom-grid').innerHTML=modelCards(custom)||'<div class="ds-empty"><strong>Aucun modèle personnalisé.</strong><span>Dupliquez un modèle intégré pour créer votre première variante.</span></div>'}
async function queuePreviews(){const queue=[...models];const worker=async()=>{while(queue.length){const model=queue.shift();const target=el(`ds-thumb-${model.id}`);if(!target)continue;try{const p=await api(`/templates/${encodeURIComponent(model.id)}/preview`,{method:'POST'});const src=await protectedObjectUrl(p.thumbnail_url);target.innerHTML=`<img src="${src}" alt="Aperçu ${escapeHtml(model.name)}"><span class="ds-origin">${model.read_only?'intégré':'personnalisé'}</span>`}catch(_){target.innerHTML='<span class="ds-preview-loading">Aperçu indisponible</span>'}}};await Promise.all([worker(),worker(),worker()])}

async function openEditor(id){
  try{
    activeTemplate=await api(`/templates/${encodeURIComponent(id)}`);
    const native=Boolean(activeTemplate.native_source);
    visualSample=deepCopy(activeTemplate.sample_data_value||{});
    visualDesign={...DEFAULT_DESIGN,...(activeTemplate.manifest?.design||{})};
    el('ds-editor-title').textContent=activeTemplate.name;
    el('ds-edit-name').value=activeTemplate.name;
    el('ds-edit-source').value=activeTemplate.source;
    el('ds-edit-sample').value=JSON.stringify(visualSample,null,2);
    el('ds-edit-name').disabled=activeTemplate.read_only;
    el('ds-edit-source').disabled=activeTemplate.read_only||native;
    el('ds-edit-sample').disabled=activeTemplate.read_only;
    const note=el('ds-readonly-note');note.hidden=!activeTemplate.read_only&&!native;note.querySelector('span').textContent=native?'Le fichier Office natif conserve sa mise en page. Modifiez ici ses valeurs d’exemple ; la source binaire reste protégée.':'Ce modèle intégré est protégé. Dupliquez-le pour enregistrer vos personnalisations.';
    const advancedButton=document.querySelector('[data-ds-editor-mode="advanced"]');if(advancedButton)advancedButton.hidden=native;
    document.querySelectorAll('.ds-design-grid input,.ds-design-grid select').forEach(control=>{control.disabled=native||activeTemplate.read_only});
    el('ds-save').hidden=activeTemplate.read_only;
    el('ds-clone').hidden=!activeTemplate.read_only;
    el('ds-set-default').disabled=activeTemplate.is_default;
    const versions=activeTemplate.versions||[];
    el('ds-edit-version').innerHTML=versions.length?versions.map(v=>`<option value="${v}">Version ${v}</option>`).join(''):'<option value="">Aucune version</option>';
    el('ds-restore-version').disabled=!versions.length;
    renderVisualEditor();
    setEditorMode('visual');
    el('ds-editor').classList.add('open');el('ds-editor').setAttribute('aria-hidden','false');
    await renderDraftPreview();
  }catch(error){toast(error.message,true)}
}
function closeEditor(){el('ds-editor')?.classList.remove('open');el('ds-editor')?.setAttribute('aria-hidden','true');activeTemplate=null}
function currentEditState(){return{name:el('ds-edit-name').value,source:el('ds-edit-source').value,sample:el('ds-edit-sample').value,design:deepCopy(visualDesign)}}
function resetEditHistory(){if(!activeTemplate)return;editHistory=[currentEditState()];editHistoryIndex=0;updateHistoryButtons()}
function recordEditState(){if(!activeTemplate)return;const state=currentEditState();const previous=editHistory[editHistoryIndex];if(previous&&JSON.stringify(previous)===JSON.stringify(state))return;editHistory=editHistory.slice(0,editHistoryIndex+1);editHistory.push(state);if(editHistory.length>50)editHistory.shift();editHistoryIndex=editHistory.length-1;updateHistoryButtons()}
function moveEditHistory(delta){const next=editHistoryIndex+delta;if(next<0||next>=editHistory.length)return;editHistoryIndex=next;const state=editHistory[next];el('ds-edit-name').value=state.name;el('ds-edit-source').value=state.source;el('ds-edit-sample').value=state.sample;visualSample=JSON.parse(state.sample);visualDesign=deepCopy(state.design);renderVisualEditor();updateHistoryButtons();renderDraftPreview()}
function updateHistoryButtons(){if(el('ds-undo'))el('ds-undo').disabled=!activeTemplate||editHistoryIndex<=0;if(el('ds-redo'))el('ds-redo').disabled=!activeTemplate||editHistoryIndex>=editHistory.length-1}
function scheduleDraftPreview(){clearTimeout(previewTimer);previewTimer=setTimeout(renderDraftPreview,450)}
async function renderDraftPreview(){if(!activeTemplate)return;el('ds-preview-state').textContent='Rendu…';try{if(editorMode==='advanced')visualSample=JSON.parse(el('ds-edit-sample').value);else syncVisualSample();if(activeTemplate.native_source){const data=await api(`/templates/${encodeURIComponent(activeTemplate.id)}/preview?force=true`,{method:'POST'});el('ds-preview-frame').removeAttribute('srcdoc');el('ds-preview-frame').src=await protectedObjectUrl(data.pdf_url);el('ds-preview-state').textContent=`Aperçu structurel · ${activeTemplate.format.toUpperCase()}`}else{const data=await api(`/templates/${encodeURIComponent(activeTemplate.id)}/preview-draft`,{method:'POST',body:JSON.stringify({source:el('ds-edit-source').value,sample_data:visualSample,design:visualDesign})});el('ds-preview-frame').src='about:blank';el('ds-preview-frame').srcdoc=data.html;el('ds-preview-state').textContent=`À jour · ${designSummary()}`}}catch(error){el('ds-preview-state').textContent=error.message}}

function designSummary(){
  const font={modern:'Moderne',inter:'Interface',classic:'Classique',technical:'Technique'}[visualDesign.font]||visualDesign.font;
  const density={compact:'Compacte',standard:'Standard',airy:'Aérée'}[visualDesign.density]||visualDesign.density;
  const layout={flow:'En-tête',free:'Placement libre'}[visualDesign.logo_layout]||visualDesign.logo_layout;
  const position=(visualDesign.logo_layout||'flow')==='free'?`${Number(visualDesign.logo_x_pct||0)} % / ${Number(visualDesign.logo_y_mm||0)} mm`:({left:'Gauche',center:'Centre',right:'Droite'}[visualDesign.logo_position]||visualDesign.logo_position);
  return `${font} · ${density} · ${layout} · ${position}`;
}
async function cloneActive(){if(!activeTemplate)return;const proposed=`${activeTemplate.id}-perso`;const id=prompt('Identifiant du nouveau modèle',proposed);if(!id)return;try{const clone=await api(`/templates/${encodeURIComponent(activeTemplate.id)}/clone`,{method:'POST',body:JSON.stringify({id,name:`${activeTemplate.name} personnalisé`})});syncVisualSample();const manifest={...activeTemplate.manifest,name:`${activeTemplate.name} personnalisé`,design:visualDesign};await api(`/templates/${encodeURIComponent(clone.id)}`,{method:'PUT',body:JSON.stringify({manifest,source:el('ds-edit-source').value,sample_data:visualSample})});toast('Modèle dupliqué avec vos réglages');await loadModels();await openEditor(clone.id)}catch(error){toast(error.message,true)}}
async function saveActive(){if(!activeTemplate||activeTemplate.read_only)return;try{if(editorMode==='advanced')visualSample=JSON.parse(el('ds-edit-sample').value);else syncVisualSample();const manifest={...activeTemplate.manifest,name:el('ds-edit-name').value,design:visualDesign};await api(`/templates/${encodeURIComponent(activeTemplate.id)}`,{method:'PUT',body:JSON.stringify({manifest,source:el('ds-edit-source').value,sample_data:visualSample})});toast('Nouvelle version enregistrée');await loadModels();await openEditor(activeTemplate.id)}catch(error){toast(error.message,true)}}
async function setDefaultActive(){if(!activeTemplate)return;try{await api(`/defaults/${encodeURIComponent(activeTemplate.kind)}/${encodeURIComponent(activeTemplate.format)}`,{method:'PUT',body:JSON.stringify({template_id:activeTemplate.id})});toast('Modèle défini par défaut');await loadModels();await openEditor(activeTemplate.id)}catch(error){toast(error.message,true)}}
async function restoreActive(){if(!activeTemplate)return;const version=el('ds-edit-version').value;if(!version)return;try{await api(`/templates/${encodeURIComponent(activeTemplate.id)}/restore/${version}`,{method:'POST'});toast(`Version ${version} restaurée`);await loadModels();await openEditor(activeTemplate.id)}catch(error){toast(error.message,true)}}

function setEditorMode(mode){
  if(!['visual','advanced'].includes(mode))return;
  if(mode==='advanced'&&activeTemplate?.native_source)return;
  if(mode==='visual'&&editorMode==='advanced'){try{visualSample=JSON.parse(el('ds-edit-sample').value);renderVisualEditor()}catch(error){toast('Corrige le JSON avant de revenir au mode visuel.',true);return}}
  editorMode=mode;
  document.querySelectorAll('[data-ds-editor-mode]').forEach(button=>button.classList.toggle('active',button.dataset.dsEditorMode===mode));
  el('ds-visual-editor').hidden=mode!=='visual';el('ds-advanced-editor').hidden=mode!=='advanced';
}
function renderVisualEditor(){
  const map={accent:'ds-design-accent',text:'ds-design-text',font:'ds-design-font',density:'ds-design-density',page_margin_mm:'ds-design-margin',logo_position:'ds-design-logo-position',logo_width_px:'ds-design-logo-width',logo_layout:'ds-design-logo-layout',logo_x_pct:'ds-design-logo-x',logo_y_mm:'ds-design-logo-y'};
  Object.entries(map).forEach(([key,id])=>{if(el(id))el(id).value=visualDesign[key]??DEFAULT_DESIGN[key]});
  el('ds-design-logo-enabled').checked=visualDesign.logo_enabled!==false;
  el('ds-design-margin-value').textContent=`${visualDesign.page_margin_mm} mm`;
  el('ds-design-logo-width-value').textContent=`${visualDesign.logo_width_px} px`;
  updateLogoPositionControls();
  renderVisualFields();updateActiveLogoNote();
  if(window.lucide)window.lucide.createIcons();
}
function renderVisualFields(){
  const target=el('ds-visual-fields');if(!target)return;
  const chunks=[];
  Object.entries(visualSample).forEach(([key,value])=>chunks.push(renderVisualValue(value,[key],humanLabel(key))));
  target.innerHTML=chunks.join('')||'<div class="ds-empty">Aucun champ d’exemple.</div>';
}
function renderVisualValue(value,path,label){
  const encoded=encodeURIComponent(JSON.stringify(path));
  if(Array.isArray(value)){
    if(!value.length)return`<div class="ds-field-group"><div class="ds-field-group-head"><strong>${escapeHtml(label)}</strong><button class="ds-icon-btn" data-ds-field-action="add" data-ds-path="${encoded}" title="Ajouter"><i data-lucide="plus"></i></button></div><span class="ds-field-help">Liste vide. Le mode avancé permet d’en définir la structure.</span></div>`;
    const objectRows=value.every(item=>item&&typeof item==='object'&&!Array.isArray(item));
    if(objectRows){const keys=[...new Set(value.flatMap(item=>Object.keys(item)))];return`<div class="ds-field-group"><div class="ds-field-group-head"><strong>${escapeHtml(label)}</strong><button class="ds-icon-btn" data-ds-field-action="add" data-ds-path="${encoded}" title="Ajouter une ligne"><i data-lucide="plus"></i></button></div><div class="ds-array-table"><div class="ds-array-row ds-array-head">${keys.map(key=>`<span>${escapeHtml(humanLabel(key))}</span>`).join('')}<span></span></div>${value.map((item,index)=>`<div class="ds-array-row">${keys.map(key=>renderScalarControl(item[key],[...path,index,key],key,true)).join('')}<button class="ds-icon-btn" data-ds-field-action="remove" data-ds-path="${encoded}" data-ds-index="${index}" title="Supprimer"><i data-lucide="minus"></i></button></div>`).join('')}</div></div>`}
    return`<div class="ds-field-group"><div class="ds-field-group-head"><strong>${escapeHtml(label)}</strong><button class="ds-icon-btn" data-ds-field-action="add" data-ds-path="${encoded}" title="Ajouter"><i data-lucide="plus"></i></button></div>${value.map((item,index)=>`<div class="ds-list-row">${renderScalarControl(item,[...path,index],String(index+1),true)}<button class="ds-icon-btn" data-ds-field-action="remove" data-ds-path="${encoded}" data-ds-index="${index}" title="Supprimer"><i data-lucide="minus"></i></button></div>`).join('')}</div>`;
  }
  if(value&&typeof value==='object')return`<div class="ds-field-group"><strong>${escapeHtml(label)}</strong><div class="ds-nested-fields">${Object.entries(value).map(([key,child])=>renderVisualValue(child,[...path,key],humanLabel(key))).join('')}</div></div>`;
  return renderScalarControl(value,path,label,false);
}
function renderScalarControl(value,path,label,compact){
  const encoded=encodeURIComponent(JSON.stringify(path));const kind=typeof value;
  if(kind==='boolean')return`<label class="ds-field ds-field-check ${compact?'compact':''}"><input type="checkbox" data-ds-value data-ds-path="${encoded}" data-ds-kind="boolean" ${value?'checked':''}><span>${escapeHtml(humanLabel(label))}</span></label>`;
  const tag=kind==='string'&&String(value).length>90&&!compact?'textarea':'input';const type=kind==='number'?'number':'text';const common=`data-ds-value data-ds-path="${encoded}" data-ds-kind="${kind}"`;
  return`<label class="ds-field ${compact?'compact':''}"><span>${escapeHtml(humanLabel(label))}</span>${tag==='textarea'?`<textarea ${common}>${escapeHtml(value??'')}</textarea>`:`<input type="${type}" ${common} value="${escapeHtml(value??'')}">`}</label>`;
}
function handleVisualFieldInput(event){
  const input=event.target.closest('[data-ds-value]');if(!input)return;
  const path=JSON.parse(decodeURIComponent(input.dataset.dsPath));let value=input.type==='checkbox'?input.checked:input.value;if(input.dataset.dsKind==='number')value=Number(value||0);setAtPath(visualSample,path,value);syncVisualSample();recordEditState();scheduleDraftPreview();
}
function handleVisualFieldAction(event){
  const button=event.target.closest('[data-ds-field-action]');if(!button)return;
  const path=JSON.parse(decodeURIComponent(button.dataset.dsPath));const list=getAtPath(visualSample,path);if(!Array.isArray(list))return;
  if(button.dataset.dsFieldAction==='remove')list.splice(Number(button.dataset.dsIndex),1);else if(list.length)list.push(emptyLike(list[list.length-1]));
  syncVisualSample();renderVisualFields();recordEditState();scheduleDraftPreview();if(window.lucide)window.lucide.createIcons();
}
function handleDesignInput(event){
  if(!activeTemplate)return;
  visualDesign={...visualDesign,accent:el('ds-design-accent').value,text:el('ds-design-text').value,font:el('ds-design-font').value,density:el('ds-design-density').value,page_margin_mm:Number(el('ds-design-margin').value),logo_position:el('ds-design-logo-position').value,logo_width_px:Number(el('ds-design-logo-width').value),logo_enabled:el('ds-design-logo-enabled').checked,logo_layout:el('ds-design-logo-layout')?.value||'flow',logo_x_pct:Number(el('ds-design-logo-x')?.value||0),logo_y_mm:Number(el('ds-design-logo-y')?.value||0)};
  el('ds-design-margin-value').textContent=`${visualDesign.page_margin_mm} mm`;el('ds-design-logo-width-value').textContent=`${visualDesign.logo_width_px} px`;updateLogoPositionControls();recordEditState();
  if(event?.type==='change'&&(event.target?.matches('select')||event.target?.type==='checkbox'||event.target?.dataset?.dsImmediate==='true')){clearTimeout(previewTimer);renderDraftPreview()}else scheduleDraftPreview();
}
function updateLogoPositionControls(){
  const free=(visualDesign.logo_layout||'flow')==='free';['ds-logo-x-field','ds-logo-y-field','ds-logo-position-pad'].forEach(id=>{if(el(id))el(id).hidden=!free});
  if(el('ds-design-logo-position'))el('ds-design-logo-position').disabled=free;
  if(el('ds-design-logo-x-value'))el('ds-design-logo-x-value').textContent=`${Number(visualDesign.logo_x_pct||0)} %`;
  if(el('ds-design-logo-y-value'))el('ds-design-logo-y-value').textContent=`${Number(visualDesign.logo_y_mm||0)} mm`;
  const dot=el('ds-logo-position-dot');if(dot){dot.style.left=`${Number(visualDesign.logo_x_pct||0)}%`;dot.style.top=`${Number(visualDesign.logo_y_mm||0)/2.4}%`}
  syncLogoChoiceButtons();
}
function setLogoPositionFromPointer(event){
  if(!activeTemplate||(visualDesign.logo_layout||'flow')!=='free')return;
  const pad=el('ds-logo-position-pad');const rect=pad.getBoundingClientRect();const x=Math.max(0,Math.min(100,Math.round((event.clientX-rect.left)/rect.width*100)));const y=Math.max(0,Math.min(240,Math.round((event.clientY-rect.top)/rect.height*240)));
  visualDesign={...visualDesign,logo_x_pct:x,logo_y_mm:y};el('ds-design-logo-x').value=x;el('ds-design-logo-y').value=y;updateLogoPositionControls();recordEditState();scheduleDraftPreview();event.preventDefault();
}
function syncVisualSample(){el('ds-edit-sample').value=JSON.stringify(visualSample,null,2)}
function deepCopy(value){return JSON.parse(JSON.stringify(value))}
function humanLabel(value){return String(value).replace(/[_-]+/g,' ').replace(/\b\w/g,char=>char.toUpperCase())}
function getAtPath(root,path){return path.reduce((value,key)=>value?.[key],root)}
function setAtPath(root,path,value){const parent=path.slice(0,-1).reduce((current,key)=>current[key],root);parent[path[path.length-1]]=value}
function emptyLike(value){if(Array.isArray(value))return[];if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,emptyLike(item)]));if(typeof value==='number')return 0;if(typeof value==='boolean')return false;return''}

let libraryTimer=null;function debounceLibrary(){clearTimeout(libraryTimer);libraryTimer=setTimeout(loadLibrary,300)}
async function loadLibrary(){const grid=el('ds-library-grid');if(!grid)return;grid.innerHTML='<div class="ds-empty">Chargement…</div>';try{const query=el('ds-library-search')?.value||'';const format=el('ds-library-format')?.value||'';const path=query?`/library/search?q=${encodeURIComponent(query)}&formats=${encodeURIComponent(format)}`:`/library?format=${encodeURIComponent(format)}`;const data=await api(path);const docs=data.documents||[];el('ds-library-count').textContent=`${docs.length} document${docs.length>1?'s':''}`;grid.innerHTML=docs.map(d=>`<article class="ds-doc-item" data-document-id="${escapeHtml(d.id)}"><div class="ds-doc-cover" data-doc-thumb="${escapeHtml(d.id)}"><div class="ds-doc-icon">${escapeHtml(d.format)}</div></div><div class="ds-doc-meta"><div class="ds-doc-name" title="${escapeHtml(d.filename)}">${escapeHtml(d.filename)}</div><div class="ds-doc-info">${formatBytes(d.size)} · ${escapeHtml(d.source_kind)} · ${new Date(d.imported_at).toLocaleDateString('fr-FR')}</div></div><button class="ds-download" data-doc-download="${escapeHtml(d.id)}" data-filename="${escapeHtml(d.filename)}" title="Télécharger" aria-label="Télécharger"><i data-lucide="download"></i></button></article>`).join('')||'<div class="ds-empty">Aucun document indexé.</div>';queueLibraryThumbnails(docs)}catch(error){grid.innerHTML=`<div class="ds-empty">${escapeHtml(error.message)}</div>`}}
async function queueLibraryThumbnails(documents){const queue=documents.filter(d=>['pdf','html','docx','xlsx','pptx'].includes(d.format));const worker=async()=>{while(queue.length){const item=queue.shift();const cover=el('ds-library-grid')?.querySelector(`[data-doc-thumb="${CSS.escape(item.id)}"]`);if(!cover)continue;try{const src=await protectedObjectUrl(`${BASE}/library/${encodeURIComponent(item.id)}/thumbnail`);cover.innerHTML=`<img src="${src}" alt="Aperçu ${escapeHtml(item.filename)}">`}catch(_){}}};await Promise.all([worker(),worker()])}
async function downloadProtected(id,filename){try{const response=await fetch(`${BASE}/library/${encodeURIComponent(id)}/download`,{headers:headers()});if(!response.ok)throw new Error(`HTTP ${response.status}`);const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=filename||'document';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error){toast(error.message,true)}}
function formatBytes(size){const n=Number(size)||0;if(n<1024)return`${n} o`;if(n<1048576)return`${(n/1024).toFixed(1)} Ko`;return`${(n/1048576).toFixed(1)} Mo`}
async function openDocument(id){
  try{
    const [d,history]=await Promise.all([
      api(`/library/${encodeURIComponent(id)}`),
      api(`/library/${encodeURIComponent(id)}/history`)
    ]);
    document.querySelector('.ds-doc-dialog')?.remove();
    const editable=['docx','xlsx','pptx'].includes(d.format);
    const recipe=d.metadata?.studio_generation;
    const revisable=Boolean(recipe&&['pdf','html'].includes(d.format));
    const revisionData=revisable?deepCopy(recipe.data||{}):null;
    const defaults={docx:[{op:'replace_text',find:'ancien texte',replace:'nouveau texte'}],xlsx:[{op:'set_cell',cell:'A1',value:'nouvelle valeur'}],pptx:[{op:'replace_text',slide:1,find:'ancien texte',replace:'nouveau texte'}]};
    const dialog=document.createElement('aside');dialog.className='ds-doc-dialog';
    const timeline=(history.transformations||[]).map(item=>`<li><strong>${escapeHtml(item.operation)}</strong><span>${new Date(item.created_at).toLocaleString('fr-FR')}</span></li>`).join('');
    const revisionEditor=revisable?`<section class="ds-revision"><div class="ds-revision-head"><div><span class="ds-eyebrow">NOUVELLE VERSION</span><h3>Modifier le contenu</h3></div><i data-lucide="file-pen-line"></i></div><p>Le modèle, sa version et le logo d’origine sont conservés.</p><div class="ds-revision-fields" data-doc-revision-fields></div><div class="ds-revision-options"><label>Nom du fichier<input data-doc-revision-filename value="${escapeHtml(`${recipe.filename_stem}-revision`)}"></label><label>Format<select data-doc-revision-format><option value="${escapeHtml(recipe.output_format)}">${escapeHtml(recipe.output_format.toUpperCase())}</option>${recipe.output_format==='pdf'?'<option value="html">HTML</option>':'<option value="pdf">PDF</option>'}</select></label></div><div class="ds-doc-actions"><button class="btn" data-doc-preview-revision><i data-lucide="eye"></i> Prévisualiser</button><button class="btn primary" data-doc-apply-revision><i data-lucide="git-branch-plus"></i> Créer la version</button></div></section>`:'';
    dialog.innerHTML=`<div class="ds-doc-dialog-head"><div><span class="ds-eyebrow">DOCUMENT ${escapeHtml(d.format.toUpperCase())}</span><h2>${escapeHtml(d.filename)}</h2></div><button class="ds-icon-btn" data-doc-close aria-label="Fermer"><i data-lucide="x"></i></button></div><div class="ds-doc-dialog-body"><div class="ds-doc-reader"><pre class="ds-doc-text">${escapeHtml(d.content_text||'Aucun texte extractible. Le fichier reste disponible au téléchargement.')}</pre>${revisable?'<iframe class="ds-revision-preview" data-doc-revision-preview title="Prévisualisation de la nouvelle version" hidden></iframe>':''}</div><div class="ds-doc-side"><dl class="ds-doc-kv"><dt>Source</dt><dd>${escapeHtml(d.source_kind)}</dd><dt>URI</dt><dd>${escapeHtml(d.source_uri||'locale')}</dd><dt>Taille</dt><dd>${formatBytes(d.size)}</dd><dt>SHA-256</dt><dd>${escapeHtml(d.sha256)}</dd><dt>Importé</dt><dd>${new Date(d.imported_at).toLocaleString('fr-FR')}</dd></dl><div class="ds-doc-actions"><a class="btn primary" href="${BASE}/library/${encodeURIComponent(d.id)}/download"><i data-lucide="download"></i> Télécharger</a><button class="btn" data-doc-export><i data-lucide="folder-output"></i> Exporter</button><select data-doc-convert><option value="">Convertir…</option>${conversionOptions(d.format).map(x=>`<option value="${x}">${x.toUpperCase()}</option>`).join('')}</select></div>${revisionEditor}${editable?`<label>Opérations transactionnelles<textarea class="ds-code ds-op-editor" data-doc-operations>${escapeHtml(JSON.stringify(defaults[d.format],null,2))}</textarea></label><div class="ds-doc-actions"><button class="btn" data-doc-preview-edit>Prévisualiser</button><button class="btn primary" data-doc-apply-edit>Créer une version</button></div>`:''}<section class="ds-history"><h3>Historique</h3><ol>${timeline||'<li><span>Aucune transformation</span></li>'}</ol></section><div class="ds-dialog-result" data-doc-result></div></div></div>`;
    el('panel-document-studio').appendChild(dialog);
    dialog.querySelector('[data-doc-close]').addEventListener('click',()=>dialog.remove());
    const downloadLink=dialog.querySelector('a[href*="/download"]');
    if(downloadLink){downloadLink.removeAttribute('href');downloadLink.setAttribute('role','button');downloadLink.addEventListener('click',()=>downloadProtected(id,d.filename))}
    dialog.querySelector('[data-doc-convert]').addEventListener('change',async event=>{if(!event.target.value)return;await runDialogAction(dialog,()=>api(`/library/${encodeURIComponent(id)}/convert`,{method:'POST',body:JSON.stringify({format:event.target.value})}),'Conversion terminée')});
    dialog.querySelector('[data-doc-export]').addEventListener('click',()=>runDialogAction(dialog,()=>api(`/library/${encodeURIComponent(id)}/export`,{method:'POST',body:'{}'}),'Copie exportée avec preuve'));
    dialog.querySelector('[data-doc-preview-edit]')?.addEventListener('click',()=>editDocument(dialog,id,false));
    dialog.querySelector('[data-doc-apply-edit]')?.addEventListener('click',()=>editDocument(dialog,id,true));
    if(revisable){
      bindGeneratedRevisionEditor(dialog,id,revisionData);
      if(d.format==='html'&&d.content_text){const preview=dialog.querySelector('[data-doc-revision-preview]');preview.srcdoc=d.content_text;preview.hidden=false;dialog.querySelector('.ds-doc-text').hidden=true}
    }
    if(window.lucide)window.lucide.createIcons();
  }catch(error){toast(error.message,true)}
}
function renderGeneratedRevisionFields(dialog,data){
  const target=dialog.querySelector('[data-doc-revision-fields]');if(!target)return;
  target.innerHTML=Object.entries(data).map(([key,value])=>renderVisualValue(value,[key],humanLabel(key))).join('')||'<div class="ds-empty">Aucune donnée modifiable.</div>';
  if(window.lucide)window.lucide.createIcons();
}
function bindGeneratedRevisionEditor(dialog,id,data){
  const fields=dialog.querySelector('[data-doc-revision-fields]');
  renderGeneratedRevisionFields(dialog,data);
  fields.addEventListener('input',event=>{const control=event.target.closest('[data-ds-value]');if(!control)return;const path=JSON.parse(decodeURIComponent(control.dataset.dsPath));const value=control.dataset.dsKind==='boolean'?control.checked:control.dataset.dsKind==='number'?Number(control.value):control.value;setAtPath(data,path,value)});
  fields.addEventListener('click',event=>{const button=event.target.closest('[data-ds-field-action]');if(!button)return;const path=JSON.parse(decodeURIComponent(button.dataset.dsPath));const list=getAtPath(data,path);if(!Array.isArray(list))return;if(button.dataset.dsFieldAction==='remove')list.splice(Number(button.dataset.dsIndex),1);else if(list.length)list.push(emptyLike(list[list.length-1]));renderGeneratedRevisionFields(dialog,data)});
  dialog.querySelector('[data-doc-preview-revision]').addEventListener('click',async()=>{const preview=dialog.querySelector('[data-doc-revision-preview]');await runDialogAction(dialog,async()=>{const result=await api(`/library/${encodeURIComponent(id)}/revise/preview`,{method:'POST',body:JSON.stringify({data,replace_data:true})});preview.srcdoc=result.html;preview.hidden=false;dialog.querySelector('.ds-doc-text').hidden=true;return result},'Prévisualisation actualisée')});
  dialog.querySelector('[data-doc-apply-revision]').addEventListener('click',async()=>{const result=await runDialogAction(dialog,()=>api(`/library/${encodeURIComponent(id)}/revise`,{method:'POST',body:JSON.stringify({data,replace_data:true,filename:dialog.querySelector('[data-doc-revision-filename]').value,output_format:dialog.querySelector('[data-doc-revision-format]').value})}),'Nouvelle version créée');if(result?.record?.id)await openDocument(result.record.id)});
}
function conversionOptions(format){return({docx:['pdf','html'],xlsx:['csv'],csv:['xlsx'],html:['pdf'],md:['pdf','docx'],odt:['docx'],ods:['xlsx'],pptx:['pdf']}[format]||[])}
async function editDocument(dialog,id,apply){await runDialogAction(dialog,async()=>{const operations=JSON.parse(dialog.querySelector('[data-doc-operations]').value);return api(`/library/${encodeURIComponent(id)}/edit/${apply?'apply':'preview'}`,{method:'POST',body:JSON.stringify({operations})})},apply?'Nouvelle version créée':'Opérations valides')}
async function runDialogAction(dialog,action,success){const result=dialog.querySelector('[data-doc-result]');result.textContent='Traitement…';try{const data=await action();result.textContent=`${success}${data.record?.filename?` · ${data.record.filename}`:''}`;toast(success);await loadLibrary();return data}catch(error){result.textContent=error.message;toast(error.message,true);return null}}
async function importFile(event){const file=event.target.files?.[0];if(!file)return;const body=new FormData();body.append('file',file);const token=currentAdminToken();try{const response=await fetch(`${BASE}/import`,{method:'POST',headers:token?{Authorization:`Bearer ${token}`}:{},body});if(!response.ok){const d=await response.json();throw new Error(d.detail||`HTTP ${response.status}`)}toast('Document importé et indexé');switchView('library');await loadLibrary()}catch(error){toast(error.message,true)}finally{event.target.value=''}}

async function searchWeb(event){event.preventDefault();const target=el('ds-web-results');target.innerHTML='<div class="ds-empty">Recherche documentaire…</div>';try{const data=await api('/web/search',{method:'POST',body:JSON.stringify({query:el('ds-web-query').value,formats:el('ds-web-formats').value.split(',').map(x=>x.trim()).filter(Boolean)})});const items=data.candidates||[];target.innerHTML=items.map((item,i)=>`<article class="ds-web-item" data-url="${escapeHtml(item.url)}"><div class="ds-web-type">${escapeHtml(item.detected_format||'doc')}</div><div><h3>${escapeHtml(item.title||item.url)}</h3><p>${escapeHtml(item.description||item.url)}</p></div><div class="ds-web-actions"><button class="btn" data-web-action="inspect" title="Inspecter"><i data-lucide="scan-search"></i></button><button class="btn primary" data-web-action="download"><i data-lucide="download"></i> Importer</button></div></article>`).join('')||'<div class="ds-empty">Aucun document correspondant.</div>'}catch(error){target.innerHTML=`<div class="ds-empty">${escapeHtml(error.message)}</div>`}}
async function handleWebAction(event){const button=event.target.closest('[data-web-action]');if(!button)return;const item=button.closest('[data-url]');const url=item.dataset.url;button.disabled=true;try{if(button.dataset.webAction==='inspect'){const info=await api('/web/inspect',{method:'POST',body:JSON.stringify({url})});toast(`${info.filename} · ${info.content_type||'type inconnu'} · ${info.size?formatBytes(info.size):'taille inconnue'}`)}else{await api('/web/download',{method:'POST',body:JSON.stringify({url})});toast('Document téléchargé, contrôlé et indexé');switchView('library');await loadLibrary()}}catch(error){toast(error.message,true)}finally{button.disabled=false}}
