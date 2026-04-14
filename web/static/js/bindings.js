// Event delegation — remplace tous les onclick inline
// Chaque element avec data-action="funcName" appelle window[funcName](arg)
document.addEventListener('click', e => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  // data-self-only : ne trigger que si le click est sur l'element lui-meme
  if (el.dataset.selfOnly === 'true' && e.target !== el) return;
  const action = el.dataset.action;
  const arg = el.dataset.arg;
  const fn = window[action];
  if (typeof fn === 'function') {
    arg !== undefined ? fn(arg, el, e) : fn(el, e);
  }
});
