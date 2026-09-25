const STORAGE_KEY='lumena_active_steering_task';
let activeTaskId=sessionStorage.getItem(STORAGE_KEY)||'';
let active=false;
let policy='next_checkpoint';
let commands=new Map();
let pollTimer=null;
let submitting=false;
let controlsBound=false;

function headers(){const value={'Content-Type':'application/json'};const token=(typeof ADMIN_TOKEN!=='undefined'&&ADMIN_TOKEN)||window.ADMIN_TOKEN||globalThis.ADMIN_TOKEN||'';if(token)value.Authorization=`Bearer ${token}`;return value}
function escText(value){return String(value||'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function statusLabel(status){return({pending:'en attente',delivered:'remise',incorporated:'prise en compte',applied:'appliquée',partially_applied:'à vérifier',rejected:'refusée',superseded:'remplacée',cancelled:'annulée',late:'arrivée trop tard'})[status]||status}

function feedback(message,kind='error'){
  if(typeof window.logC==='function')window.logC(message,kind);
  const input=document.getElementById('message-input');
  if(!input)return;
  input.setAttribute('aria-invalid',kind==='error'?'true':'false');
  input.title=message;
  input.classList.add(kind==='error'?'shake':'steering-sent');
  setTimeout(()=>{
    input.removeAttribute('aria-invalid');
    input.removeAttribute('title');
    input.classList.remove('shake','steering-sent');
  },900);
}

const ANNULABLES=new Set(['pending','delivered']);

async function annulerOrientation(commandId){
  if(!activeTaskId||!commandId)return false;
  try{
    const url=`${API_BASE}/api/tasks/${encodeURIComponent(activeTaskId)}/steering/${encodeURIComponent(commandId)}/cancel`;
    const response=await fetch(url,{method:'POST',headers:headers()});
    if(!response.ok){
      feedback(response.status===401||response.status===403
        ?'Annulation refusee : autorisation'
        :'Annulation impossible','error');
      return false;
    }
    const data=await response.json();
    const ancienne=commands.get(commandId);
    commands.set(commandId,data);
    // ORI-2a : annuler sert aussi a CORRIGER — le texte revient dans le composer,
    // sauf si on y a deja ecrit autre chose.
    const input=document.getElementById('message-input');
    if(input&&ancienne&&ancienne.text&&!input.value.trim()){
      input.value=ancienne.text;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.focus();
    }
    feedback('Orientation annulee','success');
    render();
    return true;
  }catch(error){
    feedback(`Annulation impossible: ${error.message}`,'error');
    return false;
  }
}

function render(){
  const panel=document.getElementById('steering-live');
  if(panel)panel.hidden=!active;
  document.querySelectorAll('[data-steering-policy]').forEach(button=>button.classList.toggle('active',button.dataset.steeringPolicy===policy));
  const history=document.getElementById('steering-history');
  if(history)history.innerHTML=[...commands.values()].slice(-8).map(command=>`<div class="steering-chip" data-status="${escText(command.status)}" title="Orientation ${escText(statusLabel(command.status))}"><span>${escText(command.text)}</span><strong>${escText(statusLabel(command.status))}</strong>${ANNULABLES.has(command.status)?`<button type="button" class="steering-cancel" data-steering-cancel="${escText(command.command_id)}" title="Annuler cette orientation" aria-label="Annuler cette orientation">×</button>`:''}</div>`).join('');
  const input=document.getElementById('message-input');
  if(input)input.placeholder=active?'Oriente Lumena sans interrompre son travail...':'Ecris ton message...';
}

function setActive(value,taskId=''){
  active=!!value;
  if(taskId){activeTaskId=taskId;sessionStorage.setItem(STORAGE_KEY,taskId)}
  if(!active){stopPolling()}
  else startPolling();
  render();
}

async function discoverActiveTask(){
  const conversationId=localStorage.getItem('lumena_active_conversation_id')||'';
  const query=conversationId?`?conversation_id=${encodeURIComponent(conversationId)}`:'';
  try{
    const response=await fetch(`${API_BASE}/api/work/active${query}`,{headers:headers()});
    if(!response.ok){
      if(response.status===401||response.status===403)feedback('Orientation indisponible : autorisation refusee par le serveur','error');
      return '';
    }
    const roots=(await response.json()).work||[];
    const restored=roots.find(item=>String(item.task_id||'')===activeTaskId);
    const candidates=[...roots].sort((a,b)=>{
      const runningDelta=(b.state==='running'?1:0)-(a.state==='running'?1:0);
      if(runningDelta)return runningDelta;
      return Date.parse(b.updated_at||0)-Date.parse(a.updated_at||0);
    });
    const target=restored||candidates[0];
    if(!target?.task_id){
      activeTaskId='';
      sessionStorage.removeItem(STORAGE_KEY);
      setActive(false);
      return '';
    }
    setActive(true,String(target.task_id));
    return activeTaskId;
  }catch(_error){return ''}
}

function beginWork(){
  activeTaskId='';
  sessionStorage.removeItem(STORAGE_KEY);
  setActive(true);
  setTimeout(discoverActiveTask,250);
}

function isActive(){return active}

async function refresh(){
  if(!activeTaskId)return;
  try{
    const response=await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(activeTaskId)}/steering`,{headers:headers()});
    if(response.status===409||response.status===404){setActive(false);return}
    if(!response.ok)return;
    const data=await response.json();
    for(const command of data.commands||[])commands.set(command.command_id,command);
    render();
  }catch(_error){}
}

function startPolling(){if(pollTimer)return;refresh();pollTimer=setInterval(refresh,1600)}
function stopPolling(){if(pollTimer){clearInterval(pollTimer);pollTimer=null}}

async function submitFromComposer(){
  if(submitting)return false;
  const input=document.getElementById('message-input');
  const text=(input?.value||'').trim();
  if(!text){input?.classList.add('shake');setTimeout(()=>input?.classList.remove('shake'),400);return false}
  if(!activeTaskId)await discoverActiveTask();
  if(!activeTaskId){
    feedback('Orientation impossible : travail actif introuvable','error');
    input.focus();
    return false;
  }
  const idempotency=(globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`).toString();
  const conversationId=localStorage.getItem('lumena_active_conversation_id')||null;
  submitting=true;
  try{
    const response=await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(activeTaskId)}/steering`,{
      method:'POST',headers:headers(),body:JSON.stringify({text,delivery_policy:policy,idempotency_key:idempotency,conversation_id:conversationId}),
    });
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail?.code||`HTTP ${response.status}`);
    commands.set(data.command_id,data);
    if(data.accepted){
      input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));
      if(typeof window.addMsg==='function')window.addMsg('user',text,null,`<div class="steering-chip" data-status="${escText(data.status)}"><span>Orientation · ${escText(statusLabel(data.status))}</span></div>`);
      feedback(`Orientation ${statusLabel(data.status)}`,'success');
    }else{
      setActive(false);
      input.focus();
    }
    render();
    return true;
  }catch(error){
    activeTaskId='';
    sessionStorage.removeItem(STORAGE_KEY);
    feedback(`Orientation impossible: ${error.message}`,'error');
    input.focus();
    return false;
  }finally{
    submitting=false;
  }
}

function observeStreamEvent(data){
  if(data?.conversation_id)localStorage.setItem('lumena_active_conversation_id',String(data.conversation_id));
  if(data?.task_id)setActive(true,String(data.task_id));
  if(data?.type==='done'||data?.type==='error')setTimeout(()=>{refresh();setActive(false)},250);
}

function initChatSteering(){
  document.querySelectorAll('[data-steering-policy]').forEach(button=>button.addEventListener('click',()=>{policy=button.dataset.steeringPolicy;render()}));
  if(!controlsBound){
    controlsBound=true;
    document.addEventListener('keydown',event=>{
      if(!active||event.target?.id!=='message-input'||event.key!=='Enter'||event.shiftKey||event.isComposing)return;
      event.preventDefault();
      event.stopImmediatePropagation();
      submitFromComposer();
    },true);
    document.addEventListener('click',event=>{
      const croix=event.target?.closest?.('[data-steering-cancel]');
      if(!croix)return;
      event.preventDefault();
      event.stopImmediatePropagation();
      annulerOrientation(croix.dataset.steeringCancel);
    },true);
    document.addEventListener('click',event=>{
      if(!active||!event.target?.closest?.('#send-btn'))return;
      event.preventDefault();
      event.stopImmediatePropagation();
      submitFromComposer();
    },true);
    document.addEventListener('lumena:app-ready',()=>discoverActiveTask());
  }
  setTimeout(discoverActiveTask,activeTaskId?0:500);
  render();
}

window.LumenaSteering={submitFromComposer,observeStreamEvent,setActive,refresh,discoverActiveTask,beginWork,isActive};
export {initChatSteering,submitFromComposer,observeStreamEvent,setActive,discoverActiveTask,beginWork,isActive};
