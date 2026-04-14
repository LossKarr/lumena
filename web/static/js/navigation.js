/* ============================================================
   NAVIGATION — Lumena Control Panel
   ============================================================ */
export function setupNavigation(){
  document.querySelectorAll('.nav-item').forEach(item=>{
    item.addEventListener('click',()=>switchPanel(item.dataset.panel));
  });
}

export function switchPanel(panelName){
  document.querySelectorAll('.nav-item').forEach(i=>i.classList.toggle('active',i.dataset.panel===panelName));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id===`panel-${panelName}`));
  document.getElementById('topbar-title').textContent=panelName.charAt(0).toUpperCase()+panelName.slice(1).replace(/-/g,' ');
  loadPanelData(panelName);
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
    case'sessions':loadSessions();break;
    case'overview':loadOverview();break;
    case'infra-telegram':loadTelegramDetails();break;
    case'infra-whatsapp':loadWhatsAppDetails();break;
    case'infra-autonomy':loadAutonomyDetails();break;
    case'todos':loadTodos();break;
    case'journal':loadJournal();break;
    case'facts':loadFacts();break;
    case'providers':loadProviders();break;
    case'alerts':loadAlerts();break;
    case'training':loadTraining();break;
    case'finetuning':loadFinetuning();break;
    case'logs':loadLogsRecent();break;
    case'config':loadConfig();break;
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
const cmdItems=[
  {icon:'message-circle',label:'Chat',action:()=>switchPanel('chat')},
  {icon:'layout-dashboard',label:'Overview',action:()=>switchPanel('overview')},
  {icon:'map',label:'Repo Map',action:()=>switchPanel('repomap')},
  {icon:'search',label:'Code Search',action:()=>switchPanel('search')},
  {icon:'brain',label:'Memoire',action:()=>switchPanel('memory')},
  {icon:'wrench',label:'Outils',action:()=>switchPanel('tools')},
  {icon:'scroll-text',label:'Regles',action:()=>switchPanel('rules')},
  {icon:'zap',label:'Instincts',action:()=>switchPanel('instincts')},
  {icon:'heart-pulse',label:'Emotions',action:()=>switchPanel('emotions')},
  {icon:'mic',label:'Voix',action:()=>switchPanel('voice')},
  {icon:'link',label:'Hooks',action:()=>switchPanel('hooks')},
  {icon:'radio',label:'Live Trace',action:()=>switchPanel('trace')},
  {icon:'monitor',label:'Console',action:()=>switchPanel('console')},
  {icon:'clipboard-list',label:'Taches',action:()=>switchPanel('tasks')},
  {icon:'layers',label:'Sessions',action:()=>switchPanel('sessions')},
  {icon:'send',label:'Telegram',action:()=>switchPanel('infra-telegram')},
  {icon:'message-circle',label:'WhatsApp',action:()=>switchPanel('infra-whatsapp')},
  {icon:'bot',label:'Autonomie',action:()=>switchPanel('infra-autonomy')},
  {icon:'book-open-check',label:'Documentation',action:()=>switchPanel('product-docs')},
  {icon:'cpu',label:'Fine-tuning',action:()=>switchPanel('finetuning')},
  {icon:'maximize',label:'Mode Focus',action:()=>toggleFocus()},
  {icon:'plug',label:'Toggle Agent',action:()=>toggleAgent()},
  {icon:'moon',label:'Toggle Theme',action:()=>toggleTheme()},
];
window._cmdItems=cmdItems;

export function openCommandPalette(){
  document.getElementById('cmd-palette-overlay').classList.add('open');
  const input=document.getElementById('cmd-input');input.value='';input.focus();
  filterCommands();
}

export function closeCommandPalette(){document.getElementById('cmd-palette-overlay').classList.remove('open')}

export function filterCommands(){
  const q=document.getElementById('cmd-input').value.toLowerCase();
  const filtered=q?cmdItems.filter(c=>c.label.toLowerCase().includes(q)):cmdItems;
  document.getElementById('cmd-results').innerHTML=filtered.map((c,i)=>`
    <div class="cmd-palette-item" onclick="window._cmdItems[${cmdItems.indexOf(c)}].action();closeCommandPalette()">
      <span class="icon"><i data-lucide="${c.icon}"></i></span><span>${esc(c.label)}</span>
    </div>`).join('');
}
