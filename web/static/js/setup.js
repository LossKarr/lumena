/**
 * setup.js — iOS-style first-launch wizard for Lumena.
 *
 * Loaded as a module. Checks /api/setup/status on page load.
 * If needsSetup or preview mode → shows the wizard overlay.
 * Preview mode never writes to .env.
 */

let _steps = [];
let _currentStep = 0;
let _isPreview = false;
let _config = {};
let _modelsInfo = {};
let _keyDebounceTimer = null;  // P2.1: Debounce timer for key validation

// ─── Boot ─────────────────────────────────────────────────────────
export async function initSetupWizard() {
  const url = new URL(window.location.href);
  const forcePreview = url.searchParams.get('preview') === '1' ||
                       url.hash === '#setup';
  try {
    const previewParam = forcePreview ? '1' : '0';
    const res = await fetch(`/api/setup/status?preview=${previewParam}`);
    const data = await res.json();
    if (!data.needsSetup) return;
    _isPreview = data.preview;
    await _loadSchema();
    _showWizard();
  } catch {
    // If the endpoint fails, don't block the app
  }
}

async function _loadSchema() {
  try {
    const res = await fetch('/api/setup/schema');
    const data = await res.json();
    _steps = data.steps || [];
    const modelStep = _steps.find(s => s.id === 'model');
    if (modelStep && modelStep.models_info) {
      _modelsInfo = modelStep.models_info;
    }
  } catch {
    _steps = [];
  }
}

// ─── Render ───────────────────────────────────────────────────────
function _showWizard() {
  const overlay = document.getElementById('setup-wizard-overlay');
  if (!overlay) return;
  overlay.removeAttribute('hidden');

  if (_isPreview) {
    const banner = document.createElement('div');
    banner.className = 'setup-preview-banner';
    banner.textContent = 'MODE APERCU — rien ne sera sauvegardé';
    overlay.prepend(banner);
  }

  // Build step 0 = welcome, steps 1..N = schema steps, step N+1 = summary
  _renderDots();
  _renderStep();
}

function _totalSteps() {
  return _steps.length + 2; // welcome + N schema + summary
}

function _renderDots() {
  const cont = document.getElementById('setup-dots');
  if (!cont) return;
  cont.innerHTML = '';
  for (let i = 0; i < _totalSteps(); i++) {
    const dot = document.createElement('div');
    dot.className = 'setup-dot';
    if (i === _currentStep) dot.classList.add('active');
    else if (i < _currentStep) dot.classList.add('done');
    cont.appendChild(dot);
  }
}

// P0.1: Pre-fill _config with schema defaults for fields the user hasn't touched
function _prefillDefaults(step) {
  for (const f of (step.fields || [])) {
    if (f.default !== undefined) _config[f.key] = _config[f.key] ?? f.default;
  }
  for (const arr of [step.alert_fields, step.advanced_fields, step.ops_fields, step.sandbox_fields, step.identity_fields]) {
    for (const f of (arr || [])) {
      if (f.default !== undefined) _config[f.key] = _config[f.key] ?? f.default;
    }
  }
  for (const t of (step.personality_traits || [])) {
    const key = `LUMENA_TRAIT_${t.key}`;
    if (t.default !== undefined) _config[key] = _config[key] ?? String(t.default);
  }
  if (step.mood_options && step.mood_options.length) {
    _config.LUMENA_DEFAULT_MOOD = _config.LUMENA_DEFAULT_MOOD ?? 'neutral';
  }
}

function _renderStep() {
  const cont = document.getElementById('setup-step-container');
  if (!cont) return;
  _renderDots();

  if (_currentStep === 0) {
    _renderWelcome(cont);
  } else if (_currentStep <= _steps.length) {
    const step = _steps[_currentStep - 1];
    // P0.1: Pre-fill defaults for any field the user hasn't touched yet
    _prefillDefaults(step);
    if (step.id === 'model') _renderModelStep(cont, step);
    else if (step.id === 'keys' || step.id === 'image_gen_keys') _renderKeysStep(cont, step);
    else if (step.id === 'security') _renderSecurityStep(cont, step);
    else if (step.id === 'telegram') _renderTelegramStep(cont, step);
    else if (step.id === 'twitter') _renderTwitterStep(cont, step);
    else if (step.id === 'whatsapp') _renderWhatsAppStep(cont, step);
    else if (step.id === 'voice') _renderVoiceStep(cont, step);
    else if (step.id === 'moods') _renderMoodsStep(cont, step);
    else if (step.id === 'autonomy') _renderAutonomyStep(cont, step);
    else if (step.id === 'integrations') _renderIntegrationsStep(cont, step);
    else if (step.id === 'brains') _renderBrainsStep(cont, step);
    else if (step.id === 'locale') _renderLocaleStep(cont, step);
    else _renderGenericStep(cont, step);
  } else {
    _renderSummary(cont);
  }
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function _renderWelcome(cont) {
  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-welcome-logo"><img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:64px;height:64px;object-fit:contain"></div>
      <h2>Bienvenue</h2>
      <p class="setup-subtitle">Je suis Lumena, ton assistant IA personnel.<br>Configurons-moi ensemble en quelques étapes simples.</p>
      <div class="setup-info-box">
        <i data-lucide="info" style="width:18px;height:18px;flex-shrink:0;margin-top:2px"></i>
        <div>
          <strong>Pas de panique !</strong><br>
          Tu pourras tout modifier après dans les paramètres. Ce wizard configure juste l'essentiel pour démarrer.
        </div>
      </div>
      <div id="preflight-results" class="setup-preflight" style="margin:1em 0;font-size:.9em;opacity:.7">
        <i data-lucide="loader" style="width:14px;height:14px;animation:spin 1s linear infinite"></i> Vérification du système...
      </div>
      <div class="setup-nav">
        <button class="setup-btn setup-btn-primary" id="setup-next">Commencer <i data-lucide="arrow-right" style="width:16px;height:16px;vertical-align:middle"></i></button>
      </div>
    </div>`;
  cont.querySelector('#setup-next').onclick = () => _goNext();
  // P3: Preflight — async check du système
  _loadPreflight();
}

async function _loadPreflight() {
  const box = document.getElementById('preflight-results');
  if (!box) return;
  try {
    const _pfh={};if(window.ADMIN_TOKEN)_pfh['Authorization']=`Bearer ${window.ADMIN_TOKEN}`;
    const r = await fetch('/api/preflight',{headers:_pfh});
    if (!r.ok) { box.textContent = ''; return; }
    const data = await r.json();
    const critical = (data.components || []).filter(c => c.details?.required !== false);
    const optional = (data.components || []).filter(c => c.details?.required === false);
    let html = '';
    for (const c of critical) {
      const icon = c.healthy ? '<span style="color:var(--ok)">✓</span>' : '<span style="color:var(--danger)">×</span>';
      html += `<div>${icon} ${c.message}</div>`;
    }
    if (optional.length) {
      html += '<div style="margin-top:.5em"><small style="opacity:.6">Composants optionnels :</small></div>';
      for (const c of optional) {
        const icon = c.healthy ? '<span style="color:var(--ok)">✓</span>' : '<span style="color:var(--muted)">—</span>';
        const hint = (!c.healthy && c.details?.hint) ? ` <span style="opacity:.5;font-size:.85em">— ${c.details.hint}</span>` : '';
        html += `<div>${icon} ${c.message}${hint}</div>`;
      }
    }
    box.innerHTML = html;
    // Info seulement — on ne bloque jamais le wizard (c'est son rôle de configurer)
    const critFail = critical.filter(c => !c.healthy);
    if (critFail.length > 0) {
      const btn = document.getElementById('setup-next');
      if (btn) { btn.title = 'Certains composants nécessitent attention'; }
    }
  } catch { box.textContent = ''; }
}

// ─── Model step ───────────────────────────────────────────────────
function _renderModelStep(cont, step) {
  const field = step.fields[0];
  const options = field ? field.options || [] : [];

  const groups = { 'Gratuits (NVIDIA NIM)': [], 'Payants (Cloud)': [] };
  for (const opt of options) {
    const info = _modelsInfo[opt];
    if (!info) { groups['Payants (Cloud)'].push(opt); continue; }
    // Les modèles Ollama locaux sont dans le catalogue en bas — pas besoin de les dupliquer ici
    if (info.provider && info.provider.includes('Local')) continue;
    if (info.provider && info.provider.includes('NVIDIA')) groups['Gratuits (NVIDIA NIM)'].push(opt);
    else groups['Payants (Cloud)'].push(opt);
  }

  // Sub-brand order for "Payants (Cloud)" — ensures brands stay consecutive
  const _brandOrder = ['DeepSeek', 'OpenAI', 'Anthropic', 'Google', 'Mistral', 'Moonshot', 'xAI'];
  const _brandOf = (m) => (_modelsInfo[m] || {}).provider || 'Autre';
  groups['Payants (Cloud)'].sort((a, b) => {
    const ia = _brandOrder.indexOf(_brandOf(a));
    const ib = _brandOrder.indexOf(_brandOf(b));
    if (ia !== ib) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return groups['Payants (Cloud)'].indexOf(a) - groups['Payants (Cloud)'].indexOf(b);
  });

  // Provider accent colors
  const _dotColor = (provider) => {
    if (!provider) return '#6c63ff';
    const p = provider.toLowerCase();
    if (p.includes('nvidia')) return '#76b900';
    if (p.includes('deepseek')) return '#4a9eff';
    if (p.includes('openai')) return '#10a37f';
    if (p.includes('anthropic')) return '#d4a574';
    if (p.includes('google')) return '#4285f4';
    if (p.includes('mistral')) return '#f7501e';
    if (p.includes('moonshot')) return '#ff6b9d';
    if (p.includes('xai')) return '#fff';
    if (p.includes('ollama') || p.includes('local')) return '#f59e0b';
    return '#6c63ff';
  };

  const _groupAttr = (groupName) => {
    if (groupName.toLowerCase().includes('gratuit') || groupName.toLowerCase().includes('nvidia')) return 'free';
    if (groupName.toLowerCase().includes('local')) return 'local';
    return 'paid';
  };

  let modelsHtml = '';
  for (const [groupName, models] of Object.entries(groups)) {
    if (!models.length) continue;
    modelsHtml += `<div class="setup-model-group-label" data-group="${_groupAttr(groupName)}">${_esc(groupName)}</div><div class="setup-models">`;

    // For "Payants (Cloud)", inject a brand sub-separator when the brand changes
    let lastBrand = null;
    for (const m of models) {
      const info = _modelsInfo[m] || {};
      const thisBrand = info.provider || '';
      if (groupName === 'Payants (Cloud)' && thisBrand !== lastBrand && lastBrand !== null) {
        modelsHtml += `</div><div class="setup-models-brand-sep" style="width:100%;border-top:1px solid #2a2a3a;margin:4px 0 6px"></div><div class="setup-models">`;
      }
      lastBrand = thisBrand;

      const selected = _config[field.key] === m ? ' selected' : '';
      const _badgeClass = (b) => {
        if (b === 'Recommandé') return ' recommended';
        if (b === 'Gratuit') return ' free';
        return '';  // neutral style for Legacy, Reasoning, Fallback, beta
      };
      const badge = info.badge ? `<span class="setup-badge${_badgeClass(info.badge)}">${_esc(info.badge)}</span>` : '';
      const costClass = info.cost && info.cost.toLowerCase().includes('gratuit') ? 'cost-free' : 'cost-paid';
      const dot = _dotColor(info.provider);
      modelsHtml += `
        <div class="setup-model-card${selected}" data-model="${_esc(m)}">
          <div class="model-header">
            <span class="model-dot" style="background:${dot}"></span>
            <span class="model-name">${_esc(m)}</span>
            ${badge}
          </div>
          <div class="model-provider">${_esc(info.provider || '')}</div>
          <div class="model-desc">${_esc(info.desc || '')}</div>
          <div class="model-footer">
            <span class="model-cost ${costClass}">${_esc(info.cost || '')}</span>
          </div>
        </div>`;
    }
    modelsHtml += '</div>';
  }

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="${step.icon || 'brain'}"></i></div>
      <h2>${_esc(step.title)}</h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      <div class="models-scroll-container">
        ${modelsHtml}
        <div id="ollama-pull-section" data-field-key="${_esc(field ? field.key : '')}"></div>
      </div>
      <div id="model-hint" class="setup-hint" style="text-align:center;margin-top:8px;color:var(--warning,#f2c94c)">Sélectionne un modèle pour continuer</div>
      ${_navHtml(false)}
    </div>`;

  // P1.3: Disable Next button until a model is selected
  const nextBtn = cont.querySelector('#setup-next');
  if (nextBtn && !_config[field.key]) {
    nextBtn.disabled = true;
    nextBtn.style.opacity = '0.5';
  }

  _bindNav(cont);
  for (const card of cont.querySelectorAll('.setup-model-card')) {
    card.onclick = () => {
      cont.querySelectorAll('.setup-model-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      _config[field.key] = card.dataset.model;
      // P1.3: Enable Next once a model is selected
      const btn = cont.querySelector('#setup-next');
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
      const hint = cont.querySelector('#model-hint');
      if (hint) hint.style.display = 'none';
    };
  }
  // P8: Ollama auto-pull section
  _loadOllamaModels();
}

// ─── Ollama auto-pull (P8) ────────────────────────────────────────
async function _loadOllamaModels() {
  const sec = document.getElementById('ollama-pull-section');
  if (!sec) return;
  try {
    const res = await fetch('/api/setup/ollama-models');
    if (!res.ok) { sec.innerHTML = ''; return; }
    const data = await res.json();
    const ollamaOk = !!data.ollama_available;
    const catalog = data.catalog || [];
    if (!catalog.length) { sec.innerHTML = ''; return; }

    // Group by size tier
    const tiers = [
      { label: 'Tiny — CPU/GPU léger (< 2 GB)', filter: m => ['0.6B','1B','1.7B','2.3B','3B','3.8B','4B','4.5B','137M'].includes(m.params) && m.category !== 'embedding' },
      { label: 'Small — GPU 6 GB (7-12B)', filter: m => ['7B','8B','12B'].includes(m.params) },
      { label: 'Medium — GPU 12-16 GB (14-27B)', filter: m => ['14B','22B','24B','26B','27B'].includes(m.params) },
      { label: 'Large — GPU 24 GB+ (31-70B)', filter: m => ['31B','32B','35B','70B'].includes(m.params) },
      { label: 'XL — Multi-GPU / Serveur (100B+)', filter: m => ['235B','671B'].includes(m.params) },
      { label: 'Embedding — RAG et recherche', filter: m => m.category === 'embedding' },
    ];

    // Header
    let html = `<div style="margin-top:20px;border-top:1px solid rgba(255,255,255,.08);padding-top:16px">
      <h3 style="font-size:15px;margin-bottom:6px;display:flex;align-items:center;gap:6px">
        <i data-lucide="hard-drive-download" style="width:18px;height:18px"></i> Catalogue Ollama — modèles locaux
      </h3>`;

    if (!ollamaOk) {
      html += `<div style="background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.15);border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:12px;display:flex;align-items:center;gap:8px">
        <i data-lucide="alert-triangle" style="width:16px;height:16px;color:#fbbf24;flex-shrink:0"></i>
        <span><b>Ollama non détecté.</b> <a href="https://ollama.com/download" target="_blank" rel="noopener" style="color:var(--accent)">Installe Ollama</a> pour télécharger et utiliser ces modèles gratuitement sur ta machine.</span>
      </div>`;
    }

    html += `<div style="font-size:11px;opacity:.45;margin-bottom:10px;line-height:1.5">
      Un <b>GPU NVIDIA</b> est recommandé. Les modèles Tiny (&lt;4B) tournent aussi sur CPU.${ollamaOk ? ' Coche ceux que tu veux télécharger.' : ''}
    </div>`;

    for (const tier of tiers) {
      const models = catalog.filter(tier.filter);
      if (!models.length) continue;
      html += `<div style="margin-top:10px;margin-bottom:3px;font-size:11px;font-weight:600;opacity:.5;text-transform:uppercase;letter-spacing:.5px">${tier.label}</div>`;
      for (const m of models) {
        const isInstalled = !!m.installed;
        const canCheck = ollamaOk && !isInstalled;
        const checked = isInstalled ? 'checked' : '';
        const disabled = !canCheck ? 'disabled' : '';
        const opacity = !ollamaOk && !isInstalled ? 'opacity:.5;' : '';
        const selectAttr = isInstalled ? `class="ollama-selectable" data-select="${_esc(m.id)}" title="Cliquer pour sélectionner comme cerveau principal"` : 'class=""';
        const tag = isInstalled
          ? '<span style="color:#34d399;font-size:10px;white-space:nowrap">&#10003; installé</span>'
          : `<span style="opacity:.4;font-size:10px;white-space:nowrap">${_esc(m.size || '')}</span>`;
        const vramHint = m.vram && !isInstalled ? `<span style="opacity:.3;font-size:9px;margin-left:2px;white-space:nowrap">${_esc(m.vram)}</span>` : '';
        const catBadge = m.category === 'code' ? '<span style="background:rgba(52,211,153,.1);color:#34d399;font-size:8px;padding:1px 4px;border-radius:3px;margin-left:3px">CODE</span>'
          : m.category === 'vision' ? '<span style="background:rgba(99,102,241,.1);color:#818cf8;font-size:8px;padding:1px 4px;border-radius:3px;margin-left:3px">VISION</span>'
          : m.category === 'embedding' ? '<span style="background:rgba(251,191,36,.1);color:#fbbf24;font-size:8px;padding:1px 4px;border-radius:3px;margin-left:3px">EMBED</span>'
          : '';
        html += `<label ${selectAttr} style="display:flex;align-items:center;gap:6px;padding:3px 0;cursor:${isInstalled || canCheck ? 'pointer' : 'default'};${opacity}">
          <input type="checkbox" class="ollama-model-cb" data-model="${_esc(m.id)}" ${checked} ${disabled}
                 style="width:14px;height:14px;accent-color:var(--accent);flex-shrink:0">
          <span style="font-weight:500;font-size:12px;min-width:140px">${_esc(m.id)}</span>
          <span style="opacity:.4;font-size:10px">${_esc(m.params || '')}</span>
          ${catBadge}
          <span style="flex:1"></span>
          ${tag} ${vramHint}
        </label>
        <div class="ollama-progress" data-progress-model="${_esc(m.id)}" hidden style="margin:1px 0 6px 22px">
          <div style="background:rgba(255,255,255,.08);border-radius:4px;height:5px;overflow:hidden">
            <div class="ollama-bar" style="height:100%;background:var(--accent);width:0%;transition:width .3s"></div>
          </div>
          <div class="ollama-status" style="font-size:10px;opacity:.6;margin-top:1px"></div>
        </div>`;
      }
    }
    html += '</div>';
    sec.innerHTML = html;
    if (window.lucide) lucide.createIcons();

    // Make installed models clickable to select as brain
    const fieldKey = sec.dataset.fieldKey || 'default_model';
    for (const lbl of sec.querySelectorAll('label.ollama-selectable')) {
      lbl.onclick = (e) => {
        if (e.target.type === 'checkbox') return;
        const modelId = lbl.dataset.select;
        if (!modelId) return;
        // Convert ollama model_id to AVAILABLE_MODELS key (e.g. "qwen3:8b" → "qwen3-8b")
        const key = modelId.replace(/:/g, '-').replace(/\//g, '-');
        _config[fieldKey] = key;
        // Visual: deselect all cards, mark this row
        const step = sec.closest('.setup-step');
        step?.querySelectorAll('.setup-model-card').forEach(c => c.classList.remove('selected'));
        sec.querySelectorAll('label.ollama-selectable').forEach(l => l.style.background = '');
        lbl.style.background = 'rgba(108,99,255,.12)';
        lbl.style.borderRadius = '6px';
        // Enable Next
        const btn = step?.querySelector('#setup-next');
        if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
        const hint = step?.querySelector('#model-hint');
        if (hint) hint.style.display = 'none';
      };
    }

    if (!ollamaOk) return;  // No pull hooks if Ollama not available

    // Hook into the "Next" button to trigger pull before advancing
    const nextBtn = sec.closest('.setup-step')?.querySelector('#setup-next');
    if (nextBtn) {
      const origClick = nextBtn.onclick;
      nextBtn.onclick = async (e) => {
        e.preventDefault();
        const toPull = [...sec.querySelectorAll('.ollama-model-cb:checked:not(:disabled)')]
          .map(cb => cb.dataset.model);
        if (toPull.length) {
          nextBtn.disabled = true;
          nextBtn.textContent = 'Téléchargement...';
          for (const name of toPull) await _pullOllamaModel(name, sec);
          nextBtn.disabled = false;
          nextBtn.textContent = 'Suivant';
        }
        if (origClick) origClick.call(nextBtn, e);
        else _goNext();
      };
    }
  } catch {
    sec.innerHTML = '';
  }
}

async function _pullOllamaModel(name, container) {
  const pDiv = container.querySelector(`[data-progress-model="${CSS.escape(name)}"]`);
  if (pDiv) pDiv.hidden = false;
  const bar = pDiv?.querySelector('.ollama-bar');
  const status = pDiv?.querySelector('.ollama-status');
  try {
    const res = await fetch('/api/setup/ollama-pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: name })
    });
    if (!res.ok) {
      if (status) status.textContent = `Erreur ${res.status}`;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const ev = JSON.parse(line.slice(6));
          if (ev.percent != null && bar) bar.style.width = ev.percent + '%';
          if (ev.status && status) status.textContent = ev.status;
          if (ev.done) {
            if (bar) bar.style.width = '100%';
            if (status) status.textContent = 'Installé';
            const cb = container.querySelector(`[data-model="${CSS.escape(name)}"]`);
            if (cb) { cb.checked = true; cb.disabled = true; }
          }
        } catch { /* ignore malformed SSE */ }
      }
    }
  } catch (err) {
    if (status) status.textContent = 'Erreur réseau';
  }
}

// ─── Keys step ────────────────────────────────────────────────────
function _renderKeysStep(cont, step) {
  let providersHtml = '';
  const providers = step.providers || [];

  for (const p of providers) {
    const val = _config[p.key] || '';
    const badge = p.badge ? `<span class="setup-badge${p.badge.includes('Gratuit') ? ' free' : ''}">${_esc(p.badge)}</span>` : '';
    const stepsHtml = (p.steps || []).map((s, i) =>
      `<div class="setup-guide-step"><span class="step-num">${i + 1}</span> ${_esc(s)}</div>`
    ).join('');

    providersHtml += `
      <div class="setup-provider-card${val ? ' has-key' : ''}">
        <div class="provider-header" data-provider="${_esc(p.key)}">
          <div class="provider-title">${_esc(p.name)} ${badge}</div>
          <div class="provider-cost">${_esc(p.cost || '')}</div>
          <i data-lucide="chevron-down" class="provider-chevron" style="width:18px;height:18px"></i>
        </div>
        <div class="provider-body" id="body-${_esc(p.key)}" hidden>
          <div class="setup-guide-box">
            <strong>Comment obtenir ta clé :</strong>
            ${stepsHtml}
            <a href="${_esc(p.url || '#')}" target="_blank" rel="noopener" class="setup-link-btn">
              <i data-lucide="external-link" style="width:14px;height:14px"></i> Ouvrir ${_esc(p.name)}
            </a>
          </div>
          <div class="setup-field">
            <label>Clé API ${_esc(p.name)}</label>
            <div class="setup-key-input-row">
              <input type="password" data-setup-key="${_esc(p.key)}" value="${_esc(val)}"
                     placeholder="${_esc(p.prefix || '')}..." autocomplete="off">
              <button type="button" class="setup-eye-btn" data-target="${_esc(p.key)}">
                <i data-lucide="eye" style="width:16px;height:16px"></i>
              </button>
            </div>
            <div class="setup-key-status" data-status-key="${_esc(p.key)}"></div>
          </div>
        </div>
      </div>`;
  }

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="key-round"></i></div>
      <h2>${_esc(step.title)}</h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      <div class="setup-providers">${providersHtml}</div>
      ${_navHtml(false, 'Suivant')}
    </div>`;

  _bindNav(cont);

  // Accordion toggle
  for (const header of cont.querySelectorAll('.provider-header')) {
    header.onclick = () => {
      const body = document.getElementById('body-' + header.dataset.provider);
      if (!body) return;
      const wasHidden = body.hasAttribute('hidden');
      cont.querySelectorAll('.provider-body').forEach(b => b.setAttribute('hidden', ''));
      cont.querySelectorAll('.provider-header').forEach(h => h.classList.remove('open'));
      if (wasHidden) {
        body.removeAttribute('hidden');
        header.classList.add('open');
        requestAnimationFrame(() => {
          header.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        });
      }
      if (typeof lucide !== 'undefined') lucide.createIcons();
    };
  }

  // Eye toggle
  for (const btn of cont.querySelectorAll('.setup-eye-btn')) {
    btn.onclick = (e) => {
      e.stopPropagation();
      const input = cont.querySelector(`[data-setup-key="${btn.dataset.target}"]`);
      if (!input) return;
      input.type = input.type === 'password' ? 'text' : 'password';
    };
  }

  // Key input binding + live validation
  for (const p of providers) {
    const input = cont.querySelector(`[data-setup-key="${p.key}"]`);
    if (!input) continue;
    input.oninput = () => {
      _config[p.key] = input.value;
      const card = input.closest('.setup-provider-card');
      if (card) card.classList.toggle('has-key', !!input.value);
      // P2.1: Debounce live validation to avoid flooding the server
      clearTimeout(_keyDebounceTimer);
      _keyDebounceTimer = setTimeout(() => _validateKeyLive(p.key, input.value, cont), 600);
    };
  }
}

async function _validateKeyLive(key, value, cont) {
  const statusEl = cont.querySelector(`[data-status-key="${key}"]`);
  if (!statusEl) return;
  if (!value || value.length < 5) { statusEl.innerHTML = ''; return; }

  statusEl.innerHTML = '<span class="key-checking">Vérification...</span>';
  try {
    const res = await fetch('/api/setup/test-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: key.replace('_API_KEY', '').toLowerCase(), key: value }),
    });
    const data = await res.json();
    statusEl.innerHTML = data.success
      ? `<span class="key-valid"><i data-lucide="check-circle" style="width:14px;height:14px"></i> ${_esc(data.message || 'Clé valide')}</span>`
      : `<span class="key-invalid"><i data-lucide="x-circle" style="width:14px;height:14px"></i> ${_esc(data.error || 'Format incorrect')}</span>`;
  } catch {
    statusEl.innerHTML = '';
  }
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── Security & Alerts step ───────────────────────────────────────
function _renderSecurityStep(cont, step) {
  const field = step.fields[0] || {};
  const val = _config[field.key] || '';
  const alertFields = step.alert_fields || [];
  const hostField = step.host_field || null;

  let alertHtml = '';
  for (const f of alertFields) {
    if (f.type === 'bool') {
      const on = (_config[f.key] || f.default) === '1';
      alertHtml += `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">
        <span style="font-size:13px;color:var(--text,#e6e6e6)">${_esc(f.label)}</span>
        <button class="setup-toggle${on ? ' on' : ''}" data-setup-key="${_esc(f.key)}" type="button"></button>
      </div>`;
    } else if (f.type === 'text' || f.type === 'secret') {
      const v = _config[f.key] || f.default || '';
      alertHtml += `<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">
        <label style="font-size:12px;color:var(--muted,#8c8c9a);display:block;margin-bottom:4px">${_esc(f.label)}</label>
        <input type="text" data-setup-key="${_esc(f.key)}" value="${_esc(v)}" placeholder="${_esc(f.hint || '')}" style="width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 12px;color:var(--text,#e6e6e6);font-size:13px">
      </div>`;
    } else {
      const v = _config[f.key] || f.default || '';
      alertHtml += `<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">
        <label style="font-size:12px;color:var(--muted,#8c8c9a);display:block;margin-bottom:4px">${_esc(f.label)}</label>
        <input type="number" data-setup-key="${_esc(f.key)}" value="${_esc(v)}" min="0" style="width:100px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 12px;color:var(--text,#e6e6e6);font-size:13px">
      </div>`;
    }
  }

  cont.innerHTML = `
    <div class="setup-step active" style="display:flex;flex-direction:column;max-height:82vh;overflow:hidden">
      <div style="flex-shrink:0">
        <div class="setup-step-icon"><i data-lucide="shield"></i></div>
        <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
        <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
        <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      </div>

      <div class="models-scroll-container" style="margin-top:8px;flex:1;min-height:0;max-height:none">
        <div style="display:flex;flex-direction:column;gap:16px">
          <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="lock" style="width:14px;height:14px;color:#f59f4a"></i> Token admin</div>
            <div class="setup-field" style="margin:0">
              <div class="setup-key-input-row">
                <input type="password" data-setup-key="${_esc(field.key)}" value="${_esc(val)}"
                       placeholder="${_esc(field.placeholder || 'mon-secret-2026')}" autocomplete="off">
                <button type="button" class="setup-eye-btn" id="sec-eye">
                  <i data-lucide="eye" style="width:16px;height:16px"></i>
                </button>
                <button type="button" class="setup-generate-btn" id="sec-gen">
                  <i data-lucide="refresh-cw" style="width:14px;height:14px"></i> Générer
                </button>
              </div>
              <div class="setup-hint">${_esc(field.hint || '')}</div>
            </div>
          </div>
          ${alertFields.length ? `<div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="bell" style="width:14px;height:14px;color:#f59f4a"></i> Alertes critiques</div>
            <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Reçois un message Telegram si quelque chose de grave se passe (crash, panne, intrusion). Utilise le token Telegram que tu as renseigné juste avant.</div>
            <div style="border:1px solid rgba(255,255,255,.09);border-radius:10px;padding:8px 14px">${alertHtml}</div>
          </div>` : ''}
          ${hostField ? `<div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="network" style="width:14px;height:14px;color:#f59f4a"></i> Accès réseau</div>
            <div class="setup-field" style="margin:0">
              <label>${_esc(hostField.label)}</label>
              <select data-setup-key="${_esc(hostField.key)}">
                ${(hostField.options || []).map(o => {
                  const val = typeof o === 'object' ? o.value : o;
                  const label = typeof o === 'object' ? o.label : o;
                  const sel = (_config[hostField.key] || hostField.default || '0.0.0.0') === val ? ' selected' : '';
                  return `<option value="${_esc(val)}"${sel}>${_esc(label)}</option>`;
                }).join('')}
              </select>
              <div class="setup-hint">${_esc(hostField.hint || '')}</div>
            </div>
          </div>` : ''}
        </div>
      </div>

      <div style="flex-shrink:0;margin-top:12px">
        ${step.tip ? `<div class="setup-tip"><i data-lucide="info" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.tip)}</div>` : ''}
        ${_navHtml(true, 'Passer')}
      </div>
    </div>`;

  _bindNav(cont);

  const input = cont.querySelector(`[data-setup-key="${field.key}"]`);
  if (input) input.oninput = () => { _config[field.key] = input.value; };
  const eyeBtn = cont.querySelector('#sec-eye');
  if (eyeBtn && input) eyeBtn.onclick = () => { input.type = input.type === 'password' ? 'text' : 'password'; };
  const genBtn = cont.querySelector('#sec-gen');
  if (genBtn && input) {
    genBtn.onclick = () => {
      const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_';
      const arr = new Uint8Array(32);
      crypto.getRandomValues(arr);
      const token = Array.from(arr, b => chars[b % chars.length]).join('');
      input.value = token;
      input.type = 'text';
      _config[field.key] = token;
    };
    // P8: auto-generate token if empty on first render
    if (!input.value.trim()) genBtn.click();
  }

  // Alert toggles + inputs
  if (hostField) {
    const hostSel = cont.querySelector(`select[data-setup-key="${hostField.key}"]`);
    if (hostSel) hostSel.onchange = () => { _config[hostField.key] = hostSel.value; };
  }
  for (const el of cont.querySelectorAll('.setup-toggle[data-setup-key]')) {
    if (el === cont.querySelector(`[data-setup-key="${field.key}"]`)) continue;
    el.onclick = () => { const on = el.classList.toggle('on'); _config[el.dataset.setupKey] = on ? '1' : '0'; };
  }
  for (const el of cont.querySelectorAll('input[data-setup-key]')) {
    if (el === input) continue;
    el.oninput = () => { _config[el.dataset.setupKey] = el.value; };
  }
}

// ─── Telegram step ────────────────────────────────────────────────
function _renderTelegramStep(cont, step) {
  const field = step.fields[0] || {};
  const val = _config[field.key] || '';

  const guideHtml = (step.guide_steps || []).map((s, i) =>
    `<div class="setup-guide-step"><span class="step-num">${i + 1}</span> ${_esc(s)}</div>`
  ).join('');

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="send"></i></div>
      <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      <div class="setup-guide-box">
        <strong>${_esc(step.tip || 'Comment faire :')}</strong>
        ${guideHtml}
        <a href="https://t.me/BotFather" target="_blank" rel="noopener" class="setup-link-btn">
          <i data-lucide="external-link" style="width:14px;height:14px"></i> Ouvrir @BotFather sur Telegram
        </a>
      </div>
      <div class="setup-field">
        <label>${_esc(field.label || 'Token Telegram')}</label>
        <input type="password" data-setup-key="${_esc(field.key)}" value="${_esc(val)}"
               placeholder="123456789:ABCdef..." autocomplete="off">
        <div class="setup-hint">${_esc(field.hint || '')}</div>
      </div>
      ${_navHtml(true, 'Passer')}
    </div>`;

  _bindNav(cont);
  const input = cont.querySelector(`[data-setup-key="${field.key}"]`);
  if (input) input.oninput = () => { _config[field.key] = input.value; };
}

// P0.5: Twitter step — shows guide_steps and all 5 OAuth fields
function _renderTwitterStep(cont, step) {
  const guideHtml = (step.guide_steps || []).map((s, i) =>
    `<div class="setup-guide-step"><span class="step-num">${i + 1}</span> ${_esc(s)}</div>`
  ).join('');
  const guideUrl = step.guide_url || 'https://developer.x.com/en/portal/dashboard';

  let fieldsHtml = '';
  for (const f of (step.fields || [])) {
    const val = _config[f.key] || '';
    fieldsHtml += `<div class="setup-field" style="margin-bottom:10px">
      <label>${_esc(f.label)}</label>
      <input type="password" data-twitter-key="${_esc(f.key)}" value="${_esc(val)}"
             placeholder="${_esc(f.hint || '')}" autocomplete="off">
    </div>`;
  }

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="twitter"></i></div>
      <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-guide-box">
        <strong>${_esc(step.tip || 'Comment obtenir les clés Twitter/X :')}</strong>
        ${guideHtml}
        <a href="${_esc(guideUrl)}" target="_blank" rel="noopener" class="setup-link-btn">
          <i data-lucide="external-link" style="width:14px;height:14px"></i> Ouvrir le Developer Portal
        </a>
      </div>
      ${fieldsHtml}
      ${_navHtml(true, 'Passer')}
    </div>`;

  _bindNav(cont);
  for (const f of (step.fields || [])) {
    const el = cont.querySelector(`[data-twitter-key="${f.key}"]`);
    if (el) el.oninput = () => { _config[f.key] = el.value; };
  }
}

// WhatsApp step — shows guide_steps and all config fields
function _renderWhatsAppStep(cont, step) {
  const guideHtml = (step.guide_steps || []).map((s, i) =>
    `<div class="setup-guide-step"><span class="step-num">${i + 1}</span> ${_esc(s)}</div>`
  ).join('');
  const guideUrl = 'https://developers.facebook.com/apps';

  let fieldsHtml = '';
  for (const f of (step.fields || [])) {
    const val = _config[f.key] || '';
    const inputType = f.type === 'secret' ? 'password' : 'text';
    fieldsHtml += `<div class="setup-field" style="margin-bottom:10px">
      <label>${_esc(f.label)}</label>
      <input type="${inputType}" data-wa-key="${_esc(f.key)}" value="${_esc(val)}"
             placeholder="${_esc(f.hint || '')}" autocomplete="off">
    </div>`;
  }

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="message-circle"></i></div>
      <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-guide-box">
        <strong>${_esc(step.tip || 'Comment configurer WhatsApp Business :')}</strong>
        ${guideHtml}
        <a href="${_esc(guideUrl)}" target="_blank" rel="noopener" class="setup-link-btn">
          <i data-lucide="external-link" style="width:14px;height:14px"></i> Ouvrir Meta for Developers
        </a>
      </div>
      ${fieldsHtml}
      ${_navHtml(true, 'Passer')}
    </div>`;

  _bindNav(cont);
  for (const f of (step.fields || [])) {
    const el = cont.querySelector(`[data-wa-key="${f.key}"]`);
    if (el) el.oninput = () => { _config[f.key] = el.value; };
  }
}

// P0.6: Voice step — shows STT device select from backend options
function _renderVoiceStep(cont, step) {
  const devices = step.stt_device_options || [];
  let fieldsHtml = '';
  for (const f of (step.fields || [])) {
    const val = _config[f.key] || f.default || '';
    if (f.key === 'LUMENA_STT_DEVICE' && devices.length) {
      const opts = devices.map(d =>
        `<option value="${_esc(d.value || d)}"${val === (d.value || d) ? ' selected' : ''}>${_esc(d.label || d)}</option>`
      ).join('');
      fieldsHtml += `<div class="setup-field" style="margin-bottom:10px">
        <label>${_esc(f.label)}</label>
        <select data-voice-key="${_esc(f.key)}">${opts}</select>
        <div class="setup-hint">${_esc(f.hint || '')}</div>
      </div>`;
    } else {
      fieldsHtml += `<div class="setup-field" style="margin-bottom:10px">
        <label>${_esc(f.label)}</label>
        <input type="${f.type === 'bool' ? 'checkbox' : 'text'}" data-voice-key="${_esc(f.key)}" value="${_esc(val)}"
               autocomplete="off">
        <div class="setup-hint">${_esc(f.hint || '')}</div>
      </div>`;
    }
  }

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="${step.icon || 'mic'}"></i></div>
      <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      ${fieldsHtml}
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin:12px 0">
        <button type="button" class="btn" id="setup-test-micro"><i data-lucide="mic"></i> Tester le micro</button>
        <button type="button" class="btn" id="setup-test-voice"><i data-lucide="audio-lines"></i> Tester la voix</button>
        <span id="setup-voice-test-status" class="setup-hint" style="align-self:center"></span>
      </div>
      ${_navHtml(true, 'Passer')}
    </div>`;

  _bindNav(cont);
  for (const f of (step.fields || [])) {
    const el = cont.querySelector(`[data-voice-key="${f.key}"]`);
    if (el) el.oninput = () => { _config[f.key] = el.tagName === 'SELECT' ? el.value : el.value; };
  }
  const status=cont.querySelector('#setup-voice-test-status');
  const auth=()=>{const h={};if(window.ADMIN_TOKEN)h.Authorization=`Bearer ${window.ADMIN_TOKEN}`;return h;};
  cont.querySelector('#setup-test-micro').onclick=async()=>{status.textContent='Test micro en cours…';try{const r=await fetch('/api/voice/test-micro',{method:'POST',headers:auth()});const d=await r.json();status.textContent=r.ok&&d.ok?'Micro opérationnel':'Micro indisponible ou trop silencieux';}catch(e){status.textContent='Test micro impossible';}};
  cont.querySelector('#setup-test-voice').onclick=async()=>{status.textContent='Test voix en cours…';try{const r=await fetch('/api/voice/test-output',{method:'POST',headers:auth()});status.textContent=r.ok?'Phrase de test jouée':'Démarre Voice V2 puis réessaie';}catch(e){status.textContent='Test voix impossible';}};
}

// ─── Integrations step ───────────────────────────────────────────
const _EMAIL_GUIDE_URLS = {
  'gmail.com':      'https://myaccount.google.com/apppasswords',
  'googlemail.com': 'https://myaccount.google.com/apppasswords',
  'outlook.com':    'https://support.microsoft.com/fr-fr/office/utiliser-l-authentification-en-deux-cpt',
  'hotmail.com':    'https://support.microsoft.com/fr-fr/office/utiliser-l-authentification-en-deux-cpt',
  'live.com':       'https://support.microsoft.com/fr-fr/office/utiliser-l-authentification-en-deux-cpt',
  'protonmail.com': 'https://proton.me/support/imap-smtp-and-pop3-setup',
  'pm.me':          'https://proton.me/support/imap-smtp-and-pop3-setup',
};
function _emailGuideUrl(email) {
  const domain = (email.split('@')[1] || '').toLowerCase();
  return _EMAIL_GUIDE_URLS[domain] || 'https://myaccount.google.com/apppasswords';
}

function _renderIntegrationsStep(cont, step) {
  const integrations = step.integrations || [];

  let cardsHtml = '';
  for (const integ of integrations) {
    const hasValue = integ.fields.some(f => !!_config[f.key]);
    const statusDot = hasValue
      ? '<span style="width:8px;height:8px;border-radius:50%;background:var(--ok,#22c55e);flex-shrink:0"></span>'
      : '<span style="width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.15);flex-shrink:0"></span>';

    const isEmail = integ.key === 'LUMENA_EMAIL';

    let fieldsHtml = '';
    for (const f of integ.fields) {
      const val = _config[f.key] || '';
      const dataAttr = isEmail ? `data-email-field="${_esc(f.key)}"` : '';

      let inputHtml;
      if (f.type === 'select' && Array.isArray(f.options)) {
        const opts = f.options.map(o =>
          `<option value="${_esc(o.value)}"${val === o.value ? ' selected' : ''}>${_esc(o.label)}</option>`
        ).join('');
        inputHtml = `<select data-integ-key="${_esc(f.key)}" autocomplete="off"
          style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(40,40,54,.95);color:var(--text,#e6e6e6);font-size:13px;box-sizing:border-box">${opts}</select>`;
      } else {
        const inputType = f.type === 'secret' ? 'password' : 'text';
        inputHtml = `<input type="${inputType}" data-integ-key="${_esc(f.key)}" ${dataAttr} value="${_esc(val)}" placeholder="${_esc(f.hint || '')}" autocomplete="off"
          style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);color:var(--text,#e6e6e6);font-size:13px;box-sizing:border-box">`;
      }

      fieldsHtml += `<div style="margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;color:var(--text,#e6e6e6);margin-bottom:4px">${_esc(f.label)}</div>
        ${inputHtml}
        ${f.hint && f.type !== 'select' ? `<div style="font-size:10px;color:var(--muted,#8c8c9a);margin-top:3px">${_esc(f.hint)}</div>` : ''}
      </div>`;
    }

    const guideLabel = isEmail ? 'Comment créer un App Password' : 'Obtenir les identifiants';
    const guideHrefAttr = isEmail ? `id="email-guide-link"` : '';
    const initialGuideUrl = isEmail ? _emailGuideUrl(_config['LUMENA_EMAIL'] || '') : (integ.guide_url || '');

    cardsHtml += `<details class="auto-advanced" style="margin-bottom:10px" data-integ="${_esc(integ.key)}">
      <summary style="display:flex;align-items:center;gap:10px">
        <div style="width:36px;height:36px;border-radius:10px;background:rgba(245,159,74,.12);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="${_esc(integ.icon)}" style="width:18px;height:18px;color:#f59f4a"></i></div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px"><span style="font-size:14px;font-weight:600;color:var(--text,#e6e6e6)">${_esc(integ.name)}</span>${statusDot}</div>
          <span style="font-size:11px;color:var(--muted,#8c8c9a)">${_esc(integ.desc)}</span>
        </div>
      </summary>
      <div class="auto-adv-content" style="padding:12px 14px">
        ${fieldsHtml}
        ${integ.oauth_warning ? `<div style="margin:8px 0;padding:8px 12px;background:rgba(242,201,76,.1);border:1px solid rgba(242,201,76,.3);border-radius:8px;font-size:11px;color:var(--warn,#f2c94c);display:flex;gap:6px;align-items:flex-start"><i data-lucide="alert-triangle" style="width:14px;height:14px;flex-shrink:0;margin-top:1px"></i><span>${_esc(integ.oauth_warning)}</span></div>` : ''}
        ${initialGuideUrl ? `<a href="${_esc(initialGuideUrl)}" target="_blank" rel="noopener" class="setup-link-btn" ${guideHrefAttr} style="display:inline-flex;align-items:center;gap:6px;font-size:12px;margin-top:4px">
          <i data-lucide="external-link" style="width:12px;height:12px"></i> ${guideLabel}
        </a>` : ''}
      </div>
    </details>`;
  }

  cont.innerHTML = `
    <div class="setup-step active" style="display:flex;flex-direction:column;max-height:82vh;overflow:hidden">
      <div style="flex-shrink:0">
        <div class="setup-step-icon"><i data-lucide="plug"></i></div>
        <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
        <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
        <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      </div>

      <div class="models-scroll-container" style="margin-top:8px;flex:1;min-height:0;max-height:none">
        ${cardsHtml}
      </div>

      <div style="flex-shrink:0;margin-top:12px">
        ${_navHtml(true, 'Passer')}
      </div>
    </div>`;

  _bindNav(cont);

  // Update email guide link URL when user types their email address
  const emailInput = cont.querySelector('[data-email-field="LUMENA_EMAIL"]');
  const emailGuideLink = cont.querySelector('#email-guide-link');
  if (emailInput && emailGuideLink) {
    emailInput.oninput = () => {
      _config['LUMENA_EMAIL'] = emailInput.value;
      const url = _emailGuideUrl(emailInput.value);
      emailGuideLink.href = url;
    };
  }

  // Bind integration field inputs (skip LUMENA_EMAIL already bound above)
  for (const integ of integrations) {
    for (const f of integ.fields) {
      const el = cont.querySelector(`[data-integ-key="${f.key}"]`);
      if (el) {
        const handler = () => { _config[f.key] = el.value; };
        el.oninput = handler;
        el.onchange = handler;  // pour les <select>
      }
    }
  }
}

// ─── Shared field builder for details sections ───────────────────
function _buildFieldsHtml(fields, prefix) {
  let html = '';
  for (const f of fields) {
    if (f.type === 'bool') {
      const on = (_config[f.key] || f.default) === '1';
      html += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <button class="setup-toggle${on ? ' on' : ''}" data-${prefix}-key="${_esc(f.key)}" type="button"></button></div>`;
    } else if (f.type === 'select' && Array.isArray(f.options)) {
      // P1.6: Render select fields properly (e.g. LUMENA_SANDBOX_MODE)
      const val = _config[f.key] || f.default || '';
      const opts = f.options.map(o => {
        const optVal = typeof o === 'string' ? o : o.value;
        const optLabel = typeof o === 'string' ? o : o.label;
        return `<option value="${_esc(optVal)}"${val === optVal ? ' selected' : ''}>${_esc(optLabel)}</option>`;
      }).join('');
      html += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <select class="auto-adv-input" data-${prefix}-key="${_esc(f.key)}">${opts}</select></div>`;
    } else if (f.type === 'text') {
      // P1.6: Render text fields (e.g. LUMENA_SANDBOX_MEMORY)
      const val = _config[f.key] || f.default || '';
      html += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <input type="text" class="auto-adv-input" data-${prefix}-key="${_esc(f.key)}" value="${_esc(val)}"></div>`;
    } else {
      const val = _config[f.key] || f.default || '';
      html += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <input type="number" class="auto-adv-input" data-${prefix}-key="${_esc(f.key)}" value="${_esc(val)}" min="0"></div>`;
    }
  }
  return html;
}

function _bindFieldsEvents(cont, fields, prefix) {
  for (const f of fields) {
    const el = cont.querySelector(`[data-${prefix}-key="${f.key}"]`);
    if (!el) continue;
    if (f.type === 'bool') {
      el.onclick = () => { const on = el.classList.toggle('on'); _config[f.key] = on ? '1' : '0'; };
    } else {
      el.oninput = () => { _config[f.key] = el.value; };
    }
  }
}

// ─── Autonomy step ────────────────────────────────────────────────
function _renderAutonomyStep(cont, step) {
  const categories = step.action_categories || [];
  const actionsField = step.fields.find(f => f.key === 'LUMENA_AUTONOMY_ALLOWED_ACTIONS');
  const budgetField = step.fields.find(f => f.key === 'LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR');
  const execField = step.fields.find(f => f.key === 'LUMENA_AUTONOMY_EXECUTE_ACTIONS');
  const advFields = step.advanced_fields || [];
  const opsFields = step.ops_fields || [];

  const currentActions = (actionsField ? (_config[actionsField.key] || actionsField.default || '') : '').split(',').map(s => s.trim()).filter(Boolean);
  const execOn = execField ? (_config[execField.key] || execField.default) === '1' : true;
  const budgetVal = budgetField ? parseInt(_config[budgetField.key] || budgetField.default || '12', 10) : 12;

  let catHtml = '';
  for (const cat of categories) {
    let actHtml = '';
    for (const a of cat.actions) {
      const checked = currentActions.includes(a.key) ? ' checked' : '';
      const rBg = a.risk === 'moderate' ? 'rgba(242,201,76,.12)' : 'rgba(34,197,94,.12)';
      const rClr = a.risk === 'moderate' ? '#f2c94c' : '#22c55e';
      const rBrd = a.risk === 'moderate' ? 'rgba(242,201,76,.25)' : 'rgba(34,197,94,.25)';
      const rTxt = a.risk === 'moderate' ? 'Prudent' : 'Sûr';
      actHtml += `<div data-action-key="${_esc(a.key)}" style="display:flex;flex-direction:row;align-items:center;gap:12px;padding:11px 14px;cursor:pointer;transition:background .15s;border-bottom:1px solid rgba(255,255,255,.05)" onmouseenter="this.style.background='rgba(255,255,255,.04)'" onmouseleave="this.style.background='transparent'">
        <input type="checkbox" data-action="${_esc(a.key)}"${checked} style="accent-color:#f59f4a;width:17px;height:17px;flex-shrink:0;cursor:pointer">
        <div style="width:32px;height:32px;border-radius:8px;background:rgba(245,159,74,.12);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="${_esc(a.icon)}" style="width:15px;height:15px;color:#f59f4a"></i></div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px"><span style="font-size:13px;font-weight:600;color:var(--text,#e6e6e6)">${_esc(a.label)}</span><span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;background:${rBg};color:${rClr};border:1px solid ${rBrd}">${rTxt}</span></div>
          <span style="font-size:11px;color:var(--muted,#8c8c9a);display:block;margin-top:2px">${_esc(a.desc)}</span>
        </div>
      </div>`;
    }
    catHtml += `<div style="border:1px solid rgba(255,255,255,.09);border-radius:10px;overflow:hidden">
      <div style="display:flex;align-items:center;gap:8px;padding:10px 14px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);background:rgba(255,255,255,.02);border-bottom:1px solid rgba(255,255,255,.09)"><i data-lucide="${_esc(cat.icon)}" style="width:14px;height:14px;color:#f59f4a"></i> ${_esc(cat.name)}</div>
      <div>${actHtml}</div>
    </div>`;
  }

  const budgetLabel = budgetVal <= 4 ? 'Prudent' : budgetVal <= 15 ? 'Normal' : budgetVal <= 30 ? 'Intensif' : 'Maximum';
  const budgetColor = budgetVal <= 4 ? 'var(--ok)' : budgetVal <= 15 ? 'var(--accent)' : budgetVal <= 30 ? 'var(--warn)' : 'var(--danger)';

  let advHtml = '';
  for (const f of advFields) {
    if (f.type === 'bool') {
      const on = (_config[f.key] || f.default) === '1';
      advHtml += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <button class="setup-toggle${on ? ' on' : ''}" data-setup-key="${_esc(f.key)}" type="button"></button></div>`;
    } else {
      const val = _config[f.key] || f.default || '';
      advHtml += `<div class="auto-adv-row"><div class="auto-adv-label"><strong>${_esc(f.label)}</strong></div>
        <input type="number" class="auto-adv-input" data-setup-key="${_esc(f.key)}" value="${_esc(val)}" min="0"></div>`;
    }
  }

  cont.innerHTML = `
    <div class="setup-step active setup-autonomy-step">
      <div style="flex-shrink:0">
        <div class="setup-step-icon"><i data-lucide="zap"></i></div>
        <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
        <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
        <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>

        <div class="setup-field"><div class="setup-toggle-row">
          <div><strong>${_esc(execField ? execField.label : 'Exécution autonome')}</strong></div>
          <button class="setup-toggle${execOn ? ' on' : ''}" data-setup-key="LUMENA_AUTONOMY_EXECUTE_ACTIONS" type="button"></button>
        </div></div>

        <div class="setup-field" style="margin-bottom:0">
          <label>Actions autorisées</label>
          <div style="display:flex;gap:8px;margin:8px 0 8px;flex-wrap:wrap">
            <button type="button" data-select="all" style="font-size:11px;padding:5px 12px;border-radius:20px;border:1px solid rgba(255,255,255,.12);background:transparent;color:var(--muted,#8c8c9a);cursor:pointer">Tout activer</button>
            <button type="button" data-select="safe" style="font-size:11px;padding:5px 12px;border-radius:20px;border:1px solid rgba(255,255,255,.12);background:transparent;color:var(--muted,#8c8c9a);cursor:pointer">Sûrs uniquement</button>
            <button type="button" data-select="none" style="font-size:11px;padding:5px 12px;border-radius:20px;border:1px solid rgba(255,255,255,.12);background:transparent;color:var(--muted,#8c8c9a);cursor:pointer">Tout désactiver</button>
          </div>
        </div>
      </div>

      <div class="models-scroll-container setup-autonomy-scroll">
        <div class="setup-autonomy-actions">${catHtml}</div>
      </div>

      <div style="flex-shrink:0;margin-top:12px">
        <div class="setup-field">
          <label>Budget actions / heure
            <span class="auto-budget-tag" style="color:${budgetColor}">${_esc(budgetLabel)} — ${budgetVal}/h</span>
          </label>
          <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Nombre maximum d'actions autonomes par heure. Prudent (1-4) = rare, Normal (5-15) = équilibré, Intensif (16-30) = actif, Maximum (31+) = sans limite.</div>
          <input type="range" class="auto-budget-slider" data-setup-key="LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR" min="1" max="60" value="${budgetVal}">
          <div class="auto-budget-range"><span>1/h</span><span>60/h</span></div>
        </div>

        ${advFields.length ? `<details class="auto-advanced"><summary><i data-lucide="settings" style="width:14px;height:14px"></i> Paramètres avancés</summary><div class="auto-adv-content">${advHtml}</div></details>` : ''}

        ${opsFields.length ? `<details class="auto-advanced" style="margin-top:8px"><summary><i data-lucide="archive" style="width:14px;height:14px"></i> Maintenance & Archives</summary><div class="auto-adv-content">${_buildFieldsHtml(opsFields, 'ops')}</div></details>` : ''}

        ${(step.sandbox_fields||[]).length ? `<details class="auto-advanced" style="margin-top:8px"><summary><i data-lucide="box" style="width:14px;height:14px"></i> Sandbox Docker</summary><div class="auto-adv-content">${_buildFieldsHtml(step.sandbox_fields, 'sandbox')}</div></details>` : ''}

        ${step.tip ? `<div class="setup-tip"><i data-lucide="info" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.tip)}</div>` : ''}
        ${_navHtml(true, 'Passer')}
      </div>
    </div>`;

  _bindNav(cont);

  // Toggle exec
  const execEl = cont.querySelector('[data-setup-key="LUMENA_AUTONOMY_EXECUTE_ACTIONS"]');
  if (execEl) execEl.onclick = () => { const on = execEl.classList.toggle('on'); _config['LUMENA_AUTONOMY_EXECUTE_ACTIONS'] = on ? '1' : '0'; };

  // Action checkboxes
  const _syncActions = () => {
    const checked = [...cont.querySelectorAll('[data-action]:checked')].map(c => c.dataset.action);
    _config['LUMENA_AUTONOMY_ALLOWED_ACTIONS'] = checked.join(',');
  };
  for (const cb of cont.querySelectorAll('[data-action]')) cb.onchange = _syncActions;

  // Clicking row toggles checkbox
  for (const card of cont.querySelectorAll('[data-action-key]')) {
    card.onclick = (e) => {
      if (e.target.tagName === 'INPUT') return;
      const cb = card.querySelector('[data-action]');
      if (cb) { cb.checked = !cb.checked; _syncActions(); }
    };
  }

  // Select all / safe / none
  const safeKeys = categories.flatMap(c => c.actions.filter(a => a.risk === 'safe').map(a => a.key));
  const allKeys = categories.flatMap(c => c.actions.map(a => a.key));
  for (const btn of cont.querySelectorAll('[data-select]')) {
    btn.onclick = () => {
      const mode = btn.dataset.select;
      for (const cb of cont.querySelectorAll('[data-action]')) {
        if (mode === 'all') cb.checked = true;
        else if (mode === 'none') cb.checked = false;
        else if (mode === 'safe') cb.checked = safeKeys.includes(cb.dataset.action);
      }
      _syncActions();
    };
  }

  // Budget slider
  const slider = cont.querySelector('.auto-budget-slider');
  const budgetTag = cont.querySelector('.auto-budget-tag');
  if (slider) slider.oninput = () => {
    const v = parseInt(slider.value, 10);
    _config['LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR'] = String(v);
    const lbl = v <= 4 ? 'Prudent' : v <= 15 ? 'Normal' : v <= 30 ? 'Intensif' : 'Maximum';
    const clr = v <= 4 ? 'var(--ok)' : v <= 15 ? 'var(--accent)' : v <= 30 ? 'var(--warn)' : 'var(--danger)';
    if (budgetTag) { budgetTag.textContent = `${lbl} — ${v}/h`; budgetTag.style.color = clr; }
  };

  // Advanced fields
  for (const f of advFields) {
    const el = cont.querySelector(`[data-setup-key="${f.key}"]`);
    if (!el) continue;
    if (f.type === 'bool') {
      el.onclick = () => { const on = el.classList.toggle('on'); _config[f.key] = on ? '1' : '0'; };
    } else {
      el.oninput = () => { _config[f.key] = el.value; };
    }
  }

  // Ops fields (Maintenance & Archives)
  _bindFieldsEvents(cont, opsFields, 'ops');

  // Sandbox fields (Docker)
  _bindFieldsEvents(cont, step.sandbox_fields || [], 'sandbox');
}

// ─── Moods / Personality step ─────────────────────────────────────
function _renderMoodsStep(cont, step) {
  const traits = step.personality_traits || [];
  const moods = step.mood_options || [];
  const idFields = step.identity_fields || [];

  const currentMood = _config['LUMENA_DEFAULT_MOOD'] || 'neutral';
  const enabledMoodsStr = _config['LUMENA_ENABLED_MOODS'] || moods.map(m => m.key).join(',');
  const enabledMoods = new Set(enabledMoodsStr.split(',').filter(Boolean));
  const useEmojis = (_config['LUMENA_USE_EMOJIS'] || '1') === '1';
  const emojiFreq = parseInt(_config['LUMENA_EMOJI_FREQUENCY'] || '30', 10);

  // ── Traits with toggle + slider ──
  let traitsHtml = '';
  for (const t of traits) {
    const traitEnabled = (_config[`LUMENA_TRAIT_${t.key.toUpperCase()}_ENABLED`] || '1') === '1';
    const val = parseInt(_config[`LUMENA_TRAIT_${t.key.toUpperCase()}`] || String(t.default), 10);
    const clr = val >= 80 ? 'var(--ok,#22c55e)' : val >= 50 ? 'var(--accent,#f59f4a)' : 'var(--muted,#8c8c9a)';
    const opac = traitEnabled ? '1' : '.4';
    traitsHtml += `<div data-trait-row="${_esc(t.key)}" style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.05);opacity:${opac};transition:opacity .2s">
      <input type="checkbox" data-trait-toggle="${_esc(t.key)}"${traitEnabled ? ' checked' : ''} style="accent-color:#f59f4a;width:16px;height:16px;flex-shrink:0;cursor:pointer">
      <div style="width:32px;height:32px;border-radius:8px;background:rgba(245,159,74,.12);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="${_esc(t.icon)}" style="width:15px;height:15px;color:#f59f4a"></i></div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
          <span style="font-size:13px;font-weight:600;color:var(--text,#e6e6e6)">${_esc(t.label)}</span>
          <span data-trait-val="${_esc(t.key)}" style="font-size:12px;font-weight:700;color:${clr};min-width:36px;text-align:right">${traitEnabled ? val + '%' : 'off'}</span>
        </div>
        <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:4px">${_esc(t.desc)}</div>
        <input type="range" data-trait-key="${_esc(t.key)}" min="0" max="100" value="${val}" style="width:100%;accent-color:#f59f4a;height:4px${traitEnabled ? '' : ';pointer-events:none'}">
      </div>
    </div>`;
  }

  // ── Mood cards with checkboxes ──
  let moodHtml = '';
  for (const m of moods) {
    const checked = enabledMoods.has(m.key);
    const isDefault = m.key === currentMood;
    const bg = isDefault ? 'rgba(245,159,74,.15)' : 'transparent';
    const brd = isDefault ? 'rgba(245,159,74,.5)' : checked ? 'rgba(255,255,255,.15)' : 'rgba(255,255,255,.06)';
    const opac = checked ? '1' : '.4';
    moodHtml += `<div data-mood-key="${_esc(m.key)}" style="display:flex;flex-direction:column;align-items:center;gap:4px;padding:12px 8px;border-radius:10px;border:1px solid ${brd};background:${bg};cursor:pointer;transition:all .15s;min-width:0;flex:1;opacity:${opac};position:relative">
      <input type="checkbox" data-mood-toggle="${_esc(m.key)}"${checked ? ' checked' : ''} style="position:absolute;top:6px;right:6px;accent-color:#f59f4a;width:14px;height:14px;cursor:pointer">
      <i data-lucide="${_esc(m.icon)}" style="width:20px;height:20px;color:${isDefault ? '#f59f4a' : 'var(--text,#e6e6e6)'}"></i>
      <span style="font-size:12px;font-weight:600;color:${isDefault ? '#f59f4a' : 'var(--text,#e6e6e6)'}">${_esc(m.label)}</span>
      <span style="font-size:10px;color:var(--muted,#8c8c9a);text-align:center">${_esc(m.desc)}</span>
      ${isDefault ? '<span style="font-size:8px;font-weight:700;color:#f59f4a;text-transform:uppercase;letter-spacing:.5px;margin-top:2px">par défaut</span>' : ''}
    </div>`;
  }

  // ── Emoji prefs ──
  const emojiFreqClr = emojiFreq >= 60 ? 'var(--warn,#f2c94c)' : emojiFreq >= 20 ? 'var(--accent,#f59f4a)' : 'var(--muted,#8c8c9a)';
  const emojiFreqLabel = emojiFreq <= 10 ? 'Rare' : emojiFreq <= 30 ? 'Modéré' : emojiFreq <= 60 ? 'Fréquent' : 'Intensif';

  cont.innerHTML = `
    <div class="setup-step active" style="display:flex;flex-direction:column;max-height:82vh;overflow:hidden">
      <div style="flex-shrink:0">
        <div class="setup-step-icon"><img src="/static/branding/lumena-logo.png" alt="Lumena" style="width:32px;height:32px;object-fit:contain"></div>
        <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
        <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
        <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      </div>

      <div class="models-scroll-container" style="margin-top:8px;flex:1;min-height:0;max-height:none">
        <div style="display:flex;flex-direction:column;gap:16px">

          <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="wand-2" style="width:14px;height:14px;color:#f59f4a"></i> Démarrage rapide</div>
            <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Clique sur un preset pour configurer tous les traits d'un coup, puis ajuste à la main.</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap" id="personality-presets">
              <button data-preset="professional" style="padding:6px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:var(--text,#e6e6e6);font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px"><i data-lucide="briefcase" style="width:12px;height:12px"></i> Professionnel</button>
              <button data-preset="creative" style="padding:6px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:var(--text,#e6e6e6);font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px"><i data-lucide="palette" style="width:12px;height:12px"></i> Créatif</button>
              <button data-preset="companion" style="padding:6px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:var(--text,#e6e6e6);font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px"><i data-lucide="heart" style="width:12px;height:12px"></i> Compagnon</button>
            </div>
          </div>

          <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:6px;display:flex;align-items:center;gap:6px"><i data-lucide="sliders-horizontal" style="width:14px;height:14px;color:#f59f4a"></i> Traits de caractère</div>
            <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Active ou désactive chaque trait et ajuste son intensité.</div>
            <div style="border:1px solid rgba(255,255,255,.09);border-radius:10px;overflow:hidden">${traitsHtml}</div>
          </div>

          <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:6px;display:flex;align-items:center;gap:6px"><i data-lucide="heart" style="width:14px;height:14px;color:#f59f4a"></i> Humeurs autorisées</div>
            <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Coche les humeurs que Lumena peut ressentir. Clique sur une carte pour la définir par défaut.</div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">${moodHtml}</div>
          </div>

          <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:6px;display:flex;align-items:center;gap:6px"><i data-lucide="message-circle" style="width:14px;height:14px;color:#f59f4a"></i> Style de communication</div>
            <div style="border:1px solid rgba(255,255,255,.09);border-radius:10px;overflow:hidden;padding:12px 14px;display:flex;flex-direction:column;gap:12px">
              <div style="display:flex;align-items:center;justify-content:space-between">
                <span style="font-size:13px;font-weight:600;color:var(--text,#e6e6e6)">Utiliser des emojis</span>
                <button class="setup-toggle${useEmojis ? ' on' : ''}" data-setup-key="LUMENA_USE_EMOJIS" type="button"></button>
              </div>
              <div>
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
                  <span style="font-size:13px;color:var(--text,#e6e6e6)">Fréquence des emojis</span>
                  <span data-emoji-val style="font-size:12px;font-weight:700;color:${emojiFreqClr}">${_esc(emojiFreqLabel)} — ${emojiFreq}%</span>
                </div>
                <input type="range" data-setup-key="LUMENA_EMOJI_FREQUENCY" min="0" max="100" value="${emojiFreq}" style="width:100%;accent-color:#f59f4a;height:4px">
                <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted,#8c8c9a);margin-top:2px"><span>0%</span><span>100%</span></div>
              </div>
            </div>
          </div>

          ${idFields.length ? `<div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:6px;display:flex;align-items:center;gap:6px"><i data-lucide="graduation-cap" style="width:14px;height:14px;color:#f59f4a"></i> Apprentissage</div>
            <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-bottom:8px">Lumena apprend automatiquement tes préférences et infos personnelles au fil des conversations.</div>
            <div style="border:1px solid rgba(255,255,255,.09);border-radius:10px;padding:10px 14px;display:flex;flex-direction:column;gap:8px" id="identity-fields-container"></div>
          </div>` : ''}

        </div>
      </div>

      <div style="flex-shrink:0;margin-top:12px">
        ${_navHtml(true, 'Passer')}
      </div>
    </div>`;

  _bindNav(cont);

  // ── Helper: sync enabled moods to config ──
  const _syncMoodsConfig = () => {
    const checked = [...cont.querySelectorAll('[data-mood-toggle]:checked')].map(c => c.dataset.moodToggle);
    _config['LUMENA_ENABLED_MOODS'] = checked.join(',');
  };

  // ── Trait toggles + sliders ──
  for (const cb of cont.querySelectorAll('[data-trait-toggle]')) {
    cb.onchange = () => {
      const key = cb.dataset.traitToggle;
      const row = cont.querySelector(`[data-trait-row="${key}"]`);
      const slider = cont.querySelector(`[data-trait-key="${key}"]`);
      const valEl = cont.querySelector(`[data-trait-val="${key}"]`);
      _config[`LUMENA_TRAIT_${key.toUpperCase()}_ENABLED`] = cb.checked ? '1' : '0';
      if (row) row.style.opacity = cb.checked ? '1' : '.4';
      if (slider) slider.style.pointerEvents = cb.checked ? 'auto' : 'none';
      if (valEl) {
        if (cb.checked) {
          const v = parseInt(slider ? slider.value : '50', 10);
          const c = v >= 80 ? 'var(--ok,#22c55e)' : v >= 50 ? 'var(--accent,#f59f4a)' : 'var(--muted,#8c8c9a)';
          valEl.textContent = v + '%'; valEl.style.color = c;
        } else {
          valEl.textContent = 'off'; valEl.style.color = 'var(--muted,#8c8c9a)';
        }
      }
    };
  }
  for (const slider of cont.querySelectorAll('[data-trait-key]')) {
    slider.oninput = () => {
      const key = slider.dataset.traitKey;
      const val = parseInt(slider.value, 10);
      _config[`LUMENA_TRAIT_${key.toUpperCase()}`] = String(val);
      const valEl = cont.querySelector(`[data-trait-val="${key}"]`);
      if (valEl) {
        valEl.textContent = val + '%';
        valEl.style.color = val >= 80 ? 'var(--ok,#22c55e)' : val >= 50 ? 'var(--accent,#f59f4a)' : 'var(--muted,#8c8c9a)';
      }
    };
  }

  // ── Mood toggles (checkbox) + default click ──
  for (const cb of cont.querySelectorAll('[data-mood-toggle]')) {
    cb.onchange = (e) => {
      e.stopPropagation();
      const key = cb.dataset.moodToggle;
      const card = cont.querySelector(`[data-mood-key="${key}"]`);
      if (card) card.style.opacity = cb.checked ? '1' : '.4';
      if (!cb.checked && _config['LUMENA_DEFAULT_MOOD'] === key) {
        // If unchecking the default mood, pick first available
        const first = cont.querySelector('[data-mood-toggle]:checked');
        _config['LUMENA_DEFAULT_MOOD'] = first ? first.dataset.moodToggle : 'neutral';
        _renderMoodsStep(cont, step); // re-render to update default badge
        return;
      }
      _syncMoodsConfig();
    };
  }
  for (const card of cont.querySelectorAll('[data-mood-key]')) {
    card.onclick = (e) => {
      if (e.target.tagName === 'INPUT') return;
      const key = card.dataset.moodKey;
      const cb = card.querySelector(`[data-mood-toggle="${key}"]`);
      if (!cb || !cb.checked) return; // can't set default if mood is disabled
      _config['LUMENA_DEFAULT_MOOD'] = key;
      _renderMoodsStep(cont, step); // re-render to move default badge
    };
  }

  // ── Emoji toggle ──
  const emojiToggle = cont.querySelector('[data-setup-key="LUMENA_USE_EMOJIS"]');
  if (emojiToggle) emojiToggle.onclick = () => {
    const on = emojiToggle.classList.toggle('on');
    _config['LUMENA_USE_EMOJIS'] = on ? '1' : '0';
  };

  // ── Emoji frequency slider ──
  const emojiSlider = cont.querySelector('[data-setup-key="LUMENA_EMOJI_FREQUENCY"]');
  if (emojiSlider) emojiSlider.oninput = () => {
    const val = parseInt(emojiSlider.value, 10);
    _config['LUMENA_EMOJI_FREQUENCY'] = String(val);
    const valEl = cont.querySelector('[data-emoji-val]');
    if (valEl) {
      const lbl = val <= 10 ? 'Rare' : val <= 30 ? 'Modéré' : val <= 60 ? 'Fréquent' : 'Intensif';
      const clr = val >= 60 ? 'var(--warn,#f2c94c)' : val >= 20 ? 'var(--accent,#f59f4a)' : 'var(--muted,#8c8c9a)';
      valEl.textContent = `${lbl} — ${val}%`;
      valEl.style.color = clr;
    }
  };

  // ── Personality presets ──
  const _PRESETS = {
    professional: { curiosity: 75, playfulness: 30, warmth: 70, proactivity: 90, creativity: 60, patience: 90, honesty: 95, loyalty: 85 },
    creative:     { curiosity: 95, playfulness: 80, warmth: 80, proactivity: 70, creativity: 95, patience: 70, honesty: 85, loyalty: 80 },
    companion:    { curiosity: 80, playfulness: 75, warmth: 95, proactivity: 70, creativity: 75, patience: 90, honesty: 95, loyalty: 95 },
  };
  for (const btn of cont.querySelectorAll('[data-preset]')) {
    btn.onclick = () => {
      const preset = _PRESETS[btn.dataset.preset];
      if (!preset) return;
      for (const [key, val] of Object.entries(preset)) {
        _config[`LUMENA_TRAIT_${key.toUpperCase()}`] = String(val);
        _config[`LUMENA_TRAIT_${key.toUpperCase()}_ENABLED`] = '1';
      }
      _renderMoodsStep(cont, step);
    };
  }

  // Init moods config
  _syncMoodsConfig();

  // ── Identity fields ──
  const idContainer = cont.querySelector('#identity-fields-container');
  if (idContainer && idFields.length) {
    let idHtml = '';
    for (const f of idFields) {
      if (f.type === 'bool') {
        const on = (_config[f.key] || f.default) === '1';
        idHtml += `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0">
          <div><span style="font-size:13px;font-weight:600;color:var(--text,#e6e6e6)">${_esc(f.label)}</span>${f.hint ? `<div style="font-size:10px;color:var(--muted,#8c8c9a)">${_esc(f.hint)}</div>` : ''}</div>
          <button class="setup-toggle${on ? ' on' : ''}" data-setup-key="${_esc(f.key)}" type="button"></button>
        </div>`;
      } else {
        const v = _config[f.key] || f.default || '';
        idHtml += `<div style="padding:4px 0">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <span style="font-size:13px;font-weight:600;color:var(--text,#e6e6e6)">${_esc(f.label)}</span>
            <input type="number" data-setup-key="${_esc(f.key)}" value="${_esc(v)}" min="0" style="width:80px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:6px 10px;color:var(--text);font-size:13px;text-align:center">
          </div>
          ${f.hint ? `<div style="font-size:10px;color:var(--muted,#8c8c9a)">${_esc(f.hint)}</div>` : ''}
        </div>`;
      }
    }
    idContainer.innerHTML = idHtml;
    for (const el of idContainer.querySelectorAll('.setup-toggle[data-setup-key]')) {
      el.onclick = () => { const on = el.classList.toggle('on'); _config[el.dataset.setupKey] = on ? '1' : '0'; };
    }
    for (const el of idContainer.querySelectorAll('input[data-setup-key]')) {
      el.oninput = () => { _config[el.dataset.setupKey] = el.value; };
    }
  }
}

// ─── Model → provider key mapping ────────────────────────────────
function _modelToProviderKey(m) {
  if (!m || m === 'auto') return null;
  if (m.startsWith('nvidia-'))  return 'NVIDIA_API_KEY';
  if (m.startsWith('gpt-') || m === 'dall-e-3') return 'OPENAI_API_KEY';
  if (m.startsWith('claude-'))  return 'ANTHROPIC_API_KEY';
  if (m.startsWith('gemini-'))  return 'GOOGLE_API_KEY';
  if (m.startsWith('deepseek-'))return 'DEEPSEEK_API_KEY';
  if (m.startsWith('kimi-'))    return 'MOONSHOT_API_KEY';
  if (m.startsWith('grok-'))    return 'XAI_API_KEY';
  if (m.startsWith('minimax-'))  return 'MINIMAX_API_KEY';
  return null; // Ollama / local / unknown
}

// ─── Brains step (modèles spécialisés) ───────────────────────────
function _renderBrainsStep(cont, step) {
  const brainsInfo = step.brains_info || {};
  const fields = step.fields || [];

  let brainsHtml = '';
  for (const f of fields) {
    const info = brainsInfo[f.key] || {};
    const currentVal = _config[f.key] || 'auto';
    const top = info.top || [];
    const topFree = info.top_free || [];

    const pillHtml = (models) => models.map(m => {
      const sel = currentVal === m;
      return `<button data-brain-val="${_esc(m)}" data-brain-key="${_esc(f.key)}" style="padding:4px 10px;border-radius:12px;border:1px solid ${sel ? 'rgba(245,159,74,.5)' : 'rgba(255,255,255,.15)'};background:${sel ? 'rgba(245,159,74,.18)' : 'rgba(255,255,255,.05)'};color:${sel ? '#f59f4a' : 'var(--text,#e6e6e6)'};font-size:11px;font-weight:${sel ? '700' : '500'};cursor:pointer;transition:all .15s">${_esc(m)}</button>`;
    }).join('');

    const autoSel = currentVal === 'auto';
    brainsHtml += `<div style="padding:14px;border:1px solid rgba(255,255,255,.09);border-radius:10px;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <div style="width:34px;height:34px;border-radius:9px;background:rgba(245,159,74,.12);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i data-lucide="${_esc(info.icon || 'cpu')}" style="width:16px;height:16px;color:#f59f4a"></i></div>
        <div><div style="font-size:13px;font-weight:700;color:var(--text,#e6e6e6)">${_esc(f.label || f.key)}</div>
        <div style="font-size:11px;color:var(--muted,#8c8c9a)">${_esc(info.desc || '')}</div></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px">
        <button data-brain-val="auto" data-brain-key="${_esc(f.key)}" style="padding:4px 10px;border-radius:12px;border:1px solid ${autoSel ? 'rgba(245,159,74,.5)' : 'rgba(255,255,255,.15)'};background:${autoSel ? 'rgba(245,159,74,.18)' : 'rgba(255,255,255,.05)'};color:${autoSel ? '#f59f4a' : 'var(--text,#e6e6e6)'};font-size:11px;font-weight:${autoSel ? '700' : '500'};cursor:pointer">auto</button>
        ${pillHtml(top)}
      </div>
      ${topFree.length ? `<div style="font-size:10px;color:var(--muted,#8c8c9a);margin-bottom:5px;letter-spacing:.4px">GRATUITS (NVIDIA NIM)</div><div style="display:flex;flex-wrap:wrap;gap:6px">${pillHtml(topFree)}</div>` : ''}
      ${(() => {
        if (currentVal === 'auto') return '';
        const reqKey = _modelToProviderKey(currentVal);
        if (!reqKey) return `<div style="font-size:10px;color:var(--muted,#8c8c9a);margin-top:6px;display:flex;align-items:center;gap:4px"><i data-lucide="server" style="width:11px;height:11px"></i> Ollama local — aucune clé requise</div>`;
        return _config[reqKey]
          ? `<div style="font-size:10px;color:var(--ok,#22c55e);margin-top:6px;display:flex;align-items:center;gap:4px"><i data-lucide="check-circle" style="width:11px;height:11px"></i> ${_esc(_prettyLabel(reqKey))} configurée ✓</div>`
          : `<div style="font-size:10px;color:var(--warn,#f2c94c);margin-top:6px;display:flex;align-items:center;gap:4px"><i data-lucide="alert-triangle" style="width:11px;height:11px"></i> ${_esc(_prettyLabel(reqKey))} manquante — configure-la à l\'étape Clés API</div>`;
      })()}
    </div>`;
  }

  cont.innerHTML = `
    <div class="setup-step active" style="display:flex;flex-direction:column;max-height:82vh;overflow:hidden">
      <div style="flex-shrink:0">
        <div class="setup-step-icon"><i data-lucide="cpu"></i></div>
        <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
        <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
        <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>
      </div>
      <div class="models-scroll-container" style="margin-top:8px;flex:1;min-height:0;max-height:none">
        ${brainsHtml}
        ${step.tip ? `<div class="setup-tip"><i data-lucide="info" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.tip)}</div>` : ''}
      </div>
      <div style="flex-shrink:0;margin-top:12px">${_navHtml(true, 'Passer')}</div>
    </div>`;

  _bindNav(cont);
  for (const btn of cont.querySelectorAll('[data-brain-val]')) {
    btn.onclick = () => {
      _config[btn.dataset.brainKey] = btn.dataset.brainVal;
      _renderBrainsStep(cont, step);
      if (typeof lucide !== 'undefined') lucide.createIcons();
    };
  }
}

// ─── Locale step (langue, fuseau, workspace) ──────────────────────
function _renderLocaleStep(cont, step) {
  const locales = step.locale_options || [];
  const timezones = step.timezone_options || [];
  const currentLang = _config['LUMENA_LANGUAGE'] || 'fr';
  const currentTZ   = _config['LUMENA_TIMEZONE'] || 'Europe/Paris';
  const currentWS   = _config['LUMENA_WORKSPACE_PATH'] || '';

  const langHtml = locales.map(l => {
    const sel = currentLang === l.key;
    return `<button data-lang="${_esc(l.key)}" style="padding:10px 8px;border-radius:10px;border:1px solid ${sel ? 'rgba(245,159,74,.5)' : 'rgba(255,255,255,.09)'};background:${sel ? 'rgba(245,159,74,.12)' : 'rgba(255,255,255,.04)'};color:${sel ? '#f59f4a' : 'var(--text,#e6e6e6)'};font-size:12px;font-weight:${sel ? '700' : '500'};cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:4px;transition:all .15s">
      <span style="font-size:20px">${l.flag}</span><span>${_esc(l.label)}</span>
    </button>`;
  }).join('');

  const tzOpts = timezones.map(t =>
    `<option value="${_esc(t.key)}"${t.key === currentTZ ? ' selected' : ''}>${_esc(t.label)}</option>`
  ).join('');

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="globe"></i></div>
      <h2>${_esc(step.title)} <span class="setup-optional">optionnel</span></h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      <div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help || '')}</div>

      <div style="margin-top:16px">
        <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:10px;display:flex;align-items:center;gap:6px"><i data-lucide="languages" style="width:14px;height:14px;color:#f59f4a"></i> Langue</div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">${langHtml}</div>
      </div>

      <div style="margin-top:16px">
        <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="clock" style="width:14px;height:14px;color:#f59f4a"></i> Fuseau horaire</div>
        <select data-locale-tz style="width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:10px 12px;color:var(--text,#e6e6e6);font-size:13px">${tzOpts}</select>
      </div>

      <div style="margin-top:16px">
        <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted,#8c8c9a);margin-bottom:8px;display:flex;align-items:center;gap:6px"><i data-lucide="folder" style="width:14px;height:14px;color:#f59f4a"></i> Dossier de travail <span style="font-weight:400;text-transform:none;font-size:10px;letter-spacing:0;margin-left:4px">(optionnel)</span></div>
        <input type="text" data-locale-ws value="${_esc(currentWS)}" placeholder="Ex: C:/Users/moi/projets" style="width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:10px 12px;color:var(--text,#e6e6e6);font-size:13px;box-sizing:border-box">
        <div style="font-size:11px;color:var(--muted,#8c8c9a);margin-top:4px">Répertoire où Lumena peut créer des fichiers. Laisse vide pour le dossier par défaut.</div>
        <span id="locale-ws-status" style="font-size:11px;margin-top:5px;display:block;min-height:15px"></span>
      </div>

      ${step.tip ? `<div class="setup-tip" style="margin-top:12px"><i data-lucide="info" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.tip)}</div>` : ''}
      ${_navHtml(true, 'Passer')}
    </div>`;

  _bindNav(cont);
  for (const btn of cont.querySelectorAll('[data-lang]')) {
    btn.onclick = () => { _config['LUMENA_LANGUAGE'] = btn.dataset.lang; _renderLocaleStep(cont, step); };
  }
  const tzSel = cont.querySelector('[data-locale-tz]');
  if (tzSel) tzSel.onchange = () => { _config['LUMENA_TIMEZONE'] = tzSel.value; };
  const wsIn = cont.querySelector('[data-locale-ws]');
  const wsStatus = cont.querySelector('#locale-ws-status');
  if (wsIn) {
    wsIn.oninput = () => { _config['LUMENA_WORKSPACE_PATH'] = wsIn.value; };
    wsIn.onblur = async () => {
      const val = wsIn.value.trim();
      if (!val || !wsStatus) return;
      wsStatus.textContent = 'Vérification...';
      wsStatus.style.color = 'var(--muted,#8c8c9a)';
      try {
        const r = await fetch('/api/setup/validate-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: val }),
        });
        const d = await r.json();
        wsStatus.textContent = d.message || '';
        wsStatus.style.color = d.valid ? (d.will_create ? 'var(--warn,#f2c94c)' : 'var(--ok,#22c55e)') : 'var(--danger,#ef4444)';
      } catch {
        wsStatus.textContent = '';
      }
    };
  }
}

// ─── Generic step (voice, etc.) ───────────────────────────────────
function _renderGenericStep(cont, step) {
  let fieldsHtml = '';
  for (const f of step.fields) fieldsHtml += _renderFieldEnriched(f);
  const optBadge = step.optional ? '<span class="setup-optional">optionnel</span>' : '';

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="${step.icon || 'settings'}"></i></div>
      <h2>${_esc(step.title)} ${optBadge}</h2>
      <p class="setup-subtitle">${_esc(step.subtitle || '')}</p>
      ${step.help ? `<div class="setup-help-text"><i data-lucide="lightbulb" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.help)}</div>` : ''}
      ${fieldsHtml}
      ${step.tip ? `<div class="setup-tip"><i data-lucide="info" style="width:16px;height:16px;flex-shrink:0;margin-top:2px"></i> ${_esc(step.tip)}</div>` : ''}
      ${_navHtml(step.optional, step.optional ? 'Passer' : 'Suivant')}
    </div>`;

  _bindNav(cont);
  for (const f of step.fields) {
    const el = cont.querySelector(`[data-setup-key="${f.key}"]`);
    if (!el) continue;
    if (f.type === 'bool') {
      el.onclick = () => { const on = el.classList.toggle('on'); _config[f.key] = on ? '1' : '0'; };
    } else { el.oninput = () => { _config[f.key] = el.value; }; }
  }
}

// ─── Summary helpers ─────────────────────────────────────────────
const _SUMMARY_SKIP = new Set(['LUMENA_SETUP_COMPLETE', 'LUMENA_ENABLED_MOODS']);
const _SUMMARY_SENSITIVE = k => k.includes('KEY') || k.includes('TOKEN') || k.includes('SECRET') || k.includes('PASSWORD') || k.includes('SID');
const _SUMMARY_GROUPS = [
  { label: 'Modèle IA',       icon: 'brain',         test: k => k === 'LUMENA_DEFAULT_MODEL' || k.startsWith('LUMENA_BRAIN_') },
  { label: 'Clés API',        icon: 'key-round',     test: k => k.endsWith('_API_KEY') },
  { label: 'Sécurité',        icon: 'shield',        test: k => k === 'LUMENA_ADMIN_TOKEN' || k.startsWith('LUMENA_CRITICAL_') || k === 'LUMENA_HOST' },
  { label: 'Telegram',        icon: 'send',          test: k => k === 'TELEGRAM_TOKEN' },
  { label: 'Twitter / X',     icon: 'twitter',       test: k => k.startsWith('TWITTER_') },
  { label: 'WhatsApp',        icon: 'message-circle', test: k => k.startsWith('WHATSAPP_') },
  { label: 'Voix',            icon: 'mic',           test: k => k.startsWith('LUMENA_TTS_') || k.startsWith('LUMENA_STT_') || k.startsWith('LUMENA_VOICE_') },
  { label: 'Personnalité',    icon: 'sparkles',      test: k => k === 'LUMENA_DEFAULT_MOOD' || k === 'LUMENA_USE_EMOJIS' || k === 'LUMENA_EMOJI_FREQUENCY' || k.startsWith('LUMENA_IDENTITY_') || k.startsWith('LUMENA_TRAIT_') },
  { label: 'Autonomie',       icon: 'zap',           test: k => k.startsWith('LUMENA_AUTONOMY_') || k.startsWith('LUMENA_ARCHIVE_') || k === 'LUMENA_OPS_MEMORY_PURGE_ENABLED' || k.startsWith('LUMENA_SANDBOX_') },
  { label: 'Intégrations',    icon: 'plug',          test: k => ['DISCORD_TOKEN','DISCORD_MAIN_CHANNEL_ID','GITHUB_TOKEN','NOTION_API_KEY','BRAVE_SEARCH_API_KEY','SPOTIFY_CLIENT_ID','SPOTIFY_CLIENT_SECRET','TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_FROM_NUMBER','LUMENA_ALERT_TO_NUMBER','LUMENA_EMAIL','LUMENA_EMAIL_PASSWORD','LUMENA_USER_EMAIL','STRIPE_API_KEY','STRIPE_MODE'].includes(k) },
  { label: 'Locale',          icon: 'globe',         test: k => ['LUMENA_LANGUAGE','LUMENA_TIMEZONE','LUMENA_WORKSPACE_PATH'].includes(k) },
  { label: 'Avancé',          icon: 'settings',      test: k => k.startsWith('LUMENA_REACT_') || k.startsWith('LUMENA_TASK_') || k.startsWith('LUMENA_MAX_') },
];

function _renderSummary(cont) {
  const assigned = new Set();
  let count = 0;
  let groupsHtml = '';

  // Trait keys → collapsed into single personality row
  const traitKeys = Object.keys(_config).filter(k => k.startsWith('LUMENA_TRAIT_') && !k.endsWith('_ENABLED') && _config[k]);

  for (const g of _SUMMARY_GROUPS) {
    const gKeys = Object.keys(_config).filter(k => _config[k] && !_SUMMARY_SKIP.has(k) && !assigned.has(k) && g.test(k));
    if (!gKeys.length) continue;
    gKeys.forEach(k => assigned.add(k));

    let rows = '';
    for (const k of gKeys) {
      const v = _config[k];
      if (!v) continue;
      // Traits: collapse into single summary line
      if (k.startsWith('LUMENA_TRAIT_')) {
        // Handled separately below
        continue;
      }
      count++;
      const display = _SUMMARY_SENSITIVE(k) ? '•••••' + v.slice(-4) : v;
      rows += `<div class="setup-summary-row"><span class="label">${_esc(_prettyLabel(k))}</span><span class="value">${_esc(display)}</span></div>`;
    }
    // Traits summary (for personality group)
    if (g.label === 'Personnalité' && traitKeys.length) {
      rows += `<div class="setup-summary-row"><span class="label">Traits de caractère</span><span class="value">${traitKeys.length} configurés</span></div>`;
      count++;
    }
    if (!rows) continue;
    groupsHtml += `<div style="margin-bottom:14px">`
      + `<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--muted,#8c8c9a);margin-bottom:6px;display:flex;align-items:center;gap:6px"><i data-lucide="${g.icon}" style="width:12px;height:12px;color:#f59f4a"></i> ${_esc(g.label)}</div>`
      + rows
      + `</div>`;
  }

  // Any unassigned keys (safety net)
  const remaining = Object.keys(_config).filter(k => _config[k] && !_SUMMARY_SKIP.has(k) && !assigned.has(k) && !k.startsWith('LUMENA_TRAIT_'));
  if (remaining.length) {
    const rows = remaining.map(k => { count++; const v = _config[k]; const d = _SUMMARY_SENSITIVE(k) ? '•••••' + v.slice(-4) : v; return `<div class="setup-summary-row"><span class="label">${_esc(_prettyLabel(k))}</span><span class="value">${_esc(d)}</span></div>`; }).join('');
    groupsHtml += `<div style="margin-bottom:14px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--muted,#8c8c9a);margin-bottom:6px">Autres</div>${rows}</div>`;
  }

  let rows = groupsHtml;
  if (!rows) {
    rows = '<div class="setup-info-box" style="margin:20px 0"><i data-lucide="info" style="width:18px;height:18px;flex-shrink:0"></i><div>Aucun paramètre configuré.<br>Tu peux revenir en arrière pour configurer au moins un modèle.</div></div>';
  }

  const btnLabel = _isPreview ? 'Fermer l\'aperçu' : 'Démarrer Lumena';

  cont.innerHTML = `
    <div class="setup-step active">
      <div class="setup-step-icon"><i data-lucide="check-circle"></i></div>
      <h2>Tout est prêt !</h2>
      <p class="setup-subtitle">${_isPreview ? 'Mode aperçu — rien ne sera sauvegardé.' : `${count} paramètre${count > 1 ? 's' : ''} configuré${count > 1 ? 's' : ''}. Vérifie avant de lancer.`}</p>
      <div class="setup-summary">${rows}</div>
      ${!_isPreview ? `<div style="margin-bottom:10px;display:flex;align-items:center;gap:10px;padding:0 2px">
        <button id="setup-pingtest" style="padding:7px 14px;border-radius:8px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:var(--text,#e6e6e6);font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px"><i data-lucide="wifi" style="width:14px;height:14px"></i> Tester la connexion</button>
        <span id="setup-ping-result" style="font-size:12px;color:var(--muted,#8c8c9a)"></span>
      </div>` : ''}
      <div class="setup-nav">
        <button class="setup-btn setup-btn-secondary" id="setup-back"><i data-lucide="arrow-left" style="width:16px;height:16px;vertical-align:middle"></i> Retour</button>
        <button class="setup-btn setup-btn-primary" id="setup-finish"><i data-lucide="rocket" style="width:16px;height:16px;vertical-align:middle"></i> ${btnLabel}</button>
      </div>
    </div>`;

  cont.querySelector('#setup-back').onclick = () => _goBack();
  cont.querySelector('#setup-finish').onclick = () => _finishSetup();
  const pingBtn = cont.querySelector('#setup-pingtest');
  if (pingBtn) {
    pingBtn.onclick = async () => {
      const res = cont.querySelector('#setup-ping-result');
      if (res) res.textContent = 'Test en cours...';
      pingBtn.disabled = true;
      try {
        const r = await fetch('/api/health');
        const d = await r.json();
        if (res) {
          res.textContent = d.status === 'ok' ? '✓ Lumena répond correctement' : `⚠ ${d.status}`;
          res.style.color = d.status === 'ok' ? 'var(--ok,#22c55e)' : 'var(--warn,#f2c94c)';
        }
      } catch {
        if (res) { res.textContent = '✗ Impossible de joindre Lumena'; res.style.color = 'var(--danger,#ef4444)'; }
      }
      pingBtn.disabled = false;
    };
  }
}

// ─── Helpers ──────────────────────────────────────────────────────
function _renderFieldEnriched(f) {
  const val = _config[f.key] || '';
  if (f.type === 'select' && f.options) {
    const opts = f.options.map(o =>
      `<option value="${_esc(o)}"${val === o ? ' selected' : ''}>${_esc(o)}</option>`
    ).join('');
    return `<div class="setup-field">
      <label>${_esc(f.label)}</label>
      <select data-setup-key="${_esc(f.key)}">${opts}</select>
      ${f.hint ? `<div class="setup-hint">${_esc(f.hint)}</div>` : ''}
    </div>`;
  }
  if (f.type === 'bool') {
    const on = val === '1' || val === 'true';
    return `<div class="setup-field"><div class="setup-toggle-row">
      <label>${_esc(f.label)}</label>
      <button class="setup-toggle${on ? ' on' : ''}" data-setup-key="${_esc(f.key)}" type="button"></button>
    </div>${f.hint ? `<div class="setup-hint">${_esc(f.hint)}</div>` : ''}</div>`;
  }
  const inputType = f.type === 'secret' ? 'password' : (f.type === 'number' ? 'number' : 'text');
  return `<div class="setup-field">
    <label>${_esc(f.label)}</label>
    <input type="${inputType}" data-setup-key="${_esc(f.key)}" value="${_esc(val)}" placeholder="${_esc(f.placeholder || f.default || '')}" autocomplete="off">
    ${f.hint ? `<div class="setup-hint">${_esc(f.hint)}</div>` : ''}
  </div>`;
}

function _navHtml(optional, nextLabel) {
  const label = nextLabel || (optional ? 'Passer' : 'Suivant');
  return `<div class="setup-nav">
    <button class="setup-btn setup-btn-secondary" id="setup-back"><i data-lucide="arrow-left" style="width:16px;height:16px;vertical-align:middle"></i> Retour</button>
    <button class="setup-btn setup-btn-primary" id="setup-next">${label} <i data-lucide="arrow-right" style="width:16px;height:16px;vertical-align:middle"></i></button>
  </div>`;
}

function _bindNav(cont) {
  const back = cont.querySelector('#setup-back');
  const next = cont.querySelector('#setup-next');
  if (back) back.onclick = () => _goBack();
  if (next) next.onclick = () => _goNext();
}

function _prettyLabel(key) {
  // Dynamic handler for trait keys
  if (key.startsWith('LUMENA_TRAIT_')) {
    const _traitNames = {
      CURIOSITY: 'Curiosité', PLAYFULNESS: 'Espièglerie', WARMTH: 'Chaleur',
      PROACTIVITY: 'Proactivité', CREATIVITY: 'Créativité', PATIENCE: 'Patience',
      HONESTY: 'Honnêteté', LOYALTY: 'Loyauté',
    };
    const raw = key.replace('LUMENA_TRAIT_', '');
    if (raw.endsWith('_ENABLED')) {
      const name = raw.replace('_ENABLED', '');
      return 'Trait ' + (_traitNames[name] || name.toLowerCase()) + ' actif';
    }
    return 'Trait ' + (_traitNames[raw] || raw.toLowerCase());
  }
  const map = {
    'LUMENA_DEFAULT_MODEL':             'Modèle IA',
    'DEEPSEEK_API_KEY':                 'Clé DeepSeek',
    'OPENAI_API_KEY':                   'Clé OpenAI',
    'ANTHROPIC_API_KEY':                'Clé Anthropic',
    'GOOGLE_API_KEY':                   'Clé Google',
    'NVIDIA_API_KEY':                   'Clé NVIDIA',
    'MOONSHOT_API_KEY':                 'Clé Moonshot (Kimi)',
    'XAI_API_KEY':                      'Clé xAI (Grok)',
    'LUMENA_ADMIN_TOKEN':               'Token admin',
    'TELEGRAM_TOKEN':                   'Token Telegram',
    // Voice
    'LUMENA_TTS_AUTO':                  'Voix automatique',
    'LUMENA_TTS_MODE':                  'Mode TTS',
    'LUMENA_STT_MODEL':                 'Modèle reconnaissance vocale',
    'LUMENA_VOICE_AUTO':                'Conversation vocale auto',
    'LUMENA_VOICE_CONV_TIMEOUT':        'Timeout conversation vocale (s)',
    'LUMENA_TTS_TELEGRAM':              'TTS sur Telegram',
    // Autonomy
    'LUMENA_AUTONOMY_EXECUTE_ACTIONS':  'Autonomie active',
    'LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR': 'Actions/heure max',
    'LUMENA_AUTONOMY_ALLOWED_ACTIONS':  'Actions permises',
    'LUMENA_AUTONOMY_PROGRESSIVE_MODE': 'Mode progressif',
    'LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC': 'Timeout action (s)',
    'LUMENA_AUTONOMY_GOAL_COOLDOWN_SEC': 'Cooldown objectifs (s)',
    'LUMENA_AUTONOMY_GOAL_MAX_FAILURES': 'Échecs max objectif',
    'LUMENA_ARCHIVE_MAX_AGE_DAYS':      'Rétention archives (jours)',
    'LUMENA_ARCHIVE_MAX_SIZE_GB':       'Taille archives max (Go)',
    'LUMENA_OPS_MEMORY_PURGE_ENABLED':  'Purge mémoire auto',
    // Security & alerts
    'LUMENA_CRITICAL_ALERTS_ENABLED':   'Alertes critiques actives',
    'LUMENA_CRITICAL_ALERT_COOLDOWN_SEC': 'Cooldown alertes (s)',
    // Locale
    'LUMENA_LANGUAGE':                  'Langue',
    'LUMENA_TIMEZONE':                  'Fuseau horaire',
    'LUMENA_WORKSPACE_PATH':            'Dossier de travail',
    // Brains
    'LUMENA_BRAIN_VISION':              'Cerveau Vision',
    'LUMENA_BRAIN_CODE':                'Cerveau Code',
    'LUMENA_BRAIN_WEB':                 'Cerveau Web',
    'LUMENA_BRAIN_IMAGE_GEN':           'Génération images',
    // Personality
    'LUMENA_DEFAULT_MOOD':              'Humeur par défaut',
    'LUMENA_ENABLED_MOODS':             'Humeurs actives',
    'LUMENA_USE_EMOJIS':                'Emojis activés',
    'LUMENA_EMOJI_FREQUENCY':           'Fréquence emojis',
    'LUMENA_IDENTITY_LEARNING':         'Apprentissage identité',
    'LUMENA_IDENTITY_HINT_COOLDOWN':    'Délai suggestions identité',
    // Integrations
    'DISCORD_TOKEN':                    'Token Discord',
    'DISCORD_MAIN_CHANNEL_ID':          'Discord Channel ID',
    'GITHUB_TOKEN':                     'Token GitHub',
    'NOTION_API_KEY':                   'Clé Notion',
    'BRAVE_SEARCH_API_KEY':             'Clé Brave Search',
    'SPOTIFY_CLIENT_ID':                'Spotify Client ID',
    'SPOTIFY_CLIENT_SECRET':            'Spotify Client Secret',
    'TWILIO_ACCOUNT_SID':               'Twilio SID',
    'TWILIO_AUTH_TOKEN':                'Twilio Token',
    'TWILIO_FROM_NUMBER':               'Numéro Twilio',
    'LUMENA_ALERT_TO_NUMBER':           'Numéro alertes SMS',
    'LUMENA_EMAIL':                     'Email Lumena',
    'LUMENA_EMAIL_PASSWORD':            'Mot de passe email',
    'LUMENA_USER_EMAIL':                'Ton email',
    // Twitter / X
    'TWITTER_BEARER_TOKEN':             'Twitter Bearer Token',
    'TWITTER_API_KEY':                  'Twitter API Key',
    'TWITTER_API_SECRET':               'Twitter API Secret',
    'TWITTER_ACCESS_TOKEN':             'Twitter Access Token',
    'TWITTER_ACCESS_TOKEN_SECRET':      'Twitter Access Token Secret',
    // WhatsApp
    'WHATSAPP_ACCESS_TOKEN':            'WhatsApp Access Token',
    'WHATSAPP_PHONE_NUMBER_ID':         'WhatsApp Phone Number ID',
    'WHATSAPP_VERIFY_TOKEN':            'WhatsApp Verify Token',
    'WHATSAPP_APP_SECRET':              'WhatsApp App Secret',
    'WHATSAPP_OWNER_PHONE':             'WhatsApp Numéro propriétaire',

    // Sandbox
    'LUMENA_SANDBOX_MODE':              'Mode sandbox',
    'LUMENA_SANDBOX_MEMORY':            'Mémoire Docker',
    // Server
    'LUMENA_HOST':                      'Accès réseau',
    'STRIPE_API_KEY':                   'Clé Stripe',
    'STRIPE_MODE':                      'Mode Stripe',
    // Advanced LLM
    'LUMENA_REACT_TIMEOUT':             'Timeout ReAct (s)',
    'LUMENA_MAX_REACT_ITERATIONS':      'Itérations ReAct max',
    'LUMENA_REACT_HISTORY_OBS_CHARS':   'Contexte observations (chars)',
    'LUMENA_TASK_STEP_TIMEOUT_SEC':     'Timeout étape (s)',
    'LUMENA_TASK_STEP_TIMEOUT_RETRIES': 'Retries par étape',
  };
  return map[key] || key;
}

function _esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ─── Navigation ───────────────────────────────────────────────────
function _goNext() {
  if (_currentStep < _totalSteps() - 1) {
    _currentStep++;
    _renderStep();
    const cont = document.getElementById('setup-step-container');
    if (cont) {
      cont.scrollTop = 0;
      const target = cont.querySelector('input, select, textarea') || cont.querySelector('h2');
      if (target) target.focus();
    }
  }
}

function _goBack() {
  if (_currentStep > 0) {
    _currentStep--;
    _renderStep();
    const cont = document.getElementById('setup-step-container');
    if (cont) {
      const target = cont.querySelector('input, select, textarea') || cont.querySelector('h2');
      if (target) target.focus();
    }
  }
}

async function _finishSetup() {
  if (_isPreview) {
    _closeWizard();
    return;
  }

  const btn = document.getElementById('setup-finish');
  if (btn) { btn.disabled = true; btn.textContent = 'Sauvegarde...'; }

  try {
    const res = await fetch('/api/setup/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: _config, preview: false }),
    });
    const data = await res.json();
    if (data.success) {
      // P0.11: Store admin token returned by backend
      if (data.admin_token) {
        window.ADMIN_TOKEN = data.admin_token;
      }
      // P0.7: Warn if LLM is not ready (no valid API key)
      if (data.llm_ready === false && !data.restart_needed) {
        const overlay = document.getElementById('setup-wizard-overlay');
        const inner = overlay ? overlay.querySelector('.setup-wizard') : null;
        if (inner) {
          inner.innerHTML = `
            <div class="setup-step active" style="text-align:center;padding:2em">
              <div class="setup-step-icon"><i data-lucide="alert-triangle"></i></div>
              <h2>Configuration sauvegardée</h2>
              <p style="margin:.8em 0;color:var(--muted)">Aucun modèle IA n'est accessible pour le moment.<br>
              Vérifie tes clés API ou installe Ollama pour utiliser un modèle local.</p>
              <div class="setup-nav" style="margin-top:1.5em;gap:10px">
                <button class="setup-btn setup-btn-outline" id="llm-warn-retry">Réessayer</button>
                <button class="setup-btn" id="llm-warn-continue">Continuer quand même</button>
              </div>
            </div>`;
          if (typeof lucide !== 'undefined') lucide.createIcons();
          inner.querySelector('#llm-warn-retry')?.addEventListener('click', () => location.reload());
          inner.querySelector('#llm-warn-continue')?.addEventListener('click', () => _closeWizard());
          return;
        }
      }
      if (data.restart_needed) {
        // P4: Channels/services configurés → restart nécessaire
        const overlay = document.getElementById('setup-wizard-overlay');
        const inner = overlay ? overlay.querySelector('.setup-wizard') : null;
        if (inner) {
          inner.innerHTML = `
            <div class="setup-step active" style="text-align:center;padding:2em">
              <h2>Lumena est prête !</h2>
              <p style="margin:.8em 0">Configuration sauvegardée. Le chat web fonctionne déjà.</p>
              <div class="setup-info-box" style="text-align:left;margin:1em 0">
                <i data-lucide="refresh-cw" style="width:18px;height:18px;flex-shrink:0;margin-top:2px"></i>
                <div>
                  <strong>Redémarrage recommandé</strong><br>
                  Tu as configuré des services (Telegram, Discord, Autonomie...) qui nécessitent un redémarrage pour s'activer.<br>
                  Ferme cette fenêtre et relance <code>START.bat</code>.
                </div>
              </div>
              <div class="setup-nav" style="margin-top:1.5em">
                <button class="setup-btn" id="setup-restart-btn">Utiliser le chat web maintenant</button>
              </div>
            </div>`;
          const _restartBtn = inner.querySelector('#setup-restart-btn');
          if (_restartBtn) _restartBtn.onclick = () => location.reload();
          if (typeof lucide !== 'undefined') lucide.createIcons();
        } else {
          _closeWizard();
        }
      } else {
        _closeWizard();
      }
    } else {
      alert(data.error || 'Erreur lors de la sauvegarde.');
      if (btn) { btn.disabled = false; btn.textContent = 'Démarrer Lumena'; }
    }
  } catch (e) {
    alert('Erreur réseau: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Démarrer Lumena'; }
  }
}

// P1.1: Inject a dynamic welcome message after first wizard completion
function _injectWelcomeMessage() {
  if (localStorage.getItem('wizardJustDone')) return;
  localStorage.setItem('wizardJustDone', '1');
  const thread = document.getElementById('chat-thread');
  if (!thread) return;
  // Hide static welcome placeholder
  const welcome = document.getElementById('chat-welcome');
  if (welcome) welcome.style.display = 'none';
  const model = _config.LUMENA_DEFAULT_MODEL || window.selectedModel || 'deepseek-v3';
  const toolCount = document.getElementById('welcome-tools')?.textContent || '—';
  const msg = document.createElement('div');
  msg.className = 'message assistant';
  msg.innerHTML = `<div class="message-content"><p>Bienvenue ! Je suis <strong>Lumena</strong>, ton assistante IA.</p>
    <p>Modèle : <strong>${_esc(model)}</strong> | ${_esc(toolCount)} outils disponibles</p>
    <p>Pose-moi ta première question !</p></div>`;
  thread.appendChild(msg);
}

function _closeWizard() {
  const overlay = document.getElementById('setup-wizard-overlay');
  if (!overlay) return;
  overlay.classList.add('fade-out');
  setTimeout(async () => {
    overlay.setAttribute('hidden', '');
    overlay.classList.remove('fade-out');
    // Remove preview param from URL
    const url = new URL(window.location.href);
    url.searchParams.delete('preview');
    if (url.hash === '#setup') url.hash = '';
    window.history.replaceState({}, '', url);
    // P0.2: Skip startup screen — load models + start Lumena directly
    if (!_isPreview) {
      try {
        const startup = document.getElementById('startup-screen');
        if (startup) startup.setAttribute('hidden', '');
        if (typeof window.loadStartupModels === 'function') {
          await window.loadStartupModels();
        }
        window.selectedModel = _config.LUMENA_DEFAULT_MODEL
          || (window.allModels && (window.allModels.find(m => m.available) || {}).name)
          || 'deepseek-v3';
        if (typeof window.startLumena === 'function') {
          window.startLumena();
        }
        // P1.1: Inject welcome message in chat thread after first setup
        _injectWelcomeMessage();
      } catch (e) {
        console.error('[setup] Post-wizard startup failed:', e);
        location.reload();
      }
    }
  }, 500);
}
