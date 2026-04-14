/**
 * {{PROJECT_TITLE}} — Fullstack Application JS
 * SPA Router + Admin Panel + Auth + Toasts
 */

(function () {
  'use strict';

  // ─── Router ─────────────────────────────────────────────
  window.navigateTo = function (sectionId) {
    document.querySelectorAll('main > section, .auth-card-wrapper, .admin-layout').forEach(function (el) {
      el.classList.add('hidden');
    });
    var target = document.getElementById(sectionId);
    if (target) {
      target.classList.remove('hidden');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
    // Close mobile menu
    var nav = document.querySelector('.nav-links');
    if (nav) nav.classList.remove('open');
  };

  // ─── Mobile Menu ────────────────────────────────────────
  window.toggleMobileMenu = function () {
    var nav = document.querySelector('.nav-links');
    if (nav) nav.classList.toggle('open');
  };

  // ─── Admin Tabs ─────────────────────────────────────────
  window.showAdminTab = function (tabName) {
    document.querySelectorAll('.tab-content').forEach(function (el) {
      el.classList.remove('active');
    });
    document.querySelectorAll('.sidebar-nav a').forEach(function (el) {
      el.classList.remove('active');
    });
    var tab = document.getElementById('tab-' + tabName);
    if (tab) tab.classList.add('active');
    var link = document.querySelector('.sidebar-nav a[data-tab="' + tabName + '"]');
    if (link) link.classList.add('active');
  };

  // ─── Toggle Admin Sidebar (mobile) ─────────────────────
  window.toggleAdminSidebar = function () {
    var sidebar = document.querySelector('.admin-sidebar');
    if (sidebar) sidebar.classList.toggle('open');
  };

  // ─── Auth Handlers ──────────────────────────────────────
  window.handleLogin = function (e) {
    e.preventDefault();
    var form = e.target;
    var email = form.querySelector('[name="email"]').value;
    var password = form.querySelector('[name="password"]').value;

    if (!email || !password) {
      showToast('Veuillez remplir tous les champs.', 'error');
      return;
    }

    // Appel API simulé
    fetch('/api/index.php?route=login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          localStorage.setItem('token', data.token);
          localStorage.setItem('user', JSON.stringify(data.user));
          showToast('Connexion réussie !', 'success');
          navigateTo('admin');
        } else {
          showToast(data.error || 'Identifiants invalides', 'error');
        }
      })
      .catch(function () {
        // Mode démo : connexion simulée
        localStorage.setItem('token', 'demo-token-' + Date.now());
        localStorage.setItem('user', JSON.stringify({ name: 'Admin', email: email, role: 'admin' }));
        showToast('Connexion (mode démo)', 'success');
        navigateTo('admin');
      });
  };

  window.handleRegister = function (e) {
    e.preventDefault();
    var form = e.target;
    var name = form.querySelector('[name="name"]').value;
    var email = form.querySelector('[name="email"]').value;
    var password = form.querySelector('[name="password"]').value;
    var confirm = form.querySelector('[name="confirm"]').value;

    if (!name || !email || !password || !confirm) {
      showToast('Veuillez remplir tous les champs.', 'error');
      return;
    }
    if (password !== confirm) {
      showToast('Les mots de passe ne correspondent pas.', 'error');
      return;
    }
    if (password.length < 8) {
      showToast('Le mot de passe doit faire au moins 8 caractères.', 'error');
      return;
    }

    fetch('/api/index.php?route=register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, email: email, password: password })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          showToast('Inscription réussie ! Connectez-vous.', 'success');
          navigateTo('login');
        } else {
          showToast(data.error || 'Erreur lors de l\'inscription', 'error');
        }
      })
      .catch(function () {
        showToast('Inscription réussie (mode démo)', 'success');
        navigateTo('login');
      });
  };

  // ─── Contact Form ───────────────────────────────────────
  window.handleContactForm = function (e) {
    e.preventDefault();
    var form = e.target;
    var btn = form.querySelector('button[type="submit"]');
    var originalText = btn.textContent;

    btn.textContent = 'Envoi en cours…';
    btn.disabled = true;

    setTimeout(function () {
      showToast('Message envoyé avec succès !', 'success');
      form.reset();
      btn.textContent = originalText;
      btn.disabled = false;
    }, 1200);
  };

  // ─── Toast / Notification ───────────────────────────────
  function showToast(message, type) {
    type = type || 'success';
    var existing = document.querySelector('.toast');
    if (existing) existing.remove();

    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    document.body.appendChild(toast);

    requestAnimationFrame(function () {
      toast.classList.add('show');
    });

    setTimeout(function () {
      toast.classList.remove('show');
      setTimeout(function () { toast.remove(); }, 400);
    }, 3500);
  }
  window.showToast = showToast;

  // ─── Scroll Effects ─────────────────────────────────────
  function initScrollEffects() {
    var header = document.querySelector('.header');
    if (header) {
      window.addEventListener('scroll', function () {
        header.classList.toggle('scrolled', window.scrollY > 40);
      });
    }
  }

  // ─── Intersection Observer for fade-in ──────────────────
  function initFadeIn() {
    if (!('IntersectionObserver' in window)) {
      document.querySelectorAll('.fade-in').forEach(function (el) {
        el.classList.add('visible');
      });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });

    document.querySelectorAll('.fade-in').forEach(function (el) {
      observer.observe(el);
    });
  }

  // ─── Search in Admin Table ──────────────────────────────
  window.filterTable = function (input) {
    var query = input.value.toLowerCase();
    var table = input.closest('.data-table-wrapper').querySelector('.data-table');
    if (!table) return;
    var rows = table.querySelectorAll('tbody tr');
    rows.forEach(function (row) {
      var text = row.textContent.toLowerCase();
      row.style.display = text.includes(query) ? '' : 'none';
    });
  };

  // ─── Auth Guard ─────────────────────────────────────────
  window.checkAuth = function () {
    var token = localStorage.getItem('token');
    if (!token) {
      showToast('Veuillez vous connecter.', 'error');
      navigateTo('login');
      return false;
    }
    return true;
  };

  window.logout = function () {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    showToast('Déconnexion réussie.', 'success');
    navigateTo('home');
  };

  // ─── Init ───────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    initScrollEffects();
    initFadeIn();

    // Show home section by default
    navigateTo('home');
  });
})();
