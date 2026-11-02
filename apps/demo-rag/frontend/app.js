'use strict';
const $ = id => document.getElementById(id);
const state = {collections: [], selected: null, sources: [], history: [], status: null, unlocked: false, view: 'chat', busy: false, epoch: 0};
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function notice(message) { $('notice').textContent = message; $('notice').hidden = false; clearTimeout(notice.timer); notice.timer = setTimeout(() => $('notice').hidden = true, 7000); }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {credentials: 'same-origin', ...options});
  const data = await response.json().catch(() => ({detail: 'Unexpected server response'}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/session') lock();
    const message = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join('; ') : data.detail;
    throw new Error(message || 'The request could not be completed.');
  }
  return data;
}
function json(body) { return {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}; }
function selected() { return state.collections.find(c => c.id === state.selected); }
function lock() { state.unlocked = false; $('unlock').hidden = false; $('workspace').hidden = true; clearChat(); }
function clearChat() { state.epoch += 1; state.history = []; $('messages').replaceChildren($('welcome')); $('welcome').hidden = false; $('question').value = ''; }
function showView(view) {
  state.view = view;
  for (const id of ['chat', 'library', 'settings']) $(id + '-view').hidden = id !== view;
  document.querySelectorAll('.nav-button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $('view-label').textContent = {chat: 'Ask & explore', library: 'Source library', settings: 'Models & usage'}[view];
  if (view === 'settings') loadUsage().catch(e => notice(e.message));
}
function sourceLabel(source) { return source.kind === 'website' ? 'Website' : source.kind === 'file' ? 'Document' : 'Note'; }
function renderCollections() {
  const list = $('collection-list'); list.replaceChildren();
  if (!state.collections.length) list.append(el('p', 'Create a collection to begin.', 'help'));
  for (const c of state.collections) {
    const button = el('button', undefined, 'collection-button' + (state.selected === c.id ? ' selected' : ''));
    button.append(el('span', '', 'collection-dot'), el('span', c.name, 'collection-name'), el('small', String(c.source_count)));
    button.addEventListener('click', () => selectCollection(c.id)); list.append(button);
  }
  const c = selected();
  $('active-collection').textContent = c ? c.name : 'No collection selected';
  $('library-collection').textContent = c ? c.name : 'Choose a collection';
  $('library-source-count').textContent = c ? c.source_count : 0;
  $('library-chunk-count').textContent = c ? c.chunk_count : 0;
  for (const id of ['collection-policy', 'library-policy']) $(id).textContent = c?.allow_external ? 'External processing allowed' : 'Local only';
  $('source-count').textContent = state.collections.reduce((sum, c) => sum + Number(c.source_count), 0);
  $('delete-collection').hidden = !c;
  updateMode();
}
async function refreshCollections() { state.collections = await api('/collections'); if (!state.collections.some(c => c.id === state.selected)) state.selected = state.collections[0]?.id || null; renderCollections(); }
async function selectCollection(id) { if (state.busy) { notice('Wait for the current answer before changing collections.'); return; } if (id === state.selected) return; state.selected = id; $('external-consent').checked = false; clearChat(); renderCollections(); await loadSources().catch(e => notice(e.message)); }
function updateMode() {
  const provider = $('chat-provider').value, c = selected(), general = $('chat-mode').value === 'chat';
  $('chat-model').hidden = provider !== 'openai';
  $('consent-row').hidden = !(provider === 'openai' || (!general && c?.embedding_provider === 'openai'));
  $('mode-note').textContent = provider === 'openai' ? 'OpenAI generates answers. Selected context leaves this machine.' : c?.embedding_provider === 'openai' && !general ? 'Retrieval uses OpenAI embeddings; answers show retrieved passages.' : 'Local search returns source passages without a model call.';
  if (general) { $('active-collection').textContent = 'General chat · no sources attached'; $('collection-policy').textContent = 'No collection context'; }
  else { $('active-collection').textContent = c?.name || 'No collection selected'; $('collection-policy').textContent = c?.allow_external ? 'External processing allowed' : 'Local only'; }
}
async function loadSources() {
  if (!state.selected) { state.sources = []; renderSources(); return; }
  const id = state.selected, sources = await api('/collections/' + id + '/sources');
  if (state.selected !== id) return;
  state.sources = sources; renderSources();
}
function renderSources() {
  const target = $('sources'); target.replaceChildren();
  if (!state.sources.length) {
    const box = el('div', undefined, 'empty'); box.append(el('h3', 'A little room for your sources.'), el('p', 'Add a public website, a document, or a note to get started.')); target.append(box); return;
  }
  for (const source of state.sources) {
    const row = el('div', undefined, 'source-row'), info = el('div', undefined, 'source-info');
    info.append(el('h3', source.title), el('small', sourceLabel(source) + ' · ' + source.chunk_count + ' passages' + (source.attempts ? ' · attempt ' + source.attempts : '')));
    if (source.error_code) info.append(el('small', source.error_code.replaceAll('_', ' '), 'source-error'));
    const actions = el('div', undefined, 'row-actions');
    if (source.status === 'ready') { const button = el('button', 'Preview'); button.addEventListener('click', () => previewSource(source)); actions.append(button); }
    if (source.status === 'failed') { const button = el('button', 'Retry'); button.addEventListener('click', async () => { try { await api('/sources/' + source.id + '/retry', {method: 'POST'}); await loadSources(); } catch (e) { notice(e.message); } }); actions.append(button); }
    const remove = el('button', 'Remove'); remove.addEventListener('click', async () => { if (!confirm('Remove this source, its original upload and all indexed passages?')) return; try { await api('/sources/' + source.id, {method: 'DELETE'}); await refreshCollections(); await loadSources(); clearChat(); } catch (e) { notice(e.message); } }); actions.append(remove);
    row.append(el('span', source.kind === 'website' ? '◎' : '▤', 'file-icon'), info, el('span', source.status === 'running' ? 'Indexing' : source.status[0].toUpperCase() + source.status.slice(1), 'source-state ' + source.status), actions); target.append(row);
  }
}
function showPassages(title, passages) {
  $('preview-title').textContent = title; $('preview-content').replaceChildren();
  if (!passages.length) $('preview-content').append(el('p', 'No indexed passages yet.'));
  for (const [i, p] of passages.entries()) { const section = el('section', undefined, 'preview-part'); section.append(el('small', 'Passage ' + (i + 1) + (p.page ? ' · page ' + p.page : '')), el('p', p.content));
    if (p.locator?.startsWith('https://')) { const link = el('a', 'Open original page ↗'); link.href = p.locator; link.target = '_blank'; link.rel = 'noopener noreferrer'; section.append(link); }
    $('preview-content').append(section);
  }
  $('preview-dialog').showModal();
}
async function previewSource(source) { try { showPassages(source.title, await api('/sources/' + source.id + '/preview')); } catch (e) { notice(e.message); } }
function openCollection() { if (state.busy) { notice('Wait for the current answer first.'); return; } $('collection-form').reset(); $('collection-error').textContent = ''; $('collection-dialog').showModal(); }
function sourceFields() { const kind = $('source-kind').value; $('website-fields').hidden = kind !== 'website'; $('file-fields').hidden = kind !== 'file'; $('text-fields').hidden = kind !== 'text'; $('source-title-wrap').hidden = kind === 'file'; }
function openSource() {
  if (!selected()) { openCollection(); return; }
  $('source-form').reset(); $('source-error').textContent = ''; sourceFields();
  $('source-collection-label').textContent = 'Adding to ' + selected().name;
  $('source-processing-note').textContent = selected().embedding_provider === 'openai' ? 'This collection sends extracted text to OpenAI to build semantic embeddings.' : 'Text extraction and indexing stay on this machine. Local search uses lexical hashing.';
  $('source-dialog').showModal();
}
function addMessage(role, text, data) {
  $('welcome').hidden = true;
  const item = el('article', undefined, 'message ' + role); item.append(el('div', text, 'message-body'));
  if (data) {
    const tokens = data.usage ? ' · ' + data.usage.input_tokens + ' input / ' + data.usage.output_tokens + ' output tokens' : '';
    item.append(el('div', (data.mode === 'openai' ? $('chat-model').value : data.mode === 'no-match' ? 'No supporting sources' : 'Local source search') + tokens, 'message-meta'));
    if (data.sources.length) {
      const references = el('details', undefined, 'references'); references.append(el('summary', data.sources.length + ' retrieved references · inspect passages'));
      for (const [i, p] of data.sources.entries()) { const button = el('button', '[' + (i + 1) + '] ' + p.title, 'reference'); button.append(el('small', (p.page ? 'Page ' + p.page + ' · ' : '') + 'Similarity ' + Math.round(p.score * 100) + '%')); button.addEventListener('click', () => showPassages(p.title, [p])); references.append(button); }
      item.append(references);
    }
  }
  $('messages').append(item); $('messages').scrollTop = $('messages').scrollHeight; return item;
}
async function loadUsage() {
  const rows = await api('/usage'); $('usage').replaceChildren();
  if (!rows.length) $('usage').append(el('p', 'No recorded provider calls yet. Local lexical search uses no API tokens.', 'help'));
  for (const row of rows) { const item = el('div', undefined, 'usage-row'); item.append(el('strong', row.model + ' · ' + row.operation), el('span', row.calls + ' calls'), el('span', row.input_tokens + ' input'), el('span', row.output_tokens + ' output')); $('usage').append(item); }
}
async function boot() {
  state.status = await api('/status'); state.unlocked = true; $('unlock').hidden = true; $('workspace').hidden = false;
  $('provider-status').textContent = state.status.provider_configured ? 'OpenAI configured' : 'Offline mode';
  $('connection-detail').textContent = state.status.provider_configured ? 'A local API key is configured. Model access and credit availability are checked when you send a request.' : 'No API key configured. Local source search is ready; model-backed chat will be available after setup.';
  const option = $('chat-provider').querySelector('[value=openai]'); option.disabled = !state.status.provider_configured;
  $('chat-model').replaceChildren(); for (const model of state.status.models) { const item = el('option', model); item.value = model; $('chat-model').append(item); }
  await refreshCollections(); await loadSources();
}
$('unlock-form').addEventListener('submit', async event => { event.preventDefault(); const button = event.submitter || event.currentTarget.querySelector("button[type=submit]"); button.disabled = true; $('unlock-error').textContent = ''; try { await api('/session', {method: 'POST', ...json({token: $('access-key').value})}); $('access-key').value = ''; await boot(); } catch (e) { $('unlock-error').textContent = e.message; } finally { button.disabled = false; } });
$('lock-workspace').addEventListener('click', async () => { await api('/session', {method: 'DELETE'}).catch(() => {}); lock(); });
document.querySelectorAll('.nav-button').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
document.querySelectorAll('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$('new-collection').addEventListener('click', openCollection); $('add-source').addEventListener('click', openSource); $('welcome-add').addEventListener('click', openSource); $('clear-chat').addEventListener('click', clearChat); $('source-kind').addEventListener('change', sourceFields);
for (const id of ['chat-mode', 'chat-provider']) $(id).addEventListener('change', () => { clearChat(); $('external-consent').checked = false; updateMode(); });
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => { $('question').value = button.dataset.prompt; $('question').focus(); }));
$('question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('chat-form').requestSubmit(); } });
$('collection-form').addEventListener('submit', async event => { event.preventDefault(); const button = event.submitter || event.currentTarget.querySelector("button[type=submit]"); button.disabled = true; $('collection-error').textContent = ''; try { const c = await api('/collections', {method: 'POST', ...json({name: $('collection-name').value, description: $('collection-description').value, embedding_provider: $('embedding-method').value, allow_external: $('collection-external').checked})}); state.selected = c.id; clearChat(); await refreshCollections(); await loadSources(); $('collection-dialog').close(); notice('Collection created. Add a source to begin.'); } catch (e) { $('collection-error').textContent = e.message; } finally { button.disabled = false; } });
$('source-form').addEventListener('submit', async event => { event.preventDefault(); const button = event.submitter || event.currentTarget.querySelector("button[type=submit]"); button.disabled = true; $('source-error').textContent = ''; try { const kind = $('source-kind').value; if (kind === 'file') { const file = $('source-file').files[0]; if (!file) throw new Error('Choose a file first.'); if (file.size > 10 * 1024 * 1024) throw new Error('Choose a file up to 10 MiB.'); const body = new FormData(); body.append('file', file); await api('/collections/' + state.selected + '/upload', {method: 'POST', body}); } else { await api('/collections/' + state.selected + '/sources', {method: 'POST', ...json({kind, title: $('source-title').value || 'Web source', url: $('source-url').value, text: $('source-text').value})}); } $('source-dialog').close(); showView('library'); await refreshCollections(); await loadSources(); notice('Source queued. Indexing will continue in the background.'); } catch (e) { $('source-error').textContent = e.message; } finally { button.disabled = false; } });
$('delete-collection').addEventListener('click', async () => { const c = selected(); if (!c || !confirm('Delete “' + c.name + '” and all its sources, original uploads and indexed passages?')) return; try { await api('/collections/' + c.id, {method: 'DELETE'}); state.selected = null; clearChat(); await refreshCollections(); await loadSources(); } catch (e) { notice(e.message); } });
$('chat-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  const question = $('question').value.trim(); if (!question) return;
  const knowledge = $('chat-mode').value === 'knowledge'; if (knowledge && !selected()) { notice('Create or select a collection first.'); return; }
  const external = $('chat-provider').value === 'openai' || (knowledge && selected()?.embedding_provider === 'openai');
  if (external && !$('external-consent').checked) { notice('Confirm external processing below the composer before sending.'); return; }
  if (external && knowledge && !selected().allow_external) { notice('This collection is local-only. Use local search or select a collection that allows external processing.'); return; }
  const epoch = state.epoch; state.busy = true; $('send').disabled = true; $('clear-chat').disabled = true; $('chat-mode').disabled = true; $('chat-provider').disabled = true; $('chat-model').disabled = true;
  addMessage('user', question); $('question').value = ''; const pending = addMessage('assistant', 'Looking through the context…'); pending.classList.add('loading');
  try {
    const data = await api('/chat', {method: 'POST', ...json({question, mode: $('chat-mode').value, collection_id: knowledge ? state.selected : null, provider: $('chat-provider').value, model: $('chat-model').value || 'gpt-6-luna', external_consent: $('external-consent').checked, history: state.history.slice(-8)})});
    pending.remove(); if (!state.unlocked || epoch !== state.epoch) return; addMessage('assistant', data.answer, data); state.history.push({role: 'user', content: question}, {role: 'assistant', content: data.answer.slice(0, 3000)}); state.history = state.history.slice(-8);
  } catch (e) { pending.remove(); if (!state.unlocked || epoch !== state.epoch) return; addMessage('assistant', e.message, {mode: 'error', usage: null, sources: []}); }
  finally { state.busy = false; $('send').disabled = false; $('clear-chat').disabled = false; $('chat-mode').disabled = false; $('chat-provider').disabled = false; $('chat-model').disabled = false; }
});
setInterval(async () => { if (state.unlocked && state.selected && !document.hidden) { try { await loadSources(); await refreshCollections(); } catch (_) {} } }, 4000);
boot().catch(() => lock());
