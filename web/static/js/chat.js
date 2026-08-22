/* ============================================================
   CHAT — Lumena Control Panel
   ============================================================ */
let _dictationRecorder=null;
let _dictationStream=null;
let _dictationChunks=[];
let _dictationTimer=null;
let _dictationBusy=false;
let _dictationAudioContext=null;
let _dictationAnalyser=null;
let _dictationVadFrame=null;
let _dictationSessionId=0;
let _dictationDiscard=false;
let _dictationSendDoneFor=0;
let _dictationConfig={max_duration_ms:60000,silence_ms:1800};

function _dictationHeaders(){
  const headers={};
  if(ADMIN_TOKEN)headers.Authorization=`Bearer ${ADMIN_TOKEN}`;
  return headers;
}

async function _setVoiceDictationActive(active){
  const r=await fetch(`${API_BASE}/api/voice/dictation-state?active=${active?'true':'false'}`,{
    method:'POST',headers:_dictationHeaders()
  });
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
}

async function _loadDictationConfig(){
  try{
    const r=await fetch(`${API_BASE}/api/voice/dictation-config`,{headers:_dictationHeaders()});
    if(!r.ok)return;
    const data=await r.json();
    _dictationConfig={
      max_duration_ms:Math.max(5000,Math.min(300000,Number(data.max_duration_ms)||60000)),
      silence_ms:Math.max(800,Math.min(5000,Number(data.silence_ms)||1800)),
    };
  }catch(_e){}
}

function _setDictationButton(active,busy=false){
  const btn=document.getElementById('btn-chat-dictation');if(!btn)return;
  btn.classList.toggle('recording',active);
  btn.disabled=busy;
  btn.setAttribute('aria-pressed',active?'true':'false');
  btn.title=active?'Arrêter la dictée':busy?'Transcription en cours':'Dicter un message';
  btn.innerHTML=active?'<i data-lucide="square"></i>':'<i data-lucide="mic"></i>';
  if(window.lucide)window.lucide.createIcons();
}

function _releaseDictationMedia(){
  if(_dictationTimer){clearTimeout(_dictationTimer);_dictationTimer=null;}
  if(_dictationVadFrame){cancelAnimationFrame(_dictationVadFrame);_dictationVadFrame=null;}
  _dictationAnalyser=null;
  if(_dictationAudioContext){
    try{_dictationAudioContext.close();}catch(_e){}
    _dictationAudioContext=null;
  }
  if(_dictationStream){for(const track of _dictationStream.getTracks())track.stop();}
  _dictationStream=null;_dictationRecorder=null;
}

function _stopChatDictation(){
  if(!_dictationRecorder||_dictationRecorder.state!=='recording')return;
  _dictationBusy=true;
  _setDictationButton(false,true);
  if(_dictationVadFrame){cancelAnimationFrame(_dictationVadFrame);_dictationVadFrame=null;}
  _dictationRecorder.stop();
}

function _startDictationSilenceDetector(stream){
  const AudioCtx=window.AudioContext||window.webkitAudioContext;
  if(!AudioCtx)return;
  try{
    _dictationAudioContext=new AudioCtx();
    const source=_dictationAudioContext.createMediaStreamSource(stream);
    _dictationAnalyser=_dictationAudioContext.createAnalyser();
    _dictationAnalyser.fftSize=1024;
    source.connect(_dictationAnalyser);
    const samples=new Uint8Array(_dictationAnalyser.fftSize);
    const startedAt=performance.now();
    let lastVoiceAt=startedAt;
    let speechSeen=false;
    let noiseFloor=0.008;
    const tick=()=>{
      if(!_dictationAnalyser||!_dictationRecorder||_dictationRecorder.state!=='recording')return;
      _dictationAnalyser.getByteTimeDomainData(samples);
      let sum=0;
      for(const sample of samples){const value=(sample-128)/128;sum+=value*value;}
      const rms=Math.sqrt(sum/samples.length);
      const now=performance.now();
      // N'apprend le bruit que sur un niveau bas : si l'utilisateur parle dès
      // le clic, sa voix ne doit jamais devenir le nouveau seuil de silence.
      if(now-startedAt<500&&!speechSeen&&rms<0.025){
        noiseFloor=(noiseFloor*0.8)+(rms*0.2);
      }
      const threshold=Math.max(0.018,noiseFloor*2.8);
      if(rms>=threshold){speechSeen=true;lastVoiceAt=now;}
      if(speechSeen&&now-lastVoiceAt>=_dictationConfig.silence_ms){_stopChatDictation();return;}
      _dictationVadFrame=requestAnimationFrame(tick);
    };
    _dictationVadFrame=requestAnimationFrame(tick);
  }catch(_e){
    _dictationAnalyser=null;
  }
}

function _insertDictationAtCursor(input,text){
  if(!text)return;
  const start=Number.isInteger(input.selectionStart)?input.selectionStart:input.value.length;
  const end=Number.isInteger(input.selectionEnd)?input.selectionEnd:start;
  const before=input.value.slice(0,start);
  const after=input.value.slice(end);
  const leftJoin=before&&!/\s$/.test(before)&&!/^[,.;!?]/.test(text)?' ':'';
  const rightJoin=after&&!/^\s/.test(after)&&!/[\s([{]$/.test(text)?' ':'';
  const inserted=leftJoin+text+rightJoin;
  input.value=before+inserted+after;
  const cursor=before.length+inserted.length;
  input.setSelectionRange(cursor,cursor);
  input.dispatchEvent(new Event('input',{bubbles:true}));
}

async function _finishChatDictation(blob,sessionId){
  if(sessionId!==_dictationSessionId||_dictationDiscard)return;
  _dictationBusy=true;_setDictationButton(false,true);
  try{
    if(!blob||blob.size<256)throw new Error('Enregistrement audio vide ou trop court');
    const form=new FormData();
    const ext=blob.type.includes('ogg')?'ogg':blob.type.includes('wav')?'wav':'webm';
    form.append('audio',blob,`dictation.${ext}`);
    const r=await fetch(`${API_BASE}/api/voice/transcribe`,{
      method:'POST',headers:_dictationHeaders(),body:form
    });
    const data=await r.json();
    if(!r.ok){
      const detail=typeof data.detail==='object'?(data.detail.message||data.detail.code):data.detail;
      throw new Error(detail||`HTTP ${r.status}`);
    }
    const text=(data.text||'').trim();
    const input=document.getElementById('message-input');
    _insertDictationAtCursor(input,text);
    input.focus();
    if(data.should_send){
      if(!input.value.trim()){
        logC('Commande Envoyer ignorée : le message est vide','warning');
      }else if(isLoading){
        logC('Commande Envoyer détectée, mais Lumena répond déjà : texte conservé','warning');
      }else if(_dictationSendDoneFor!==sessionId){
        _dictationSendDoneFor=sessionId;
        logC('Commande Envoyer détectée','success');
        sendMessage();
        return;
      }
    }
    if(!text){logC('Aucune parole détectée','warning');return;}
    logC('Dictée ajoutée au message','success');
  }catch(e){
    logC(`Dictée impossible: ${e.message}`,'error');
  }finally{
    _dictationBusy=false;
    _releaseDictationMedia();
    try{await _setVoiceDictationActive(false);}catch(e){}
    _setDictationButton(false,false);
  }
}

export async function toggleChatDictation(){
  if(_dictationBusy)return;
  if(_dictationRecorder&&_dictationRecorder.state==='recording'){
    _stopChatDictation();
    return;
  }
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
    logC('La dictée micro n’est pas disponible dans ce navigateur','warning');
    return;
  }
  try{
    await _loadDictationConfig();
    await _setVoiceDictationActive(true);
    _dictationStream=await navigator.mediaDevices.getUserMedia({audio:true});
    const candidates=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus'];
    const mime=candidates.find(x=>MediaRecorder.isTypeSupported?.(x))||'';
    const sessionId=++_dictationSessionId;
    _dictationDiscard=false;
    _dictationChunks=[];
    _dictationRecorder=new MediaRecorder(_dictationStream,mime?{mimeType:mime}:undefined);
    _dictationRecorder.ondataavailable=e=>{if(e.data&&e.data.size)_dictationChunks.push(e.data);};
    _dictationRecorder.onerror=e=>{
      _dictationDiscard=true;
      logC(`Erreur micro: ${e.error?.message||'inconnue'}`,'error');
      _releaseDictationMedia();
      _dictationBusy=false;_setDictationButton(false,false);
      _setVoiceDictationActive(false).catch(()=>{});
    };
    _dictationRecorder.onstop=()=>{
      const type=_dictationRecorder?.mimeType||mime||'audio/webm';
      const blob=new Blob(_dictationChunks,{type});
      _finishChatDictation(blob,sessionId);
    };
    _dictationRecorder.start(250);
    _setDictationButton(true,false);
    _startDictationSilenceDetector(_dictationStream);
    _dictationTimer=setTimeout(()=>{
      if(_dictationRecorder?.state==='recording')_stopChatDictation();
    },_dictationConfig.max_duration_ms);
  }catch(e){
    _releaseDictationMedia();
    try{await _setVoiceDictationActive(false);}catch(_e){}
    _setDictationButton(false,false);
    logC(`Micro indisponible: ${e.message}`,'error');
  }
}

export function setupTextarea(){
  const ta=document.getElementById('message-input');
  if(!ta)return;
  const resize=()=>{
    const maxHeight=Math.min(320, Math.max(180, Math.floor(window.innerHeight*0.34)));
    ta.style.height='36px';
    const contentHeight=ta.scrollHeight;
    const minReadableHeight=(ta.value.length||document.activeElement===ta)?128:36;
    const nextHeight=Math.min(Math.max(contentHeight,minReadableHeight),maxHeight);
    ta.style.height=nextHeight+'px';
    ta.style.overflowY=contentHeight>maxHeight?'auto':'hidden';
  };
  const scheduleResize=()=>setTimeout(resize,0);
  ta.addEventListener('input',resize);
  ta.addEventListener('keyup',resize);
  ta.addEventListener('focus',resize);
  ta.addEventListener('blur',resize);
  ta.addEventListener('paste',()=>setTimeout(resize,0));
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}else{scheduleResize()}});
  window.addEventListener('resize',scheduleResize);
  resize();
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
    return`<span style="font-size:11px;padding:3px 8px;background:rgba(0,0,0,0.2);border:1px solid var(--border);border-radius:var(--radius-sm)">${esc(a.name)}</span>`;
  }).join('')+'</div>':'';
  addMsg('user',message,null,userExtra);
  _pushChat('user',message);
  logC(`"${message.substring(0,50)}..."`,`info`);
  input.value='';input.style.height='auto';input.style.overflowY='hidden';
  isLoading=true;
  _pendingCancel=false; // Réinitialiser l'annulation différée pour ce nouveau run
  _abortController=new AbortController();
  _setSendBtnStop(true);
  // Reset checkpoint dedup state pour ce nouveau message
  window._lastCheckpointText=null;window._lastCheckpointEl=null;window._lastCheckpointCount=1;

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
    const _stepIconMap={'⚡':'zap','🔧':'wrench','📖':'eye','📄':'file-text','🖥️':'terminal','🤖':'bot','⏳':'loader','❌':'alert-circle','⚠️':'alert-triangle','✅':'check-circle-2','💭':'message-circle','📝':'pencil','🔄':'refresh-cw','▶':'play'};
    const _lucideName=_stepIconMap[icon];
    const _iconHtml=_lucideName?`<i data-lucide="${_lucideName}" style="width:13px;height:13px"></i>`:icon;
    step.innerHTML=`<span class="thinking-step-icon">${_iconHtml}</span><span class="thinking-step-text">${text}</span>`;
    if(_lucideName&&window.lucide)window.lucide.createIcons({nodes:[step.querySelector('.thinking-step-icon')]});
    body.appendChild(step);
    // Keep max 50 steps visible
    while(body.children.length>50)body.removeChild(body.firstChild);
    body.scrollTop=body.scrollHeight;
    thinking.scrollIntoView({block:'end',behavior:'instant'});
    return step;
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
    const langIcon=ext?ext.toUpperCase():'FILE';
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
      if(silence>=120){apiStatus.style.display='block';apiStatus.style.color='#e74c3c';apiStatus.textContent='API sans réponse depuis '+silence+'s — probable timeout';}
      else if(silence>=45){apiStatus.style.display='block';apiStatus.style.color='#f39c12';apiStatus.textContent='En attente API depuis '+silence+'s...';}
      else if(silence>=15){apiStatus.style.display='block';apiStatus.style.color='var(--muted)';apiStatus.textContent='Appel LLM en cours ('+silence+'s)';}
      else{apiStatus.style.display='none';}
    }
  },1000);

  // Build request body
  const reqBody={message,use_agent:useAgent};
  if(activeConversationId)reqBody.conversation_id=activeConversationId;
  if(uploadedPaths.length)reqBody.attachments=uploadedPaths;

  try{
    const h={'Content-Type':'application/json'};
    if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const response=await fetch(`${API_BASE}/api/chat/stream`,{method:'POST',headers:h,body:JSON.stringify(reqBody),signal:_abortController.signal});
    if(!response.ok){const _err=await response.json().catch(()=>({}));throw new Error(_err.detail||`HTTP ${response.status}`);}
    if(!response.body)throw new Error('SSE indisponible');

    const reader=response.body.getReader();
    const decoder=new TextDecoder();let buffer='';let finalResponse=null;
    pendingFileEdits=[];pendingEditSessionId=null;pendingUndoAvailable=false;

    while(true){
      let done,value;
      try{({done,value}=await reader.read());}
      catch(readErr){
        if(readErr.name==='AbortError')throw readErr;
        throw new Error('Stream interrompu: '+readErr.message);
      }
      if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      const lines=buffer.split('\n\n');buffer=lines.pop()||'';
      for(const line of lines){
        if(!line.startsWith('data: '))continue;
        try{
          const data=JSON.parse(line.slice(6));
          if(data.type!=='heartbeat')_lastEventTs=Date.now();
          if(data.type==='start'||data.type==='thinking'){
            addThinkingStep('⚡',esc(data.content||'Demarrage...'));
            pushActivity('checkpoint','',data.content||'Demarrage...');
            if(data.type==='start')resetTaskProgress();
          }
          else if(data.type==='thought'){
            activityCounts.thoughts++;
            addThinkingStep('💭','<em>'+esc(data.content)+'</em>');
            pushActivity('thought','',data.content);
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
            pushActivity('tool','',data.content);
            logC(data.content,'tool');
          }
          else if(data.type==='file_read'){
            activityCounts.obs++;
            openFileBlock(data.path||'',data.lines||'',data.content||'');
            pushActivity('observation','',`Lecture: ${data.path||'?'} (${data.lines||'?'})`);
            logC(`Lecture: ${(data.path||'').substring(0,80)}`,'tool');
          }
          else if(data.type==='observation'){
            activityCounts.obs++;
            addThinkingStep('📖','Observation: <span style="color:var(--muted)">'+esc((data.content||'').substring(0,120))+'</span>');
            pushActivity('observation','',(data.content||'').substring(0,300));
            logC('Observation: '+(data.content||'').substring(0,80),'tool');
          }
          else if(data.type==='terminal_open'){
            openTerminalBlock(data.content||'');
            pushActivity('tool','',data.content||'');
            logC('Terminal: '+(data.content||'').substring(0,80),'tool');
          }
          else if(data.type==='terminal_output'){
            appendTerminalLine(data.content||'',data.stream||'stdout');
          }
          else if(data.type==='terminal_close'){
            closeTerminalBlock(data.content||'');
            logC('Terminal terminé: '+(data.content||''),'tool');
          }
          else if(data.type==='agent_step'){
            // CodeAgent iteration detail — show action + path
            const detail=data.content||'';
            window._lastAgentStepDetail=detail;
            addThinkingStep('🤖',esc(detail));
            pushActivity('action','',detail);
            logC(`Agent: ${detail}`,'tool');
          }
          else if(data.type==='checkpoint'){
            const cp=data.checkpoint||data;
            // Skip action_detail if it was already shown by a recent agent_step event
            if(cp.action_detail && cp.action_detail===window._lastAgentStepDetail){
              logC(`Checkpoint (skip dup): ${cp.phase||'?'}`,'info');
            } else {
              let cpText=`Phase: ${esc(cp.phase||'processing')}`;
              if(cp.action_detail)cpText=esc(cp.action_detail);
              else if(cp.thoughts)cpText+=` — ${cp.thoughts} pensees`;
              if(cp.file_edits)cpText+=` — ${cp.file_edits} edits`;
              if(cp.retry_count)cpText+=` — retry #${cp.retry_count}`;
              // Dédup : si le texte est identique au dernier checkpoint affiché,
              // on incrémente juste un compteur sur la ligne existante au lieu
              // de rajouter une nouvelle ligne « Phase: running — 46 pensees » à l'infini.
              if(window._lastCheckpointText===cpText && window._lastCheckpointEl){
                window._lastCheckpointCount=(window._lastCheckpointCount||1)+1;
                const badge=window._lastCheckpointEl.querySelector('.dup-badge');
                const badgeHtml=` <span class="dup-badge" style="opacity:.6;font-size:.85em">×${window._lastCheckpointCount}</span>`;
                if(badge)badge.textContent=`×${window._lastCheckpointCount}`;
                else window._lastCheckpointEl.insertAdjacentHTML('beforeend',badgeHtml);
                logC(`Checkpoint (dup ×${window._lastCheckpointCount}): ${cp.phase||'?'}`,'info');
              } else {
                const el=addThinkingStep('⏳',cpText);
                window._lastCheckpointEl=el;
                window._lastCheckpointText=cpText;
                window._lastCheckpointCount=1;
                pushActivity('checkpoint','',cpText);
                logC(`Checkpoint: ${cp.phase||'?'} ${cp.action_detail||''}`,'info');
              }
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
              pushActivity('file-edit','',`${data.edit.action||'updated'}: ${fp}`);
              logC(`${data.edit.action||'updated'} ${fp}`,'tool');
            }
          }
          else if(data.type==='error'){
            activityCounts.errors++;
            addThinkingStep('❌',esc(data.content),'error');
            pushActivity('error','',data.content);
            logC(data.content,'error');
          }
          else if(data.type==='llm_retry'){
            addThinkingStep('🔄',esc(data.content));
            const apiStatus=thinking.querySelector('.thinking-api-status');
            if(apiStatus){apiStatus.style.display='block';apiStatus.style.color='#f39c12';apiStatus.textContent='Retry: '+esc(data.content);}
            pushActivity('thought','',data.content);
            logC(`LLM retry: ${data.content}`,'warning');
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
              thread.insertAdjacentHTML('beforeend',`<div class="msg-group assistant" id="streaming-msg"><div class="msg-avatar"><img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:28px;height:28px;object-fit:contain"></div><div class="msg-bubble streaming"><span class="streaming-text"></span><span class="streaming-cursor"></span></div></div>`);
              if(window.lucide)window.lucide.createIcons({nodes:[thread.querySelector('#streaming-msg .msg-avatar')]});
              window._streamingMsgEl=thread.querySelector('#streaming-msg .streaming-text');
              window._streamingRaw='';
              thread.scrollTop=thread.scrollHeight;
            }
            window._streamingRaw+=(data.content||'');
            // Render markdown progressively
            window._streamingMsgEl.innerHTML=_renderMarkdown(esc(window._streamingRaw)).replace(/\n/g,'<br>');
            window._streamingMsgEl.closest('.msg-group').scrollIntoView({block:'end',behavior:'smooth'});
          }
          else if(data.type==='stream_id'){
            window._currentStreamId=data.stream_id;
            // Annulation différée : Stop cliqué avant réception du stream_id
            if(_pendingCancel){
              const _h={'Content-Type':'application/json'};
              if(ADMIN_TOKEN)_h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
              fetch(`${API_BASE}/api/chat/cancel`,{method:'POST',headers:_h,body:JSON.stringify({stream_id:data.stream_id})}).catch(()=>{});
              window._currentStreamId=null;
              _pendingCancel=false;
            }
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
      if(finalResponse.conversation_id){
        activeConversationId=finalResponse.conversation_id;
        localStorage.setItem('lumena_active_conversation_id',activeConversationId);
      }
      pushActivity('checkpoint','',`Reponse terminee — ${finalResponse.provider_used||'?'}/${finalResponse.model_used||'?'}`);
      logC('Reponse recue','success');
      if(finalResponse.mood)setText('mood-value',finalResponse.mood);
      logC(`${finalResponse.provider_used||'?'} / ${finalResponse.model_used||'?'}`,'info');
      if(finalResponse.fallback_used){pushActivity('error','',`Fallback: ${finalResponse.fallback_reason||'?'}`);logC(`Fallback: ${finalResponse.fallback_reason||'?'}`,'warning')}
      if(finalResponse.continuation_used){pushActivity('checkpoint','',`Continuation x${finalResponse.continuation_steps||0}`);logC(`Continuation x${finalResponse.continuation_steps||0}`,'tool')}
      if((finalResponse.agent_repair_attempts||0)>0)pushActivity('tool','',`Auto-repair x${finalResponse.agent_repair_attempts}`);
    }else{
      thread.insertAdjacentHTML('beforeend',`<div class="msg-group assistant"><div class="msg-avatar"><img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:28px;height:28px;object-fit:contain"></div><div class="msg-bubble" style="display:flex;align-items:center;gap:12px"><span>Pas de réponse reçue (timeout API).</span><button onclick="retryLastMessage()" style="flex-shrink:0;background:var(--accent);color:#fff;border:none;border-radius:6px;padding:5px 14px;cursor:pointer;font-size:13px">↺ Réessayer</button></div></div>`);
      if(window.lucide)window.lucide.createIcons({nodes:[thread.lastElementChild.querySelector('.msg-avatar')]});
      thread.scrollTop=thread.scrollHeight;
      pushActivity('error','','Aucune reponse recue — cliquez Reessayer');
    }
    updateActivityStats();
    _abortController=null;
    _setSendBtnStop(false);
  }catch(e){
    if(_thinkingTimer){clearInterval(_thinkingTimer);_thinkingTimer=null;}
    thinking.remove();stopActivityFeed();
    const _streamEl=document.getElementById('streaming-msg');if(_streamEl)_streamEl.remove();
    window._streamingMsgEl=null;window._streamingRaw='';
    if(e.name==='AbortError'){
      addMsg('assistant','Génération interrompue.');
      pushActivity('checkpoint','','Annulé par l\'utilisateur');
      logC('Stream annulé','warning');
    }else{
      addMsg('assistant','Erreur de connexion.');
      pushActivity('error','',e.message);
      logC(e.message,'error');
    }
  }
  isLoading=false;
  _abortController=null;
  _setSendBtnStop(false);
}

export function retryLastMessage(){
  if(isLoading||!_lastSentMessage)return;
  const input=document.getElementById('message-input');
  input.value=_lastSentMessage;
  sendMessage();
}

export function cancelStream(){
  const sid=window._currentStreamId;
  if(sid){
    // stream_id déjà connu → annulation immédiate
    const h={'Content-Type':'application/json'};
    if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    fetch(`${API_BASE}/api/chat/cancel`,{method:'POST',headers:h,body:JSON.stringify({stream_id:sid})}).catch(()=>{});
    window._currentStreamId=null;
  }else if(isLoading){
    // stream_id pas encore reçu → poser le flag pour annulation dès réception
    _pendingCancel=true;
  }
  if(_abortController){_abortController.abort();}
}

function _setSendBtnStop(isStop){
  const btn=document.getElementById('send-btn');
  if(!btn)return;
  if(isStop){
    btn.className='btn-stop';
    btn.title='Arrêter';
    btn.innerHTML='<i data-lucide="square" style="width:16px;height:16px"></i>';
  }else{
    btn.className='btn-send';
    btn.title='Envoyer';
    btn.innerHTML='<i data-lucide="arrow-up"></i>';
  }
  if(window.lucide)window.lucide.createIcons({nodes:[btn]});
}

/* ============================================================
   MESSAGE RENDERING
   ============================================================ */
/* FT-6: Feedback thumbs — envoie le quality_flag au backend */
async function sendFeedback(contentHash, flag, btnEl) {
  if (!contentHash) return;
  // Feedback visuel immédiat avant même la réponse réseau
  if (btnEl) {
    const container = btnEl.closest('.msg-feedback');
    if (container) {
      container.querySelectorAll('button').forEach(b => {
        b.style.opacity = '0.3';
        b.style.color = 'var(--text-muted,#888)';
      });
      btnEl.style.opacity = '1';
      btnEl.style.color = flag === 'positive_explicit' ? 'var(--success,#4caf50)' : 'var(--error,#f44336)';
      btnEl.style.transform = 'scale(1.4)';
      setTimeout(() => { btnEl.style.transform = 'scale(1)'; }, 250);
    }
  }
  try {
    const h = {'Content-Type': 'application/json'};
    if (ADMIN_TOKEN) h['Authorization'] = `Bearer ${ADMIN_TOKEN}`;
    await fetch(`${API_BASE}/api/chat/feedback`, {
      method: 'POST',
      headers: h,
      body: JSON.stringify({ content_hash: contentHash, flag }),
    });
  } catch (e) { /* silencieux */ }
}

export function addMsg(role,content,meta=null,extraHtml=''){
  const thread=document.getElementById('chat-thread');
  const avatar=role==='assistant'
    ?'<img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:28px;height:28px;object-fit:contain">'
    :'<i data-lucide="user" style="width:18px;height:18px;color:var(--accent)"></i>';
  const metaHtml=role==='assistant'?buildMetaHtml(meta):'';
  const fileEditsHtml=role==='assistant'?buildDiffViewerHtml(meta&&Array.isArray(meta.file_edits)?meta.file_edits:[],meta?meta.edit_session_id:null,meta?!!meta.undo_available:false):'';
  const documentsHtml=role==='assistant'?buildDocumentsHtml(meta):'';

  // FT-6: Boutons 👍/👎 pour les messages assistant avec content_hash
  let feedbackHtml = '';
  if (role === 'assistant' && meta && meta.content_hash) {
    const h = meta.content_hash;
    const _btnStyle = 'background:none;border:none;cursor:pointer;padding:3px;border-radius:4px;color:var(--text-muted,#888);display:inline-flex;align-items:center;transition:all 0.2s';
    feedbackHtml = `<div class="msg-feedback" style="display:flex;gap:4px;margin-top:5px;opacity:0.65">` +
      `<button data-feedback-hash="${h}" data-flag="positive_explicit" onclick="window._sendFeedback('${h}','positive_explicit',this)" ` +
      `style="${_btnStyle}" title="Bonne réponse"><i data-lucide="thumbs-up" style="width:14px;height:14px"></i></button>` +
      `<button data-feedback-hash="${h}" data-flag="negative_explicit" onclick="window._sendFeedback('${h}','negative_explicit',this)" ` +
      `style="${_btnStyle}" title="Mauvaise réponse"><i data-lucide="thumbs-down" style="width:14px;height:14px"></i></button>` +
      `</div>`;
  }

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
        <div class="msg-content-col">${fileEditsHtml}${documentsHtml}<div class="msg-bubble">${extraHtml}${content}${metaHtml}${feedbackHtml}</div></div>
      </div>`);
  }else{
    thread.insertAdjacentHTML('beforeend',`
      <div class="msg-group ${role}">
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-bubble">${extraHtml}${content}${metaHtml}${feedbackHtml}</div>
      </div>`);
  }
  const _lastEl=thread.lastElementChild;
  if(window.lucide){
    const _iconNodes=[
      _lastEl.querySelector('.msg-avatar'),
      ..._lastEl.querySelectorAll('.msg-feedback i[data-lucide]'),
    ].filter(Boolean);
    if(_iconNodes.length)window.lucide.createIcons({nodes:_iconNodes});
  }
  thread.scrollTop=thread.scrollHeight;
}

// FT-6: Exposer sendFeedback globalement (utilisé dans les onclick inline)
window._sendFeedback = sendFeedback;

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
  if(meta.provider_used||meta.model_used)pills.push(`<span class="meta-pill">${esc(meta.provider_used||'?')} / ${esc(meta.model_used||'?')}</span>`);
  if(meta.prompt_tokens!=null||meta.completion_tokens!=null){const _in=meta.prompt_tokens!=null?meta.prompt_tokens:'?';const _out=meta.completion_tokens!=null?meta.completion_tokens:'?';const _total=(typeof meta.prompt_tokens==='number'&&typeof meta.completion_tokens==='number')?(meta.prompt_tokens+meta.completion_tokens):null;pills.push(`<span class="meta-pill">${_in} in / ${_out} out${_total!=null?' (Σ'+_total+')':''}</span>`)}
  if(meta.fallback_used)pills.push(`<span class="meta-pill warn">fallback: ${esc(meta.fallback_reason||'?')}</span>`);
  if(meta.continuation_used)pills.push(`<span class="meta-pill">continuation x${meta.continuation_steps||0}</span>`);
  if(meta.finish_reason&&meta.finish_reason!=='stop')pills.push(`<span class="meta-pill warn">finish: ${esc(meta.finish_reason)}</span>`);
  if(meta.agent_output_incomplete)pills.push(`<span class="meta-pill warn">${esc(meta.agent_output_warning||'incomplete')}</span>`);
  if((meta.agent_repair_attempts||0)>0)pills.push(`<span class="meta-pill">repair x${meta.agent_repair_attempts}</span>`);
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
  return ext?ext.toUpperCase():'FILE';
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
  html+=`<div class="diff-viewer-summary"><span class="diff-summary-title">${edits.length} fichier${edits.length>1?'s':''} modifie${edits.length>1?'s':''}</span><div class="diff-summary-stats"><span class="additions">+${totalAdd}</span><span class="deletions">-${totalDel}</span></div></div>`;

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
    const icon=ext?ext.toUpperCase():'FILE';
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
    logC(`Undo session OK (${d.restored||0})`,'success');addMsg('assistant',`Undo session: ${d.restored||0} restauration(s).`);
  }catch(e){logC(e.message,'error')}
}

export async function undoSingleFile(sid,fp){
  if(!sid||!fp)return;
  try{
    const h={'Content-Type':'application/json'};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/edits/undo`,{method:'POST',headers:h,body:JSON.stringify({session_id:sid,file_path:fp})});
    const d=await r.json();
    if(!r.ok||!d.success){logC(`Undo failed: ${d.message||'?'}`,'error');return}
    logC(`Undo: ${fp}`,'success');addMsg('assistant',`Undo: \`${fp}\``);
  }catch(e){logC(e.message,'error')}
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
      thumb=`<span style="font-size:11px;padding:4px 6px;background:rgba(255,255,255,.06);border-radius:4px;color:var(--muted)">${(att.file.name.split('.').pop()||'file').toUpperCase()}</span>`;
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
  // FT-3 (fix): conserver content_hash pour que les boutons feedback survivent au rechargement
  _chatMessages.push({role,text,meta:meta?{provider_used:meta.provider_used,model_used:meta.model_used,content_hash:meta.content_hash||undefined}:null});
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
  activeConversationId='';
  localStorage.removeItem('lumena_active_conversation_id');
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

export async function resumeSessionInChat(convId){
  if(!convId)return;
  try{
    const h={};if(ADMIN_TOKEN)h['Authorization']=`Bearer ${ADMIN_TOKEN}`;
    const r=await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(convId)}?limit=1000`,{headers:h});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const messages=d.messages||[];
    activeConversationId=convId;
    localStorage.setItem('lumena_active_conversation_id',convId);
    _chatMessages=[];
    const thread=document.getElementById('chat-thread');
    if(thread)thread.innerHTML='';
    const welcome=document.getElementById('chat-welcome');
    if(welcome)welcome.style.display='none';
    chatHasMessages=true;
    for(const m of messages){
      const meta={
        conversation_id:convId,
        task_id:m.task_id||null,
        trace_id:m.trace_id||null,
        provider_used:m.provider_used||undefined,
        model_used:m.model_used||undefined,
      };
      addMsg(m.role==='user'?'user':'assistant',m.content||'',meta);
      _pushChat(m.role==='user'?'user':'assistant',m.content||'',meta);
    }
    if(typeof switchPanel==='function')switchPanel('chat');
    logC(`Session reprise: ${convId.substring(0,16)}`,'success');
  }catch(e){logC(`Reprise session: ${e.message}`,'error')}
}
