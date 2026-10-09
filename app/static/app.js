import { createPreview, staticPreview } from './preview.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {
  status: null, templates: [], projects: [], archived: [], project: null,
  route: 'home', library: 'templates', showArchived: false,
  busy: false, controller: null, view: 'preview', baseline: '', dirty: false,
};
const STARTER = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>我的应用</title>
  <style>
    body { margin: 0; padding: 48px 24px; font-family: system-ui, sans-serif; background: #f5f6fb; color: #30384e; }
    main { max-width: 640px; margin: auto; padding: 32px; border-radius: 20px; background: white; }
  </style>
</head>
<body>
  <main><h1>你好，我的新应用</h1><p>从这里开始，写下你想实现的功能。</p></main>
</body>
</html>`;
let toastTimer, navigationToken = 0, currentHash = '#/home';
const liveModel = () => state.status?.mode === 'deepseek' && state.status.configured;
const sourceLabel = item => ({ template: '实用模板', manual: '手动编辑' }[item.source] || (item.mode === 'mock' ? '离线示例' : 'AI 生成'));
const dateText = value => value ? new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function button(label, className, handler) {
  const result = node('button', className, label);
  result.type = 'button'; result.dataset.lock = ''; result.disabled = state.busy;
  result.onclick = () => safely(handler); return result;
}
function toast(text) {
  $('#toast').textContent = text; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4600);
}
async function safely(action) { try { await action(); } catch (error) { toast(error.message); } }
function errorText(value) {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(x => x.msg).join('；');
  return '请求失败，请稍后重试。';
}
async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(data ? errorText(data.detail) : '服务返回异常，请检查服务是否正在运行。');
    error.status = response.status; throw error;
  }
  return data;
}
const post = (path, value = {}) => api(path, { method: 'POST', body: JSON.stringify(value) });
const preview = createPreview($('#preview'), api, message => {
  $('#runtime-error').hidden = false;
  $('#runtime-error span').textContent = '预览脚本错误：' + message;
}, message => $('#data-status').textContent = message);

// One dialog implementation keeps keyboard, Escape and cancellation consistent.
function modal({ title, description = '', confirm = '确定', input, inputLabel = '项目名称', extra = '', cancel = true }) {
  if ($('#action-dialog').open) return Promise.resolve(null);
  $('#dialog-title').textContent = title;
  $('#dialog-kicker').textContent = 'ATOMS · 工作空间';
  $('#dialog-description').textContent = description;
  $('#dialog-confirm').textContent = confirm;
  $('#dialog-cancel').hidden = !cancel;
  $('#dialog-field').hidden = input === undefined;
  $('#dialog-label').textContent = inputLabel;
  $('#dialog-input').value = input ?? '';
  $('#dialog-input').required = input !== undefined;
  $('#dialog-extra').replaceChildren();
  if (extra) $('#dialog-extra').append(node('div', 'dialog-extra-box', extra));
  $('#dialog-error').hidden = true;
  return new Promise(resolve => {
    let result = null;
    const dialog = $('#action-dialog');
    $('#dialog-close').onclick = $('#dialog-cancel').onclick = () => dialog.close();
    $('#dialog-form').onsubmit = event => {
      event.preventDefault();
      if (input !== undefined && !$('#dialog-input').value.trim()) {
        $('#dialog-error').textContent = '请输入项目名称'; $('#dialog-error').hidden = false; return;
      }
      result = input === undefined ? true : $('#dialog-input').value.trim(); dialog.close();
    };
    dialog.onclose = () => resolve(result);
    dialog.showModal();
    if (input !== undefined) { $('#dialog-input').focus(); $('#dialog-input').select(); }
    else $('#dialog-confirm').focus();
  });
}
async function canLeave() {
  if (state.busy) { toast('请等待当前操作完成；生成任务可以点击停止。'); return false; }
  if (!state.dirty) return true;
  return Boolean(await modal({ title: '离开前，保留这次修改？', description: '代码编辑器中有尚未保存的内容。取消后可以先保存；继续将放弃这些修改。', confirm: '放弃修改' }));
}
async function setupGuide() {
  await modal({ title: liveModel() ? 'DeepSeek 已配置' : '先从模板开始，也能做出应用',
    description: liveModel() ? '后端已读取模型配置。实际可用性以生成结果为准。' : '现在可以使用三个应用模板、手动编辑代码并保存。自由描述生成应用需要启用 DeepSeek。',
    extra: liveModel() ? `当前模型：${state.status.model}。密钥仅由后端读取。` : '准备接入时，在本机项目 .env 中填写 DEEPSEEK_API_KEY，然后使用 LLM_MODE=deepseek bash run.sh 重启服务。不要把密钥填进需求框。', confirm: '知道了', cancel: false });
}
function syncControls() {
  $$('[data-lock]').forEach(item => item.disabled = state.busy);
  const p = state.project, readonly = p?.archived;
  $('#prompt').disabled = state.busy || readonly;
  $('#home-prompt').disabled = state.busy;
  $('#code-editor').readOnly = state.busy || readonly;
  $('#send').disabled = state.busy || readonly || !$('#prompt').value.trim();
  $('#stop').hidden = !state.controller;
  $('#download').disabled = state.busy || !p?.html;
  $('#save-code').disabled = state.busy || readonly || (!state.dirty && Boolean(p?.html));
  $('#discard-code').disabled = state.busy || !state.dirty;
  $('#refresh-preview').disabled = state.busy || !p?.html;
  $('#rename-project').disabled = state.busy || readonly;
  $('#retry').disabled = state.busy || readonly;
  $('#dirty-indicator').hidden = !state.dirty;
  $('#char-count').textContent = `${$('#prompt').value.length} / 4000`;
  $('#code-size').textContent = `${(new TextEncoder().encode($('#code-editor').value).length / 1024).toFixed(1)} KB`;
}
async function locked(action) {
  if (state.busy) return;
  state.busy = true; syncControls();
  try { return await action(); }
  finally { state.busy = false; syncControls(); }
}
async function refreshLists() {
  [state.projects, state.archived] = await Promise.all([api('/projects'), api('/projects?archived=true')]);
  renderSidebar(); if (!state.project) renderLibrary();
}
function remember(id) {
  try {
    const old = JSON.parse(localStorage.getItem('atoms-recent') || '[]');
    localStorage.setItem('atoms-recent', JSON.stringify([id, ...old.filter(x => x !== id)].slice(0, 8)));
  } catch { /* Browser privacy settings can disable localStorage. SQLite still saves projects. */ }
}
function renderSidebar() {
  $('#project-count').textContent = state.projects.length;
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem('atoms-recent') || '[]'); } catch { /* optional */ }
  const ordered = [...state.projects].sort((a, b) => {
    const rank = id => recent.includes(id) ? recent.indexOf(id) : 100;
    return rank(a.id) - rank(b.id) || b.updated_at.localeCompare(a.updated_at);
  }).slice(0, 7);
  $('#recent-projects').replaceChildren();
  if (!ordered.length) $('#recent-projects').append(node('p', 'recent-empty', '第一个作品，从一个模板开始。'));
  ordered.forEach(p => {
    const item = button('', 'recent-project' + (p.id === state.project?.id ? ' active' : ''), () => navigate('project/' + p.id));
    item.append(node('span', '', '◇'), node('span', '', p.title)); item.title = p.title;
    $('#recent-projects').append(item);
  });
  $$('.nav-items [data-route]').forEach(item => {
    const selected = item.dataset.route === (state.project ? 'projects' : state.route);
    item.classList.toggle('active', selected);
    if (selected) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
}
function renderLibrary() {
  const templates = state.library === 'templates';
  $('#home-intro').hidden = state.route !== 'home';
  $('#page-label').textContent = { home: '工作空间', templates: '应用模板', projects: '我的项目' }[state.route];
  $('#library-title').textContent = templates ? (state.route === 'home' ? '从这里开始' : '找到你的下一个好用工具') : state.showArchived ? '归档箱' : '我的项目';
  $('#library-description').textContent = templates ? '无需 API，选一个模板就能开始使用。每个作品都可以继续编辑。' : state.showArchived ? '归档项目保留代码和数据，恢复后可以继续编辑。' : '把想法留在这里，每次修改都能回头看看。';
  $('#library-all').hidden = state.route !== 'home';
  $('#library-create').hidden = templates;
  $('#tab-templates').setAttribute('aria-selected', String(templates));
  $('#tab-projects').setAttribute('aria-selected', String(!templates));
  $('#project-filters').hidden = templates;
  $('#archive-toggle').setAttribute('aria-pressed', String(state.showArchived));
  $('#archive-toggle').textContent = state.showArchived ? '返回活动项目' : '归档箱';
  let items = templates ? state.templates : [...(state.showArchived ? state.archived : state.projects)];
  if (!templates) {
    const query = $('#search').value.trim().toLocaleLowerCase();
    items = items.filter(p => p.title.toLocaleLowerCase().includes(query));
    const sort = $('#project-sort').value;
    items.sort((a, b) => sort === 'title' ? a.title.localeCompare(b.title, 'zh-CN') : (sort === 'created' ? b.created_at.localeCompare(a.created_at) : b.updated_at.localeCompare(a.updated_at)));
  }
  if (state.route === 'home') items = items.slice(0, 6);
  $('#cards').replaceChildren(...items.map(item => renderCard(item, templates)));
  $('#cards-empty').hidden = items.length > 0;
  $('#cards-empty h3').textContent = $('#search').value.trim() && !templates ? '没有找到匹配的项目' : state.showArchived ? '归档箱还是空的' : '还没有项目';
  $('#cards-empty p').textContent = $('#search').value.trim() && !templates ? '换一个名称试试，或清空搜索条件。' : '从模板开始，或创建一个空白项目。';
}
function renderCard(item, template) {
  const card = node('article', 'app-card'), cover = node('div', 'card-preview');
  const html = template ? item.html : item.preview_html;
  if (html) {
    const frame = node('iframe'); frame.setAttribute('sandbox', ''); frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1; frame.loading = 'lazy'; frame.title = item.title + '缩略图'; frame.srcdoc = staticPreview(html); cover.append(frame);
  } else cover.append(node('div', 'card-preview-placeholder', '</>'));
  const open = () => template ? createProject(item.id) : navigate('project/' + item.id);
  const overlay = button('', 'card-overlay', open);
  overlay.setAttribute('aria-label', (template ? '使用模板：' : '打开项目：') + item.title);
  overlay.append(node('span', '', template ? '使用这个模板 ↗' : '打开项目 ↗')); cover.append(overlay);
  const body = node('div', 'card-body'), info = node('div', 'card-info'), label = node('div');
  label.append(node('h3', 'card-title', item.title), node('p', 'card-meta', template ? '实用工具 · 数据自动保存' : `${dateText(item.updated_at)} · ${item.version_count} 个版本`));
  info.append(node('span', 'card-icon', template ? ({ tasks: '✓', expenses: '￥', focus: '◷' }[item.id] || '✦') : item.title.slice(0, 1)), label); body.append(info);
  if (template) body.append(node('p', 'card-description', item.description));
  const bottom = node('div', 'card-bottom');
  bottom.append(node('span', 'card-tag', template ? '无需 API' : item.archived ? '已归档' : sourceLabel(item)), button(template ? '使用模板 ↗' : '打开项目 ↗', 'card-use', open));
  body.append(bottom);
  if (!template) {
    const actions = node('div', 'card-actions');
    if (!item.archived) actions.append(button('重命名', '', () => renameProject(item)));
    actions.append(button('复制', '', () => duplicateProject(item)), button('备份', '', () => downloadFile(item, 'backup')),
      button(item.archived ? '恢复项目' : '归档', '', () => archiveProject(item)));
    if (item.archived) actions.append(button('永久删除', 'danger-action', () => deleteProject(item)));
    body.append(actions);
  }
  card.append(cover, body); return card;
}
function setHash(route) {
  currentHash = '#/' + route;
  if (location.hash !== currentHash) history.pushState(null, '', currentHash);
}
async function navigate(route, fromHistory = false) {
  if (!(await canLeave())) {
    if (fromHistory) history.replaceState(null, '', currentHash);
    return;
  }
  const token = ++navigationToken;
  await locked(async () => {
    if (state.project) await preview.flush(state.project.id);
    if (route.startsWith('project/')) {
      const id = route.slice(8);
      const project = await api('/projects/' + encodeURIComponent(id));
      if (token !== navigationToken) return;
      showProject(project);
    } else {
      state.project = null; state.dirty = false; preview.clear();
      state.route = ['home', 'templates', 'projects'].includes(route) ? route : 'home';
      state.library = state.route === 'projects' ? 'projects' : 'templates'; state.showArchived = false;
      $('#dashboard').hidden = false; $('#studio').hidden = true; $('#app').classList.remove('studio-open');
      await refreshLists(); renderLibrary(); renderSidebar();
    }
    setHash(state.project ? 'project/' + state.project.id : state.route);
  });
}
function showProject(project) {
  state.project = project; state.route = 'projects'; remember(project.id);
  $('#dashboard').hidden = true; $('#studio').hidden = false; $('#app').classList.add('studio-open');
  $('#project-title').textContent = project.title; $('#project-title').title = project.title;
  $('#studio-notice').hidden = liveModel() && !project.archived;
  $('#studio-notice').textContent = project.archived ? '这个项目已归档，仅供查看。请到“我的项目 → 归档箱”恢复后继续使用。' : '当前使用离线模板和代码编辑功能。接入 DeepSeek 后，可用左侧对话继续生成和修改。';
  $('#chat-mode').textContent = liveModel() ? state.status.model : '模板已就绪 · 自由生成待接入';
  $('#composer-hint').textContent = liveModel() ? '⌘ / Ctrl + Enter 发送' : '当前可在右侧编辑代码；发送需求会显示接入说明';
  $('#chat-welcome').hidden = project.messages.length > 0;
  $('#messages').replaceChildren(); project.messages.forEach(m => addMessage(m.role, m.content, m.created_at));
  $('#prompt').value = '';
  state.baseline = project.html || STARTER; $('#code-editor').value = state.baseline;
  state.dirty = false; $('#code-error').hidden = true;
  const version = project.versions.find(v => v.id === project.current_version);
  $('#version-label').textContent = version ? `v${version.number} · ${sourceLabel(version)}` : '尚无版本';
  $('#saved-status').textContent = project.html ? `已保存 · ${dateText(project.updated_at)}` : '空白项目 · 保存代码开始预览';
  $('#data-status').textContent = project.archived ? '已归档 · 只读' : '数据自动保存';
  const last = project.last_run;
  $('#run-error').hidden = !last || last.status === 'completed';
  $('#run-error p').textContent = last?.error || (last?.status === 'running' ? '项目仍在生成，请稍后刷新状态。' : '上次生成未完成，可以重新尝试。');
  $('#retry').hidden = !last?.prompt || last.status === 'running';
  renderHistory(); renderPreview(); renderSidebar(); syncControls(); scrollChat();
  setHash('project/' + project.id);
}
function renderPreview() {
  $('#runtime-error').hidden = true;
  $('#preview-empty').hidden = Boolean(state.project?.html); $('#preview').hidden = !state.project?.html;
  if (state.project?.html) preview.render(state.project); else preview.clear();
}
function addMessage(role, content, time) {
  const message = node('div', 'message ' + (role === 'user' ? 'user' : 'assistant'));
  if (role !== 'user') message.append(node('span', 'message-label', '✦ 构建助手'));
  message.append(document.createTextNode(content));
  if (time) message.append(node('small', 'message-time', dateText(time)));
  $('#messages').append(message);
}
function scrollChat() { $('#chat-scroll').scrollTop = $('#chat-scroll').scrollHeight; }
function setView(view) {
  state.view = view;
  for (const name of ['preview', 'code']) {
    $('#' + name + '-panel').hidden = view !== name;
    $('#' + name + '-tab').setAttribute('aria-selected', String(view === name));
    $('#' + name + '-tab').tabIndex = view === name ? 0 : -1;
  }
}
function renderHistory() {
  $('#history-list').replaceChildren();
  for (const version of state.project.versions) {
    const active = version.id === state.project.current_version;
    const item = node('div', 'history-item' + (active ? ' active' : ''));
    item.append(node('h3', '', `版本 ${version.number} · ${sourceLabel(version)}`), node('p', '', version.summary), node('small', '', dateText(version.created_at)));
    if (active) item.append(node('span', 'current-label', '正在使用'));
    else if (!state.project.archived) item.append(button('恢复此版本', 'text-btn', () => restoreVersion(version)));
    $('#history-list').append(item);
  }
  if (!state.project.versions.length) $('#history-list').append(node('p', 'recent-empty', '保存第一版代码后，历史记录会出现在这里。'));
}
async function createProject(templateId) {
  if (!(await canLeave())) return;
  await locked(async () => {
    if (state.project) await preview.flush(state.project.id);
    const p = templateId ? await post('/projects/from-template', { template_id: templateId }) : await post('/projects', { title: '新项目' });
    showProject(p); await refreshLists(); setView(templateId ? 'preview' : 'code');
    toast(templateId ? '模板已创建，现在就可以使用' : '空白项目已创建，可以编辑代码');
  });
}
async function renameProject(project) {
  if (state.busy) return;
  const title = await modal({ title: '给作品一个名字', input: project.title, confirm: '保存名称' });
  if (!title || title === project.title) return;
  await locked(async () => {
    const p = await api('/projects/' + project.id, { method: 'PATCH', body: JSON.stringify({ title }) });
    if (state.project?.id === p.id) { state.project.title = p.title; $('#project-title').textContent = p.title; }
    await refreshLists(); toast('名称已更新');
  });
}
async function duplicateProject(project) {
  if (!(await canLeave())) return;
  await locked(async () => {
    await preview.flush(project.id);
    const p = await post(`/projects/${project.id}/duplicate`);
    showProject(p); await refreshLists(); toast('已复制为独立项目，包含已保存的代码和数据');
  });
}
async function archiveProject(project) {
  if (state.busy) return;
  if (!project.archived && !await modal({ title: `归档“${project.title}”？`, description: '代码、版本与数据会保留。稍后可从归档箱恢复。', confirm: '归档项目' })) return;
  await locked(async () => {
    await preview.flush(project.id);
    await post(`/projects/${project.id}/${project.archived ? 'unarchive' : 'archive'}`);
    await refreshLists(); toast(project.archived ? '项目已恢复' : '项目已移到归档箱');
  });
}
async function deleteProject(project) {
  if (state.busy || !project.archived) return;
  const typed = await modal({
    title: `永久删除“${project.title}”？`,
    description: '项目、全部代码版本和应用数据都会删除，且无法恢复。请先备份需要保留的内容。',
    input: '', inputLabel: `输入项目名称“${project.title}”确认`, confirm: '永久删除',
  });
  if (!typed) return;
  if (typed !== project.title) { toast('项目名称不一致，未删除'); return; }
  await locked(async () => {
    await api(`/projects/${project.id}`, { method: 'DELETE' });
    await refreshLists(); toast('项目已永久删除');
  });
}
async function saveCode() {
  if (state.busy || state.project?.archived || (!state.dirty && state.project?.html)) return;
  $('#code-error').hidden = true;
  await locked(async () => {
    try {
      await preview.flush(state.project.id);
      const p = await post(`/projects/${state.project.id}/versions`, { html: $('#code-editor').value, summary: '手动编辑并保存代码', base_version_id: state.project.current_version });
      showProject(p); await refreshLists(); setView('preview'); toast('新版本已保存');
    } catch (error) {
      $('#code-error').textContent = error.status === 409 ? error.message + '。草稿已保留，请复制草稿后刷新项目状态再合并。' : error.message;
      $('#code-error').hidden = false;
    }
  });
}
async function restoreVersion(version) {
  if (!(await canLeave())) return;
  if (!await modal({ title: `恢复到版本 ${version.number}？`, description: '切换当前使用的代码，全部历史版本继续保留。待办、账目等业务数据不会回退。', confirm: '恢复此版本' })) return;
  await locked(async () => {
    await preview.flush(state.project.id);
    showProject(await post(`/projects/${state.project.id}/restore/${version.id}`));
    await refreshLists(); toast('已恢复代码版本，业务数据保持不变');
  });
}
async function downloadFile(project, kind) {
  if (state.busy) return;
  await locked(async () => {
    await preview.flush(project.id);
    const response = await fetch(`/api/projects/${project.id}/${kind}`);
    if (!response.ok) throw new Error(errorText((await response.json()).detail));
    const url = URL.createObjectURL(await response.blob());
    const link = node('a'); link.href = url;
    link.download = project.title.replace(/[\\/:*?"<>|]/g, '_') + (kind === 'backup' ? '.json' : '.html');
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(kind === 'backup' ? '已导出项目代码、版本和业务数据' : '已导出 HTML；独立页面从空白业务数据开始');
  });
}
async function importFile(file) {
  if (!file || !(await canLeave())) return;
  await locked(async () => {
    if (file.size > 20_000_000) throw new Error('备份文件不能超过20MB');
    let backup; try { backup = JSON.parse(await file.text()); } catch { throw new Error('这不是有效的 JSON 备份文件'); }
    if (backup?.format !== 'atoms-demo-backup') throw new Error('请选择从工作台导出的项目 JSON 备份');
    if (state.project) await preview.flush(state.project.id);
    const p = await post('/projects/import', backup); showProject(p); await refreshLists(); setView('preview'); toast('备份已导入为新项目');
  });
}

function onProgress(item) {
  if (item.type === 'stage') {
    $('#stage-message').textContent = item.message;
    const index = ['plan', 'build', 'check'].indexOf(item.stage === 'repair' ? 'check' : item.stage);
    $$('[data-stage]').forEach((step, i) => step.classList.toggle('active', i <= index));
  } else if (item.type === 'plan') {
    $('#plan-list').replaceChildren(...item.steps.map(step => node('li', '', step)));
  } else if (item.type === 'error') throw new Error(item.message);
  scrollChat();
}
async function generate(prompt) {
  if (!prompt.trim() || state.busy || !state.project || state.project.archived) return;
  if (!liveModel()) { await setupGuide(); return; }
  if (!(await canLeave())) return;
  await locked(async () => {
    const pid = state.project.id;
    await preview.flush(pid);
    state.controller = new AbortController(); syncControls();
    $('#run-error').hidden = true; $('#chat-welcome').hidden = true; $('#plan-list').replaceChildren();
    $('#progress').hidden = false; addMessage('user', prompt); setView('preview');
    onProgress({ type: 'stage', stage: 'plan', message: '正在连接构建助手' });
    let completed = false, failure;
    try {
      const response = await fetch(`/api/projects/${pid}/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }), signal: state.controller.signal });
      if (!response.ok) throw new Error(errorText((await response.json()).detail));
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }); let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          const line = block.split('\n').find(x => x.startsWith('data: '));
          if (line) { const item = JSON.parse(line.slice(6)); if (item.type === 'done') completed = true; else onProgress(item); }
        }
      }
      if (!completed) throw new Error('连接提前结束，请刷新项目状态后重试。');
    } catch (error) {
      failure = error.name === 'AbortError' ? '已停止生成，已保存的版本仍然保留。' : error.message;
    } finally {
      state.controller = null; $('#progress').hidden = true;
      // Keep the operation locked until final data and sidebar are refreshed.
      try { showProject(await api('/projects/' + pid)); await refreshLists(); }
      catch (error) { failure = failure || error.message; }
      if (failure) {
        $('#run-error').hidden = false; $('#run-error p').textContent = failure; $('#retry').hidden = false;
        $('#prompt').value = prompt;
      } else { $('#prompt').value = ''; toast('新版本已保存，可以在右侧试用'); }
      scrollChat();
    }
  });
}

// UI wiring. Every operation has an error surface and shares the mutation lock.
$$('[data-route]').forEach(item => item.onclick = () => safely(() => navigate(item.dataset.route)));
$('#new-project').onclick = $('#home-blank').onclick = $('#empty-create').onclick = $('#library-create').onclick = () => safely(() => createProject());
$('#back-home').onclick = () => safely(() => navigate('home'));
$('#studio-template').onclick = () => safely(() => navigate('templates'));
$('#library-all').onclick = () => safely(() => navigate(state.library));
for (const tab of ['templates', 'projects']) $('#tab-' + tab).onclick = () => {
  state.library = tab; state.showArchived = false; renderLibrary();
};
$('#search').oninput = $('#project-sort').onchange = renderLibrary;
$('#archive-toggle').onclick = () => { state.showArchived = !state.showArchived; renderLibrary(); };
$('#connection').onclick = $('#top-mode').onclick = () => safely(setupGuide);
$('#rename-project').onclick = () => safely(() => renameProject(state.project));
$('#backup-project').onclick = () => safely(() => downloadFile(state.project, 'backup'));
$('#download').onclick = () => safely(() => downloadFile(state.project, 'download'));
$('#import-project').onclick = $('#library-import').onclick = () => $('#import-file').click();
$('#import-file').onchange = () => { const file = $('#import-file').files[0]; $('#import-file').value = ''; safely(() => importFile(file)); };
$('#save-code').onclick = () => safely(saveCode);
$('#discard-code').onclick = () => safely(async () => {
  if (!await modal({ title: '撤销尚未保存的修改？', description: '代码会恢复为当前已保存的版本。', confirm: '撤销编辑' })) return;
  $('#code-editor').value = state.baseline; state.dirty = false; $('#code-error').hidden = true; syncControls();
});
$('#code-editor').oninput = () => { state.dirty = $('#code-editor').value !== state.baseline; syncControls(); };
$('#code-editor').onkeydown = event => {
  if (event.key === 'Tab' && !event.shiftKey && !$('#code-editor').readOnly) {
    event.preventDefault(); const input = $('#code-editor'); input.setRangeText('  ', input.selectionStart, input.selectionEnd, 'end'); input.dispatchEvent(new Event('input'));
  }
};
$('#refresh-project').onclick = () => safely(async () => {
  if (!(await canLeave())) return;
  await locked(async () => { await preview.flush(state.project.id); showProject(await api('/projects/' + state.project.id)); await refreshLists(); });
});
$('#refresh-preview').onclick = () => safely(() => locked(async () => { await preview.flush(state.project.id); renderPreview(); }));
for (const view of ['preview', 'code']) {
  $('#' + view + '-tab').onclick = () => setView(view);
  $('#' + view + '-tab').onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); const next = view === 'preview' ? 'code' : 'preview'; setView(next); $('#' + next + '-tab').focus(); }
  };
}
for (const mode of ['desktop', 'mobile']) $('#' + mode).onclick = () => {
  $('#preview-panel').classList.toggle('mobile', mode === 'mobile');
  for (const name of ['desktop', 'mobile']) { $('#' + name).classList.toggle('selected', name === mode); $('#' + name).setAttribute('aria-pressed', String(name === mode)); }
};
function toggleHistory(show) { $('#history-panel').hidden = !show; $('#history-toggle').setAttribute('aria-expanded', String(show)); }
$('#history-toggle').onclick = () => toggleHistory($('#history-panel').hidden);
$('#history-close').onclick = () => toggleHistory(false);
function fillPrompt(value) { $('#prompt').value = value; syncControls(); $('#prompt').focus(); }
$$('[data-suggestion]').forEach(item => item.onclick = () => fillPrompt(item.dataset.suggestion));
$$('[data-idea]').forEach(item => item.onclick = () => {
  if (!liveModel()) { safely(() => createProject(item.dataset.template)); return; }
  $('#home-prompt').value = item.dataset.idea; $('#home-prompt').focus();
});
$('#repair-prompt').onclick = () => fillPrompt('请修复当前应用的脚本错误，并保留原有功能：' + $('#runtime-error span').textContent);
$('#composer').onsubmit = event => { event.preventDefault(); safely(() => generate($('#prompt').value.trim())); };
$('#home-composer').onsubmit = event => { event.preventDefault(); safely(async () => {
  if (state.busy) return;
  if (!liveModel()) { await setupGuide(); return; }
  const prompt = $('#home-prompt').value.trim(); if (!prompt) { $('#home-prompt').focus(); return; }
  await createProject(); $('#prompt').value = prompt; syncControls(); await generate(prompt);
}); };
$('#retry').onclick = () => safely(() => generate($('#prompt').value.trim() || state.project?.last_run?.prompt || ''));
$('#prompt').oninput = syncControls;
$('#prompt').onkeydown = event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); $('#composer').requestSubmit(); } };
$('#stop').onclick = () => state.controller?.abort();
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && state.project && state.view === 'code' && !$('#action-dialog').open) { event.preventDefault(); safely(saveCode); }
});
window.addEventListener('beforeunload', event => { if (state.busy || state.dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('hashchange', () => safely(() => navigate(location.hash.slice(2) || 'home', true)));
$('#reload-app').onclick = () => location.reload();
async function init() {
  // Establish owner cookie before parallel requests, otherwise each request may
  // accidentally start its own first-time session.
  state.status = await api('/status');
  [state.templates, state.projects, state.archived] = await Promise.all([api('/templates'), api('/projects'), api('/projects?archived=true')]);
  $('#mode-label').textContent = liveModel() ? 'DeepSeek 已配置' : '本地应用模式';
  $('#mode-description').textContent = liveModel() ? state.status.model : '模板与编辑 · 无需 API';
  $('#connection-icon').textContent = liveModel() ? '✓' : '✧';
  $('#top-mode').textContent = liveModel() ? '✦ DeepSeek 已配置' : '✧ 无需 API，也能开始';
  $('#top-mode').classList.toggle('ready', liveModel());
  $('#home-mode-note').textContent = liveModel() ? 'DeepSeek 驱动 · 自动保存版本' : '点击下方快捷入口，直接打开可用模板';
  renderSidebar();
  try { await navigate(location.hash.slice(2) || 'home'); }
  catch (error) {
    if (error.status !== 404) throw error;
    toast(error.message); await navigate('home');
  }
}
init().catch(error => {
  $('#fatal-error').hidden = false; $('#fatal-error p').textContent = error.message;
  $('#dashboard').hidden = true; $('#studio').hidden = true;
});
