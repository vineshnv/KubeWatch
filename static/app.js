let token = null;
let data = {};
let activeTable = 'pods';
let busy = false;
const $ = (id) => document.getElementById(id);
const escapeHTML = (v) => String(v ?? '').replace(/[&<>"']/g, (s) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.style.display = 'block';
  setTimeout(() => (el.style.display = 'none'), 4200);
}

function showPage(page) {
  document.querySelectorAll('.page').forEach((el) => (el.hidden = el.id !== page));
  document.querySelectorAll('.navbtn').forEach((el) => el.classList.toggle('active', el.dataset.target === page));
  $('pageTitle').textContent = {
    dashboard: 'Environment overview',
    cluster: 'Kubernetes resources',
    linux: 'Linux diagnostics',
    connections: 'Connections'
  }[page];
  $('pageDescription').textContent = {
    dashboard: 'Host diagnostics and Kubernetes runtime data in a single workspace.',
    cluster: 'Browse nodes, pods, deployments, events, and utilization output.',
    linux: 'Inspect host summary, process activity, and journal output.',
    connections: 'Connect to the local host or a remote Linux environment.'
  }[page];
}

document.querySelectorAll('.navbtn').forEach((el) => el.addEventListener('click', () => showPage(el.dataset.target)));
$('mode').onchange = () => {
  $('sshFields').hidden = $('mode').value !== 'ssh';
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers['X-Session'] = token;
  const response = await fetch('/api/' + path, { ...options, headers, cache: 'no-store' });
  let result;
  try {
    result = await response.json();
  } catch {
    result = { detail: 'Unexpected server response' };
  }
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Request failed');
  return result;
}

$('connectForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (token) await disconnect();
    const req = {
      mode: $('mode').value,
      host: $('host').value.trim(),
      port: Number($('port').value),
      username: $('username').value.trim(),
      password: $('password').value
    };
    const result = await api('connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req)
    });
    token = result.token;
    $('password').value = '';
    $('status').textContent = req.mode === 'local' ? 'Connected · Local' : 'Connected · SSH';
    $('status').className = 'status-badge online';
    $('statusConnection').textContent = req.mode === 'local' ? 'Local session' : 'SSH session';
    toast('Connection established.');
    await refresh();
    showPage('dashboard');
  } catch (err) {
    $('password').value = '';
    toast(err.message);
  }
});

$('kubeForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (!token) {
    toast('Connect to an environment first.');
    return;
  }
  const file = $('kubeFile').files[0];
  if (!file) {
    toast('Select a kubeconfig file.');
    return;
  }
  const form = new FormData();
  form.append('file', file);
  try {
    await api('kubeconfig', { method: 'POST', body: form });
    $('kubeFile').value = '';
    toast('Kubeconfig attached.');
    await refresh();
  } catch (err) {
    toast(err.message);
  }
});

async function disconnect() {
  if (token) {
    try {
      await api('disconnect', { method: 'POST' });
    } catch {}
  }
  token = null;
  data = {};
  $('status').textContent = 'Offline';
  $('status').className = 'status-badge';
  $('statusConnection').textContent = 'Not connected';
  $('statusKube').textContent = 'Unknown';
  $('statusIssues').textContent = '—';
  $('statusReady').textContent = '—';
  $('metricNodes').textContent = '—';
  $('metricPods').textContent = '—';
  $('metricIssues').textContent = '—';
  $('metricDeploy').textContent = '—';
  $('landscapeTag').textContent = 'No data';
  $('clusterTable').innerHTML = '<div class="empty">No cluster data loaded.</div>';
  $('eventSummary').innerHTML = '<div class="empty">Connect a cluster to load events.</div>';
  $('hostSummary').textContent = 'Host summary unavailable.';
  $('topOutput').textContent = 'Metrics output unavailable.';
  $('linuxOverview').textContent = 'Connect to an environment to view host output.';
  $('linuxProcesses').textContent = 'Process data unavailable.';
  $('linuxJournal').textContent = 'Journal output unavailable.';
  $('logAlerts').innerHTML = '<div class="empty">No recent alert matches.</div>';
  $('bars').replaceChildren();
  $('chartLabels').replaceChildren();
  toast('Disconnected.');
}

async function refresh() {
  if (!token) {
    showPage('connections');
    toast('Connect to an environment first.');
    return;
  }
  if (busy) return;
  busy = true;
  try {
    const names = ['overview', 'processes', 'journal', 'nodes', 'pods', 'deployments', 'events', 'top', 'log-alerts'];
    const results = await Promise.all(
      names.map(async (name) => {
        try {
          return [name, await api(name === 'log-alerts' ? 'log-alerts' : 'diagnostics/' + name)];
        } catch (e) {
          return [name, { ok: false, output: '', error: e.message }];
        }
      })
    );
    data = Object.fromEntries(results);
    update();
  } finally {
    busy = false;
  }
}

function tag(status) {
  const value = String(status);
  const cls = ['Running', 'True', 'Normal', 'Ready'].includes(value)
    ? 'ok'
    : ['Pending', 'Warning'].includes(value)
      ? 'warn'
      : 'fail';
  return `<span class="tag ${cls}">${escapeHTML(value)}</span>`;
}

function update() {
  const items = (name) => data[name]?.rows || [];
  const nodes = items('nodes');
  const pods = items('pods');
  const deploys = items('deployments');
  const events = items('events');

  $('metricNodes').textContent = data.nodes?.ok ? nodes.length : '—';
  $('metricPods').textContent = data.pods?.ok ? pods.filter((p) => p.status === 'Running').length : '—';
  const issues = pods.filter((p) => p.status !== 'Running');
  $('metricIssues').textContent = data.pods?.ok ? issues.length : '—';
  $('metricDeploy').textContent = data.deployments?.ok ? deploys.length : '—';

  $('statusKube').textContent = data.nodes?.ok ? 'Connected' : 'Unavailable';
  $('statusIssues').textContent = data.pods?.ok ? `${issues.length} item(s)` : '—';
  $('statusReady').textContent = data.nodes?.ok ? `${nodes.filter((n) => n.status === 'True').length}/${nodes.length}` : '—';

  $('linuxOverview').textContent = data.overview?.output || data.overview?.error || 'No response';
  $('linuxProcesses').textContent = (data.processes?.output || data.processes?.error || 'No response').split('\n').slice(0, 12).join('\n');
  $('linuxJournal').textContent = data.journal?.output || data.journal?.error || 'No journal lines available';
  $('hostSummary').textContent = (data.overview?.output || data.overview?.error || 'Not available').slice(0, 800);
  $('topOutput').textContent = data.top?.output || data.top?.error || 'Metrics output unavailable';

  const alerts = data['log-alerts'];
  $('logAlerts').innerHTML = alerts?.ok && alerts.alerts?.length
    ? alerts.alerts.map((entry) => `
        <div class="meta-row gap-3">
          <span class="tag warn">${escapeHTML(entry.category)}</span>
          <span class="caption flex-grow-1">${escapeHTML(entry.message)}</span>
        </div>`).join('')
    : `<div class="empty">${escapeHTML(alerts?.error || 'No recent alert matches.')}</div>`;

  const namespaceCounts = {};
  pods.forEach((pod) => {
    namespaceCounts[pod.namespace] = (namespaceCounts[pod.namespace] || 0) + 1;
  });
  const keys = Object.keys(namespaceCounts).sort((a, b) => namespaceCounts[b] - namespaceCounts[a]).slice(0, 18);
  $('landscapeTag').textContent = data.pods?.ok ? 'Live data' : 'No data';
  $('bars').replaceChildren();
  $('chartLabels').replaceChildren();
  if (!keys.length) {
    const hint = document.createElement('div');
    hint.className = 'empty w-100';
    hint.textContent = 'No pod counts available.';
    $('bars').append(hint);
  } else {
    const maxValue = Math.max(...keys.map((key) => namespaceCounts[key]));
    keys.forEach((key) => {
      const bar = document.createElement('div');
      bar.className = 'bar';
      bar.style.height = Math.max(14, Math.round((100 * namespaceCounts[key]) / maxValue)) + '%';
      bar.title = `${key}: ${namespaceCounts[key]} pods`;
      $('bars').append(bar);
      const label = document.createElement('span');
      label.className = 'chart-axis-label';
      label.textContent = key;
      label.title = `${key}: ${namespaceCounts[key]} pods`;
      $('chartLabels').append(label);
    });
  }

  $('eventSummary').innerHTML = events.length
    ? events.slice(0, 5).map((event) => `
        <div class="meta-row">
          <div>
            ${tag(event.type)}
            <div class="mt-2"><strong>${escapeHTML(event.reason)}</strong></div>
            <div class="caption mt-1">${escapeHTML(event.message)}</div>
          </div>
          <span class="caption">${escapeHTML(event.namespace)}</span>
        </div>`).join('')
    : '<div class="empty">No Kubernetes events available.</div>';

  renderTable(activeTable);
}

function renderTable(name) {
  activeTable = name;
  const source = data[name];
  if (!source?.ok) {
    $('clusterTable').innerHTML = `<div class="empty">${escapeHTML(source?.error || 'Connect to a cluster and refresh to view this resource.')}</div>`;
    return;
  }
  const rows = source.rows || [];
  if (!rows.length) {
    $('clusterTable').innerHTML = `<div class="empty">No ${escapeHTML(name)} found.</div>`;
    return;
  }
  const columns = Object.keys(rows[0]);
  $('clusterTable').innerHTML = `
    <div class="table-scroll">
      <table class="table table-hover align-middle">
        <thead><tr>${columns.map((column) => `<th>${escapeHTML(column.toUpperCase())}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map((row) => `<tr>${columns.map((column) => `<td>${['status', 'type'].includes(column) ? tag(row[column]) : escapeHTML(row[column])}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

showPage('connections');
