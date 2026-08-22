import { esc } from './utils.js';

const CODEX_BASE='/api/codex-subscription';
let _root=null;
let _items=[];
let _loginId='';
let _collaborationThreads=[];

function _headers(json=false){
  const headers={'Authorization':`Bearer ${ADMIN_TOKEN}`};
  if(json)headers['Content-Type']='application/json';
  return headers;
}

async function _request(path,options={}){
  const response=await fetch(`${API_BASE}${CODEX_BASE}${path}`,{
    ...options,
    headers:{..._headers(Boolean(options.body)),...(options.headers||{})},
  });
  const payload=await response.json().catch(()=>({}));
  if(!response.ok){
    const detail=payload.detail;
    const message=typeof detail==='string'?detail:(detail?.message||payload.message||`HTTP ${response.status}`);
    const error=new Error(message);
    error.code=detail?.code||'';
    throw error;
  }
  return payload;
}

function _value(key,fallback=''){
  const item=_items.find(entry=>entry.key===key);
  return item?.value??item?.default??fallback;
}

function _setStatus(text,tone='muted'){
  const node=_root?.querySelector('[data-codex-status]');
  if(!node)return;
  node.className=`codex-access-status ${tone}`;
  node.textContent=text;
}

function _setBusy(busy){
  _root?.querySelectorAll('button[data-codex-action]').forEach(button=>{
    button.disabled=busy;
  });
}

function _renderPreflight(result){
  const node=_root?.querySelector('[data-codex-preflight]');
  if(!node)return;
  const preflight=result?.preflight||{};
  const state=preflight.state||'unknown';
  const ready=Boolean(preflight.ready);
  const labels={
    ready:'Codex CLI pret',not_found:'Codex CLI introuvable',
    inaccessible:'Codex CLI inaccessible',timed_out:'Sonde Codex expiree',
    broken:'Codex CLI inutilisable',app_server_unsupported:'App Server absent',
    protocol_incompatible:'Protocole incompatible',unknown:'Etat inconnu',
  };
  node.innerHTML=`<div class="codex-access-line"><span class="codex-dot ${ready?'ok':'warn'}"></span><strong>${esc(labels[state]||state)}</strong>${preflight.version?`<span>${esc(preflight.version)}</span>`:''}</div><p>${esc(preflight.detail||'Indiquez un chemin CLI ou lancez une nouvelle verification.')}</p>`;
}

function _quotaText(quota){
  if(!quota)return'Quota non charge';
  if(quota.exhausted)return'Quota Codex atteint';
  const values=[quota.primary_used_percent,quota.secondary_used_percent]
    .filter(value=>value!==null&&value!==undefined)
    .map(value=>`${Math.round(Number(value))}% utilise`);
  return values.length?values.join(' / '):'Quota disponible';
}

function _renderAccount(payload){
  const node=_root?.querySelector('[data-codex-account]');
  if(!node)return;
  if(!payload?.running||!payload.account){
    node.innerHTML='<div class="codex-account-empty">Aucune session Codex active dans Lumena.</div>';
    return;
  }
  const account=payload.account;
  const connected=Boolean(account.subscription_usable);
  node.innerHTML=`<div class="codex-account-head"><span class="codex-dot ${connected?'ok':'warn'}"></span><div><strong>${connected?'Compte ChatGPT connecte':'Session non utilisable'}</strong><span>${esc(account.plan_type||account.account_type||'')}</span></div></div><div class="codex-account-meta"><span>${esc(account.email_masked||'Email masque')}</span>${account.workspace_name?`<span>${esc(account.workspace_name)}</span>`:''}<span>${esc(_quotaText(payload.quota))}</span></div>`;
}

function _renderModels(payload){
  const select=_root?.querySelector('[data-cfg="LUMENA_CODEX_DEFAULT_MODEL"]');
  const note=_root?.querySelector('[data-codex-model-note]');
  if(!select)return;
  const models=payload?.models||[];
  const configured=_value('LUMENA_CODEX_DEFAULT_MODEL','');
  const selected=configured||payload?.selected_model||'';
  select.innerHTML='<option value="">Modele recommande par Codex</option>'+models.map(model=>`<option value="${esc(model.model_id)}" ${model.model_id===selected?'selected':''}>${esc(model.display_name||model.model_id)}${model.is_default?' (recommande)':''}</option>`).join('');
  select.disabled=!models.length;
  if(note)note.textContent=models.length?`${models.length} modele(s) autorise(s) par ce compte. Liste fournie en direct par Codex.`:'Connectez le compte pour charger les modeles disponibles.';
}

function _renderChallenge(challenge){
  const node=_root?.querySelector('[data-codex-challenge]');
  if(!node)return;
  const code=challenge?.user_code||'';
  const url=challenge?.verification_url||challenge?.auth_url||'';
  node.hidden=false;
  node.innerHTML=`<div><strong>Connexion ChatGPT en cours</strong><p>Terminez la connexion dans la fenetre officielle.${code?' Code appareil :':''}</p></div>${code?`<button type="button" class="codex-device-code" data-copy-code="${esc(code)}" title="Copier le code">${esc(code)} <i data-lucide="copy"></i></button>`:''}${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Rouvrir la page officielle</a>`:''}<button type="button" class="btn" data-codex-action="cancel"><i data-lucide="x"></i> Annuler</button>`;
  if(window.lucide)window.lucide.createIcons();
}

function _syncEnhancedSelect(select){
  const wrap=select?.closest('.dark-select');
  if(!wrap)return;
  const current=select.options[select.selectedIndex];
  const label=wrap.querySelector('.dark-select-text');
  if(label)label.textContent=current?.textContent||'';
  wrap.querySelectorAll('.dark-select-option').forEach(option=>{
    option.classList.toggle('selected',option.dataset.value===select.value);
  });
}

function _renderCollaboration(settings,threadsPayload){
  const section=_root?.querySelector('[data-codex-collaboration]');
  const list=_root?.querySelector('[data-codex-thread-list]');
  const select=_root?.querySelector('[data-codex-share-mode]');
  if(!section||!list||!select)return;
  const mode=settings?.share_mode||'selected';
  select.value=mode;
  _syncEnhancedSelect(select);
  _collaborationThreads=threadsPayload?.threads||[];
  if(mode==='none'){
    list.innerHTML='<div class="codex-thread-empty">Partage desactive. Aucun historique Codex n\'est lu.</div>';
    return;
  }
  if(!_collaborationThreads.length){
    list.innerHTML='<div class="codex-thread-empty">Aucune tache Codex visible pour le workspace Lumena.</div>';
    return;
  }
  list.innerHTML=_collaborationThreads.map(thread=>`<article class="codex-thread-card" data-thread-id="${esc(thread.thread_id)}"><div><strong>${esc(thread.name||thread.preview||'Tache Codex')}</strong><span>${esc(thread.status||'notLoaded')}${thread.active_flags?.includes('waitingOnApproval')?' · approbation utilisateur requise':''}</span></div><p>${esc(thread.preview||'Aucun apercu')}</p><div class="codex-action-row">${thread.linked?`<button type="button" class="btn" data-thread-action="read"><i data-lucide="book-open"></i> Historique</button><button type="button" class="btn" data-thread-action="handoff"><i data-lucide="clipboard-check"></i> Passation</button><button type="button" class="btn" data-thread-action="review"><i data-lucide="scan-search"></i> Confier une revue</button><button type="button" class="btn danger" data-thread-action="unlink"><i data-lucide="unlink"></i> Dissocier</button>`:`<button type="button" class="btn" data-thread-action="link"><i data-lucide="link"></i> Lier</button>`}</div></article>`).join('');
  if(window.lucide)window.lucide.createIcons();
}

function _renderCollaborationDetail(payload,title='Historique autorise'){
  const node=_root?.querySelector('[data-codex-thread-detail]');
  if(!node)return;
  const turns=payload?.turns||[];
  const handoff=payload?.handoff;
  let content='';
  if(handoff){
    content=`<p><strong>Objectif :</strong> ${esc(handoff.objective||'Non renseigne')}</p><p><strong>Fichiers :</strong> ${esc((handoff.files_touched||[]).join(', ')||'Aucun rapporte')}</p><p><strong>Tests :</strong> ${esc((handoff.tests||[]).join(' · ')||'Aucune preuve')}</p><p><strong>Suite :</strong> ${esc(handoff.next_action||'Verifier sur disque')}</p>`;
  }else{
    content=turns.map(turn=>`<div class="codex-thread-turn"><span>${esc(turn.status||'')}</span>${(turn.items||[]).map(item=>item.text?`<p>${esc(item.text)}</p>`:item.command?`<code>${esc(item.command)} · exit ${esc(item.exit_code)}</code>`:item.paths?`<p>Fichiers : ${esc(item.paths.join(', '))}</p>`:'').join('')}</div>`).join('')||'<p>Aucun tour visible.</p>';
  }
  node.hidden=false;
  node.innerHTML=`<div class="codex-thread-detail-head"><strong>${esc(title)}</strong><button type="button" class="btn icon-only" data-thread-action="close-detail" aria-label="Fermer"><i data-lucide="x"></i></button></div>${content}`;
  if(window.lucide)window.lucide.createIcons();
}

async function _loadCollaboration(){
  try{
    const settings=await _request('/collaboration/settings');
    const threads=settings.share_mode==='none'?{threads:[]} : await _request('/collaboration/threads');
    _renderCollaboration(settings,threads);
  }catch(error){
    const list=_root?.querySelector('[data-codex-thread-list]');
    if(list)list.innerHTML=`<div class="codex-thread-empty">${esc(error.message)}</div>`;
  }
}

async function _setShareMode(mode){
  try{
    await _request('/collaboration/settings',{method:'POST',body:JSON.stringify({share_mode:mode})});
    await _loadCollaboration();
  }catch(error){_setStatus(error.message,'danger');}
}

async function _threadAction(action,threadId){
  try{
    if(action==='link')await _request('/collaboration/link',{method:'POST',body:JSON.stringify({thread_id:threadId})});
    else if(action==='unlink')await _request(`/collaboration/link/${encodeURIComponent(threadId)}`,{method:'DELETE'});
    else if(action==='read')_renderCollaborationDetail(await _request(`/collaboration/thread/${encodeURIComponent(threadId)}`));
    else if(action==='handoff'){
      const payload=await _request('/collaboration/handoff',{method:'POST',body:JSON.stringify({thread_id:threadId,approve_memory:false})});
      _renderCollaborationDetail(payload,'Passation structuree');
    }else if(action==='review'){
      const instruction=String(_root?.querySelector('[data-codex-collaboration-instruction]')?.value||'').trim();
      if(!instruction){_setStatus('Ecris d\'abord la revue a confier','danger');return;}
      await _request('/collaboration/turn/start',{method:'POST',body:JSON.stringify({thread_id:threadId,instruction,write:false})});
      _setStatus('Revue Codex lancee en lecture seule','ok');
    }
    if(['link','unlink'].includes(action))await _loadCollaboration();
  }catch(error){_setStatus(error.message,'danger');}
}

async function _loadModels(){
  try{_renderModels(await _request('/models'));}
  catch(_error){_renderModels({models:[]});}
}

async function _refresh(){
  _setBusy(true);
  _setStatus('Verification locale...', 'muted');
  try{
    const [preflight,status]=await Promise.all([
      _request('/preflight'),_request('/account/status'),
    ]);
    _renderPreflight(preflight);
    _renderAccount(status);
    const connected=Boolean(status?.account?.subscription_usable);
    _setStatus(connected?'Connecte':'Non connecte',connected?'ok':'muted');
    if(connected){await _loadModels();await _loadCollaboration();}
    else{
      _renderModels({models:[]});
      const list=_root?.querySelector('[data-codex-thread-list]');
      if(list)list.innerHTML='<div class="codex-thread-empty">Connectez le compte pour voir les taches autorisees.</div>';
    }
  }catch(error){
    _setStatus('Erreur locale','danger');
    _renderAccount(null);
    const node=_root?.querySelector('[data-codex-preflight]');
    if(node)node.innerHTML=`<p class="codex-access-error">${esc(error.message)}</p>`;
  }finally{_setBusy(false);}
}

async function _adopt(){
  _setBusy(true);_setStatus('Lecture de la session...', 'muted');
  try{
    const payload=await _request('/adopt',{method:'POST'});
    _renderAccount({running:true,...payload});
    _setStatus('Connecte','ok');
    await _loadModels();
  }catch(error){_setStatus(error.message,'danger');}
  finally{_setBusy(false);}
}

async function _startLogin(){
  const popup=window.open('about:blank','lumena_codex_login','popup,width=720,height=780');
  _setBusy(true);_setStatus('Connexion en cours...', 'muted');
  try{
    const payload=await _request('/login/start',{method:'POST'});
    const challenge=payload.challenge||{};
    _loginId=challenge.login_id||'';
    if(challenge.auth_url&&popup)popup.location.href=challenge.auth_url;
    else if(popup)popup.close();
    _renderChallenge(challenge);
    const completed=await _request(`/login/wait?login_id=${encodeURIComponent(_loginId)}&timeout_s=120`);
    _renderAccount({running:true,account:completed.account,quota:null});
    _setStatus('Connecte','ok');
    const challengeNode=_root?.querySelector('[data-codex-challenge]');
    if(challengeNode)challengeNode.hidden=true;
    _loginId='';
    await _refresh();
  }catch(error){
    if(popup&&!popup.closed){try{if(popup.location.href==='about:blank')popup.close();}catch(_error){}}
    _setStatus(error.message,'danger');
  }finally{_setBusy(false);}
}

async function _cancelLogin(){
  if(!_loginId)return;
  try{
    await _request('/login/cancel',{method:'POST',body:JSON.stringify({login_id:_loginId})});
    _loginId='';
    const node=_root?.querySelector('[data-codex-challenge]');
    if(node)node.hidden=true;
    _setStatus('Connexion annulee','muted');
  }catch(error){_setStatus(error.message,'danger');}
}

async function _logout(){
  if(!confirm('Deconnecter le compte Codex partage par les clients locaux de cette machine ?'))return;
  _setBusy(true);
  try{
    await _request('/logout',{method:'POST',body:JSON.stringify({confirm_shared_codex_logout:true})});
    _setStatus('Deconnecte','muted');
    await _refresh();
  }catch(error){_setStatus(error.message,'danger');}
  finally{_setBusy(false);}
}

function _selectMode(mode){
  const input=_root?.querySelector('[data-cfg="LUMENA_OPENAI_ACCESS_MODE"]');
  if(input)input.value=mode;
  _root?.querySelectorAll('[data-codex-mode]').forEach(button=>{
    const selected=button.dataset.codexMode===mode;
    button.classList.toggle('active',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
  const subscription=_root?.querySelector('[data-codex-subscription-body]');
  if(subscription)subscription.hidden=mode!=='chatgpt_codex';
}

function _setSurface(surface,enabled){
  const input=_root?.querySelector('[data-cfg="LUMENA_CODEX_SURFACES"]');
  const selected=new Set(String(input?.value||'codeagent').split(',').map(value=>value.trim()).filter(Boolean));
  selected.add('codeagent');
  if(enabled)selected.add(surface);else selected.delete(surface);
  const order=['codeagent','chat','agent','missions'];
  if(input)input.value=order.filter(value=>selected.has(value)).join(',');
  const button=_root?.querySelector(`[data-codex-surface="${surface}"]`);
  if(button){
    button.classList.toggle('active',enabled);
    button.setAttribute('aria-pressed',String(enabled));
    const label=surface==='missions'?'Missions':'Agent';
    button.querySelector('span').textContent=enabled?(surface==='chat'?'Actif':`${label} actif`):(surface==='chat'?'Inactif':`${label} inactif`);
  }
}

function _setChatSurface(enabled){_setSurface('chat',enabled);}

function _bind(){
  _root.addEventListener('click',event=>{
    const modeButton=event.target.closest('[data-codex-mode]');
    if(modeButton){_selectMode(modeButton.dataset.codexMode);return;}
    const copyButton=event.target.closest('[data-copy-code]');
    if(copyButton){navigator.clipboard?.writeText(copyButton.dataset.copyCode||'');return;}
    const surfaceButton=event.target.closest('[data-codex-surface]');
    if(surfaceButton){_setSurface(surfaceButton.dataset.codexSurface,surfaceButton.getAttribute('aria-pressed')!=='true');return;}
    const threadButton=event.target.closest('[data-thread-action]');
    if(threadButton){
      const threadAction=threadButton.dataset.threadAction;
      if(threadAction==='close-detail'){
        const detail=_root.querySelector('[data-codex-thread-detail]');
        if(detail)detail.hidden=true;
      }else{
        const threadId=threadButton.closest('[data-thread-id]')?.dataset.threadId||'';
        if(threadId)_threadAction(threadAction,threadId);
      }
      return;
    }
    const action=event.target.closest('[data-codex-action]')?.dataset.codexAction;
    if(action==='refresh')_refresh();
    else if(action==='adopt')_adopt();
    else if(action==='login')_startLogin();
    else if(action==='cancel')_cancelLogin();
    else if(action==='logout')_logout();
  });
  _root.addEventListener('change',event=>{
    const select=event.target.closest('[data-codex-share-mode]');
    if(select)_setShareMode(select.value);
  });
}

export function mountCodexSubscriptionCard(container,items=[]){
  _root=container;
  _items=items;
  const mode=_value('LUMENA_OPENAI_ACCESS_MODE','api');
  const cliPath=_value('LUMENA_CODEX_CLI_PATH','');
  const fallback=_value('LUMENA_CODEX_API_FALLBACK','never');
  container.innerHTML=`<div class="codex-access-card"><div class="codex-access-header"><div><span class="codex-access-kicker">OpenAI</span><h3>Acces OpenAI</h3><p>Choisissez la facturation API historique ou les quotas Codex de votre abonnement ChatGPT.</p></div><span class="codex-access-status muted" data-codex-status>Verification...</span></div><input type="hidden" data-cfg="LUMENA_OPENAI_ACCESS_MODE" value="${esc(mode)}"><input type="hidden" data-cfg="LUMENA_CODEX_SURFACES" value="codeagent"><div class="codex-access-segment" role="group" aria-label="Mode d'acces OpenAI"><button type="button" data-codex-mode="api" aria-pressed="false"><i data-lucide="key-round"></i><span>Cle API</span></button><button type="button" data-codex-mode="chatgpt_codex" aria-pressed="false"><i data-lucide="badge-check"></i><span>Abonnement ChatGPT</span></button></div><div class="codex-api-note"><i data-lucide="shield-check"></i><span>Le mode API actuel reste intact. Changer ce choix ne remplace ni ne supprime votre cle.</span></div><div data-codex-subscription-body><div class="codex-access-grid"><section><div class="codex-section-title"><i data-lucide="terminal-square"></i> Codex CLI</div><label>Chemin facultatif<input class="input" type="text" data-cfg="LUMENA_CODEX_CLI_PATH" value="${esc(cliPath)}" placeholder="Detection automatique ou chemin vers codex.exe"></label><div class="codex-preflight" data-codex-preflight>Verification locale en attente.</div><div class="codex-action-row"><button type="button" class="btn" data-codex-action="refresh"><i data-lucide="refresh-cw"></i> Verifier</button><a class="btn codex-guide-link" href="https://developers.openai.com/codex/cli" target="_blank" rel="noopener noreferrer"><i data-lucide="external-link"></i> Guide officiel</a></div></section><section><div class="codex-section-title"><i data-lucide="user-round-check"></i> Compte ChatGPT</div><div class="codex-account" data-codex-account><div class="codex-account-empty">Chargement...</div></div><div class="codex-action-row"><button type="button" class="btn" data-codex-action="adopt"><i data-lucide="link"></i> Utiliser la session</button><button type="button" class="btn primary" data-codex-action="login"><i data-lucide="log-in"></i> Se connecter</button><button type="button" class="btn icon-only" data-codex-action="logout" title="Deconnecter le compte partage" aria-label="Deconnecter"><i data-lucide="log-out"></i></button></div><div class="codex-challenge" data-codex-challenge hidden></div></section></div><section class="codex-model-section"><div class="codex-section-title"><i data-lucide="sparkles"></i> Modele de l'abonnement</div><select class="input" data-cfg="LUMENA_CODEX_DEFAULT_MODEL" disabled><option value="">Connectez le compte pour charger les modeles</option></select><p data-codex-model-note>La liste vient du compte Codex reel, jamais d'un catalogue statique Lumena.</p></section><section class="codex-surface-section"><div><strong>CodeAgent</strong><span class="codex-surface-state pilot">Pilote S4</span></div><div><strong>Chat texte</strong><span class="codex-surface-state locked">Bloque avant S5</span></div><div><strong>Agent et Missions</strong><span class="codex-surface-state locked">Non certifies</span></div></section><section class="codex-collaboration-section" data-codex-collaboration><div class="codex-collaboration-head"><div><div class="codex-section-title"><i data-lucide="git-compare-arrows"></i> Continuite Lumena et Codex</div><p>Historique borne au workspace, passations sans raisonnement cache et un seul writer.</p></div><select class="input" data-codex-share-mode aria-label="Niveau de partage Codex"><option value="none">Aucun partage</option><option value="selected">Taches selectionnees</option><option value="workspace">Workspace Lumena</option><option value="all_local">Toutes les taches locales</option></select></div><textarea class="input codex-collaboration-instruction" data-codex-collaboration-instruction rows="2" placeholder="Instruction de revue en lecture seule"></textarea><div class="codex-thread-list" data-codex-thread-list><div class="codex-thread-empty">Connectez le compte pour voir les taches autorisees.</div></div><div class="codex-thread-detail" data-codex-thread-detail hidden></div></section><label class="codex-fallback">Fallback API payant<select class="input" data-cfg="LUMENA_CODEX_API_FALLBACK"><option value="never" ${fallback==='never'?'selected':''}>Jamais</option><option value="ask" ${fallback==='ask'?'selected':''}>Demander avant</option></select></label><div class="codex-no-billing"><i data-lucide="shield-ban"></i><strong>Aucun fallback API payant implicite.</strong> L'abonnement couvre les usages Codex autorises, pas les API image, voix, video ou Realtime.</div></div></div>`;
  const configuredSurfaces=String(_value('LUMENA_CODEX_SURFACES','codeagent')).split(',').map(value=>value.trim());
  const hiddenSurfaces=container.querySelector('[data-cfg="LUMENA_CODEX_SURFACES"]');
  if(hiddenSurfaces)hiddenSurfaces.value=['codeagent','chat','agent','missions'].filter(value=>value==='codeagent'||configuredSurfaces.includes(value)).join(',');
  const surfaceRows=container.querySelectorAll('.codex-surface-section>div');
  if(surfaceRows[1])surfaceRows[1].innerHTML='<strong>Chat texte</strong><button type="button" class="codex-surface-toggle" data-codex-surface="chat" aria-pressed="false"><i data-lucide="message-circle"></i><span>Inactif</span></button>';
  if(surfaceRows[2]){
    surfaceRows[2].innerHTML='<strong>Agent</strong><button type="button" class="codex-surface-toggle" data-codex-surface="agent" aria-pressed="false"><i data-lucide="bot"></i><span>Agent inactif</span></button>';
    surfaceRows[2].insertAdjacentHTML('afterend','<div><strong>Missions</strong><button type="button" class="codex-surface-toggle" data-codex-surface="missions" aria-pressed="false"><i data-lucide="rocket"></i><span>Missions inactives</span></button></div>');
  }
  if(surfaceRows[0])surfaceRows[0].querySelector('.codex-surface-state').textContent='Certifie S4';
  _bind();
  _setChatSurface(configuredSurfaces.includes('chat'));
  _setSurface('agent',configuredSurfaces.includes('agent'));
  _setSurface('missions',configuredSurfaces.includes('missions'));
  _selectMode(mode);
  if(window.lucide)window.lucide.createIcons();
  _refresh();
}
