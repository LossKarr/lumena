/* ============================================================
   NAVIGATION — Lumena Control Panel
   ============================================================ */
export function setupNavigation(){
  document.querySelectorAll('.nav-item').forEach(item=>{
    item.addEventListener('click',()=>switchPanel(item.dataset.panel));
  });
}

export function switchPanel(panelName){
  // Lot 4.3 — ferme le flux SSE des missions quand on quitte le panneau (1 seule conn max).
  if(panelName!=='missions'&&window.closeMissionStream){try{window.closeMissionStream()}catch(_){}}
  // Overview owns a cancellable batch and a WebGL loop; suspend both off-panel.
  if(panelName!=='overview'&&window.stopOverview){try{window.stopOverview()}catch(_){}}
  document.querySelectorAll('.nav-item').forEach(i=>i.classList.toggle('active',i.dataset.panel===panelName));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id===`panel-${panelName}`));
  document.getElementById('topbar-title').textContent=panelName.charAt(0).toUpperCase()+panelName.slice(1).replace(/-/g,' ');
  loadPanelData(panelName);
  document.dispatchEvent(new CustomEvent('lumena:panel-changed',{detail:{panel:panelName}}));
}

export function toggleSection(el){el.closest('.nav-section').classList.toggle('collapsed')}
export function toggleNavCollapse(){
  navCollapsed=!navCollapsed;
  document.getElementById('app-shell').classList.toggle('shell--nav-collapsed',navCollapsed);
}
export function toggleMobileNav(){document.getElementById('shell-nav').classList.toggle('mobile-open')}
export function toggleFocus(){
  focusMode=!focusMode;
  document.getElementById('app-shell').classList.toggle('shell--focus',focusMode);
}
export function toggleTheme(){
  const root=document.documentElement;
  const mode=root.getAttribute('data-theme-mode')==='dark'?'light':'dark';
  applyTheme(mode);
}
export function applyTheme(mode){
  const root=document.documentElement;
  root.setAttribute('data-theme-mode',mode);
  localStorage.setItem('lumena_theme',mode);
  const orb=document.getElementById('theme-orb');
  if(orb){
    orb.innerHTML=mode==='dark'?'<i data-lucide="moon"></i>':'<i data-lucide="sun"></i>';
    if(typeof lucide!=='undefined')lucide.createIcons({attrs:{class:'lucide'}})
  }
}

/* ============================================================
   PANEL DATA LOADERS
   ============================================================ */
export function loadPanelData(p){
  switch(p){
    case'repomap':loadRepoMap();break;case'memory':loadRecentMemories();break;
    case'rules':loadRules();break;case'instincts':loadInstincts();break;
    case'tools':loadTools();break;case'emotions':loadEmotions();break;
    case'hooks':loadHooks();break;case'voice':loadVoiceStatus();break;
    case'trace':loadTraceRecent();break;case'tasks':loadActiveTasks();break;
    case'missions':loadMissions();break;
    case'document-studio':loadDocumentStudio();break;
    case'sessions':loadSessions();break;
    case'overview':loadOverview();break;
    case'infra-telegram':loadTelegramDetails();break;
    case'infra-whatsapp':loadWhatsAppDetails();break;
    case'infra-autonomy':loadAutonomyDetails();break;
    case'infra-network':loadNetworkSimple();break;
    case'journal':loadJournal();break;
    case'facts':loadFacts();break;
    case'providers':loadProviders();break;
    case'alerts':loadAlerts();break;
    case'training':loadTraining();break;
    case'finetuning':loadFinetuning();break;
    case'logs':loadLogsRecent();break;
    case'config':loadConfig();break;
    case'ionos':loadIonosSites();break;
    case'mcp':loadMcp();break;
    case'docs':loadDocs();break;
    case'product-docs':loadProductDocs();break;
    case'stripe-overview':loadStripeOverview();break;
    case'stripe-payments':loadStripePayments();break;
    case'stripe-subscriptions':loadStripeSubscriptions();break;
    case'stripe-products':loadStripeProducts();break;
    case'workspaces':loadWorkspaces();break;
  }
}

/* ============================================================
   COMMAND PALETTE
   ============================================================ */
const CMD_RECENTS_KEY='lumena_command_palette_recents_v1';
const CMD_CATEGORY_ORDER=['Essentiel','Intelligence','Supervision','Connexions','Commerce','Systeme','Actions'];

const cmdItems=[
  {id:'chat',panel:'chat',icon:'message-circle',label:'Chat',category:'Essentiel',hint:'Parler et agir avec Lumena',keywords:'conversation assistant accueil'},
  {id:'overview',panel:'overview',icon:'layout-dashboard',label:'Overview',category:'Essentiel',hint:'Vue generale et etat du systeme',keywords:'tableau de bord dashboard sante'},
  {id:'workspaces',panel:'workspaces',icon:'folder-kanban',label:'Projets',category:'Essentiel',hint:'Ouvrir et organiser les espaces de travail',keywords:'workspace fichiers projet'},
  {id:'missions',panel:'missions',icon:'rocket',label:'Missions',category:'Essentiel',hint:'Suivre les missions et leurs workers',keywords:'sous agents delegation background'},
  {id:'documents',panel:'document-studio',icon:'library-big',label:'Documents',category:'Essentiel',hint:'Creer, modifier et classer les documents',keywords:'pdf docx csv studio modeles'},
  {id:'tasks',panel:'tasks',icon:'clipboard-list',label:'Taches',category:'Essentiel',hint:'Voir les travaux planifies et actifs',keywords:'scheduler jobs planning'},
  {id:'sessions',panel:'sessions',icon:'layers',label:'Sessions',category:'Essentiel',hint:'Retrouver les sessions de travail',keywords:'historique conversations'},

  {id:'repomap',panel:'repomap',icon:'map',label:'Repo Map',category:'Intelligence',hint:'Cartographier le depot courant',keywords:'code architecture symboles'},
  {id:'search',panel:'search',icon:'search-code',label:'Code Search',category:'Intelligence',hint:'Rechercher dans le code indexe',keywords:'recherche grep symboles'},
  {id:'memory',panel:'memory',icon:'brain',label:'Memoire',category:'Intelligence',hint:'Consulter la memoire persistante',keywords:'souvenirs chromadb contexte'},
  {id:'journal',panel:'journal',icon:'book-open-text',label:'Journal',category:'Intelligence',hint:'Lire les traces et reflexions conservees',keywords:'historique journee apprentissage'},
  {id:'facts',panel:'facts',icon:'badge-check',label:'Identite',category:'Intelligence',hint:'Verifier les faits et preferences connus',keywords:'profil utilisateur faits'},
  {id:'tools',panel:'tools',icon:'wrench',label:'Outils',category:'Intelligence',hint:'Explorer les capacites disponibles',keywords:'handlers actions fonctions'},
  {id:'rules',panel:'rules',icon:'scroll-text',label:'Regles',category:'Intelligence',hint:'Consulter les regles actives',keywords:'policy securite comportement'},
  {id:'instincts',panel:'instincts',icon:'zap',label:'Instincts',category:'Intelligence',hint:'Voir les automatismes appris',keywords:'patterns confiance reactions'},
  {id:'training',panel:'training',icon:'graduation-cap',label:'Apprentissage',category:'Intelligence',hint:'Suivre les donnees et evaluations',keywords:'training dataset eval'},
  {id:'finetuning',panel:'finetuning',icon:'cpu',label:'Fine-tuning',category:'Intelligence',hint:'Piloter les entrainements specialises',keywords:'modele entrainement'},

  {id:'trace',panel:'trace',icon:'radio',label:'Live Trace',category:'Supervision',hint:'Observer les evenements en direct',keywords:'sse activite temps reel'},
  {id:'console',panel:'console',icon:'square-terminal',label:'Console',category:'Supervision',hint:'Inspecter les sorties techniques',keywords:'terminal debug'},
  {id:'logs',panel:'logs',icon:'file-clock',label:'Logs',category:'Supervision',hint:'Lire les journaux recents',keywords:'erreurs historique diagnostic'},
  {id:'alerts',panel:'alerts',icon:'bell-ring',label:'Alertes',category:'Supervision',hint:'Voir les alertes actives',keywords:'warning incidents notifications'},
  {id:'emotions',panel:'emotions',icon:'heart-pulse',label:'Emotions',category:'Supervision',hint:'Consulter l etat emotionnel',keywords:'humeur energie'},
  {id:'voice',panel:'voice',icon:'mic',label:'Voix',category:'Supervision',hint:'Piloter la conversation vocale',keywords:'tts stt audio parole'},
  {id:'hooks',panel:'hooks',icon:'link',label:'Hooks',category:'Supervision',hint:'Inspecter les integrations evenementielles',keywords:'events callbacks'},

  {id:'telegram',panel:'infra-telegram',icon:'send',label:'Telegram',category:'Connexions',hint:'Etat et configuration Telegram',keywords:'canal messages'},
  {id:'whatsapp',panel:'infra-whatsapp',icon:'message-circle',label:'WhatsApp',category:'Connexions',hint:'Etat et configuration WhatsApp',keywords:'canal messages'},
  {id:'autonomy',panel:'infra-autonomy',icon:'bot',label:'Autonomie',category:'Connexions',hint:'Superviser le daemon autonome',keywords:'24 7 daemon scheduler'},
  {id:'network',panel:'infra-network',icon:'network',label:'Instances & Reseau',category:'Connexions',hint:'Voir les pairs et les instances',keywords:'p2p flotte peers'},
  {id:'mcp',panel:'mcp',icon:'plug-zap',label:'MCP',category:'Connexions',hint:'Gerer les serveurs et outils MCP',keywords:'plugins protocol tools',action:()=>switchPanel('mcp')},
  {id:'providers',panel:'providers',icon:'boxes',label:'Providers LLM',category:'Connexions',hint:'Verifier les fournisseurs de modeles',keywords:'api llm openai anthropic google deepseek'},
  {id:'ionos',panel:'ionos',icon:'cloud',label:'IONOS',category:'Connexions',hint:'Gerer les sites et hebergements',keywords:'hosting web domaine'},

  {id:'stripe-overview',panel:'stripe-overview',icon:'credit-card',label:'Paiements - Vue d ensemble',category:'Commerce',hint:'Synthese Stripe',keywords:'stripe ventes revenu'},
  {id:'stripe-payments',panel:'stripe-payments',icon:'wallet-cards',label:'Paiements',category:'Commerce',hint:'Consulter les transactions',keywords:'stripe transactions'},
  {id:'stripe-subscriptions',panel:'stripe-subscriptions',icon:'repeat-2',label:'Abonnements',category:'Commerce',hint:'Consulter les abonnements',keywords:'stripe recurring clients'},
  {id:'stripe-products',panel:'stripe-products',icon:'package',label:'Produits',category:'Commerce',hint:'Consulter le catalogue commercial',keywords:'stripe prix catalogue'},

  {id:'config',panel:'config',icon:'settings',label:'Configuration',category:'Systeme',hint:'Configurer Lumena',keywords:'parametres preferences api'},
  {id:'docs',panel:'docs',icon:'file-cog',label:'Documentation interne',category:'Systeme',hint:'Modifier les documents de controle',keywords:'rules heartbeat mcp methode'},
  {id:'product-docs',panel:'product-docs',icon:'book-open-check',label:'Documentation produit',category:'Systeme',hint:'Consulter le guide utilisateur',keywords:'aide manuel tutoriel'},

  {id:'focus',icon:'maximize',label:'Basculer le mode Focus',category:'Actions',hint:'Masquer ou restaurer la navigation',keywords:'plein ecran concentration',shortcut:'F',action:()=>toggleFocus()},
  {id:'agent',icon:'plug',label:'Basculer le mode Agent',category:'Actions',hint:'Activer ou desactiver l execution autonome',keywords:'chat agent outils',shortcut:'A',action:()=>{if(typeof window.toggleAgent==='function')window.toggleAgent()}},
  {id:'theme',icon:'sun-moon',label:'Changer de theme',category:'Actions',hint:'Alterner entre clair et sombre',keywords:'apparence dark light',shortcut:'T',action:()=>toggleTheme()},
];

// Préserve le contrat historique de window._cmdItems pour les extensions locales.
cmdItems.forEach(command=>{
  if(command.panel&&!command.action)command.action=()=>switchPanel(command.panel);
});

let cmdRendered=[];
let cmdSelectedIndex=0;
let cmdBindingsReady=false;
let cmdPreviousFocus=null;

function commandText(value){
  return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
}

function commandHtml(value){
  return String(value??'').replace(/[&<>'"]/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'
  })[ch]);
}

function availableCommands(){
  return cmdItems.filter(command=>!command.panel||document.getElementById(`panel-${command.panel}`));
}

function recentCommandIds(){
  try{
    const parsed=JSON.parse(localStorage.getItem(CMD_RECENTS_KEY)||'[]');
    return Array.isArray(parsed)?parsed.filter(id=>typeof id==='string').slice(0,5):[];
  }catch(_){return []}
}

function rememberCommand(id){
  try{
    const next=[id,...recentCommandIds().filter(item=>item!==id)].slice(0,5);
    localStorage.setItem(CMD_RECENTS_KEY,JSON.stringify(next));
  }catch(_){}
}

function activePanelName(){
  const panel=document.querySelector('.panel.active');
  return panel?.id?.replace(/^panel-/,'')||'';
}

function commandGroups(query){
  const all=availableCommands();
  if(query){
    const needle=commandText(query);
    const matches=all.filter(command=>commandText([
      command.label,command.hint,command.category,command.keywords
    ].join(' ')).includes(needle));
    return matches.length?[{name:'Resultats',items:matches}]:[];
  }
  const byId=new Map(all.map(command=>[command.id,command]));
  const recent=recentCommandIds().map(id=>byId.get(id)).filter(Boolean);
  const recentIds=new Set(recent.map(command=>command.id));
  const groups=recent.length?[{name:'Recents',items:recent}]:[];
  CMD_CATEGORY_ORDER.forEach(category=>{
    const items=all.filter(command=>command.category===category&&!recentIds.has(command.id));
    if(items.length)groups.push({name:category,items});
  });
  return groups;
}

function updateCommandSelection(nextIndex,{scroll=true}={}){
  if(!cmdRendered.length){cmdSelectedIndex=0;return}
  cmdSelectedIndex=(nextIndex+cmdRendered.length)%cmdRendered.length;
  const input=document.getElementById('cmd-input');
  document.querySelectorAll('.cmd-palette-item').forEach((item,index)=>{
    const selected=index===cmdSelectedIndex;
    item.classList.toggle('selected',selected);
    item.setAttribute('aria-selected',String(selected));
    if(selected){
      input?.setAttribute('aria-activedescendant',item.id);
      if(scroll)item.scrollIntoView({block:'nearest'});
    }
  });
}

function executeCommand(command){
  if(!command)return;
  rememberCommand(command.id);
  closeCommandPalette(false);
  if(command.panel)switchPanel(command.panel);
  else if(typeof command.action==='function')command.action();
}

function ensureCommandPaletteBindings(){
  if(cmdBindingsReady)return;
  const input=document.getElementById('cmd-input');
  const results=document.getElementById('cmd-results');
  if(!input||!results)return;
  cmdBindingsReady=true;
  input.addEventListener('input',filterCommands);
  input.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();updateCommandSelection(cmdSelectedIndex+1)}
    else if(event.key==='ArrowUp'){event.preventDefault();updateCommandSelection(cmdSelectedIndex-1)}
    else if(event.key==='Home'){event.preventDefault();updateCommandSelection(0)}
    else if(event.key==='End'){event.preventDefault();updateCommandSelection(cmdRendered.length-1)}
    else if(event.key==='Enter'){
      event.preventDefault();
      executeCommand(cmdRendered[cmdSelectedIndex]);
    }else if(event.key==='Escape'){
      event.preventDefault();
      closeCommandPalette();
    }
  });
  results.addEventListener('mouseover',event=>{
    const item=event.target.closest('.cmd-palette-item');
    if(item)updateCommandSelection(Number(item.dataset.index),{scroll:false});
  });
  results.addEventListener('click',event=>{
    const item=event.target.closest('.cmd-palette-item');
    if(item)executeCommand(cmdRendered[Number(item.dataset.index)]);
  });
}

export function openCommandPalette(){
  const overlay=document.getElementById('cmd-palette-overlay');
  const input=document.getElementById('cmd-input');
  if(!overlay||!input)return;
  ensureCommandPaletteBindings();
  cmdPreviousFocus=document.activeElement;
  overlay.classList.add('open');
  overlay.setAttribute('aria-hidden','false');
  document.body.classList.add('cmd-palette-open');
  input.value='';
  filterCommands();
  requestAnimationFrame(()=>input.focus());
}

export function closeCommandPalette(restoreFocus=true){
  const overlay=document.getElementById('cmd-palette-overlay');
  if(!overlay?.classList.contains('open'))return;
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden','true');
  document.body.classList.remove('cmd-palette-open');
  if(restoreFocus&&cmdPreviousFocus&&typeof cmdPreviousFocus.focus==='function')cmdPreviousFocus.focus();
}

export function filterCommands(){
  const input=document.getElementById('cmd-input');
  const results=document.getElementById('cmd-results');
  const count=document.getElementById('cmd-result-count');
  if(!input||!results)return;
  const groups=commandGroups(input.value);
  cmdRendered=groups.flatMap(group=>group.items);
  cmdSelectedIndex=0;
  if(count)count.textContent=cmdRendered.length?`${cmdRendered.length} acces`:'Aucun resultat';
  if(!cmdRendered.length){
    results.innerHTML=`<div class="cmd-palette-empty"><i data-lucide="search-x"></i><strong>Aucun acces trouve</strong><span>Essaie un panneau, un outil ou une fonction.</span></div>`;
  }else{
    let index=0;
    const current=activePanelName();
    results.innerHTML=groups.map(group=>`
      <section class="cmd-palette-group" aria-label="${commandHtml(group.name)}">
        <div class="cmd-palette-group-title">${commandHtml(group.name)}</div>
        ${group.items.map(command=>{
          const itemIndex=index++;
          const isCurrent=command.panel===current;
          return `<button type="button" id="cmd-option-${itemIndex}" class="cmd-palette-item${itemIndex===0?' selected':''}" role="option" aria-selected="${itemIndex===0}" data-index="${itemIndex}">
            <span class="cmd-palette-icon"><i data-lucide="${command.icon}"></i></span>
            <span class="cmd-palette-copy"><strong>${commandHtml(command.label)}</strong><small>${commandHtml(command.hint)}</small></span>
            ${isCurrent?'<span class="cmd-palette-current">Ouvert</span>':command.shortcut?`<kbd>${commandHtml(command.shortcut)}</kbd>`:'<i class="cmd-palette-arrow" data-lucide="arrow-up-right"></i>'}
          </button>`;
        }).join('')}
      </section>`).join('');
    input.setAttribute('aria-activedescendant','cmd-option-0');
  }
  if(typeof lucide!=='undefined')lucide.createIcons({attrs:{class:'lucide'}});
}

// Conservé pour les extensions historiques qui inspectent la palette.
window._cmdItems=cmdItems;
