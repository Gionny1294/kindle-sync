'use strict';
(() => {
 const el = id => document.getElementById(id);
 const T = (message, values) => window.KindleI18n.t(message, values);
 const accounts = window.KindleAccounts;
 const busy = new Set(['checking', 'installing']);
 let current = '', info = null, timer, submitting = false, error = '', generation = 0;

 function render() {
  if (!info) return;
  const status = el('update-status');
  const messages = {
   idle: 'In attesa del controllo delle nuove versioni.',
   checking: 'Controllo delle nuove versioni…',
   ready: 'Kindle Sync è aggiornato.',
   installing: 'Installazione in corso. L’app si riavvierà al termine.',
   installed: 'Aggiornamento installato.',
   check_failed: 'Impossibile controllare le versioni su GitHub. Riprova.',
   install_failed: 'Aggiornamento non riuscito. Riprova dal launcher se l’app non si riavvia.',
   offline: 'Avvia Kindle Sync con il launcher sul computer che lo ospita.'
  };
  el('update-current').textContent = T('Versione installata {version}', {version: info.current});
  status.textContent = error || (info.latest && info.phase === 'ready'
   ? T('Versione {version} disponibile.', {version: info.latest})
   : T(messages[info.phase]));
  el('update-available').hidden = !info.latest || !info.can_manage || busy.has(info.phase);
  el('update-check').hidden = !info.can_manage || info.phase === 'offline';
  el('update-check').disabled = submitting || busy.has(info.phase);
  el('update-install').hidden = !info.can_manage || !info.latest || info.phase === 'offline';
  el('update-install').disabled = submitting || busy.has(info.phase);
 }

 function schedule() {
  clearTimeout(timer);
  if (!accounts.active) return;
  const settings = !el('page-settings').hidden;
  timer = setTimeout(refresh, info?.phase === 'installing' ? 2000 : settings ? 4000 : 60000);
 }

 async function refresh() {
  if (!accounts.active) return;
  const epoch = ++generation;
  try {
   const result = await accounts.request('/api/updates');
   if (epoch !== generation) return;
   if (current && result.current !== current) {
    location.reload();
    return;
   }
   current = result.current;
   info = result;
   render();
  } catch {
   // The previous status stays visible while the containers restart.
  } finally {
   if (epoch === generation) schedule();
  }
 }

 async function send(action) {
  if (submitting || !info?.can_manage) return;
  const version = info.latest;
  if (action === 'install' && !await confirmAction('Installa aggiornamento',
   'Kindle Sync si riavvierà. Libri e impostazioni restano disponibili dopo il riavvio.')) return;
  ++generation;
  submitting = true;
  error = '';
  render();
  try {
   const result = await accounts.request('/api/updates', {action, version});
   if (info) info.phase = result.phase;
  } catch (failure) {
   error = failure.message;
  } finally {
   submitting = false;
   render();
   schedule();
  }
 }

 el('update-check').onclick = () => send('check');
 el('update-install').onclick = () => send('install');
 window.addEventListener('kindle:page', event => {
  if (event.detail.page === 'settings' && accounts.active) refresh();
 });
 window.addEventListener('kindle:language', render);
 window.addEventListener('kindle:account', event => {
  ++generation;
  clearTimeout(timer);
  info = null;
  error = '';
  el('update-available').hidden = true;
  el('update-check').hidden = true;
  el('update-install').hidden = true;
  el('update-current').textContent = '';
  el('update-status').textContent = '';
  if (event.detail.user) refresh();
 });
})();
