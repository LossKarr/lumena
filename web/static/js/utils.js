/* ============================================================
   UTILITIES — Lumena Control Panel
   ============================================================ */
export function esc(v){if(v==null)return'';return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
export function setText(id,v){const el=document.getElementById(id);if(el)el.textContent=v}
export function fmtDur(ms){if(ms==null)return'-';const v=Number(ms);if(isNaN(v))return'-';if(v<1)return'<1ms';return Math.round(v)+'ms'}
export function loadingDots(text){return`<div style="display:flex;align-items:center;gap:8px"><div class="reading-dots"><span></span><span></span><span></span></div><span style="color:var(--muted)">${text}</span></div>`}

/* ============================================================
   CONSOLE
   ============================================================ */
export function logC(message,type='info'){
  const el=document.getElementById('console-output');if(!el)return;
  const colors={info:'var(--muted)',success:'var(--ok)',warning:'var(--warn)',error:'var(--danger)',tool:'var(--accent)'};
  const time=new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const line=document.createElement('div');
  line.innerHTML=`<span style="color:var(--muted)">[${time}]</span> <span style="color:${colors[type]||colors.info}">${message}</span>`;
  el.appendChild(line);el.scrollTop=el.scrollHeight;
}

export function clearConsole(){
  document.getElementById('console-output').innerHTML='<span style="color:var(--ok)"><i data-lucide="sparkles"></i> Lumena v1.0.0 - Beta 2026 Console</span>\n<span style="color:var(--muted)">[INFO] Console effacee</span>';
}
