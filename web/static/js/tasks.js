/* ============================================================
   TASK MANAGEMENT — Lumena Control Panel
   ============================================================ */
export function showNewTaskForm(){document.getElementById('new-task-form').style.display='block'}

export async function createTask(){
  const desc=document.getElementById('task-description').value.trim();
  if(!desc)return;
  const convId='web_'+Date.now();
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/tasks/start`,{method:'POST',headers:h,body:JSON.stringify({conversation_id:convId,channel:'web',message_preview:desc,metadata:{source:'control_panel'}})});
    if(!r.ok){const err=await r.json();logC(`❌ Task: ${err.detail||'erreur'}`,'error');return}
    const d=await r.json();
    const task=d.task||d;
    const tid=task.task_id||task.id||convId;
    logC(`✅ Tache creee: ${tid}`,'success');
    document.getElementById('task-description').value='';
    document.getElementById('new-task-form').style.display='none';
    if(tid){activeTasks.set(tid,{task_id:tid,description:desc,status:'pending',created_at:new Date().toISOString(),...task});startTaskPoll(tid)}
    renderTasks();
  }catch(e){logC(`❌ ${e.message}`,'error')}
}

export function startTaskPoll(taskId){
  if(taskPollTimers.has(taskId))return;
  const timer=setInterval(async()=>{
    try{
      const _tph={};if(ADMIN_TOKEN)_tph['Authorization']=`Bearer ${ADMIN_TOKEN}`;
      const r=await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(taskId)}`,{headers:_tph});
      if(!r.ok){clearInterval(timer);taskPollTimers.delete(taskId);return}
      const d=await r.json();
      const task=d.task||d;
      activeTasks.set(taskId,task);
      renderTasks();
      const st=task.state||task.status||'';
      if(st==='done'||st==='failed'||st==='cancelled'){
        clearInterval(timer);taskPollTimers.delete(taskId);
        logC(`📋 Tache ${taskId.substring(0,8)}: ${st}`,st==='done'?'success':'error');
        if(st==='done'&&task.result_summary)addMsg('assistant',`📋 **Tache terminee**\n${task.result_summary}`);
      }
    }catch(e){clearInterval(timer);taskPollTimers.delete(taskId)}
  },1500);
  taskPollTimers.set(taskId,timer);
}

export async function cancelTask(taskId){
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(taskId)}/cancel`,{method:'POST',headers:h});
    if(r.ok){logC(`✅ Tache annulee: ${taskId.substring(0,8)}`,'success');loadActiveTasks()}
  }catch(e){logC(`❌ ${e.message}`,'error')}
}

export async function loadActiveTasks(){
  try{
    const _ath={};if(ADMIN_TOKEN)_ath['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/tasks?limit=200`,{headers:_ath});
    if(!r.ok)return;
    const d=await r.json();
    const tasks=Array.isArray(d.tasks)?d.tasks:[];
    // Merge server tasks into activeTasks map (server is source of truth)
    activeTasks.clear();
    for(const t of tasks){
      const tid=t.task_id||t.id;
      if(tid)activeTasks.set(tid,t);
    }
    // Start polling for active tasks not yet polled
    for(const [tid,t] of activeTasks){
      const s=t.state||t.status||'';
      if((s==='running'||s==='queued'||s==='waiting_io'||s==='checkpointed')&&!taskPollTimers.has(tid)){
        startTaskPoll(tid);
      }
    }
  }catch(e){}
  renderTasks();
  loadDaemonActivity();
}

export function renderTasks(){
  const list=document.getElementById('tasks-list');if(!list)return;
  const tasks=Array.from(activeTasks.values()).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''));
  const badge=document.getElementById('badge-tasks');
  const activeCount=tasks.filter(t=>{
    if(t.type==='scheduler') return !t.cancelled_at;
    const s=t.state||t.status||'';return s==='running'||s==='queued'||s==='waiting_io'||s==='checkpointed';
  }).length;
  if(badge){badge.textContent=activeCount;badge.style.background=activeCount>0?'var(--ok)':'var(--muted)'}
  if(!tasks.length){list.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucune tache. Cliquez "+ Nouvelle tache" pour commencer.</div>';return}
  list.innerHTML=tasks.map(t=>{
    if(t.type==='scheduler'){
      const s=t.cancelled_at?'cancelled':t.run_count>0?'running':'queued';
      const sColor={running:'ok',queued:'warn',cancelled:'muted'};
      const sIcon={running:'🔄',queued:'⏱️',cancelled:'🚫'};
      const sLabel={running:'actif',queued:'en attente',cancelled:'annulé'};
      const lastRun=t.last_run?(new Date(t.last_run)).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'jamais';
      return`<div class="list-item">
        <div style="flex:1">
          <div class="list-item-title">${sIcon[s]||'?'} ${esc(t.name||t.task_id)}</div>
          <div class="list-item-sub">📅 ${esc(t.schedule||'—')}</div>
          <div class="list-item-sub">Exécutions: ${t.run_count||0} | Dernière: ${esc(lastRun)}</div>
          <div style="font-size:12px;color:var(--muted);margin-top:4px;padding:6px 8px;background:rgba(0,0,0,0.15);border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(t.action||'')}">${esc((t.action||'').substring(0,120))}${(t.action||'').length>120?'…':''}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="pill ${sColor[s]||''}">${sLabel[s]||s}</span>
        </div>
      </div>`;
    }
    const s=t.state||t.status||'queued';
    const statusColors={running:'ok',queued:'warn',waiting_io:'accent',checkpointed:'accent',done:'ok',failed:'danger',cancelled:'muted'};
    const statusIcons={running:'⏳',queued:'⏱️',waiting_io:'📡',checkpointed:'💾',done:'✅',failed:'❌',cancelled:'🚫'};
    return`<div class="list-item">
      <div style="flex:1">
        <div class="list-item-title">${statusIcons[s]||'?'} ${esc(t.message_preview||t.description||(t.task_id||'').substring(0,12))}</div>
        <div class="list-item-sub">ID: ${esc((t.task_id||'').substring(0,12))} | Conv: ${esc((t.conversation_id||'').substring(0,16))} | ${esc(t.channel||'web')}</div>
        <div class="list-item-sub">Cree: ${esc(t.created_at||'')} | MaJ: ${esc(t.updated_at||'')}</div>
        ${t.result_summary?`<div style="font-size:12px;color:var(--text);margin-top:6px;padding:8px;background:rgba(0,0,0,0.2);border-radius:8px">${esc((t.result_summary||'').substring(0,300))}</div>`:''}
        ${t.last_error?`<div style="font-size:12px;color:var(--danger);margin-top:6px">${esc(t.last_error)}</div>`:''}
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="pill ${statusColors[s]||''}">${s}</span>
        ${s==='running'||s==='queued'||s==='waiting_io'?`<button class="btn danger" style="font-size:11px;padding:4px 10px" onclick="cancelTask('${esc(t.task_id)}')">Annuler</button>`:''}
      </div>
    </div>`;
  }).join('');
}

let _daemonData=null;
export async function loadDaemonActivity(){
  const list=document.getElementById('daemon-list');
  if(list)list.innerHTML=loadingDots('Chargement...');
  try{
    const _dah={};if(ADMIN_TOKEN)_dah['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/daemon/activity`,{headers:_dah});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    _daemonData=await r.json();
  }catch(e){
    if(list)list.innerHTML=`<div style="color:var(--muted);padding:12px;font-size:13px">Daemon injoignable ou inactif.</div>`;
    return;
  }
  renderDaemon();
}

export function renderDaemon(){
  const list=document.getElementById('daemon-list');if(!list)return;
  if(!_daemonData){list.innerHTML='';return;}
  const handlers=_daemonData.handlers||[];
  const ops=_daemonData.ops||{};
  const incidents=(ops.incidents_today||[]).slice(-5).reverse();
  const counters=ops.daily_counters||{};
  const friendlyNames={
    runtime_health:'Sante systeme',
    micro_eval_light:'Evaluation legere',
    micro_eval_full:'Evaluation complete',
    provider_probe:'Sonde LLM',
    backup_rollback_test:'Test backup/rollback',
    daily_report:'Rapport quotidien',
    daily_github_project:'GitHub project quotidien',
    data_ingest_delta:'Ingestion donnees',
    judge_pipeline:'Pipeline jugement (DPO)',
    learning_curation:'Curation apprentissage',
    memory_hygiene:'Nettoyage memoire',
    rejection_sampling_light:'Echantillonnage rejet',
    retrain_readiness:'Preparation re-entrainement',
    workspace_archive:'Archive workspace',
  };
  let html='';
  // Incidents
  if(incidents.length){
    html+=`<div class="list-item" style="background:rgba(255,80,80,0.07);border-left:3px solid var(--danger)">
      <div style="flex:1">
        <div class="list-item-title">🚨 Incidents aujourd'hui (${incidents.length})</div>
        ${incidents.map(inc=>`<div class="list-item-sub" style="margin-top:4px">${esc((inc.time||'').substring(11,16))} — <span style="color:${inc.status==='critical'?'var(--danger)':'var(--warn)'}">${esc(inc.status)}</span>: ${esc((inc.alerts||[]).join(' | ').substring(0,120))}</div>`).join('')}
      </div>
    </div>`;
  }
  // Compteurs journaliers
  const cEntries=Object.entries(counters).filter(([,v])=>v>0);
  if(cEntries.length){
    html+=`<div class="list-item">
      <div style="flex:1">
        <div class="list-item-title">📊 Compteurs aujourd'hui</div>
        <div class="list-item-sub">${cEntries.map(([k,v])=>`${esc(k)}: <b>${v}</b>`).join(' | ')}</div>
      </div>
    </div>`;
  }
  // Handlers
  if(!handlers.length){html+='<div style="color:var(--muted);padding:12px;font-size:13px">Aucune activite daemon enregistree.</div>';}
  else{
    html+=handlers.map(h=>{
      const ok=h.success!==false;
      const ts=h.timestamp?(new Date(h.timestamp)).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
      const alerts=h.alerts||[];
      const name=friendlyNames[h.handler]||h.handler;
      let extra='';
      if(typeof h.score_percent==='number') extra+=` | Score: <b>${h.score_percent.toFixed(0)}%</b>`;
      if(typeof h.uptime_hours==='number') extra+=` | Uptime: <b>${h.uptime_hours.toFixed(0)}h</b>`;
      if(typeof h.scheduler_pending==='number') extra+=` | En attente: <b>${h.scheduler_pending}</b>`;
      if(typeof h.scheduler_overdue==='number'&&h.scheduler_overdue>0) extra+=` | <span style="color:var(--warn)">En retard: ${h.scheduler_overdue}</span>`;
      return`<div class="list-item">
        <div style="flex:1">
          <div class="list-item-title">${ok?'✅':'❌'} ${esc(name)}</div>
          <div class="list-item-sub">Derniere exec: ${esc(ts)}${extra}</div>
          ${alerts.length?`<div class="list-item-sub" style="color:var(--warn);margin-top:3px">⚠ ${esc(alerts.join(' | ').substring(0,200))}</div>`:''}
          ${h.summary&&!alerts.length?`<div class="list-item-sub" style="margin-top:3px">${esc(h.summary.substring(0,150))}</div>`:''}
        </div>
        <span class="pill ${ok?'ok':'danger'}">${ok?'ok':'erreur'}</span>
      </div>`;
    }).join('');
  }
  list.innerHTML=html;
}

/* ============================================================
   SCHEDULED TASKS & OVERVIEW TRACE
   ============================================================ */
const SCHEDULED_TASKS=[
  {name:'Runtime Health',interval:'45min',type:'ops'},
  {name:'Provider Probe',interval:'15min',type:'ops'},
  {name:'Data Ingest Delta',interval:'30min',type:'ops'},
  {name:'Memory Hygiene',interval:'30min',type:'ops'},
  {name:'Micro Eval Light',interval:'1h',type:'ops'},
  {name:'Save State',interval:'15min',type:'ops'},
  {name:'Curiosity Update',interval:'5min',type:'routine'},
  {name:'Daily Report',interval:'CRON 23:55',type:'cron'},
  {name:'Micro Eval Full',interval:'CRON 01:00',type:'cron'},
  {name:'Judge Pipeline',interval:'CRON 02:00',type:'cron'},
  {name:'Rejection Sampling',interval:'CRON 03:00',type:'cron'},
  {name:'Retrain Readiness',interval:'CRON 04:00',type:'cron'},
  {name:'Daily GitHub Project',interval:'CRON 12:00',type:'cron'},
  {name:'Workspace Archive',interval:'CRON 04:00',type:'cron'},
  {name:'Daily Code Analysis',interval:'6h',type:'routine'},
  {name:'Daily Skill Autonomy',interval:'24h',type:'routine'},
  {name:'Weekly Auto-Improve',interval:'dim 03:00',type:'routine'},
];

export function renderScheduledTasks(){
  const el=document.getElementById('ov-scheduler');if(!el)return;
  const typeColors={ops:'accent',cron:'warn',routine:'ok'};
  const typeIcons={ops:'⚙️',cron:'⏰',routine:'🔄'};
  el.innerHTML=SCHEDULED_TASKS.map(t=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
      <span style="font-size:12px">${typeIcons[t.type]||''} ${esc(t.name)}</span>
      <div style="display:flex;gap:6px;align-items:center">
        <span style="font-size:11px;color:var(--muted)">${esc(t.interval)}</span>
        <span class="pill ${typeColors[t.type]||''}" style="font-size:10px">${t.type}</span>
      </div>
    </div>
  `).join('');
}

let _overviewTraceEvents=[];

export function pushOverviewTraceEvent(ev){
  _overviewTraceEvents.unshift(ev);
  if(_overviewTraceEvents.length>30)_overviewTraceEvents.length=30;
  renderOverviewTraceFeed();
}

export function renderOverviewTraceFeed(){
  const el=document.getElementById('ov-trace-feed');if(!el)return;
  const ct=document.getElementById('ov-trace-count');
  if(ct)ct.textContent=traceStats.total;
  if(!_overviewTraceEvents.length){el.innerHTML='<div style="color:var(--muted)">En attente d\'evenements...</div>';return}
  el.innerHTML=_overviewTraceEvents.map(ev=>{
    const isErr=ev.status==='error'||ev.error;
    const hasTool=!!ev.tool_name;
    const color=isErr?'var(--danger)':hasTool?'var(--ok)':'var(--text)';
    return`<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border);font-size:11px">
      <span style="color:${color}">${esc(ev.stage||'?')} ${hasTool?'🔧 '+esc(ev.tool_name):''} ${isErr?'❌':''}</span>
      <span style="color:var(--muted)">${ev.duration_ms?Math.round(ev.duration_ms)+'ms':''} ${esc((ev.ts||'').substring(11,19))}</span>
    </div>`;
  }).join('');
}

/* ============================================================
   TASK PROGRESS (plan d'execution ReAct — affiche au-dessus du chat)
   ============================================================ */
let _taskProgressTimer=null;

export function renderTaskProgress(todos){
  const wrap=document.getElementById('task-progress-wrap');
  const list=document.getElementById('task-progress-list');
  const fill=document.getElementById('task-progress-fill');
  const count=document.getElementById('task-progress-count');
  if(!wrap||!list||!todos||!todos.length)return;
  const done=todos.filter(t=>t.status==='completed').length;
  const total=todos.length;
  if(fill)fill.style.width=Math.round((done/total)*100)+'%';
  if(count)count.textContent=`${done}/${total}`;
  list.innerHTML=todos.map(t=>{
    const icon=t.status==='completed'?'✅':t.status==='in-progress'?'⏳':'○';
    const toolBadge=(t.status==='in-progress'&&t.current_tool&&t.current_tool!=='')
      ?` <span style="font-size:10px;color:var(--accent);font-family:var(--mono);opacity:.8">[${esc(t.current_tool)}]</span>`
      :'';
    return`<div class="task-item ${t.status||'not-started'}">
      <span class="task-item-icon">${icon}</span>
      <span class="task-item-text">${esc(t.title||t.description||'')}${toolBadge}</span>
    </div>`;
  }).join('');
  wrap.style.display='block';
  if(_taskProgressTimer){clearTimeout(_taskProgressTimer);_taskProgressTimer=null}
}

export function resetTaskProgress(){
  const wrap=document.getElementById('task-progress-wrap');
  const list=document.getElementById('task-progress-list');
  const fill=document.getElementById('task-progress-fill');
  if(list)list.innerHTML='';
  if(fill)fill.style.width='0%';
  if(wrap)wrap.style.display='none';
  if(_taskProgressTimer){clearTimeout(_taskProgressTimer);_taskProgressTimer=null}
}

export function hideTaskProgressDelayed(){
  if(_taskProgressTimer)return;
  _taskProgressTimer=setTimeout(()=>{
    const wrap=document.getElementById('task-progress-wrap');
    if(wrap)wrap.style.display='none';
    _taskProgressTimer=null;
  },4000);
}

/* ============================================================
   TODOS
   ============================================================ */
export function loadTodos(){renderTodoList()}

export function getTodosFromStorage(){
  try{return JSON.parse(localStorage.getItem('lumena_todos')||'[]')}catch(e){return[]}
}

export function saveTodosToStorage(todos){
  localStorage.setItem('lumena_todos',JSON.stringify(todos));
  updateTodoBadge(todos);
}

export function updateTodoBadge(todos){
  if(!todos)todos=getTodosFromStorage();
  const badge=document.getElementById('badge-todos');
  const pending=todos.filter(t=>!t.done).length;
  if(badge){badge.textContent=pending;badge.style.background=pending>0?'var(--warn)':'var(--muted)'}
}

export function addTodo(){
  const input=document.getElementById('todo-input');
  const text=(input.value||'').trim();if(!text)return;
  const todos=getTodosFromStorage();
  todos.unshift({id:Date.now(),text,done:false,created:new Date().toISOString()});
  saveTodosToStorage(todos);
  input.value='';
  renderTodoList();
}

export function toggleTodo(id){
  const todos=getTodosFromStorage();
  const t=todos.find(t=>t.id===id);
  if(t)t.done=!t.done;
  saveTodosToStorage(todos);
  renderTodoList();
}

export function deleteTodo(id){
  const todos=getTodosFromStorage().filter(t=>t.id!==id);
  saveTodosToStorage(todos);
  renderTodoList();
}

export function renderTodoList(){
  const el=document.getElementById('todo-list');
  const todos=getTodosFromStorage();
  updateTodoBadge(todos);
  const stats=document.getElementById('todo-stats');
  const done=todos.filter(t=>t.done).length;
  if(stats)stats.textContent=`${done}/${todos.length} terminees`;
  if(!el)return;
  if(!todos.length){el.innerHTML='<div style="color:var(--muted);padding:20px;text-align:center">Aucune tache. Ajoutez-en une ci-dessus.</div>';return}
  el.innerHTML=todos.map(t=>`
    <div class="list-item" style="opacity:${t.done?'0.5':'1'}">
      <div style="display:flex;align-items:center;gap:12px;flex:1;cursor:pointer" onclick="toggleTodo(${t.id})">
        <span style="font-size:18px">${t.done?'✅':'⬜'}</span>
        <div>
          <div class="list-item-title" style="${t.done?'text-decoration:line-through;color:var(--muted)':''}">${esc(t.text)}</div>
          <div class="list-item-sub">${esc(t.created.substring(0,16).replace('T',' '))}</div>
        </div>
      </div>
      <button class="btn danger" style="font-size:11px;padding:4px 8px" onclick="deleteTodo(${t.id})">🗑️</button>
    </div>
  `).join('');
}
