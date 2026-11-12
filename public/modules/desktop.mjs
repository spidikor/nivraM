/** Native persistence is bound only after both editor and host bridges are ready. */
let initialized = false;
const $ = id => document.getElementById(id);
function message(text, error = false) {
  const toast = $('toast');
  toast.textContent = text; toast.hidden = false;
  toast.classList.toggle('error', error);
}

async function initialize() {
  if (initialized || !window.nivramDocument || !window.pywebview?.api?.save_project) return;
  initialized = true;
  const editor = window.nivramDocument, native = window.pywebview.api;
  await editor.ready;
  let diskHash = null, destinationUnconfirmed = false, destinationEpoch = 0;
  let saving = false, opening = false, autosaveTimer, lastRecoveryRevision = -1;
  const status = $('save-status');
  let recoveryGeneration = null;

  async function save(saveAs = false) {
    if (saving || opening) return;
    saving = true;
    const snapshot = editor.getSnapshot();
    const epoch = destinationEpoch;
    const unconfirmed = () => {
      if (destinationEpoch === epoch) destinationUnconfirmed = true;
      message('Save could not be confirmed. The drawing remains open. Inspect the destination before saving again, or use Save As to create a separate copy.', true);
    };
    try {
      const result = await native.save_project(snapshot.document, diskHash, saveAs || destinationUnconfirmed);
      if (result?.status === 'saved' && typeof result.disk_hash === 'string' &&
          /^[0-9a-f]{64}$/.test(result.disk_hash) && typeof result.name === 'string' && result.name.length) {
        if (destinationEpoch !== epoch) {
          message('The earlier document was saved. The current drawing still requires its own save.');
          return;
        }
        diskHash = result.disk_hash;
        destinationUnconfirmed = false;
        editor.markSaved(snapshot.revision, snapshot.document);
        message(`Project saved and verified: ${result.name}`);
      } else if (result?.status === 'conflict') {
        message('The file changed on disk. Reopen it to inspect the newer version, or use Save As to keep a separate copy.', true);
      } else if (result?.status === 'unsupported' && result.outcome === 'not_committed') {
        if (destinationEpoch === epoch) destinationUnconfirmed = true;
        message('This location does not support protected updates. Use Save As to create a separate copy.', true);
      } else if (result?.status === 'failed' && result.outcome === 'not_committed') {
        message('Save failed before the project was committed. The drawing remains open. Check the destination and try again.', true);
      } else if (result?.status !== 'cancelled') {
        unconfirmed();
      }
    } catch { unconfirmed(); }
    finally { saving = false; }
  }

  async function open() {
    if (saving || opening) return;
    const before = editor.getSnapshot();
    if (before.dirty && !confirm('Open another project? Unsaved changes remain in recovery when autosave has succeeded.')) return;
    opening = true;
    let committed = false;
    try {
      const result = await native.open_project();
      if (result.status !== 'opened') return;
      if (editor.getSnapshot().revision !== before.revision) {
        message('The drawing changed while the file was being opened. Open again to replace it.', true); return;
      }
      const accepted = await editor.openProject(result.document);
      if (accepted === false) return;
      committed = true;
      resetDestination();
      const epoch = destinationEpoch;
      const opened = editor.getSnapshot();
      await native.accept_open(result.open_id);
      if (epoch !== destinationEpoch) {
        message('The active document changed while opening. Its next save will require Save As.', true); return;
      }
      diskHash = result.disk_hash;
      destinationUnconfirmed = false;
      editor.markSaved(opened.revision, opened.document);
      message(`Opened ${result.name}`);
    } catch {
      message(committed ? 'Project contents opened, but its save destination could not be confirmed. Use Save As.' :
        'Project not opened. The preceding document is retained.', true);
    } finally { opening = false; }
  }

  function recoveryNotice(issues) {
    const messages = [];
    if (issues.invalid) messages.push('Damaged recovery files were retained.');
    if (issues.unsupported) messages.push('Recovery files using an unsupported format were retained.');
    if (issues.unreadable) messages.push('Some recovery files could not be read.');
    if (issues.interrupted) messages.push('Interrupted recovery files were retained.');
    return messages.join(' ');
  }

  function checkedRecoveryInventory(value) {
    const kinds = ['invalid', 'unsupported', 'unreadable', 'interrupted'];
    if (value?.schemaVersion !== 1 || !['ready', 'unavailable'].includes(value.status) ||
        !Array.isArray(value.records) || value.records.length > 1000 || !value.issues ||
        Object.keys(value.issues).length !== kinds.length ||
        !kinds.every(k => Number.isSafeInteger(value.issues[k]) && value.issues[k] >= 0 && value.issues[k] <= 1000) ||
        (value.status === 'unavailable' && value.records.length)) throw new Error('Invalid recovery inventory');
    const ids = new Set();
    for (const row of value.records) {
      if (typeof row?.id !== 'string' || !/^\d+-[0-9a-f]{32}$/.test(row.id) || ids.has(row.id) ||
          !Number.isFinite(row.created_ns) || row.created_ns <= 0 ||
          Number.isNaN(new Date(row.created_ns / 1e6).getTime())) throw new Error('Invalid recovery record');
      ids.add(row.id);
    }
    return value;
  }

  async function recoveryReply(promise) {
    let timer;
    try {
      return await Promise.race([promise,
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Recovery response timed out')), 5000); })]);
    } finally { clearTimeout(timer); }
  }

  async function recover() {
    if (saving || opening) return;
    opening = true;
    const before = editor.getSnapshot(), originalEpoch = destinationEpoch;
    let committed = false;
    const changed = () => editor.getSnapshot().revision !== before.revision || destinationEpoch !== originalEpoch;
    try {
      const inventory = checkedRecoveryInventory(await recoveryReply(native.recovery_inventory()));
      if (inventory.status !== 'ready') return message('Recovery files could not be checked. Keep the drawing open and use Save As.', true);
      const notice = recoveryNotice(inventory.issues);
      if (changed()) return message('The drawing changed; recovery was not applied.', true);
      if (!inventory.records.length) return message(notice ?
        notice + ' No readable recovery copy is available. Keep the drawing open and use Save As.' :
        'No recovery copies are available.', !!notice);
      const latest = inventory.records[0];
      const date = new Date(latest.created_ns / 1e6).toLocaleString();
      if (!confirm((notice ? notice + '\n\n' : '') + 'Open the most recent readable recovery from ' + date + ' as an unsaved project?')) return;
      const result = await recoveryReply(native.recover_project(latest.id));
      if (changed()) return message('The drawing changed; recovery was not applied.', true);
      if (result?.status !== 'recovered' || typeof result.recovery_id !== 'string' ||
          !/^[0-9a-f]{32}$/.test(result.recovery_id)) throw new Error('Invalid recovered project receipt');
      if (await editor.openProject(result.document, {asUnsaved: true}) === false) return;
      committed = true;
      resetDestination();
      const epoch = destinationEpoch;
      const accepted = await recoveryReply(native.accept_recovery(result.recovery_id));
      if (accepted?.status !== 'accepted') throw new Error('Unconfirmed recovery destination');
      if (destinationEpoch !== epoch) return message('The earlier recovery was opened. The current document needs its own Save As.', true);
      message('Recovery opened as an unsaved project. Use Save As to choose its destination.' + (notice ? ' ' + notice : ''), !!notice);
    } catch {
      message(committed ? 'Recovered contents opened, but its save destination could not be confirmed. Use Save As.' :
        'Recovery could not be opened. Existing files are retained.', true);
    } finally { opening = false; }
  }

  function updateRecovery(snapshot) {
    status.textContent = snapshot.dirty ? (recoveryGeneration ? 'Unsaved changes' : 'Recovery unavailable · save manually') : 'Saved state';
    clearTimeout(autosaveTimer);
    if (!snapshot.dirty || !recoveryGeneration) return;
    autosaveTimer = setTimeout(async () => {
      const current = editor.getSnapshot();
      if (!current.dirty || current.revision === lastRecoveryRevision) return;
      try {
        if (current.recoveryGeneration !== recoveryGeneration || !Number.isSafeInteger(current.revision) || current.revision < 0) {
          throw new Error('Invalid recovery identity');
        }
        const result = await native.autosave_project(current.document, recoveryGeneration, current.revision);
        if (result?.status !== 'recovered-copy-saved' || typeof result.id !== 'string' ||
            !/^[1-9][0-9]{0,19}-[0-9a-f]{32}$/.test(result.id) ||
            result.generation !== recoveryGeneration || !Number.isSafeInteger(result.revision) ||
            result.revision !== current.revision) throw new Error('Unconfirmed recovery');
        lastRecoveryRevision = Math.max(lastRecoveryRevision, current.revision);
        const latest = editor.getSnapshot();
        if (latest.revision === current.revision) {
          status.textContent = latest.dirty ? 'Recovery copy saved · project unsaved' : 'Saved state';
        }
      } catch {
        const latest = editor.getSnapshot();
        if (latest.revision === current.revision && latest.dirty && lastRecoveryRevision !== current.revision) {
          status.textContent = 'Recovery could not be confirmed · save a copy';
        }
      }
    }, 2000);
  }
  editor.subscribe(updateRecovery);
  function resetDestination() {
    destinationEpoch += 1;
    diskHash = null;
    destinationUnconfirmed = true;
  }
  window.nivramNative = Object.freeze({save, open, recover, resetDestination});
  window.dispatchEvent(new Event('nivram-native-ready'));
  if (typeof native.list_profiles === 'function') {
    import('./profiles.mjs').then(module => module.initializeProfiles(editor, native)).catch(() => message('Rendering profiles could not initialize.', true));
  }
  if (typeof native.diagnostics === 'function') {
    import('./diagnostics.mjs').then(module => module.initializeDiagnostics(native)).catch(() => message('Local diagnostics could not initialize.', true));
  }
  if (typeof native.begin_batch_export === 'function') {
    import('./batch-export.mjs').catch(() => message('Batch export could not initialize.', true));
  }
  $('save-button').onclick = () => save();
  $('open-button').onclick = open;
  $('save-as-button').hidden = false; $('save-as-button').onclick = () => save(true);
  $('recovery-button').hidden = false; $('recovery-button').onclick = recover;
  $('open-molecule-button').hidden = false; $('open-molecule-button').onclick = () => {
    $('file-input').click();
  };
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || document.querySelector('dialog[open]') || !(event.ctrlKey || event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key !== 's' && key !== 'o') return;
    event.preventDefault(); event.stopImmediatePropagation();
    key === 's' ? save(event.shiftKey) : open();
  }, true);
  status.textContent = recoveryGeneration ? 'Native local saving · recovery enabled' : 'Recovery unavailable · save manually';
  // Manual saving and edit observation are installed before awaiting the host.
  // A stalled bridge must not disable saving or lose edits made during startup.
  void (async () => {
    let deadline;
    try {
      const context = await Promise.race([
        native.recovery_context(),
        new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Recovery context timed out')), 5000); })
      ]);
      if (typeof context?.generation !== 'string' || !/^[0-9a-f]{32}$/.test(context.generation)) {
        throw new Error('Invalid recovery context');
      }
      editor.setRecoveryGeneration(context.generation);
      recoveryGeneration = context.generation;
      updateRecovery(editor.getSnapshot());
    } catch {
      status.textContent = 'Recovery unavailable · save manually';
      message('Recovery could not initialize. Save your work manually and restart the app.', true);
    } finally { clearTimeout(deadline); }
  })();
}

window.addEventListener('nivram-document-ready', initialize);
window.addEventListener('pywebviewready', initialize);
initialize();
