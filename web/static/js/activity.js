/* ============================================================
   ACTIVITY FEED — Lumena Control Panel
   ============================================================ */
export function openSidebar(content){
  const feed=document.getElementById('activity-feed');
  if(feed&&!content){document.getElementById('chat-sidebar').classList.add('open');return}
  if(feed)feed.innerHTML='';
  const sc=document.getElementById('sidebar-content');
  if(sc&&content)sc.textContent=content;
  document.getElementById('chat-sidebar').classList.add('open');
}
export function closeSidebar(){document.getElementById('chat-sidebar').classList.remove('open')}
export function toggleSidebar(){
  const sb=document.getElementById('chat-sidebar');
  if(sb)sb.classList.toggle('open');
}

export function startActivityFeed(){
  activityCounts={tools:0,thoughts:0,edits:0,obs:0,actions:0,errors:0};
  activityStartTime=Date.now();
  const feed=document.getElementById('activity-feed');
  if(feed)feed.innerHTML='';
  const stats=document.getElementById('activity-stats');
  if(stats)stats.style.display='';
  // Don't auto-open — show the toggle tab instead
  const tab=document.getElementById('activity-toggle-tab');
  if(tab)tab.style.display='';
  const dot=document.getElementById('activity-dot');
  if(dot){dot.classList.add('ok');dot.title='En cours...'}
  ['act-tools','act-thoughts','act-edits','act-obs','act-elapsed'].forEach(id=>setText(id,'0'));
  if(activityElapsedTimer)clearInterval(activityElapsedTimer);
  activityElapsedTimer=setInterval(updateActivityStats,1000);
}

export function pushActivity(type,_icon,text){
  const feed=document.getElementById('activity-feed');
  if(!feed)return;
  const item=document.createElement('div');
  item.className='activity-item '+type;
  const now=new Date();
  const ts=String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0')+':'+String(now.getSeconds()).padStart(2,'0');
  const safeText=(text||'').length>400?(text.substring(0,400)+'…'):text;
  const _typeIcon={checkpoint:'check-circle-2',tool:'wrench',observation:'eye',action:'bot',error:'alert-circle',warning:'alert-triangle','file-edit':'pencil'};
  const iconName=_typeIcon[type]||'circle';
  item.innerHTML=`<div class="activity-icon"><i data-lucide="${iconName}" style="width:14px;height:14px"></i></div><div class="activity-body"><div class="activity-label">${esc(type.toUpperCase())}</div><div class="activity-text">${safeText}</div></div><div class="activity-time">${ts}</div>`;
  feed.appendChild(item);
  if(window.lucide)window.lucide.createIcons({nodes:[feed.lastElementChild.querySelector('.activity-icon')]});
  while(feed.children.length>300)feed.removeChild(feed.firstChild);
  feed.scrollTop=feed.scrollHeight;
}

export function updateActivityStats(){
  setText('act-tools',String(activityCounts.tools));
  setText('act-thoughts',String(activityCounts.thoughts));
  setText('act-edits',String(activityCounts.edits));
  setText('act-obs',String(activityCounts.obs));
  if(activityStartTime){
    const elapsed=Math.round((Date.now()-activityStartTime)/1000);
    const m=Math.floor(elapsed/60);const s=elapsed%60;
    setText('act-elapsed',(m>0?m+'m ':'')+s+'s');
  }
}

export function stopActivityFeed(){
  if(activityElapsedTimer){clearInterval(activityElapsedTimer);activityElapsedTimer=null}
  updateActivityStats();
  const dot=document.getElementById('activity-dot');
  if(dot){dot.classList.remove('ok');dot.title='Termine'}
  // Hide the toggle tab when done (sidebar can still be opened via X close)
  const tab=document.getElementById('activity-toggle-tab');
  if(tab&&!document.getElementById('chat-sidebar').classList.contains('open'))tab.style.display='none';
}
