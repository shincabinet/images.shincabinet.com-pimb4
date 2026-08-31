const csrf = document.querySelector('meta[name="csrf-token"]').content;
let currentPath = '';
let selectedFiles = [];
let moveSource = '';
let replaceImageId = '';
let replaceImagePath = '';

const grid = document.getElementById('grid');
const message = document.getElementById('message');
const emptyState = document.getElementById('empty-state');
const breadcrumbs = document.getElementById('breadcrumbs');
const fileInput = document.getElementById('file-input');
const uploadPanel = document.getElementById('upload-panel');
const uploadList = document.getElementById('upload-list');
const diskUsage = document.getElementById('disk-usage');

function humanBytes(bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(bytes || 0);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function flash(text, type = 'success') {
  message.innerHTML = `<div class="alert ${type}">${escapeHtml(text)}</div>`;
  setTimeout(() => { message.innerHTML = ''; }, 4500);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

async function api(url, options = {}) {
  options.headers = options.headers || {};
  if (options.method && options.method !== 'GET') options.headers['X-CSRF-Token'] = csrf;
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function loadConfig() {
  const data = await api('/api/config');
  diskUsage.textContent = `${humanBytes(data.disk.used)} used / ${humanBytes(data.disk.total)}`;
}

function renderBreadcrumbs(path) {
  breadcrumbs.innerHTML = '';
  const rootBtn = document.createElement('button');
  rootBtn.textContent = 'images';
  rootBtn.onclick = () => loadFolder('');
  breadcrumbs.append(rootBtn);
  if (!path) return;
  let built = '';
  for (const part of path.split('/')) {
    const sep = document.createElement('span'); sep.className = 'sep'; sep.textContent = '/'; breadcrumbs.append(sep);
    built = built ? `${built}/${part}` : part;
    const target = built;
    const btn = document.createElement('button'); btn.textContent = part; btn.onclick = () => loadFolder(target); breadcrumbs.append(btn);
  }
}

async function loadFolder(path = '') {
  try {
    const data = await api(`/api/list?path=${encodeURIComponent(path)}`);
    currentPath = data.path;
    renderBreadcrumbs(currentPath);
    renderItems(data.items);
  } catch (error) { flash(error.message, 'error'); }
}

function renderItems(items) {
  grid.innerHTML = '';
  emptyState.classList.toggle('hidden', items.length !== 0);
  for (const item of items) {
    const card = document.createElement('article'); card.className = 'item';
    const preview = document.createElement('div'); preview.className = 'item-preview';
    if (item.type === 'folder') {
      preview.innerHTML = '<div class="folder-icon">📁</div>';
      preview.style.cursor = 'pointer'; preview.onclick = () => loadFolder(item.path);
    } else {
      const img = document.createElement('img'); img.loading = 'lazy'; img.src = item.thumbnailUrl; img.alt = item.name; preview.append(img);
    }
    const body = document.createElement('div'); body.className = 'item-body';
    const name = document.createElement('div'); name.className = 'item-name'; name.textContent = item.name; body.append(name);
    const meta = document.createElement('div'); meta.className = 'item-meta';
    if (item.type === 'image') {
      const dims = item.width && item.height ? `${item.width}×${item.height} · ` : '';
      meta.innerHTML = `${escapeHtml(dims)}${escapeHtml(humanBytes(item.size))}<br><code>${escapeHtml(item.id || '')}</code>`;
    } else meta.textContent = 'Folder';
    body.append(meta);
    const actions = document.createElement('div'); actions.className = 'item-actions';
    if (item.type === 'folder') actions.append(makeButton('Open', () => loadFolder(item.path)));
    else {
      actions.append(makeButton('Copy ID', async () => {
        await navigator.clipboard.writeText(item.id); flash(`Copied ${item.id}`);
      }));
      actions.append(makeButton('Copy stable URL', async () => {
        await navigator.clipboard.writeText(item.url); flash(`Copied ${item.url}`);
      }));
      actions.append(makeButton('Replace', () => openReplace(item), 'secondary'));
      const open = document.createElement('a'); open.href = item.url; open.target = '_blank'; open.rel = 'noopener'; open.textContent = 'Open by ID'; actions.append(open);
    }
    actions.append(makeButton('Rename / move', () => openMove(item.path), 'secondary'));
    actions.append(makeButton('Delete', () => deleteItem(item), 'danger'));
    body.append(actions); card.append(preview, body); grid.append(card);
  }
}

function makeButton(label, fn, klass='secondary') {
  const btn = document.createElement('button'); btn.className = klass; btn.textContent = label; btn.onclick = fn; return btn;
}

fileInput.addEventListener('change', () => {
  selectedFiles = [...fileInput.files];
  if (!selectedFiles.length) return;
  uploadPanel.classList.remove('hidden');
  uploadList.innerHTML = selectedFiles.map(f => `<div class="upload-row"><span>${escapeHtml(f.name)}</span><span class="muted">${humanBytes(f.size)}</span></div>`).join('');
});

document.getElementById('cancel-upload').onclick = () => {
  selectedFiles = []; fileInput.value = ''; uploadPanel.classList.add('hidden');
};

document.getElementById('start-upload').onclick = async () => {
  if (!selectedFiles.length) return;
  const form = new FormData();
  form.append('path', currentPath);
  form.append('overwrite', document.getElementById('overwrite').checked ? 'true' : 'false');
  for (const file of selectedFiles) form.append('files', file);
  try {
    const response = await fetch('/api/upload', {method: 'POST', headers: {'X-CSRF-Token': csrf}, body: form});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Upload failed.');
    const failed = data.results.filter(r => !r.ok);
    if (failed.length) flash(failed.map(r => `${r.name}: ${r.error}`).join(' | '), 'error');
    else flash(`Uploaded ${data.results.length} image${data.results.length === 1 ? '' : 's'}.`);
    selectedFiles = []; fileInput.value = ''; uploadPanel.classList.add('hidden'); await loadFolder(currentPath); await loadConfig();
  } catch (error) { flash(error.message, 'error'); }
};

const folderDialog = document.getElementById('folder-dialog');
document.getElementById('new-folder').onclick = () => { document.getElementById('folder-name').value=''; folderDialog.showModal(); };
document.getElementById('folder-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const name = document.getElementById('folder-name').value;
  try {
    await api('/api/folders', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({parent:currentPath,name})});
    folderDialog.close(); flash('Folder created.'); await loadFolder(currentPath);
  } catch (error) { flash(error.message, 'error'); }
});

const moveDialog = document.getElementById('move-dialog');
function openMove(path) {
  moveSource = path; document.getElementById('move-source').textContent = path; document.getElementById('move-destination').value = path; moveDialog.showModal();
}
document.getElementById('move-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const destination = document.getElementById('move-destination').value;
  try {
    await api('/api/move', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source:moveSource,destination})});
    moveDialog.close(); flash('Moved successfully.'); await loadFolder(currentPath);
  } catch (error) { flash(error.message, 'error'); }
});

const replaceDialog = document.getElementById('replace-dialog');
function openReplace(item) {
  replaceImageId = item.id;
  replaceImagePath = item.path;
  document.getElementById('replace-id').textContent = item.id;
  document.getElementById('replace-file').value = '';
  replaceDialog.showModal();
}

document.getElementById('replace-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const input = document.getElementById('replace-file');
  const file = input.files?.[0];
  if (!file) return flash('Select a replacement image.', 'error');
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch(`/api/image/${encodeURIComponent(replaceImageId)}/replace`, {method:'POST', headers:{'X-CSRF-Token':csrf}, body:form});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Replacement failed (${response.status})`);
    replaceDialog.close();
    flash(`Replaced image. ID ${replaceImageId} is unchanged.`);
    await loadFolder(currentPath);
    await loadConfig();
  } catch (error) { flash(error.message, 'error'); }
});

async function deleteItem(item) {
  const label = item.type === 'folder' ? `empty folder “${item.path}”` : `image “${item.path}”`;
  if (!confirm(`Delete ${label}? This cannot be undone.`)) return;
  try {
    await api('/api/item', {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path:item.path})});
    flash('Deleted.'); await loadFolder(currentPath); await loadConfig();
  } catch (error) { flash(error.message, 'error'); }
}

loadConfig().catch(e => flash(e.message, 'error'));
loadFolder('');
