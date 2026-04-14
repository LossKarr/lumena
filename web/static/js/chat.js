/* ============================================================
   CHAT — Lumena Control Panel
   ============================================================ */
export function setupTextarea(){
  const ta=document.getElementById('message-input');
  ta.addEventListener('input',()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,400)+'px'});
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
}

export function quickSend(msg){document.getElementById('message-input').value=msg;sendMessage()}

export async function sendMessage(){
  if(isLoading)return;
  const input=document.getElementById('message-input');
  const message=input.value.trim();if(!message&&!_pendingAttachments.length){input.classList.add('shake');setTimeout(()=>input.classList.remove('shake'),400);return}

  // Hide welcome
  const welcome=document.getElementById('chat-welcome');
  if(welcome)welcome.style.display='none';
  chatHasMessages=true;

  _lastSentMessage=message;

  // Handle attachments
  let attachmentInfos=[];
  let uploadedPaths=[];
  if(_pendingAttachments.length){
    for(const att of _pendingAttachments){
      attachmentInfos.push({name:att.file.name,size:att.file.size,type:att.file.type});
    }
    // Upload files first
    try{
      const formData=new FormData();
      for(const att of _pendingAttachments)formData.append('files',att.file);
      const upHeaders={};
      if(ADMIN_TOKEN)upHeaders['Authorization']=`Bearer ${ADMIN_TOKEN}`;
      const upRes=await fetch(`${API_BASE}/api/upload`,{method:'POST',headers:upHeaders,body:formData});
      if(upRes.ok){const upData=await upRes.json();uploadedPaths=upData.files||[];}
    }catch(e){logC('Upload error: '+e.message,'error')}
    clearAttachments();
  }

  // Show user message with attachments
  const userExtra=attachmentInfos.length?'<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px">'+attachmentInfos.map(a=>{
    const isImg=a.type&&a.type.startsWith('image/');
    return`<span style="font-size:11px;padding:3px 8px;background:rgba(0,0,0,0.2);border:1px solid var(--border);border-radius:var(--radius-sm)">${isImg?'🖼️':'📄'} ${esc(a.name)}</span>`;
  }).join('')+'</div>':'';
  addMsg('user',message,null,userExtra);
  _pushChat('user',message);
  logC(`📤 "${message.substring(0,50)}..."`,`info`);
  input.value='';input.style.height='auto';
  isLoading=true;

  // Open activity sidebar & reset
  startActivityFeed();

  const thread=document.getElementById('chat-thread');
  const thinking=document.createElement('div');
  thinking.className='thinking-live';
  let _thinkingCollapsed=false;
  let _thinkingStepCount=0;
  let _terminalBlock=null,_terminalPre=null,_terminalLineCount=0;
  thinking.innerHTML=`<div class="thinking-header" onclick="this.parentElement.querySelector('.thinking-body').classList.toggle('collapsed');this.querySelector('.thinking-header-chevron').classList.toggle('open')"><div class="reading-dots"><span></span><span></span><span></span></div><span class="thinking-header-label">Working</span><span class="thinking-header-chevron open">▶</span><span class="thinking-header-elapsed">0s</span></div><div class="thinking-body"></div><div class="thinking-api-status" style="display:none"></div>`;
  thread.appendChild(thinking);requestAnimationFrame(()=>thinking.scrollIntoView({block:'end',behavior:'instant'}));

  function addThinkingStep(icon,text,cls=''){
    _thinkingStepCount++;
    const body=thinking.querySelector('.thinking-body');
    if(!body)return;
    // Mark previous active steps as done
    body.querySelectorAll('.thinking-step.active').forEach(s=>{s.classList.remove('active');s.classList.add('done');const ic=s.querySelector('.thinking-step-icon');if(ic&&!ic.dataset.locked)ic.textContent='✓';});
    const step=document.createElement('div');
    step.className='thinking-step active '+cls;
    step.innerHTML=`<span class="thinking-step-icon">${icon}</span><span class="thinking-step-text">${text}</span>`;
    body.appendChild(step);
    // Keep max 50 steps visible
    while(body.children.length>50)body.removeChild(body.firstChild);
    body.scrollTop=body.scrollHeight;
    thinking.scrollIntoView({block:'end',behavior:'instant'});
  }

  function openFileBlock(filePath,linesInfo,content){
    const body=thinking.querySelector('.thinking-body');
    if(!body)return;
    body.querySelectorAll('.thinking-step.active').forEach(s=>{s.classList.remove('active');s.classList.add('done');const ic=s.querySelector('.thinking-step-icon');if(ic&&!ic.dataset.locked)ic.textContent='✓';});
    const step=document.createElement('div');
    step.className='thinking-step done thinking-file-viewer';
    // Header
    const hdr=document.createElement('div');
    hdr.className='file-block-header';
    const fname=filePath.split('/').pop()||filePath.split('\\').pop()||filePath;
    const ext=(fname.match(/\.([^.]+)$/)||[])[1]||'';
    const langIcon={'html':'🌐','css':'🎨','js':'⚡','ts':'💠','py':'🐍','json':'📋','md':'📝','yaml':'📄','yml':'📄'}[ext]||'📄';
    hdr.innerHTML=`<span class="file-icon">${langIcon}</span><span class="file-name">${esc(fname)}</span><span class="file-path">${esc(filePath)}</span>${linesInfo?`<span class="file-lines">${esc(linesInfo)}</span>`:''}`;
    hdr.style.cursor='pointer';
    // Collapsible content
    const pre=document.createElement('pre');
    pre.className='file-content-pre';
    // Truncate for display
    const lines=(content||'').split('\n');
    const maxLines=30;
    let displayText=lines.slice(0,maxLines).join('\n');
    if(lines.length>maxLines) displayText+=`\n\n… ${lines.length-maxLines} lignes supplémentaires`;
    pre.textContent=displayText;
    pre.style.display='none';
    hdr.addEventListener('click',()=>{pre.style.display=pre.style.display==='none'?'block':'none';hdr.querySelector('.file-chevron').classList.toggle('open');});
    hdr.innerHTML+=`<span class="file-chevron">▶</span>`;
    step.appendChild(hdr);
    step.appendChild(pre);
    body.appendChild(step);
    while(body.children.length>50)body.removeChild(body.firstChild);
    thinking.scrollIntoView({block:'end',behavior:'instant'});
    _thinkingStepCount++;
  }

  function openTerminalBlock(cmd){
    _terminalLineCount=0;
    const body=thinking.querySelector('.thinking-body');
    if(!body)return;
    body.querySelectorAll('.thinking-step.active').forEach(s=>{s.classList.remove('active');s.classList.add('done');const ic=s.querySelector('.thinking-step-icon');if(ic&&!ic.dataset.locked)ic.textContent='✓';});
    const step=document.createElement('div');
    step.className='thinking-step active thinking-terminal';
    _terminalBlock=step;
    const hdr=document.createElement('div');
    hdr.className='term-block-header';
    hdr.innerHTML=`<span class="term-dots"><span style="background:#ff5f57"></span><span style="background:#febc2e"></span><span style="background:#28c840"></span></span><span class="term-label">Terminal</span><code class="term-block-cmd">❯ ${esc(cmd.substring(0,150))}</code>`;
    const pre=document.createElement('pre');
    pre.className='term-output-pre';
    _terminalPre=pre;
    step.appendChild(hdr);
    step.appendChild(pre);
    body.appendChild(step);
    while(body.children.length>50)body.removeChild(body.firstChild);
    thinking.scrollIntoView({block:'end',behavior:'instant'});
    _thinkingStepCount++;
  }

  function appendTerminalLine(line,stream){
    if(!_terminalPre)return;
    _terminalLineCount++;
    if(_terminalLineCount>1000)return;
    const span=document.createElement('span');
    if(stream==='stderr')span.className='term-line-err';
    span.textContent=line+'\n';
    _terminalPre.appendChild(span);
    // Scroll automatique tant qu'on est proche du bas
    if(_terminalPre.scrollHeight-_terminalPre.scrollTop<_terminalPre.clientHeight+80)
      _terminalPre.scrollTop=_terminalPre.scrollHeight;
    thinking.scrollIntoView({block:'end',behavior:'instant'});
  }

  function closeTerminalBlock(info){
    if(_terminalBlock){
      _terminalBlock.classList.remove('active');
      _terminalBlock.classList.add('done');
      const isOk=info===''||info.includes('exit:0');
      const ftr=document.createElement('div');
      ftr.className='term-block-footer';
      ftr.innerHTML=`<span style="color:${isOk?'var(--ok)':'var(--danger)'}">${esc(info||'exit:0')}</span>`;
      _terminalBlock.appendChild(ftr);
    }
    _terminalBlock=null;_terminalPre=null;
    thinking.scrollIntoView({block:'end',behavior:'instant'});
  }

  _thinkingStart=Date.now();_lastEventTs=Date.now();
  if(_thinkingTimer)clearInterval(_thinkingTimer);
  _thinkingTimer=setInterval(()=>{
    const chrono=thinking.querySelector('.thinking-header-elapsed');
    const apiStatus=thinking.querySelector('.thinking-api-status');
    if(!chrono)return;
    const total=Math.round((Date.now()-_thinkingStart)/1000);
    const silence=Math.round((Date.now()-_lastEventTs)/1000);
    chrono.textContent=total+'s';
    if(apiStatus){
      if(silence>=120){apiStatus.style.display='block';apiStatus.style.color='#e74c3c';apiStatus.textContent='🔴 API sans réponse depuis '+silence+'s — probable timeout';}
      else if(silence>=45){apiStatus.style.display='block';apiStatus.style.color='#f39c12';apiStatus.textContent='⏳ En attente API depuis '+silence+'s...';}
      else if(silence>=15){apiStatus.style.display='block';apiStatus.style.color='var(--muted)';apiStatus.textContent='↗️ Appel LLM en cours ('+silence+'s)';}
      else{apiStatus.style.display='none';}
    }
  },1000);

  // Build request body
  const reqBody={message,use_agent:useAgent};
  if(uploadedPaths.length)reqBody.attachments=uploadedPaths;

  try{
    const h={'Content-Type':'application/json'};
    if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const response=await fetch(`${API_BASE}/api/chat/stream`,{method:'POST',headers:h,body:JSON.stringify(reqBody)});
    if(!response.ok){const _err=await response.json().catch(()=>({}));throw new Error(_err.detail||`HTTP ${response.status}`);}
    if(!response.body)throw new Error('SSE indisponible');

    const reader=response.body.getReader();
    const decoder=new TextDecoder();let buffer='';let finalResponse=null;
    pendingFileEdits=[];pendingEditSessionId=null;pendingUndoAvailable=false;

    while(true){
      const{done,value}=await reader.read();if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      const lines=buffer.split('\n\n');buffer=lines.pop()||'';
      for(const line of lines){
        if(!line.startsWith('data: '))continue;
        try{
          const data=JSON.parse(line.slice(6));
          if(data.type!=='heartbeat')_lastEventTs=Date.now();
          if(data.type==='start'||data.type==='thinking'){
            addThinkingStep('⚡',esc(data.content||'Demarrage...'));
            pushActivity('checkpoint','⚡',data.content||'Demarrage...');
            if(data.type==='start')resetTaskProgress();
          }
          else if(data.type==='thought'){
            activityCounts.thoughts++;
            addThinkingStep('💭','<em>'+esc(data.content)+'</em>');
            pushActivity('thought','💭',data.content);
          }
          else if(data.type==='action'){
            activityCounts.actions++;
            const mFull=data.content.match(/^(.+?)\((.*)\)$/s);
            const mName=data.content.match(/^(.+?)\(/);
            const fnName=mFull?mFull[1]:(mName?mName[1]:data.content.substring(0,30));
            const fnArg=mFull?mFull[2]:'';
            const argHtml=fnArg?`<span style="color:var(--accent-light,#7ec8e3)">${esc(fnArg.substring(0,80))}</span>`:'<span style="color:var(--muted)">…</span>';
            addThinkingStep('▶',`${esc(fnName)}(${argHtml})`);
            pushActivity('action','▶',data.content);
            logC(`▶ Action: ${data.content.substring(0,80)}`,'tool');
          }
          else if(data.type==='tool'){
            activityCounts.tools++;
            const m=data.content.match(/Outil[:\s]*(\S+)/);
            addThinkingStep('🔧',esc(m?m[1]:data.content.substring(0,60)));
            pushActivity('tool','🔧',data.content);
            logC(data.content,'tool');
          }
          else if(data.type==='file_read'){
            activityCounts.obs++;
            openFileBlock(data.path||'',data.lines||'',data.content||'');
            pushActivity('observation','📄',`Lecture: ${data.path||'?'} (${data.lines||'?'})`);
            logC(`📄 Lecture: ${(data.path||'').substring(0,80)}`,'tool');
          }
          else if(data.type==='observation'){
            activityCounts.obs++;
            addThinkingStep('📖','Observation: <span style="color:var(--muted)">'+esc((data.content||'').substring(0,120))+'</span>');
            pushActivity('observation','📖',(data.content||'').substring(0,300));
            logC('📖 Observation: '+(data.content||'').substring(0,80),'tool');
          }
          else if(data.type==='terminal_open'){
            openTerminalBlock(data.content||'');
            pushActivity('tool','🖥️',data.content||'');
            logC('🖥️ Terminal: '+(data.content||'').substring(0,80),'tool');
          }
          else if(data.type==='terminal_output'){
            appendTerminalLine(data.content||'',data.stream||'stdout');
          }
          else if(data.type==='terminal_close'){
            closeTerminalBlock(data.content||'');
            logC('🖥️ Terminal terminé: '+(data.content||''),'tool');
          }
          else if(data.type==='agent_step'){
            // CodeAgent iteration detail — show action + path
            const detail=data.content||'';
            window._lastAgentStepDetail=detail;
            addThinkingStep('🤖',esc(detail));
            pushActivity('action','🤖',detail);
            logC(`🤖 CodeAgent: ${detail}`,'tool');
          }
          else if(data.type==='checkpoint'){
            const cp=data.checkpoint||data;
            // Skip action_detail if it was already shown by a recent agent_step event
            if(cp.action_detail && cp.action_detail===window._lastAgentStepDetail){
              logC(`⏳ Checkpoint (skip dup): ${cp.phase||'?'}`,'info');
            } else {
              let cpText=`Phase: ${esc(cp.phase||'processing')}`;
              if(cp.action_detail)cpText=`🤖 ${esc(cp.action_detail)}`;
              else if(cp.thoughts)cpText+=` — ${cp.thoughts} pensees`;
              if(cp.file_edits)cpText+=` — ${cp.file_edits} edits`;
              if(cp.retry_count)cpText+=` — retry #${cp.retry_count}`;
              addThinkingStep('⏳',cpText);
              pushActivity('checkpoint','⏳',cpText);
              logC(`⏳ Checkpoint: ${cp.phase||'?'} ${cp.action_detail||''}`,'info');
            }
          }
          else if(data.type==='todo_update'){renderTaskProgress(data.todos||[])}
          else if(data.type==='done'){
            // Update streaming preview with the properly rendered full response
            // so the user sees correct markdown even if tokens arrived without newlines
            if(window._streamingMsgEl && data.response){
              window._streamingRaw=data.response;
              window._streamingMsgEl.innerHTML=_renderMarkdown(esc(data.response)).replace(/\n/g,'<br>');
            }
            // Store final response — streaming-msg removed just before addMsg to avoid flash
            finalResponse=data;hideTaskProgressDelayed();
          }
          else if(data.type==='file_edit'){
            activityCounts.edits++;
            if(data.edit){
              pendingFileEdits=mergeEdits(pendingFileEdits,[data.edit]);
              if(data.edit.session_id)pendingEditSessionId=data.edit.session_id;
              pendingUndoAvailable=true;
              const fp=data.edit.workspace_relative||data.edit.file_path||'';
              addThinkingStep('📝',`${esc(data.edit.action||'updated')} <code>${esc(fp)}</code>`);
              pushActivity('file-edit','📝',`${data.edit.action||'updated'} <code>${esc(fp)}</code>`);
              logC(`📝 ${data.edit.action||'updated'} ${fp}`,'tool');
            }
          }
          else if(data.type==='error'){
            activityCounts.errors++;
            addThinkingStep('❌',esc(data.content),'error');
            pushActivity('error','❌',data.content);
            logC(`❌ ${data.content}`,'error');
          }
          else if(data.type==='llm_retry'){
            addThinkingStep('🔄',esc(data.content));
            const apiStatus=thinking.querySelector('.thinking-api-status');
            if(apiStatus){apiStatus.style.display='block';apiStatus.style.color='#f39c12';apiStatus.textContent='🔄 '+esc(data.content);}
            pushActivity('thought','🔄',data.content);
            logC(`🔄 LLM retry: ${data.content}`,'warning');
            _lastEventTs=Date.now();
          }
          else if(data.type==='token'){
            // Token streaming — build response word by word with typing effect
            if(!window._streamingMsgEl){
              const thread=document.getElementById('chat-thread');
              // Collapse thinking panel before showing response
              const thinkBody=thinking.querySelector('.thinking-body');
              if(thinkBody&&!thinkBody.classList.contains('collapsed')){
                thinkBody.classList.add('collapsed');
                const chev=thinking.querySelector('.thinking-header-chevron');
                if(chev)chev.classList.remove('open');
              }
              thread.insertAdjacentHTML('beforeend',`<div class="msg-group assistant" id="streaming-msg"><div class="msg-avatar">🌟</div><div class="msg-bubble streaming"><span class="streaming-text"></span><span class="streaming-cursor"></span></div></div>`);
              window._streamingMsgEl=thread.querySelector('#streaming-msg .streaming-text');
              window._streamingRaw='';
              thread.scrollTop=thread.scrollHeight;
            }
            window._streamingRaw+=(data.content||'');
            // Render markdown progressively
            window._streamingMsgEl.innerHTML=_renderMarkdown(esc(window._streamingRaw)).replace(/\n/g,'<br>');
            window._streamingMsgEl.closest('.msg-group').scrollIntoView({block:'end',behavior:'smooth'});
          }
          else if(data.type==='heartbeat'){/* keep alive */}
          updateActivityStats();
        }catch(e){}
      }
    }

    if(_thinkingTimer){clearInterval(_thinkingTimer);_thinkingTimer=null;}
    // Collapse thinking panel instead of removing it — keep as summary
    const thinkBody=thinking.querySelector('.thinking-body');
    if(thinkBody){
      thinkBody.querySelectorAll('.thinking-step.active').forEach(s=>{s.classList.remove('active');s.classList.add('done');const ic=s.querySelector('.thinking-step-icon');if(ic&&!ic.dataset.locked)ic.textContent='✓';});
      thinkBody.classList.add('collapsed');
    }
    const thinkChevron=thinking.querySelector('.thinking-header-chevron');
    if(thinkChevron)thinkChevron.classList.remove('open');
    const thinkDots=thinking.querySelector('.reading-dots');
    if(thinkDots)thinkDots.style.display='none';
    const thinkLabel=thinking.querySelector('.thinking-header-label');
    if(thinkLabel)thinkLabel.textContent=`Terminé — ${_thinkingStepCount} etapes`;
    const thinkApiSt=thinking.querySelector('.thinking-api-status');
    if(thinkApiSt)thinkApiSt.style.display='none';
    thinking.style.borderStyle='solid';thinking.style.opacity='0.7';
    stopActivityFeed();
    hideTaskProgressDelayed();

    if(finalResponse){
      finalResponse.file_edits=mergeEdits(finalResponse.file_edits||[],pendingFileEdits||[]);
      if(!finalResponse.edit_session_id&&pendingEditSessionId)finalResponse.edit_session_id=pendingEditSessionId;
      if(!finalResponse.undo_available&&pendingUndoAvailable)finalResponse.undo_available=true;
      // Remove streaming preview right before inserting final message — no visible gap
      const _streamEl=document.getElementById('streaming-msg');
      if(_streamEl)_streamEl.remove();
      window._streamingMsgEl=null;
      window._streamingRaw='';
      addMsg('assistant',finalResponse.response,finalResponse);
      _pushChat('assistant',finalResponse.response,finalResponse);
      pushActivity('checkpoint','✅',`Reponse terminee — ${finalResponse.provider_used||'?'}/${finalResponse.model_used||'?'}`);
      logC('✅ Reponse recue','success');
      if(finalResponse.mood)setText('mood-value',finalResponse.mood);
      logC(`🧠 ${finalResponse.provider_used||'?'} / ${finalResponse.model_used||'?'}`,'info');
      if(finalResponse.fallback_used){pushActivity('error','⚠️',`Fallback: ${finalResponse.fallback_reason||'?'}`);logC(`⚠️ Fallback: ${finalResponse.fallback_reason||'?'}`,'warning')}
      if(finalResponse.continuation_used){pushActivity('checkpoint','↩️',`Continuation x${finalResponse.continuation_steps||0}`);logC(`↩️ Continuation x${finalResponse.continuation_steps||0}`,'tool')}
      if((finalResponse.agent_repair_attempts||0)>0)pushActivity('tool','🔧',`Auto-repair x${finalResponse.agent_repair_attempts}`);
    }else{
      thread.insertAdjacentHTML('beforeend',`<div class="msg-group assistant"><div class="msg-avatar">🌟</div><div class="msg-bubble" style="display:flex;align-items:center;gap:12px"><span>❌ Pas de réponse reçue (timeout API).</span><button onclick="retryLastMessage()" style="flex-shrink:0;background:var(--accent);color:#fff;border:none;border-radius:6px;padding:5px 14px;cursor:pointer;font-size:13px">↺ Réessayer</button></div></div>`);
      thread.scrollTop=thread.scrollHeight;
      pushActivity('error','❌','Aucune reponse recue — cliquez Reessayer');
    }
    updateActivityStats();
  }catch(e){
    if(_thinkingTimer){clearInterval(_thinkingTimer);_thinkingTimer=null;}
    thinking.remove();stopActivityFeed();
    addMsg('assistant','❌ Erreur de connexion.');
    pushActivity('error','❌',e.message);
    logC(`❌ ${e.message}`,'error');
  }
  isLoading=false;
}

export function retryLastMessage(){
  if(isLoading||!_lastSentMessage)return;
  const input=document.getElementById('message-input');
  input.value=_lastSentMessage;
  sendMessage();
}

/* ============================================================
   MESSAGE RENDERING
   ============================================================ */
export function addMsg(role,content,meta=null,extraHtml=''){
  const thread=document.getElementById('chat-thread');
  const avatar=role==='assistant'?'🌟':'👤';
  const metaHtml=role==='assistant'?buildMetaHtml(meta):'';
  const fileEditsHtml=role==='assistant'?buildDiffViewerHtml(meta&&Array.isArray(meta.file_edits)?meta.file_edits:[],meta?meta.edit_session_id:null,meta?!!meta.undo_available:false):'';
  const documentsHtml=role==='assistant'?buildDocumentsHtml(meta):'';

  content=esc(content);
  if(role==='assistant'){
    content=_renderMarkdown(content);
  }
  content=content.replace(/\n/g,'<br>');

  const hasCards=fileEditsHtml||documentsHtml;
  if(hasCards){
    thread.insertAdjacentHTML('beforeend',`
      <div class="msg-group ${role}">
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-content-col">${fileEditsHtml}${documentsHtml}<div class="msg-bubble">${extraHtml}${content}${metaHtml}</div></div>
      </div>`);
  }else{
    thread.insertAdjacentHTML('beforeend',`
      <div class="msg-group ${role}">
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-bubble">${extraHtml}${content}${metaHtml}</div>
      </div>`);
  }
  thread.scrollTop=thread.scrollHeight;
}

/* Markdown renderer for assistant messages */
function _renderMarkdown(text){
  // Code blocks (triple backticks) — must be first
  text=text.replace(/```(\w*)\n?([\s\S]*?)```/g,function(_,lang,code){
    return '<pre class="msg-codeblock"><code'+(lang?' data-lang="'+lang+'"':'')+'>'+code+'</code></pre>';
  });
  // Headers (## and ###) — process lines
  text=text.replace(/^(#{1,3})\s+(.+)$/gm,function(_,hashes,title){
    const level=hashes.length;
    const sizes={1:'18px',2:'16px',3:'14px'};
    return `<div style="font-size:${sizes[level]||'14px'};font-weight:700;color:var(--text-strong);margin:10px 0 4px;${level===1?'border-bottom:1px solid var(--border);padding-bottom:4px':''}">` + title + '</div>';
  });
  // Ordered lists (1. 2. 3.)
  text=text.replace(/^(\d+)\.\s+(.+)$/gm,function(_,num,item){
    return `<div style="display:flex;gap:8px;align-items:flex-start;margin:2px 0"><span style="color:var(--accent);font-weight:700;min-width:20px">${num}.</span><span>${item}</span></div>`;
  });
  // Unordered lists (- item)
  text=text.replace(/^[-•]\s+(.+)$/gm,function(_,item){
    return `<div style="display:flex;gap:8px;align-items:flex-start;margin:2px 0"><span style="color:var(--accent)">•</span><span>${item}</span></div>`;
  });
  // Inline code (backticks)
  text=text.replace(/`([^`]+)`/g,'<code>$1</code>');
  // Bold
  text=text.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  // Italic
  text=text.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g,'<em>$1</em>');
  // Horizontal rule
  text=text.replace(/^---+$/gm,'<hr style="border:none;border-top:1px solid var(--border);margin:8px 0">');
  // Links [text](url)
  text=text.replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:underline">$1</a>');
  // Plain localhost URLs (auto-linkify preview links)
  text=text.replace(/(?<!\(")(https?:\/\/localhost:[0-9]+[^\s<"]*)/g,'<a href="$1" target="_blank" rel="noopener" style="color:var(--accent);font-weight:600;text-decoration:underline">$1</a>');
  return text;
}

export function buildMetaHtml(meta){
  if(!meta)return'';
  const pills=[];
  if(meta.provider_used||meta.model_used)pills.push(`<span class="meta-pill">🤖 ${esc(meta.provider_used||'?')} / ${esc(meta.model_used||'?')}</span>`);
  if(meta.fallback_used)pills.push(`<span class="meta-pill warn">⚠️ fallback: ${esc(meta.fallback_reason||'?')}</span>`);
  if(meta.continuation_used)pills.push(`<span class="meta-pill">↩️ continuation x${meta.continuation_steps||0}</span>`);
  if(meta.finish_reason&&meta.finish_reason!=='stop')pills.push(`<span class="meta-pill warn">finish: ${esc(meta.finish_reason)}</span>`);
  if(meta.agent_output_incomplete)pills.push(`<span class="meta-pill warn">🔍 ${esc(meta.agent_output_warning||'incomplete')}</span>`);
  if((meta.agent_repair_attempts||0)>0)pills.push(`<span class="meta-pill">🔧 repair x${meta.agent_repair_attempts}</span>`);
  return pills.length?`<div class="msg-meta">${pills.join('')}</div>`:'';
}

export function normalizeEdit(item){
  if(!item||typeof item!=='object')return null;
  return{
    id:String(item.id||`${item.trace_id||'t'}:${item.file_path||'f'}:${item.action||'u'}`),
    trace_id:String(item.trace_id||''),turn_id:String(item.turn_id||''),
    session_id:item.session_id?String(item.session_id):null,
    tool_name:String(item.tool_name||''),action:String(item.action||'updated'),
    file_path:String(item.file_path||''),
    workspace_relative:item.workspace_relative?String(item.workspace_relative):null,
    additions:Number(item.additions||0),deletions:Number(item.deletions||0),
    summary:String(item.summary||''),
    diff_preview:Array.isArray(item.diff_preview)?item.diff_preview.map(l=>String(l)):[],
    after_content:item.after_content!=null?String(item.after_content):null,
    before_content:item.before_content!=null?String(item.before_content):null
  };
}

export function mergeEdits(base,extra){
  const m=new Map();
  const push=items=>{if(!Array.isArray(items))return;for(const raw of items){const n=normalizeEdit(raw);if(n)m.set(n.id,n)}};
  push(base);push(extra);return Array.from(m.values());
}

export function _diffFileIcon(path){
  const ext=(path||'').split('.').pop().toLowerCase();
  const m={'js':'🟨','ts':'🔷','py':'🐍','html':'🌐','css':'🎨','json':'📦','md':'📝','svg':'🖼️','xml':'📄','txt':'📝','sh':'⚙️','yml':'⚙️','yaml':'⚙️'};
  return m[ext]||'📄';
}

export function _parseDiffLines(preview){
  /* Parse unified diff lines into structured objects with old/new line numbers */
  const result=[];
  let oldLine=0,newLine=0;
  for(const raw of preview){
    if(raw.startsWith('@@')){
      const m=raw.match(/@@ -(\d+)/);if(m){oldLine=parseInt(m[1])-1;}
      const m2=raw.match(/\+(\d+)/);if(m2){newLine=parseInt(m2[1])-1;}
      result.push({type:'hunk',text:raw,oldNum:null,newNum:null});
    }else if(raw.startsWith('+')){
      newLine++;
      result.push({type:'plus',text:raw.slice(1),oldNum:null,newNum:newLine});
    }else if(raw.startsWith('-')){
      oldLine++;
      result.push({type:'minus',text:raw.slice(1),oldNum:oldLine,newNum:null});
    }else{
      oldLine++;newLine++;
      result.push({type:'context',text:raw.startsWith(' ')?raw.slice(1):raw,oldNum:oldLine,newNum:newLine});
    }
  }
  return result;
}

export function _renderDiffLines(parsed){
  let html='';
  for(const ln of parsed){
    const cls=ln.type;
    const sign=ln.type==='plus'?'+':ln.type==='minus'?'-':ln.type==='hunk'?'':' ';
    const oNum=ln.oldNum!=null?ln.oldNum:'';
    const nNum=ln.newNum!=null?ln.newNum:'';
    html+=`<div class="diff-line ${cls}"><div class="diff-line-nums"><span class="diff-line-num-old">${oNum}</span><span class="diff-line-num-new">${nNum}</span></div><span class="diff-line-sign">${sign}</span><span class="diff-line-content">${esc(ln.text)}</span></div>`;
  }
  return html;
}

export function _renderFullFile(content,cardId){
  if(!content)return'<div style="padding:12px;color:var(--muted);font-size:12px">(contenu non disponible)</div>';
  const lines=content.split('\n');
  let html='';
  lines.forEach((l,i)=>{
    html+=`<div class="diff-full-line"><span class="diff-full-line-num">${i+1}</span><span class="diff-full-line-content">${esc(l)}</span></div>`;
  });
  return html;
}

export function toggleDiffView(cardId,mode){
  const diffEl=document.getElementById(cardId+'_diff');
  const fullEl=document.getElementById(cardId+'_full');
  const btns=document.querySelectorAll('#'+cardId+'_toolbar .dtb-btn');
  if(!diffEl||!fullEl)return;
  btns.forEach(b=>b.classList.remove('active'));
  if(mode==='full'){
    diffEl.style.display='none';fullEl.classList.add('visible');
    btns.forEach(b=>{if(b.dataset.mode==='full')b.classList.add('active')});
  }else{
    diffEl.style.display='';fullEl.classList.remove('visible');
    btns.forEach(b=>{if(b.dataset.mode==='diff')b.classList.add('active')});
  }
}

export function copyDiffContent(cardId){
  const el=document.getElementById(cardId+'_full');
  const diffEl=document.getElementById(cardId+'_diff');
  let text='';
  if(el&&el.classList.contains('visible')){text=el.innerText}else if(diffEl){text=diffEl.innerText}
  if(text)navigator.clipboard.writeText(text).then(()=>{
    const ind=document.getElementById(cardId+'_copied');
    if(ind){ind.style.display='inline';setTimeout(()=>{ind.style.display='none'},1500)}
  });
}

export function buildDiffViewerHtml(edits,sessionId,undoAvail){
  edits=mergeEdits([],edits);if(!edits.length)return'';
  const totalAdd=edits.reduce((s,e)=>s+(e.additions||0),0);
  const totalDel=edits.reduce((s,e)=>s+(e.deletions||0),0);
  const uid='dv_'+Math.random().toString(36).slice(2,8);

  let html=`<div class="diff-viewer-wrap" id="${uid}">`;
  html+=`<div class="diff-viewer-summary"><span class="diff-summary-icon">📝</span><span class="diff-summary-title">${edits.length} fichier${edits.length>1?'s':''} modifie${edits.length>1?'s':''}</span><div class="diff-summary-stats"><span class="additions">+${totalAdd}</span><span class="deletions">-${totalDel}</span></div></div>`;

  edits.forEach((e,idx)=>{
    const path=esc(e.workspace_relative||e.file_path||'(?)');
    const shortName=path.split('/').pop()||path.split('\\').pop()||path;
    const action=(e.action||'updated').toLowerCase();
    const actionCls=action==='created'?'created':action==='deleted'?'deleted':(/edit/.test(action)?'edited':'modified');
    const cardId=uid+'_f'+idx;
    const sEnc=encodeURIComponent(sessionId||'');
    const fEnc=encodeURIComponent(e.file_path||'');
    const icon=_diffFileIcon(path);
    const hasContent=!!(e.after_content);
    const parsed=_parseDiffLines(e.diff_preview||[]);
    const hasDiff=parsed.length>0&&!(parsed.length===1&&parsed[0].text==='(no diff preview available)');

    html+=`<div class="diff-file-card" id="${cardId}">`;
    // Header
    html+=`<div class="diff-file-header" onclick="toggleDiffFile('${cardId}')">`;
    html+=`<span class="chevron open" id="${cardId}_chev">▶</span>`;
    html+=`<span class="diff-file-action ${actionCls}">${esc(action)}</span>`;
    html+=`<span style="font-size:14px">${icon}</span>`;
    html+=`<span class="diff-file-path" title="${path}">${shortName}</span>`;
    html+=`<div class="diff-file-stats"><span class="plus">+${e.additions||0}</span><span class="minus">-${e.deletions||0}</span></div>`;
    html+=`</div>`;

    // Toolbar
    html+=`<div class="diff-file-toolbar" id="${cardId}_toolbar">`;
    html+=`<button class="dtb-btn active" data-mode="diff" onclick="toggleDiffView('${cardId}','diff')">Diff</button>`;
    if(hasContent){html+=`<button class="dtb-btn" data-mode="full" onclick="toggleDiffView('${cardId}','full')">Fichier complet</button>`}
    html+=`<button class="dtb-btn" onclick="copyDiffContent('${cardId}')">Copier</button>`;
    html+=`<span class="diff-copied" id="${cardId}_copied" style="display:none">✓ copie</span>`;
    html+=`<span style="flex:1"></span>`;
    html+=`<span style="font-size:11px;color:var(--muted)">${path}</span>`;
    html+=`</div>`;

    // Diff body — auto-expanded
    html+=`<div class="diff-file-body open" id="${cardId}_body">`;
    html+=`<pre class="diff-file-diff" id="${cardId}_diff">`;
    if(hasDiff){
      html+=_renderDiffLines(parsed);
    }else{
      html+=`<div class="diff-line context"><div class="diff-line-nums"><span class="diff-line-num-old"></span><span class="diff-line-num-new"></span></div><span class="diff-line-sign"></span><span class="diff-line-content" style="color:var(--muted)">(pas de diff disponible)</span></div>`;
    }
    html+=`</pre>`;
    // Full file view (hidden by default)
    html+=`<div class="diff-full-file" id="${cardId}_full">`;
    if(hasContent){html+=_renderFullFile(e.after_content,cardId)}
    html+=`</div>`;
    html+=`</div>`;

    // Per-file actions
    if(undoAvail&&sessionId){
      html+=`<div class="diff-actions"><button class="diff-btn diff-btn-reject" onclick="undoSingleFileEnc('${sEnc}','${fEnc}');this.closest('.diff-file-card').style.opacity='0.4';this.disabled=true">Annuler</button></div>`;
    }
    html+=`</div>`;
  });

  // Global actions
  if(undoAvail&&sessionId){
    const sEnc=encodeURIComponent(sessionId);
    html+=`<div class="diff-actions" style="border:1px solid var(--border-strong);border-radius:var(--radius-md);background:rgba(0,0,0,0.15)"><button class="diff-btn diff-btn-accept" onclick="acceptAllEdits('${uid}')">Conserver tout</button><button class="diff-btn diff-btn-reject" onclick="undoSessionEnc('${sEnc}');document.getElementById('${uid}').style.opacity='0.4'">Annuler tout</button><button class="diff-btn-toggle" onclick="toggleAllDiffs('${uid}')">Voir/Masquer tout</button></div>`;
  }
  html+=`</div>`;
  return html;
}

export function toggleDiffFile(cardId){
  const body=document.getElementById(cardId+'_body');
  const chev=document.getElementById(cardId+'_chev');
  if(!body)return;
  body.classList.toggle('open');
  if(chev)chev.classList.toggle('open');
}

export function toggleAllDiffs(wrapId){
  const wrap=document.getElementById(wrapId);
  if(!wrap)return;
  const bodies=wrap.querySelectorAll('.diff-file-body');
  const anyOpen=Array.from(bodies).some(b=>b.classList.contains('open'));
  bodies.forEach(b=>{if(anyOpen)b.classList.remove('open');else b.classList.add('open')});
  wrap.querySelectorAll('.chevron').forEach(c=>{if(anyOpen)c.classList.remove('open');else c.classList.add('open')});
}

export function acceptAllEdits(wrapId){
  const wrap=document.getElementById(wrapId);
  if(!wrap)return;
  wrap.style.opacity='0.5';
  wrap.querySelectorAll('.diff-actions').forEach(a=>a.innerHTML='<span style="color:var(--ok);font-size:12px;font-weight:600">✓ Conservé</span>');
}

/* Document output in chat */
export function buildDocumentsHtml(meta){
  if(!meta||!meta.created_documents||!meta.created_documents.length)return'';
  return meta.created_documents.map(doc=>{
    const name=esc(doc.name||doc.path||'document');
    const ext=(doc.name||doc.path||'').split('.').pop().toLowerCase();
    const isImg=['png','jpg','jpeg','gif','webp','svg'].includes(ext);
    const icon=isImg?'🖼️':ext==='pdf'?'📕':ext==='md'?'📝':'📄';
    let bodyHtml='';
    if(isImg&&doc.url){bodyHtml=`<img src="${esc(doc.url)}" alt="${name}" style="max-width:100%">`}
    else if(doc.content){bodyHtml=`<code>${esc(doc.content.substring(0,2000))}</code>`}
    else{bodyHtml='<span style="color:var(--muted)">Fichier cree</span>'}
    const downloadBtn=doc.url?`<button onclick="window.open('${esc(doc.url)}','_blank')">Telecharger</button>`:'';
    return`<div class="chat-document"><div class="chat-document-header"><span class="chat-document-icon">${icon}</span><span class="chat-document-name">${name}</span><div class="chat-document-actions">${downloadBtn}</div></div><div class="chat-document-body">${bodyHtml}</div></div>`;
  }).join('');
}

export async function undoSessionEdits(sid){
  if(!sid)return;
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/edits/undo`,{method:'POST',headers:h,body:JSON.stringify({session_id:sid})});
    const d=await r.json();
    if(!r.ok||!d.success){logC(`Undo failed: ${d.message||d.detail||'?'}`,'error');return}
    logC(`✅ Undo session OK (${d.restored||0})`,'success');addMsg('assistant',`↩ Undo session: ${d.restored||0} restauration(s).`);
  }catch(e){logC(`❌ ${e.message}`,'error')}
}

export async function undoSingleFile(sid,fp){
  if(!sid||!fp)return;
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/edits/undo`,{method:'POST',headers:h,body:JSON.stringify({session_id:sid,file_path:fp})});
    const d=await r.json();
    if(!r.ok||!d.success){logC(`Undo failed: ${d.message||'?'}`,'error');return}
    logC(`✅ Undo: ${fp}`,'success');addMsg('assistant',`↩ Undo: \`${fp}\``);
  }catch(e){logC(`❌ ${e.message}`,'error')}
}

export function undoSessionEnc(enc){undoSessionEdits(decodeURIComponent(enc||''))}
export function undoSingleFileEnc(sEnc,fEnc){undoSingleFile(decodeURIComponent(sEnc||''),decodeURIComponent(fEnc||''))}

/* ============================================================
   FILE UPLOAD / ATTACHMENTS
   ============================================================ */
let _pendingAttachments=[];
const _MAX_UPLOAD_SIZE=20*1024*1024; // 20MB
const _ALLOWED_TYPES=['image/png','image/jpeg','image/gif','image/webp','image/svg+xml','application/pdf','text/plain','text/markdown','text/csv','text/html','text/css','application/json','application/xml'];
const _ALLOWED_EXTS=['.png','.jpg','.jpeg','.gif','.webp','.svg','.pdf','.txt','.md','.py','.js','.ts','.html','.css','.json','.xml','.csv','.doc','.docx'];

export function handleFileSelect(event){
  const files=event.target.files;
  if(!files||!files.length)return;
  for(const file of files)addAttachment(file);
  event.target.value='';
}

export function addAttachment(file){
  // Validate extension
  const ext='.'+file.name.split('.').pop().toLowerCase();
  const typeOk=_ALLOWED_TYPES.includes(file.type)||_ALLOWED_EXTS.includes(ext);
  if(!typeOk){logC(`Type non supporté: ${file.name}`,'warning');return}
  if(file.size>_MAX_UPLOAD_SIZE){logC(`Fichier trop gros: ${file.name} (${Math.round(file.size/1024/1024)}MB)`,'warning');return}
  if(_pendingAttachments.length>=5){logC('Max 5 fichiers','warning');return}
  // Avoid duplicates
  if(_pendingAttachments.some(a=>a.file.name===file.name&&a.file.size===file.size))return;

  const att={file,id:'att_'+Math.random().toString(36).slice(2,8)};
  _pendingAttachments.push(att);
  renderAttachments();
}

export function removeAttachment(id){
  _pendingAttachments=_pendingAttachments.filter(a=>a.id!==id);
  renderAttachments();
}

export function clearAttachments(){
  _pendingAttachments=[];
  renderAttachments();
}

export function renderAttachments(){
  const wrap=document.getElementById('compose-attachments');
  if(!wrap)return;
  if(!_pendingAttachments.length){wrap.style.display='none';wrap.innerHTML='';return}
  wrap.style.display='flex';
  wrap.innerHTML=_pendingAttachments.map(att=>{
    const isImg=att.file.type&&att.file.type.startsWith('image/');
    const sizeStr=att.file.size<1024?att.file.size+'B':att.file.size<1048576?Math.round(att.file.size/1024)+'KB':Math.round(att.file.size/1048576*10)/10+'MB';
    let thumb='';
    if(isImg){
      const url=URL.createObjectURL(att.file);
      thumb=`<img src="${url}" alt="">`;
    }else{
      thumb=`<span style="font-size:20px">📄</span>`;
    }
    return`<div class="attachment-preview">${thumb}<div><div class="attachment-name">${esc(att.file.name)}</div><div class="attachment-size">${sizeStr}</div></div><button class="attachment-remove" onclick="removeAttachment('${att.id}')">✕</button></div>`;
  }).join('');
}

// Drag & drop + paste
(function setupUploadHandlers(){
  document.addEventListener('DOMContentLoaded',()=>{
    const box=document.getElementById('compose-box');
    const drop=document.getElementById('compose-drop-zone');
    if(!box||!drop)return;
    let dragCounter=0;
    box.addEventListener('dragenter',e=>{e.preventDefault();dragCounter++;drop.classList.add('active')});
    box.addEventListener('dragleave',e=>{e.preventDefault();dragCounter--;if(dragCounter<=0){dragCounter=0;drop.classList.remove('active')}});
    box.addEventListener('dragover',e=>{e.preventDefault()});
    box.addEventListener('drop',e=>{
      e.preventDefault();dragCounter=0;drop.classList.remove('active');
      if(e.dataTransfer&&e.dataTransfer.files){for(const f of e.dataTransfer.files)addAttachment(f)}
    });
    // Paste images
    const input=document.getElementById('message-input');
    if(input)input.addEventListener('paste',e=>{
      if(e.clipboardData&&e.clipboardData.files&&e.clipboardData.files.length){
        for(const f of e.clipboardData.files){
          if(f.type.startsWith('image/')){addAttachment(f);e.preventDefault();break}
        }
      }
    });
  });
})();

/* ============================================================
   CHAT PERSISTENCE — localStorage
   ============================================================ */
const _CHAT_KEY='lumena_chat_history';
let _chatMessages=[];

export function _pushChat(role,text,meta){
  _chatMessages.push({role,text,meta:meta?{provider_used:meta.provider_used,model_used:meta.model_used}:null});
  try{localStorage.setItem(_CHAT_KEY,JSON.stringify(_chatMessages.slice(-100)))}catch(e){}
}

export function loadChatHistory(){
  try{
    const raw=localStorage.getItem(_CHAT_KEY);
    if(!raw)return;
    _chatMessages=JSON.parse(raw);
    if(!_chatMessages.length)return;
    const welcome=document.getElementById('chat-welcome');
    if(welcome)welcome.style.display='none';
    chatHasMessages=true;
    for(const m of _chatMessages)addMsg(m.role,m.text,m.meta);
  }catch(e){_chatMessages=[]}
}

export function clearChatHistory(){
  _chatMessages=[];
  localStorage.removeItem(_CHAT_KEY);
  const thread=document.getElementById('chat-thread');
  if(thread)thread.innerHTML='';
  const welcome=document.getElementById('chat-welcome');
  if(welcome)welcome.style.display='';
  chatHasMessages=false;
}

export function exportChatMarkdown(){
  if(!_chatMessages.length){logC('Aucun message a exporter','warning');return}
  const lines=_chatMessages.map(m=>`### ${m.role==='user'?'Vous':'Lumena'}\n\n${m.text}\n`);
  const md='# Conversation Lumena — '+new Date().toLocaleDateString('fr-FR')+'\n\n'+lines.join('\n---\n\n');
  const blob=new Blob([md],{type:'text/markdown'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='lumena-chat-'+new Date().toISOString().slice(0,10)+'.md';
  a.click();
  URL.revokeObjectURL(a.href);
}
