/* ============================================================
   STRIPE DASHBOARD — Lumena Control Panel
   ============================================================ */

/* ---- Helpers ---- */
function _authHeaders(extra = {}) {
  const h = { ...extra };
  if (ADMIN_TOKEN) h['Authorization'] = `Bearer ${ADMIN_TOKEN}`;
  return h;
}

function _fmtMoney(cents, currency = 'eur') {
  const amount = (cents / 100).toFixed(2);
  const sym = currency.toLowerCase() === 'usd' ? '$' : currency.toLowerCase() === 'gbp' ? '£' : '€';
  return `${sym}${amount}`;
}

function _fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
}

function _statusBadge(status) {
  const map = {
    succeeded: ['#22c55e', '✓ Réussi'],
    requires_payment_method: ['#f59e0b', 'En attente'],
    requires_confirmation: ['#f59e0b', 'Confirmation'],
    canceled: ['#ef4444', '✗ Annulé'],
    processing: ['#6366f1', '⟳ Traitement'],
    active: ['#22c55e', '● Actif'],
    trialing: ['#6366f1', '▷ Essai'],
    past_due: ['#f59e0b', '⚠ Échu'],
    canceled_sub: ['#ef4444', '✗ Annulé'],
    incomplete: ['#94a3b8', '○ Incomplet'],
  };
  const [color, label] = map[status] || ['#94a3b8', status];
  return `<span style="background:${color}22;color:${color};border:1px solid ${color}44;border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600">${label}</span>`;
}

function _tableHead(...cols) {
  const cells = cols.map(c => `<th style="text-align:left;padding:10px 14px;font-size:11px;color:var(--muted);font-weight:600;border-bottom:1px solid var(--border)">${c}</th>`).join('');
  return `<thead><tr>${cells}</tr></thead>`;
}

function _emptyRow(msg, cols) {
  return `<tr><td colspan="${cols}" style="padding:32px;text-align:center;color:var(--muted);font-size:13px">${msg}</td></tr>`;
}

function _tableWrap(inner) {
  return `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">${inner}</table></div>`;
}

/* ---- Overview ---- */
export async function loadStripeOverview() {
  try {
    const r = await fetch('/api/stripe/dashboard/summary', { headers: _authHeaders() });
    if (!r.ok) { _stripeError('stripe-recent-payments', r.status); return; }
    const d = await r.json();

    // Balance cards
    const avail = d.balance?.available ?? null;
    const pend = d.balance?.pending ?? null;
    const cur = d.balance?.currency ?? 'eur';
    document.getElementById('stripe-balance-available').textContent = avail !== null ? _fmtMoney(avail, cur) : '—';
    document.getElementById('stripe-balance-pending').textContent = pend !== null ? _fmtMoney(pend, cur) : '—';
    document.getElementById('stripe-balance-currency').textContent = cur.toUpperCase();

    // Month stats
    const rev = d.month?.revenue ?? null;
    const cnt = d.month?.count ?? 0;
    const mCur = d.month?.currency ?? cur;
    document.getElementById('stripe-month-revenue').textContent = rev !== null ? _fmtMoney(rev, mCur) : '—';
    document.getElementById('stripe-month-count').textContent = cnt ? `${cnt} paiement${cnt > 1 ? 's' : ''}` : '';

    // Update sidebar badge
    const badge = document.getElementById('stat-stripe-balance');
    if (badge && avail !== null) badge.textContent = _fmtMoney(avail, cur);

    // Recent payments table
    const payments = d.recent_payments ?? [];
    const el = document.getElementById('stripe-recent-payments');
    if (!payments.length) {
      el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:13px">Aucun paiement récent</div>`;
      return;
    }
    const rows = payments.map(p => `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:10px 14px">${_fmtDate(p.created)}</td>
      <td style="padding:10px 14px;font-weight:600">${_fmtMoney(p.amount, p.currency)}</td>
      <td style="padding:10px 14px">${_statusBadge(p.status)}</td>
      <td style="padding:10px 14px;color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.description || '—'}</td>
      <td style="padding:10px 14px;font-family:monospace;font-size:11px;color:var(--muted)">${p.id?.slice(-12) ?? ''}</td>
    </tr>`).join('');
    el.innerHTML = _tableWrap(_tableHead('Date', 'Montant', 'Statut', 'Description', 'ID') + `<tbody>${rows}</tbody>`);
    window.lucide?.createIcons();
  } catch (e) {
    _stripeError('stripe-recent-payments', e.message);
  }
}

/* ---- Payments ---- */
export async function loadStripePayments() {
  const el = document.getElementById('stripe-payments-list');
  el.innerHTML = `<div style="padding:20px;text-align:center;color:var(--muted);font-size:13px">Chargement...</div>`;
  try {
    const r = await fetch('/api/stripe/dashboard/payments?limit=50', { headers: _authHeaders() });
    if (!r.ok) { _stripeErrorEl(el, r.status); return; }
    const d = await r.json();
    const payments = d.payments ?? [];
    if (!payments.length) {
      el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:13px">Aucun paiement</div>`;
      return;
    }
    const rows = payments.map(p => `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:10px 14px">${_fmtDate(p.created)}</td>
      <td style="padding:10px 14px;font-weight:600">${_fmtMoney(p.amount, p.currency)}</td>
      <td style="padding:10px 14px">${_statusBadge(p.status)}</td>
      <td style="padding:10px 14px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.description || '—'}</td>
      <td style="padding:10px 14px;color:var(--muted)">${p.customer_email || p.customer || '—'}</td>
      <td style="padding:10px 14px;font-family:monospace;font-size:11px;color:var(--muted)">${p.id ?? ''}</td>
    </tr>`).join('');
    el.innerHTML = _tableWrap(_tableHead('Date', 'Montant', 'Statut', 'Description', 'Client', 'ID') + `<tbody>${rows}</tbody>`);
  } catch (e) {
    _stripeErrorEl(el, e.message);
  }
}

/* ---- Subscriptions ---- */
export async function loadStripeSubscriptions() {
  const el = document.getElementById('stripe-subscriptions-list');
  el.innerHTML = `<div style="padding:20px;text-align:center;color:var(--muted);font-size:13px">Chargement...</div>`;
  try {
    const r = await fetch('/api/stripe/dashboard/subscriptions?limit=50', { headers: _authHeaders() });
    if (!r.ok) { _stripeErrorEl(el, r.status); return; }
    const d = await r.json();
    const subs = d.subscriptions ?? [];
    if (!subs.length) {
      el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:13px">Aucun abonnement</div>`;
      return;
    }
    const rows = subs.map(s => `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:10px 14px">${_fmtDate(s.created)}</td>
      <td style="padding:10px 14px;color:var(--muted)">${s.customer_email || s.customer || '—'}</td>
      <td style="padding:10px 14px">${_statusBadge(s.status)}</td>
      <td style="padding:10px 14px;font-weight:600">${s.amount !== undefined ? _fmtMoney(s.amount, s.currency) : '—'} <span style="color:var(--muted);font-size:11px">/ ${s.interval || '?'}</span></td>
      <td style="padding:10px 14px;color:var(--muted);font-size:12px">${s.price_id || '—'}</td>
      <td style="padding:10px 14px;font-size:12px">${s.current_period_end ? _fmtDate(s.current_period_end) : '—'}</td>
    </tr>`).join('');
    el.innerHTML = _tableWrap(_tableHead('Créé', 'Client', 'Statut', 'Montant', 'Prix', 'Renouvellement') + `<tbody>${rows}</tbody>`);
  } catch (e) {
    _stripeErrorEl(el, e.message);
  }
}

/* ---- Products ---- */
export async function loadStripeProducts() {
  const el = document.getElementById('stripe-products-list');
  el.innerHTML = `<div style="padding:20px;text-align:center;color:var(--muted);font-size:13px">Chargement...</div>`;
  try {
    const r = await fetch('/api/stripe/dashboard/products?limit=50', { headers: _authHeaders() });
    if (!r.ok) { _stripeErrorEl(el, r.status); return; }
    const d = await r.json();
    const products = d.products ?? [];
    if (!products.length) {
      el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:13px">Aucun produit</div>`;
      return;
    }
    const rows = products.flatMap(p => {
      const prices = p.prices ?? [];
      if (!prices.length) {
        return [`<tr style="border-bottom:1px solid var(--border)">
          <td style="padding:10px 14px;font-weight:600">${p.name || '—'}</td>
          <td style="padding:10px 14px;color:var(--muted);font-size:12px">${p.description || ''}</td>
          <td style="padding:10px 14px;color:var(--muted)">—</td>
          <td style="padding:10px 14px;color:var(--muted)">—</td>
          <td style="padding:10px 14px">${p.active ? '<span style="color:#22c55e">● Actif</span>' : '<span style="color:#94a3b8">○ Inactif</span>'}</td>
        </tr>`];
      }
      return prices.map((pr, i) => `<tr style="border-bottom:1px solid var(--border)">
        ${i === 0 ? `<td style="padding:10px 14px;font-weight:600" rowspan="${prices.length}">${p.name || '—'}</td>
        <td style="padding:10px 14px;color:var(--muted);font-size:12px" rowspan="${prices.length}">${p.description || ''}</td>` : ''}
        <td style="padding:10px 14px;font-weight:600">${_fmtMoney(pr.unit_amount, pr.currency)}</td>
        <td style="padding:10px 14px;color:var(--muted);font-size:12px">${pr.recurring ? `/ ${pr.recurring.interval}` : 'Unique'}</td>
        ${i === 0 ? `<td style="padding:10px 14px" rowspan="${prices.length}">${p.active ? '<span style="color:#22c55e">● Actif</span>' : '<span style="color:#94a3b8">○ Inactif</span>'}</td>` : ''}
      </tr>`);
    });
    el.innerHTML = _tableWrap(_tableHead('Produit', 'Description', 'Prix', 'Type', 'État') + `<tbody>${rows.join('')}</tbody>`);
  } catch (e) {
    _stripeErrorEl(el, e.message);
  }
}

/* ---- Quick Payment Link ---- */
window.stripeCreateQuickLink = async function () {
  const amount = parseFloat(document.getElementById('stripe-ql-amount').value);
  const desc = document.getElementById('stripe-ql-desc').value.trim();
  const resultEl = document.getElementById('stripe-ql-result');
  if (!amount || amount < 0.5) {
    resultEl.style.display = 'block';
    resultEl.innerHTML = `<div style="color:#ef4444;font-size:12px">Montant invalide (minimum 0.50 €)</div>`;
    return;
  }
  resultEl.style.display = 'block';
  resultEl.innerHTML = `<div style="color:var(--muted);font-size:12px">Génération...</div>`;
  try {
    const r = await fetch('/api/stripe/dashboard/payment-link', {
      method: 'POST',
      headers: _authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ amount_cents: Math.round(amount * 100), description: desc || 'Paiement Lumena', currency: 'eur' }),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      resultEl.innerHTML = `<div style="color:#ef4444;font-size:12px">${d.error || 'Erreur serveur'}</div>`;
      return;
    }
    resultEl.innerHTML = `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <a href="${d.url}" target="_blank" style="color:var(--accent);font-size:13px;word-break:break-all">${d.url}</a>
      <button class="btn" style="font-size:11px;padding:3px 10px" onclick="navigator.clipboard.writeText('${d.url}').then(()=>this.textContent='✓ Copié!')">Copier</button>
    </div>`;
  } catch (e) {
    resultEl.innerHTML = `<div style="color:#ef4444;font-size:12px">${e.message}</div>`;
  }
};

/* ---- Error helpers ---- */
function _stripeError(elId, detail) {
  const el = document.getElementById(elId);
  if (el) _stripeErrorEl(el, detail);
}
function _stripeErrorEl(el, detail) {
  el.innerHTML = `<div style="padding:20px;text-align:center;color:#ef4444;font-size:13px">
    Erreur de connexion Stripe (${detail})<br>
    <span style="color:var(--muted);font-size:11px">Vérifiez que STRIPE_API_KEY est configuré dans les paramètres</span>
  </div>`;
}
