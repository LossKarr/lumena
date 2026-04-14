/* ═══════════════════════════════════════════════════════════
   {{PROJECT_TITLE}} — Application JavaScript
   ═══════════════════════════════════════════════════════════ */

// ═══ ROUTEUR SPA — GLOBAL (hors DOMContentLoaded) ═══
function navigateTo(pageId) {
  // Masquer toutes les pages
  document.querySelectorAll('section[id^="page-"]').forEach(s => {
    s.classList.remove('active');
    s.style.display = 'none';
  });

  // Afficher la page cible
  const target = document.getElementById(pageId);
  if (target) {
    target.classList.add('active');
    target.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // Re-trigger animations
    target.querySelectorAll('.fade-in').forEach(el => {
      el.classList.remove('visible');
      setTimeout(() => el.classList.add('visible'), 50);
    });
  }

  // Mettre à jour nav active
  document.querySelectorAll('[data-page]').forEach(link => {
    link.classList.toggle('active', link.dataset.page === pageId);
  });

  // Fermer menu mobile si ouvert
  closeMobileMenu();
}

// ═══ MENU MOBILE ═══
function toggleMobileMenu() {
  const nav = document.getElementById('mainNav');
  const overlay = document.getElementById('mobileOverlay');
  nav.classList.toggle('open');
  overlay.classList.toggle('active');
}

function closeMobileMenu() {
  const nav = document.getElementById('mainNav');
  const overlay = document.getElementById('mobileOverlay');
  if (nav) nav.classList.remove('open');
  if (overlay) overlay.classList.remove('active');
}

// ═══ FORMULAIRE CONTACT ═══
function handleContactForm(e) {
  e.preventDefault();
  const form = e.target;
  const name = form.querySelector('#name').value;

  // Feedback visuel
  const btn = form.querySelector('button[type="submit"]');
  const originalText = btn.innerHTML;
  btn.innerHTML = '<i class="fas fa-check"></i> Envoyé !';
  btn.style.background = 'hsl(145, 60%, 45%)';
  btn.disabled = true;

  setTimeout(() => {
    form.reset();
    btn.innerHTML = originalText;
    btn.style.background = '';
    btn.disabled = false;
  }, 3000);
}

// ═══ INITIALISATION ═══
document.addEventListener('DOMContentLoaded', () => {
  // IntersectionObserver pour animations
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const delay = parseInt(entry.target.dataset.delay || '0');
        setTimeout(() => entry.target.classList.add('visible'), delay);
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.fade-in').forEach(el => observer.observe(el));

  // Header scroll effect
  const header = document.getElementById('main-header');
  if (header) {
    window.addEventListener('scroll', () => {
      header.classList.toggle('scrolled', window.scrollY > 50);
    });
  }

  // Activer la première page
  const activePage = document.querySelector('section[id^="page-"].active');
  if (activePage) {
    activePage.style.display = 'block';
    activePage.querySelectorAll('.fade-in').forEach(el => {
      const delay = parseInt(el.dataset.delay || '0');
      setTimeout(() => el.classList.add('visible'), delay + 300);
    });
  }
});
