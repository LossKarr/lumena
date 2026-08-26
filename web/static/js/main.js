/* ============================================================
   MAIN — ES Module entry point
   Imports all modules and exposes functions on window
   for HTML onclick handler backward compatibility.
   ============================================================ */

// ── Utils ──
import {
  esc, setText, fmtDur, loadingDots, logC, clearConsole
} from './utils.js';

// ── Navigation ──
import {
  setupNavigation, switchPanel, toggleSection, toggleNavCollapse,
  toggleMobileNav, toggleFocus, toggleTheme, applyTheme,
  loadPanelData, openCommandPalette, closeCommandPalette, filterCommands
} from './navigation.js?v=2';

// ── Activity ──
import {
  openSidebar, closeSidebar, toggleSidebar, startActivityFeed, pushActivity,
  updateActivityStats, stopActivityFeed
} from './activity.js';

// ── Chat ──
import {
  setupTextarea, quickSend, sendMessage, retryLastMessage, cancelStream, toggleChatDictation,
  addMsg, buildMetaHtml, normalizeEdit, mergeEdits,
  toggleDiffView, copyDiffContent, buildDiffViewerHtml,
  toggleDiffFile, toggleAllDiffs, acceptAllEdits, buildDocumentsHtml,
  undoSessionEdits, undoSingleFile, undoSessionEnc, undoSingleFileEnc,
  handleFileSelect, addAttachment, removeAttachment, clearAttachments,
  renderAttachments, loadChatHistory, clearChatHistory, exportChatMarkdown,
  resumeSessionInChat
} from './chat.js?v=1';

// ── API ──
import {
  loadStatus, loadRepoMap, loadRules, loadInstincts, loadTools,
  renderTools, filterTools, loadEmotions, loadHooks,
  loadVoiceStatus, toggleVoiceAssistant, updateVoiceUI, stopVoiceAudio, toggleVoiceMute, testVoiceOutput,
  searchCode, loadRecentMemories, searchMemory,
  initTraceStream, loadTraceRecent, renderTraceEvent,
  checkHealth, filterTrace, clearTraceList, updateTraceStats
} from './api.js';

// ── Panels ──
import {
  loadJournal, renderJournal, loadFacts, loadProviders, loadAlerts,
  loadTraining, loadFinetuning, loadLogsRecent, renderLogs, loadConfig, toggleSecret,
  saveConfig, showCfgMsg, setCfgLevel, loadSessions, filterSessions,
  loadSessionDetail, closeSessionDetail, archiveSession, exportSessionMarkdown,
  loadTelegramDetails, loadAutonomyDetails, loadWhatsAppDetails,
  loadDocs, switchDoc, saveDoc,
  loadProductDocs, switchDocSection,
  loadIonosSites, addIonosSite, removeIonosSite,
  openIonosDbModal, closeIonosDbModal, saveIonosDb, testIonosDb, clearIonosDb,
  openIonosDbExplorer, closeIonosDbExplorer, ionosDbSchema, ionosDbPreview,
  refreshIonosBridgeStatus, installIonosBridge, removeIonosBridge,
  openIonosWriteConfig, saveIonosWriteConfig, openIonosDbWriteModal, submitIonosDbWrite,
  toggleIonosSandbox, openIonosSandboxCreate, submitIonosSandboxCreate,
  openIonosSnapshots, toggleIonosRestore, restoreIonosSnapshot, deleteIonosSnapshot,
  openIonosDeleteConfig, saveIonosDeleteConfig, openIonosDbDeleteModal, submitIonosDbDelete,
  openIonosPendingActions, toggleIonosReact, toggleIonosReactDelete, toggleIonosSandboxDrop, toggleIonosSandboxClear, approveIonosAction, rejectIonosAction,
  loadInstancesNetwork, discoverLanPeers, pairSelectedPeer, blockSelectedPeer,
  generatePairingCode, acceptPairing, loadFirewallCommand, applyFirewallRule,
  loadNetworkSimple, toggleNetworkAdvanced, showSimplePairingForm, blockPeerSimple,
  loadPeerHistory, filterPeerHistory, selectPeerExchange,
  showNetworkHistory, backToNetworkSimple, togglePeerMaster, togglePeerHalt, releasePeerQuarantine, loadDeliverables, cancelPeerMission, relaunchPeerMission, setAutonomyMode, testSuggestion,
  deletePeerSimple, deleteLocalInstance, cleanupLocalInstances,
  testDelegation, loadNetworkDiagnostic, hideNetworkDiagnostic,
  loadCollaborationPanel, createSharedKnowledgeFromUi, shareKnowledgeFromUi,
  revokeKnowledgeFromUi, importKnowledgeFromUi, setPeerScope, setPeerCapability,
  setPeerAlias, setPeerScopesBulk, revokePeerToken, probePeer,
  loadNetworkObservability, cleanupPeerRuntime, sendTeamPromptFromUi, refreshNetworkLive,
  loadMissions, cancelMissionUi, closeMissionStream, toggleMissionCard
} from './panels.js?v=13';

// ── Overview ──
import { loadOverview, stopOverview } from './overview.js?v=2';

// ── Stripe ──
import {
  loadStripeOverview, loadStripePayments,
  loadStripeSubscriptions, loadStripeProducts
} from './stripe.js';

// ── Workspaces ──
import {
  loadWorkspaces, renderWorkspaces,
  serveAndOpenWorkspace, stopWorkspace, continueWorkspace, deleteWorkspace,
  toggleWsTree, filterWorkspaces, sortWorkspaces
} from './workspaces.js';

import { loadDocumentStudio } from './document-studio.js?v=14';
import { initOnboarding, replayOnboarding } from './onboarding.js?v=8';

// ── Tasks ──
import {
  showNewTaskForm, createTask, startTaskPoll, cancelTask,
  loadActiveTasks, renderTasks, loadDaemonActivity, renderDaemon,
  renderScheduledTasks, pushOverviewTraceEvent, renderOverviewTraceFeed,
  renderTaskProgress, resetTaskProgress, hideTaskProgressDelayed
} from './tasks.js';

// ── Startup ──
import {
  loadStartupModels, selectStartupModel, startLumena,
  toggleModelDropdown, closeModelPicker, setModelFilter, setModelPanel, setModelSource, filterModelSearch,
  loadModels, loadImageModels, switchModel, switchCatalogModel, toggleAgent,
  startLiveRefreshLoops, scheduleStatusRefresh
} from './startup.js?v=2';

// ── Expose ALL public functions on window for onclick compat ──
Object.assign(window, {
  // utils
  esc, setText, fmtDur, loadingDots, logC, clearConsole,
  // navigation
  setupNavigation, switchPanel, toggleSection, toggleNavCollapse,
  toggleMobileNav, toggleFocus, toggleTheme, applyTheme,
  loadPanelData, openCommandPalette, closeCommandPalette, filterCommands,
  loadDocumentStudio,
  // activity
  openSidebar, closeSidebar, toggleSidebar, startActivityFeed, pushActivity,
  updateActivityStats, stopActivityFeed,
  // chat
  setupTextarea, quickSend, sendMessage, retryLastMessage, cancelStream, toggleChatDictation,
  addMsg, buildMetaHtml, normalizeEdit, mergeEdits,
  toggleDiffView, copyDiffContent, buildDiffViewerHtml,
  toggleDiffFile, toggleAllDiffs, acceptAllEdits, buildDocumentsHtml,
  undoSessionEdits, undoSingleFile, undoSessionEnc, undoSingleFileEnc,
  handleFileSelect, addAttachment, removeAttachment, clearAttachments,
  renderAttachments, loadChatHistory, clearChatHistory, exportChatMarkdown,
  resumeSessionInChat,
  // api
  loadStatus, loadRepoMap, loadRules, loadInstincts, loadTools,
  renderTools, filterTools, loadEmotions, loadHooks,
  loadVoiceStatus, toggleVoiceAssistant, updateVoiceUI, stopVoiceAudio, toggleVoiceMute, testVoiceOutput,
  searchCode, loadRecentMemories, searchMemory,
  initTraceStream, loadTraceRecent, renderTraceEvent,
  checkHealth, filterTrace, clearTraceList, updateTraceStats,
  // panels
  loadJournal, renderJournal, loadFacts, loadProviders, loadAlerts,
  loadTraining, loadFinetuning, loadLogsRecent, renderLogs, loadConfig, toggleSecret,
  saveConfig, showCfgMsg, setCfgLevel, loadSessions, filterSessions,
  loadSessionDetail, closeSessionDetail, archiveSession, exportSessionMarkdown, loadOverview, stopOverview,
  loadTelegramDetails, loadAutonomyDetails, loadWhatsAppDetails,
  loadDocs, switchDoc, saveDoc,
  loadProductDocs, switchDocSection,
  loadIonosSites, addIonosSite, removeIonosSite,
  openIonosDbModal, closeIonosDbModal, saveIonosDb, testIonosDb, clearIonosDb,
  openIonosDbExplorer, closeIonosDbExplorer, ionosDbSchema, ionosDbPreview,
  refreshIonosBridgeStatus, installIonosBridge, removeIonosBridge,
  openIonosWriteConfig, saveIonosWriteConfig, openIonosDbWriteModal, submitIonosDbWrite,
  toggleIonosSandbox, openIonosSandboxCreate, submitIonosSandboxCreate,
  openIonosSnapshots, toggleIonosRestore, restoreIonosSnapshot, deleteIonosSnapshot,
  openIonosDeleteConfig, saveIonosDeleteConfig, openIonosDbDeleteModal, submitIonosDbDelete,
  openIonosPendingActions, toggleIonosReact, toggleIonosReactDelete, toggleIonosSandboxDrop, toggleIonosSandboxClear, approveIonosAction, rejectIonosAction,
  loadInstancesNetwork, discoverLanPeers, pairSelectedPeer, blockSelectedPeer,
  generatePairingCode, acceptPairing, loadFirewallCommand, applyFirewallRule,
  loadNetworkSimple, toggleNetworkAdvanced, showSimplePairingForm, blockPeerSimple,
  loadPeerHistory, filterPeerHistory, selectPeerExchange,
  showNetworkHistory, backToNetworkSimple, togglePeerMaster, togglePeerHalt, releasePeerQuarantine, loadDeliverables, cancelPeerMission, relaunchPeerMission, setAutonomyMode, testSuggestion,
  deletePeerSimple, deleteLocalInstance, cleanupLocalInstances,
  testDelegation, loadNetworkDiagnostic, hideNetworkDiagnostic,
  loadCollaborationPanel, createSharedKnowledgeFromUi, shareKnowledgeFromUi,
  revokeKnowledgeFromUi, importKnowledgeFromUi, setPeerScope, setPeerCapability,
  setPeerAlias, setPeerScopesBulk, revokePeerToken, probePeer,
  loadNetworkObservability, cleanupPeerRuntime, sendTeamPromptFromUi, refreshNetworkLive,
  loadMissions, cancelMissionUi, closeMissionStream, toggleMissionCard,
  // stripe
  loadStripeOverview, loadStripePayments,
  loadStripeSubscriptions, loadStripeProducts,
  // workspaces
  loadWorkspaces, renderWorkspaces,
  serveAndOpenWorkspace, stopWorkspace, continueWorkspace, deleteWorkspace,
  toggleWsTree, filterWorkspaces, sortWorkspaces,
  // tasks
  showNewTaskForm, createTask, startTaskPoll, cancelTask,
  loadActiveTasks, renderTasks, loadDaemonActivity, renderDaemon,
  renderScheduledTasks, pushOverviewTraceEvent, renderOverviewTraceFeed,
  renderTaskProgress, resetTaskProgress, hideTaskProgressDelayed,
  // startup
  loadStartupModels, selectStartupModel, startLumena,
  toggleModelDropdown, closeModelPicker, setModelFilter, setModelPanel, setModelSource, filterModelSearch,
  loadModels, loadImageModels, switchModel, switchCatalogModel, toggleAgent,
  startLiveRefreshLoops, scheduleStatusRefresh,
  initOnboarding, replayOnboarding,
});

// ── Shutdown Lumena ──
window._shutdownLumena = async function() {
  if (!confirm('Arrêter Lumena ? Le serveur et tous les services seront stoppés.')) return;
  const btn = document.getElementById('shutdown-btn');
  if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
  try {
    const resp = await fetch(`${API_BASE || ''}/api/shutdown`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${ADMIN_TOKEN || ''}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;background:var(--bg,#0a0f14);color:var(--text,#d4d4d8);font-family:sans-serif"><h2 style="margin-bottom:12px">Lumena arrêtée</h2><p style="color:var(--muted,#636370);font-size:14px">Le serveur a été stoppé proprement. Vous pouvez fermer cet onglet.</p></div>';
  } catch (e) {
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
    alert('Erreur shutdown: ' + e.message);
  }
};

// ── Init (module scripts are deferred so DOM is ready) ──
(function _init() {
  loadStartupModels();
  const agentBtn = document.getElementById('agent-toggle');
  if (agentBtn) {
    agentBtn.innerHTML = useAgent
      ? '<i data-lucide="plug"></i> Agent ON'
      : '<i data-lucide="plug"></i> Agent OFF';
    agentBtn.classList.toggle('active', useAgent);
  }
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); openCommandPalette(); }
    if (e.key === 'Escape') closeCommandPalette();
  });

  // ── Compose toolbar listeners (replaces inline onclick) ──
  const q = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };
  q('btn-export-md', () => exportChatMarkdown());
  q('btn-clear-chat', () => { if (confirm('Effacer la conversation ?')) clearChatHistory(); });
  q('btn-attach-file', () => document.getElementById('file-upload-input').click());
  q('btn-chat-dictation', () => toggleChatDictation());
  q('btn-toggle-focus', () => toggleFocus());
  q('send-btn', () => { if(isLoading) cancelStream(); else sendMessage(); });
  q('replay-onboarding-btn', () => replayOnboarding());
  const fileInput = document.getElementById('file-upload-input');
  if (fileInput) fileInput.addEventListener('change', e => handleFileSelect(e));
})();

// ── Lucide Icons — init + auto-refresh on DOM changes ──
(function _initLucide() {
  if (typeof lucide === 'undefined') return;
  lucide.createIcons();
  let pending = false;
  new MutationObserver(() => {
    if (!pending) {
      pending = true;
      requestAnimationFrame(() => { lucide.createIcons(); pending = false; });
    }
  }).observe(document.body, { childList: true, subtree: true });
})();

// ── Global dark <select> auto-upgrade ──
// Replaces ALL native <select> with a dark-themed custom dropdown.
// The original <select> stays hidden and synced for value compat.
(function _initDarkSelects() {
  const _ARROW_SVG = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

  function upgradeSelect(sel) {
    if (sel.dataset.darkUpgraded || sel.closest('.dark-select') || sel.closest('.ft-custom-select')) return;
    sel.dataset.darkUpgraded = '1';

    const wrap = document.createElement('div');
    wrap.className = 'dark-select';
    // Carry over sizing from original select
    const cs = sel.style;
    if (cs.width)    wrap.style.width = cs.width;
    if (cs.minWidth)  wrap.style.minWidth = cs.minWidth;
    if (cs.height)    wrap.style.setProperty('--ds-h', cs.height);
    if (cs.fontSize)  wrap.style.setProperty('--ds-fs', cs.fontSize);
    if (!cs.width && !cs.minWidth) wrap.style.width = '100%';
    // Copy classes that matter for layout
    if (sel.classList.contains('input')) wrap.classList.add('input-wrap');
    if (sel.classList.contains('auto-adv-input')) {
      wrap.style.width = '';
      wrap.style.minWidth = '170px';
    }

    sel.parentNode.insertBefore(wrap, sel);
    sel.style.cssText = 'position:absolute;opacity:0;pointer-events:none;width:0;height:0;overflow:hidden';
    wrap.appendChild(sel);

    // Trigger
    const trigger = document.createElement('div');
    trigger.className = 'dark-select-trigger';
    const trigText = document.createElement('span');
    trigText.className = 'dark-select-text';
    const arrow = document.createElement('span');
    arrow.className = 'dark-select-arrow';
    arrow.innerHTML = _ARROW_SVG;
    trigger.appendChild(trigText);
    trigger.appendChild(arrow);
    wrap.insertBefore(trigger, sel);

    // Dropdown
    const dropdown = document.createElement('div');
    dropdown.className = 'dark-select-dropdown';
    wrap.insertBefore(dropdown, sel);

    function rebuild() {
      dropdown.innerHTML = '';
      for (const child of sel.children) {
        if (child.tagName === 'OPTGROUP') {
          const lbl = document.createElement('div');
          lbl.className = 'dark-select-group-label';
          lbl.textContent = child.label;
          dropdown.appendChild(lbl);
          for (const opt of child.children) appendOption(opt);
        } else if (child.tagName === 'OPTION') {
          appendOption(child);
        }
      }
      syncDisplay();
    }

    function appendOption(opt) {
      const div = document.createElement('div');
      div.className = 'dark-select-option';
      div.textContent = opt.textContent;
      div.dataset.value = opt.value;
      if (opt.disabled) div.classList.add('disabled');
      div.addEventListener('click', e => {
        e.stopPropagation();
        if (opt.disabled) return;
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        syncDisplay();
        wrap.classList.remove('open');
      });
      dropdown.appendChild(div);
    }

    function syncDisplay() {
      const cur = sel.options[sel.selectedIndex];
      trigText.textContent = cur ? cur.textContent : '';
      dropdown.querySelectorAll('.dark-select-option').forEach(d => {
        d.classList.toggle('selected', d.dataset.value === sel.value);
      });
    }

    trigger.addEventListener('click', e => {
      e.stopPropagation();
      document.querySelectorAll('.dark-select.open').forEach(w => { if (w !== wrap) w.classList.remove('open'); });
      wrap.classList.toggle('open');
    });

    // Close on outside click (single global listener)
    if (!_initDarkSelects._outsideListener) {
      _initDarkSelects._outsideListener = true;
      document.addEventListener('click', () => {
        document.querySelectorAll('.dark-select.open').forEach(w => w.classList.remove('open'));
      });
    }

    // Watch for dynamic option changes (e.g. JS rebuilds the <select>)
    new MutationObserver(rebuild).observe(sel, { childList: true, subtree: true, attributes: true });
    rebuild();
  }

  // Upgrade existing selects
  document.querySelectorAll('select').forEach(upgradeSelect);

  // Auto-upgrade dynamically inserted selects
  new MutationObserver(muts => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.tagName === 'SELECT') upgradeSelect(n);
        if (n.querySelectorAll) n.querySelectorAll('select').forEach(upgradeSelect);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });

  window._upgradeSelect = upgradeSelect;
})();
