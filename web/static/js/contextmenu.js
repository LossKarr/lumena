/* ============================================================
   MENU CONTEXTUEL (clic droit) — édition de texte
   Couper · Copier · Coller · Sélectionner · Tout sélectionner
   Léger, autonome, thématisé (pro). Remplace le menu natif.
   ============================================================ */

let _menuEl = null;
let _targetEl = null;
let _clickX = 0, _clickY = 0;

const _WORD_RE = /[^\s.,;:!?()\[\]{}"'`«»…]/;

function _isEditable(el) {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA') return !el.disabled && !el.readOnly;
  if (tag === 'INPUT') {
    const t = (el.type || 'text').toLowerCase();
    return ['text', 'search', 'url', 'tel', 'email', 'password', 'number', ''].includes(t) && !el.disabled && !el.readOnly;
  }
  return !!el.isContentEditable;
}

function _isTextInput(el) {
  return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
}

function _selectionText(el) {
  if (_isTextInput(el)) {
    const s = el.selectionStart ?? 0, e = el.selectionEnd ?? 0;
    return e > s ? String(el.value).slice(s, e) : '';
  }
  return (window.getSelection?.().toString()) || '';
}

const _ITEMS = [
  { act: 'cut',       label: 'Couper',            keys: 'Ctrl+X' },
  { act: 'copy',      label: 'Copier',            keys: 'Ctrl+C' },
  { act: 'paste',     label: 'Coller',            keys: 'Ctrl+V' },
  { sep: true },
  { act: 'select',    label: 'Sélectionner',      keys: '' },
  { act: 'selectAll', label: 'Tout sélectionner', keys: 'Ctrl+A' },
];

function _buildMenu() {
  const el = document.createElement('div');
  el.id = 'lumena-ctxmenu';
  el.style.cssText = [
    'position:fixed', 'z-index:99999', 'display:none', 'min-width:184px',
    'background:var(--surface,#1b1b1f)', 'border:1px solid var(--border,#333)',
    'border-radius:10px', 'padding:5px', 'box-shadow:0 10px 30px rgba(0,0,0,.5)',
    'font-size:12.5px', 'user-select:none', '-webkit-user-select:none',
    'font-family:var(--font,inherit)',
  ].join(';');
  el.innerHTML = _ITEMS.map(it => {
    if (it.sep) return `<div style="height:1px;background:var(--border,#333);margin:4px 6px"></div>`;
    return `<div class="lctx-item" data-act="${it.act}"
      style="display:flex;align-items:center;justify-content:space-between;gap:24px;padding:7px 11px;border-radius:7px;cursor:pointer;color:var(--text,#eee);transition:background .08s">
      <span>${it.label}</span>${it.keys ? `<span style="color:var(--muted,#888);font-size:11px">${it.keys}</span>` : ''}</div>`;
  }).join('');
  el.querySelectorAll('.lctx-item').forEach(it => {
    it.addEventListener('mouseenter', () => { if (it.dataset.disabled !== '1') it.style.background = 'var(--accent-soft,rgba(255,255,255,.07))'; });
    it.addEventListener('mouseleave', () => { it.style.background = 'transparent'; });
    it.addEventListener('mousedown', (e) => {
      e.preventDefault();  // garde le focus/sélection de la cible
      if (it.dataset.disabled === '1') return;
      _runAction(it.dataset.act);
      _hide();
    });
  });
  document.body.appendChild(el);
  return el;
}

function _setItemState(act, enabled) {
  const it = _menuEl.querySelector(`.lctx-item[data-act="${act}"]`);
  if (!it) return;
  it.dataset.disabled = enabled ? '0' : '1';
  it.style.opacity = enabled ? '1' : '.4';
  it.style.cursor = enabled ? 'pointer' : 'default';
}

async function _runAction(act) {
  const el = _targetEl;
  try {
    if (act === 'copy') {
      const sel = _selectionText(el);
      if (sel) await _clipWrite(sel);
    } else if (act === 'cut') {
      const sel = _selectionText(el);
      if (sel && _isEditable(el)) { await _clipWrite(sel); _replaceSelection(el, ''); }
    } else if (act === 'paste') {
      if (_isEditable(el)) { const t = await _clipRead(); if (t) _replaceSelection(el, t); }
    } else if (act === 'select') {
      _selectWord(el);
    } else if (act === 'selectAll') {
      _selectAll(el);
    }
  } catch (_) { /* clipboard refusé : échec silencieux */ }
}

async function _clipWrite(text) {
  if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(text); return; } catch (_) {} }
  try { document.execCommand('copy'); } catch (_) {}
}

async function _clipRead() {
  if (navigator.clipboard?.readText) { try { return await navigator.clipboard.readText(); } catch (_) {} }
  return '';
}

function _replaceSelection(el, text) {
  if (_isTextInput(el)) {
    const s = el.selectionStart ?? el.value.length, e = el.selectionEnd ?? el.value.length;
    el.value = el.value.slice(0, s) + text + el.value.slice(e);
    const pos = s + text.length;
    el.selectionStart = el.selectionEnd = pos;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.focus();
  } else if (el?.isContentEditable) {
    document.execCommand('insertText', false, text);
  }
}

// « Sélectionner » = le mot sous le curseur.
function _selectWord(el) {
  if (_isTextInput(el)) {
    const v = el.value || '';
    let pos = el.selectionStart ?? 0;
    if (pos > 0 && (pos >= v.length || !_WORD_RE.test(v[pos]))) pos--;
    let s = pos, e = pos;
    while (s > 0 && _WORD_RE.test(v[s - 1])) s--;
    while (e < v.length && _WORD_RE.test(v[e])) e++;
    el.focus();
    el.selectionStart = s; el.selectionEnd = Math.max(s, e);
    return;
  }
  let range = null;
  if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(_clickX, _clickY);
  else if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(_clickX, _clickY);
    if (p) { range = document.createRange(); range.setStart(p.offsetNode, p.offset); range.collapse(true); }
  }
  if (!range) return;
  const node = range.startContainer;
  if (node.nodeType !== 3) return;  // pas un nœud texte
  const text = node.textContent || '';
  let i = range.startOffset, s = i, e = i;
  while (s > 0 && _WORD_RE.test(text[s - 1])) s--;
  while (e < text.length && _WORD_RE.test(text[e])) e++;
  const r = document.createRange();
  r.setStart(node, s); r.setEnd(node, e);
  const sel = window.getSelection();
  sel.removeAllRanges(); sel.addRange(r);
}

// « Tout sélectionner » = tout le champ, sinon toute la page.
function _selectAll(el) {
  if (_isTextInput(el)) { el.focus(); el.select(); return; }
  let container = el?.isContentEditable ? el : document.body;
  const r = document.createRange();
  r.selectNodeContents(container);
  const sel = window.getSelection();
  sel.removeAllRanges(); sel.addRange(r);
}

function _hide() { if (_menuEl) _menuEl.style.display = 'none'; }

function _onContextMenu(e) {
  _targetEl = e.target;
  _clickX = e.clientX; _clickY = e.clientY;
  if (!_menuEl) _menuEl = _buildMenu();

  const editable = _isEditable(_targetEl);
  const sel = _selectionText(_targetEl);
  _setItemState('copy', !!sel);
  _setItemState('cut', !!sel && editable);
  _setItemState('paste', editable);
  _setItemState('select', true);
  _setItemState('selectAll', true);

  e.preventDefault();
  const mw = 200, mh = 200;
  const x = Math.min(e.clientX, window.innerWidth - mw);
  const y = Math.min(e.clientY, window.innerHeight - mh);
  _menuEl.style.left = `${Math.max(4, x)}px`;
  _menuEl.style.top = `${Math.max(4, y)}px`;
  _menuEl.style.display = 'block';
}

export function initContextMenu() {
  document.addEventListener('contextmenu', _onContextMenu);
  document.addEventListener('click', _hide);
  document.addEventListener('scroll', _hide, true);
  window.addEventListener('blur', _hide);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') _hide(); });
}
