/* First-run product tour. Declarative, resumable and inert once completed. */
const API = '/api/onboarding';
const FLOWS = {
  default:['orientation','first_goal','mode_choice','first_message','work_progress','files','next_destination','complete'],
  chat:['orientation','first_goal','mode_choice','first_message','files','next_destination','complete'],
  agent:['orientation','first_goal','mode_choice','first_message','work_progress','files','next_destination','complete'],
  file:['orientation','first_goal','files','mode_choice','first_message','next_destination','complete'],
};
let ORDER = FLOWS.default;

let state = null;
let active = false;
let current = '';
let resizeObserver = null;
let positionFrame = 0;
let agentProgressSeen = false;
let dismissConfirm = false;
let revealAttempted = false;

const copy = {
  orientation: {
    eyebrow:'Bienvenue dans Lumena', title:'Tout reste à portée de main',
    text:'La navigation regroupe tes projets, la mémoire, les missions et les réglages. Tu peux revenir au Chat à tout moment.',
    target:'navigation', action:'Découvrir le mode de travail',
  },
  first_goal: {
    eyebrow:'Ton premier succès', title:'Que veux-tu faire maintenant ?',
    text:'Le parcours s’adapte à ton objectif. Rien ne sera lancé ni envoyé sans ton action.',
    target:'chat-area', goals:true, centered:true,
  },
  mode_choice: {
    eyebrow:'Chat ou Agent', title:'Choisis le niveau d’action',
    text:'Chat répond et échange. Agent peut planifier, utiliser les outils et produire des livrables. Ton choix change réellement le mode actif.',
    target:'agent-mode', modeOptions:true, proof:true,
  },
  first_message: {
    eyebrow:'Première action', title:'Parle à Lumena',
    text:'Écris ton propre message puis envoie-le. Cette étape consommera des tokens uniquement quand tu appuieras toi-même sur Envoyer.',
    target:'composer', action:'En attente d’une réponse réelle…', proof:true,
  },
  work_progress: {
    eyebrow:'Suivre le travail', title:'Les preuves apparaissent ici',
    text:'En mode Agent, Lumena affiche son plan et sa progression. En Chat, tu peux continuer sans cette étape.',
    target:'chat-area', action:'Continuer', optional:true,
  },
  files: {
    eyebrow:'Travailler avec tes fichiers', title:'Ajoute du contexte quand tu en as besoin',
    text:'Ce bouton joint un document, une image ou du code. Aucun fichier n’est envoyé tant que tu ne le sélectionnes pas.',
    target:'file-button', action:'Continuer',
  },
  next_destination: {
    eyebrow:'Aller plus loin', title:'Que veux-tu découvrir ?',
    text:'Choisis une destination. Le tutoriel ouvrira le vrai panneau, sans lancer de tâche automatiquement.',
    target:'agent-navigation', destinations:true,
  },
  complete: {
    eyebrow:'Lumena est prête', title:'Tu gardes le contrôle',
    text:'Utilise Ctrl+K pour naviguer rapidement. Le tutoriel reste rejouable depuis Configuration.',
    action:'Terminer', final:true,
  },
};

function currentAdminToken(){
  return typeof ADMIN_TOKEN!=='undefined'&&ADMIN_TOKEN ? ADMIN_TOKEN : (window.ADMIN_TOKEN||'');
}

function headers(){
  const value = currentAdminToken();
  return {'Content-Type':'application/json', ...(value ? {Authorization:`Bearer ${value}`} : {})};
}

async function api(path, body){
  const response = await fetch(`${API}${path}`, {
    method: body === undefined ? 'GET' : 'POST', headers:headers(),
    ...(body === undefined ? {} : {body:JSON.stringify(body)}),
  });
  if(!response.ok){
    let detail=`HTTP ${response.status}`;
    try{detail=(await response.json()).detail||detail}catch(_){}
    throw new Error(detail);
  }
  return response.json();
}

function ensureUi(){
  if(document.getElementById('onboarding-layer'))return;
  const layer=document.createElement('div');
  layer.id='onboarding-layer';
  layer.className='onboarding-layer';
  layer.hidden=true;
  layer.innerHTML=`
    <div class="onboarding-focus" aria-hidden="true" hidden></div>
    <section class="onboarding-popover" role="dialog" aria-modal="false" aria-labelledby="onboarding-title">
      <div class="onboarding-head">
        <span class="onboarding-brand"><img src="/static/branding/lumena-logo.png" alt=""> Lumena</span>
        <button class="onboarding-icon" type="button" data-onboarding-skip title="Passer le tutoriel" aria-label="Passer le tutoriel"><i data-lucide="x"></i></button>
      </div>
      <div class="onboarding-progress" aria-label="Progression du tutoriel"><span></span></div>
      <div class="onboarding-eyebrow"></div>
      <h2 id="onboarding-title"></h2>
      <p class="onboarding-copy"></p>
      <div class="onboarding-modes" hidden>
        <button type="button" data-mode="chat"><i data-lucide="message-circle"></i><span>Chat</span></button>
        <button type="button" data-mode="agent"><i data-lucide="plug"></i><span>Agent</span></button>
      </div>
      <div class="onboarding-goals" hidden>
        <button type="button" data-goal="chat"><i data-lucide="messages-square"></i><span><strong>Échanger</strong><small>Poser une question et recevoir une réponse</small></span></button>
        <button type="button" data-goal="agent"><i data-lucide="wand-sparkles"></i><span><strong>Faire agir Lumena</strong><small>Planifier et suivre une action réelle</small></span></button>
        <button type="button" data-goal="file"><i data-lucide="file-search"></i><span><strong>Analyser un fichier</strong><small>Joindre un document puis demander son analyse</small></span></button>
      </div>
      <div class="onboarding-destinations" hidden>
        <button type="button" data-destination="missions"><i data-lucide="rocket"></i><span>Missions</span></button>
        <button type="button" data-destination="document-studio"><i data-lucide="library-big"></i><span>Documents</span></button>
        <button type="button" data-destination="workspaces"><i data-lucide="folder-open"></i><span>Projets</span></button>
      </div>
      <div class="onboarding-note" role="status" aria-live="polite" hidden></div>
      <button class="onboarding-suggestion" type="button" hidden><i data-lucide="text-cursor-input"></i><span></span></button>
      <div class="onboarding-actions">
        <button class="onboarding-back" type="button"><i data-lucide="arrow-left"></i> Retour</button>
        <button class="onboarding-next" type="button">Continuer <i data-lucide="arrow-right"></i></button>
      </div>
    </section>`;
  document.body.appendChild(layer);
  layer.querySelector('[data-onboarding-skip]').addEventListener('click',showDismissConfirm);
  layer.querySelector('.onboarding-back').addEventListener('click', goBack);
  layer.querySelector('.onboarding-next').addEventListener('click',()=>safely(goNext));
  layer.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>safely(()=>chooseMode(button.dataset.mode))));
  layer.querySelectorAll('[data-goal]').forEach(button=>button.addEventListener('click',()=>safely(()=>chooseGoal(button.dataset.goal))));
  layer.querySelectorAll('[data-destination]').forEach(button=>button.addEventListener('click',()=>safely(()=>chooseDestination(button.dataset.destination))));
  layer.querySelector('.onboarding-suggestion').addEventListener('click',fillSuggestedPrompt);
  window.addEventListener('resize',queuePosition,{passive:true});
  window.addEventListener('scroll',queuePosition,{passive:true,capture:true});
  document.addEventListener('keydown',event=>{
    if(!active)return;
    if(event.key==='Escape')showDismissConfirm();
    if(event.key==='ArrowLeft'&&!event.target.matches('input,textarea'))goBack();
    if(event.key==='ArrowRight'&&!event.target.matches('input,textarea')&&!copy[current]?.proof&&!copy[current]?.destinations)safely(goNext);
  });
}

function setFlow(goal){ORDER=FLOWS[goal]||FLOWS.default}

function suggestedPrompt(){
  if(state?.selected_goal==='file')return 'Résume ce fichier, relève les points importants et indique les actions à retenir.';
  if(state?.selected_goal==='agent')return 'Analyse mon espace de travail et propose une première action utile, sans rien modifier avant mon accord.';
  return 'Présente-toi brièvement et explique ce que tu peux faire pour moi au quotidien.';
}

function fillSuggestedPrompt(){
  const input=document.getElementById('message-input');if(!input)return;
  input.value=suggestedPrompt();input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
}

function showError(error){
  const note=document.querySelector('#onboarding-layer .onboarding-note');
  if(!note)return;
  note.hidden=false;
  note.textContent=`Impossible d’enregistrer cette étape : ${error.message}. Réessaie sans quitter Lumena.`;
}

async function safely(action){
  try{return await action()}catch(error){showError(error);return null}
}

function targetFor(step){
  return document.querySelector(`[data-onboarding-target="${copy[step]?.target||''}"]`);
}

function queuePosition(){
  cancelAnimationFrame(positionFrame);
  positionFrame=requestAnimationFrame(position);
}

function layerCoordinateScale(){
  const root=document.documentElement;
  if(!root.classList.contains('lumena-desktop-zoom'))return 1;
  const scale=Number.parseFloat(root.dataset.lumenaDesktopZoom||'1');
  return Number.isFinite(scale)&&scale>0?scale:1;
}

function layerPx(value,scale){
  return `${value/scale}px`;
}

function visibleTargetRect(target){
  const source=target.getBoundingClientRect();
  let left=Math.max(0,source.left), top=Math.max(0,source.top);
  let right=Math.min(innerWidth,source.right), bottom=Math.min(innerHeight,source.bottom);
  for(let parent=target.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
    const style=getComputedStyle(parent);
    const bounds=parent.getBoundingClientRect();
    if(/auto|scroll|hidden|clip/.test(`${style.overflowX} ${style.overflow}`)){
      left=Math.max(left,bounds.left);right=Math.min(right,bounds.right);
    }
    if(/auto|scroll|hidden|clip/.test(`${style.overflowY} ${style.overflow}`)){
      top=Math.max(top,bounds.top);bottom=Math.min(bottom,bounds.bottom);
    }
  }
  left=Math.max(0,Math.min(innerWidth,left));right=Math.max(left,Math.min(innerWidth,right));
  top=Math.max(0,Math.min(innerHeight,top));bottom=Math.max(top,Math.min(innerHeight,bottom));
  return {left,top,right,bottom,width:right-left,height:bottom-top};
}

function popoverPosition(rect,pw,ph){
  const edge=12,gap=16;
  const clampX=value=>Math.min(innerWidth-pw-edge,Math.max(edge,value));
  const clampY=value=>Math.min(innerHeight-ph-edge,Math.max(edge,value));
  const centeredX=clampX(rect.left+(rect.width-pw)/2);
  const centeredY=clampY(rect.top+(rect.height-ph)/2);
  const candidates=[
    {x:rect.right+gap,y:centeredY,fits:rect.right+gap+pw<=innerWidth-edge},
    {x:rect.left-pw-gap,y:centeredY,fits:rect.left-pw-gap>=edge},
    {x:centeredX,y:rect.bottom+gap,fits:rect.bottom+gap+ph<=innerHeight-edge},
    {x:centeredX,y:rect.top-ph-gap,fits:rect.top-ph-gap>=edge},
  ];
  const chosen=candidates.find(item=>item.fits);
  return chosen||{x:clampX(rect.right+gap),y:clampY(rect.top)};
}

function position(){
  const layer=document.getElementById('onboarding-layer');
  const pop=layer?.querySelector('.onboarding-popover');
  const focus=layer?.querySelector('.onboarding-focus');
  if(!layer||!pop||!focus||!active)return;
  const target=targetFor(current);
  if(copy[current]?.centered||!target||!target.getClientRects().length){
    layer.classList.add('is-centered'); focus.hidden=true; pop.style.removeProperty('--tour-x');pop.style.removeProperty('--tour-y');return;
  }
  layer.classList.remove('is-centered');
  const r=visibleTargetRect(target);
  if(r.width<2||r.height<2){
    if(!revealAttempted){
      revealAttempted=true;
      target.scrollIntoView({block:'nearest',inline:'nearest'});
      queuePosition();
      return;
    }
    layer.classList.add('is-centered');focus.hidden=true;return;
  }
  const padding=6;
  const focusLeft=Math.max(8,r.left-padding),focusTop=Math.max(8,r.top-padding);
  const focusRight=Math.min(innerWidth-8,r.right+padding),focusBottom=Math.min(innerHeight-8,r.bottom+padding);
  const scale=layerCoordinateScale();
  focus.style.left=layerPx(focusLeft,scale);focus.style.top=layerPx(focusTop,scale);
  focus.style.width=layerPx(Math.max(0,focusRight-focusLeft),scale);
  focus.style.height=layerPx(Math.max(0,focusBottom-focusTop),scale);
  focus.hidden=false;
  const popRect=pop.getBoundingClientRect();
  const pw=popRect.width||Math.min(390,innerWidth-24), ph=popRect.height||330;
  let {x,y}=popoverPosition(r,pw,ph);
  if(innerWidth<760){x=12;y=Math.max(12,innerHeight-ph-12)}
  pop.style.setProperty('--tour-x',layerPx(x,scale));pop.style.setProperty('--tour-y',layerPx(y,scale));
}

function render(step){
  ensureUi();
  dismissConfirm=false;
  revealAttempted=false;
  current=ORDER.includes(step)?step:'orientation';
  const spec=copy[current];
  const layer=document.getElementById('onboarding-layer');
  active=true;layer.hidden=false;layer.querySelector('.onboarding-focus').hidden=true;
  layer.querySelector('.onboarding-eyebrow').textContent=spec.eyebrow;
  layer.querySelector('#onboarding-title').textContent=spec.title;
  layer.querySelector('.onboarding-copy').textContent=spec.text;
  layer.querySelector('.onboarding-progress span').style.width=`${((ORDER.indexOf(current)+1)/ORDER.length)*100}%`;
  layer.querySelector('.onboarding-modes').hidden=!spec.modeOptions;
  layer.querySelector('.onboarding-goals').hidden=!spec.goals;
  layer.querySelector('.onboarding-destinations').hidden=!spec.destinations;
  const suggestion=layer.querySelector('.onboarding-suggestion');
  suggestion.hidden=current!=='first_message';
  suggestion.querySelector('span').textContent=current==='first_message'?`Préremplir : « ${suggestedPrompt()} »`:'';
  const note=layer.querySelector('.onboarding-note');
  note.hidden=!spec.proof;
  note.textContent=current==='first_message'
    ?'La suite se débloque uniquement après une réponse reçue.'
    :(current==='mode_choice'?'Choisis le mode qui te convient pour continuer.':'');
  const back=layer.querySelector('.onboarding-back');
  back.hidden=ORDER.indexOf(current)===0;
  back.innerHTML='<i data-lucide="arrow-left"></i> Retour';
  const next=layer.querySelector('.onboarding-next');
  next.hidden=!!spec.destinations||!!spec.modeOptions||!!spec.goals;
  const fileProof=current==='files'&&state?.selected_goal==='file';
  next.disabled=!!spec.proof||fileProof;
  next.innerHTML=`${spec.action||'Continuer'}${spec.final?'':' <i data-lucide="arrow-right"></i>'}`;
  if(typeof lucide!=='undefined')lucide.createIcons({nodes:[layer]});
  resizeObserver?.disconnect();
  const target=targetFor(current);
  if(target&&window.ResizeObserver){resizeObserver=new ResizeObserver(queuePosition);resizeObserver.observe(target)}
  queuePosition();
  if(current==='first_message')setTimeout(()=>document.getElementById('message-input')?.focus(),0);
  if(fileProof){note.hidden=false;note.textContent='Choisis réellement un fichier pour continuer. Il ne sera envoyé qu’avec ton prochain message.'}
}

function showDismissConfirm(){
  if(!active||dismissConfirm)return;
  dismissConfirm=true;
  const layer=document.getElementById('onboarding-layer');
  layer.classList.add('is-centered');
  layer.querySelector('.onboarding-eyebrow').textContent='Reprendre plus tard';
  layer.querySelector('#onboarding-title').textContent='Quitter le tutoriel ?';
  layer.querySelector('.onboarding-copy').textContent='Ta progression est conservée. Tu pourras relancer le parcours depuis Configuration.';
  layer.querySelector('.onboarding-modes').hidden=true;
  layer.querySelector('.onboarding-goals').hidden=true;
  layer.querySelector('.onboarding-destinations').hidden=true;
  layer.querySelector('.onboarding-suggestion').hidden=true;
  layer.querySelector('.onboarding-note').hidden=true;
  const back=layer.querySelector('.onboarding-back');back.hidden=false;back.textContent='Continuer le tutoriel';
  const next=layer.querySelector('.onboarding-next');next.hidden=false;next.disabled=false;next.textContent='Reprendre plus tard';
  queuePosition();
}

function hide(){
  active=false;
  resizeObserver?.disconnect();
  const layer=document.getElementById('onboarding-layer');if(layer)layer.hidden=true;
}

async function mark(step,event=null){
  state=await api('/progress',{step,...(event?{event}:{})});
  return state;
}

async function goNext(){
  if(dismissConfirm){await skipTour();return}
  const spec=copy[current];
  if(spec.proof)return;
  if(current==='work_progress'&&spec.optional){
    state=await api('/skip',{steps:['work_progress'],dismiss:false});render(state.current_step||'files');return;
  }
  if(spec.final){state=await api('/complete',{});hide();return}
  await mark(current);
  const index=ORDER.indexOf(current);
  render(ORDER[Math.min(index+1,ORDER.length-1)]);
}

function goBack(){
  if(dismissConfirm){render(current);return}
  const index=ORDER.indexOf(current);
  if(index>0)render(ORDER[index-1]);
}

async function skipTour(){
  try{state=await api('/skip',{steps:ORDER.filter(step=>step!=='complete'&&!state?.completed_steps?.includes(step)),dismiss:true})}catch(_){}
  hide();
}

async function chooseDestination(panel){
  if(typeof window.switchPanel==='function')window.switchPanel(panel);
  await mark('next_destination');
  render('complete');
}

async function chooseGoal(goal){
  state=await api('/goal',{goal});setFlow(goal);render(goal==='file'?'files':'mode_choice');
}

async function chooseMode(mode){
  const wantsAgent=mode==='agent';
  const toggle=targetFor('mode_choice');
  const isAgent=!!toggle?.classList.contains('active');
  if(toggle&&isAgent!==wantsAgent){
    toggle.click();
    return;
  }
  state=await mark('mode_choice','mode_selected');
  render('first_message');
}

async function onApplicationEvent(event){
  if(!active)return;
  if(event.type==='lumena:mode-changed'&&current==='mode_choice'){
    state=await mark('mode_choice','mode_selected');render('first_message');return;
  }
  if(event.type==='lumena:agent-progress')agentProgressSeen=true;
  if(event.type==='lumena:chat-response'&&current==='first_message'){
    state=await mark('first_message','chat_response_received');
    if(state?.selected_goal==='file'){render('next_destination');return}
    if(agentProgressSeen){state=await mark('work_progress','agent_progress_observed');render('files')}
    else render(event.detail?.agent?'work_progress':'files');
  }else if(event.type==='lumena:agent-progress'&&current==='work_progress'){
    state=await mark('work_progress','agent_progress_observed');render('files');
  }
}

export async function initOnboarding({force=false}={}){
  ensureUi();
  try{
    state=await api('/status');
    setFlow(state.selected_goal);
    const pending=localStorage.getItem('lumena_onboarding_pending')==='1';
    const legacyDone=localStorage.getItem('wizardJustDone')==='1';
    if(!force&&!pending&&legacyDone&&state.tour_status==='not_started'){
      state=await api('/complete',{});
      return;
    }
    if(force||pending){state=await api('/start',{});setFlow(state.selected_goal);localStorage.removeItem('lumena_onboarding_pending')}
    if(state.setup_completed&&state.tour_status==='in_progress')render(state.current_step);
  }catch(error){console.warn('[onboarding] indisponible:',error.message)}
}

export async function replayOnboarding(){
  try{agentProgressSeen=false;state=await api('/reset',{});state=await api('/start',{});setFlow(null);render('orientation')}catch(error){
    ensureUi();
    const layer=document.getElementById('onboarding-layer');
    active=true;layer.hidden=false;layer.classList.add('is-centered');
    layer.querySelector('.onboarding-eyebrow').textContent='Tutoriel indisponible';
    layer.querySelector('#onboarding-title').textContent='Impossible de démarrer le parcours';
    layer.querySelector('.onboarding-copy').textContent='Lumena reste utilisable. Vérifie ta session puis réessaie depuis Configuration.';
    layer.querySelector('.onboarding-progress span').style.width='0%';
    layer.querySelector('.onboarding-modes').hidden=true;
    layer.querySelector('.onboarding-goals').hidden=true;
    layer.querySelector('.onboarding-destinations').hidden=true;
    layer.querySelector('.onboarding-suggestion').hidden=true;
    layer.querySelector('.onboarding-back').hidden=true;
    layer.querySelector('.onboarding-next').hidden=true;
    showError(error);
    queuePosition();
  }
}

document.addEventListener('lumena:app-ready',()=>initOnboarding());
document.addEventListener('lumena:mode-changed',event=>safely(()=>onApplicationEvent(event)));
document.addEventListener('lumena:chat-response',event=>safely(()=>onApplicationEvent(event)));
document.addEventListener('lumena:agent-progress',event=>safely(()=>onApplicationEvent(event)));
document.getElementById('file-upload-input')?.addEventListener('change',event=>safely(async()=>{
  if(!active||current!=='files'||!event.target.files?.length)return;
  state=await mark('files');render(state?.selected_goal==='file'?'mode_choice':'next_destination');
}));
