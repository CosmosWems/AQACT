/* ─────────────────────────────────────────────────────────
   Plotly guard
   ───────────────────────────────────────────────────────── */
function plotlyReady(cb) {
  if (typeof Plotly !== 'undefined') { cb(); return; }
  let n = 0;
  const t = setInterval(() => {
    n++;
    if (typeof Plotly !== 'undefined') { clearInterval(t); cb(); return; }
    if (n > 150) { clearInterval(t); document.getElementById('plotly-error-banner').style.display='flex'; }
  }, 100);
}

/* ─────────────────────────────────────────────────────────
   Global State
   ───────────────────────────────────────────────────────── */
const STATE = {
  sessionId:null, columns:[], shape:[0,0], dtypes:{},
  isCleaned:false, queuedFiles:[], batchFiles:[], msFiles:[],
  mapInstance:null, mapHeatLayer:null, mapMarkers:[], mapLabels:[],
  mapFittedFor:null, mapStyle:'topographic', mapTileLayer:null, mapBaseLayer:null,
  showHeat:true, showMarkers:true,
  savedPlots:[], galleryCount:0,
  cleanToggles:{ apply_pm25_correction:true, resample_hourly:true },
  extremeToggles:{ who:true, epa:true },
  msToggles:{ apply_pm25_correction:true, resample_hourly:true },
  batchId:null,
};
// Expose the state object to the enhancement and assistant layers.
window.STATE = STATE;

/* ─────────────────────────────────────────────────────────
   Celestial chart layout
   ───────────────────────────────────────────────────────── */
function getCelestialLayout() {
  const dark = document.documentElement && document.documentElement.getAttribute('data-theme') === 'dark';
  const surface = dark ? '#171717' : '#ffffff';
  const ink = dark ? '#fafafa' : '#0a0a0a';
  const grid = dark ? '#333333' : '#e2e8f0';
  const axis = dark ? '#525252' : '#b7c6db';
  const legendBorder = dark ? '#3f3f46' : '#d6e1f0';
  const legendInk = dark ? '#fafafa' : '#001737';
  return {
    paper_bgcolor:surface, plot_bgcolor:surface,
    font:{ color:ink, size:11, family:'IBM Plex Mono, ui-monospace, monospace' },
    margin:{ t:44,r:20,b:56,l:60 },
    xaxis:{ gridcolor:grid, zerolinecolor:axis, linecolor:axis },
    yaxis:{ gridcolor:grid, zerolinecolor:axis, linecolor:axis },
    legend:{ bgcolor:surface, bordercolor:legendBorder, borderwidth:1, font:{color:legendInk} },
  };
}

function rethemePlots() {
  if (typeof Plotly === 'undefined') return;
  const base = getCelestialLayout();
  const patch = {
    paper_bgcolor:base.paper_bgcolor,
    plot_bgcolor:base.plot_bgcolor,
    'font.color':base.font.color,
    'font.family':base.font.family,
    'xaxis.gridcolor':base.xaxis.gridcolor,
    'xaxis.zerolinecolor':base.xaxis.zerolinecolor,
    'xaxis.linecolor':base.xaxis.linecolor,
    'yaxis.gridcolor':base.yaxis.gridcolor,
    'yaxis.zerolinecolor':base.yaxis.zerolinecolor,
    'yaxis.linecolor':base.yaxis.linecolor,
    'legend.bgcolor':base.legend.bgcolor,
    'legend.bordercolor':base.legend.bordercolor,
    'legend.font.color':base.legend.font.color,
  };
  document.querySelectorAll('.js-plotly-plot').forEach((el) => {
    try {
      const result = Plotly.relayout(el, patch);
      if (result && typeof result.catch === 'function') result.catch(() => {});
    } catch (_) {
      /* A partially rendered plot can reject a relayout; later renders use the new layout. */
    }
  });
}
window.rethemePlots = rethemePlots;

function applyCelestialLayout(extra) {
  const base = getCelestialLayout();
  return Object.assign({}, base, extra,
    extra.xaxis ? { xaxis: Object.assign({}, base.xaxis, extra.xaxis) } : {},
    extra.yaxis ? { yaxis: Object.assign({}, base.yaxis, extra.yaxis) } : {}
  );
}

const PLOT_CONFIG = {
  responsive:true, displaylogo:false,
  modeBarButtonsToRemove:['lasso2d','select2d'],
  toImageButtonOptions:{ format:'png', scale:2, filename:'AQACT_plot' },
};

/* ─────────────────────────────────────────────────────────
   Utilities
   ───────────────────────────────────────────────────────── */
function toast(msg, type='success', duration=4000) {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => { el.style.opacity='0'; el.style.transition='opacity .4s';
    setTimeout(()=>el.remove(), 400); }, duration);
}

/* Flip-card presentation helper. It only toggles presentation state; all
   embedded controls and handlers remain in the DOM and unchanged. */
function syncFlipA11y(card) {
  const flipped = card.classList.contains('is-flipped');
  card.querySelectorAll('.flip-action').forEach(btn => {
    btn.setAttribute('aria-pressed', flipped ? 'true' : 'false');
    const label = btn.dataset.flipLabel || btn.textContent.replace(/\s*[↻←→]\s*$/, '').trim();
    btn.dataset.flipLabel = label;
    btn.setAttribute('aria-label', `${flipped ? 'Show front of' : 'Show back of'} ${label}`);
  });
  const front = card.querySelector('.flip-face.front');
  const back = card.querySelector('.flip-face.back');
  if (front) {
    front.setAttribute('aria-hidden', flipped ? 'true' : 'false');
    if ('inert' in front) front.inert = flipped;
  }
  if (back) {
    back.setAttribute('aria-hidden', flipped ? 'false' : 'true');
    if ('inert' in back) back.inert = !flipped;
  }
}

function toggleFlip(button) {
  const card = button && button.closest ? button.closest('.flip-card') : null;
  if (!card) return;
  card.classList.toggle('is-flipped');
  syncFlipA11y(card);
}

function initFlipCards() {
  document.querySelectorAll('.flip-card').forEach(syncFlipA11y);
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initFlipCards, { once: true });
} else {
  initFlipCards();
}

async function clearSessionAndCache() {
  try {
    try { await fetch('/api/reset', { method: 'POST' }); } catch (_) { /* browser state can still be reset offline */ }
    if (window.STATE?.mapInstance) window.STATE.mapInstance.remove();
    if (window.caches?.keys) {
      const keys = await caches.keys();
      await Promise.all(keys.map(key => caches.delete(key)));
    }
    if (window.indexedDB?.databases) {
      const databases = await indexedDB.databases();
      await Promise.all(databases.map(db => db.name && new Promise(resolve => {
        const request = indexedDB.deleteDatabase(db.name);
        request.onsuccess = request.onerror = request.onblocked = resolve;
      })));
    }
    localStorage.clear();
    sessionStorage.clear();
    window.location.reload();
  } catch (error) {
    toast(`Could not fully clear browser state: ${error.message}`, 'error', 6000);
  }
}

async function api(endpoint, body={}, method='POST') {
  const res = await fetch(endpoint, {
    method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch (_) { data = { error: text.slice(0, 240) || `HTTP ${res.status}` }; }
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.code = data.code;
    err.hint = data.hint;
    err.requestId = data.request_id;
    throw err;
  }
  return data;
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  if (loading) { btn.dataset.orig = btn.innerHTML; btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;display:inline-block"></span> Working…'; }
  else          { btn.innerHTML = btn.dataset.orig || ''; }
}

function buildTable(tableId, columns, rows, maxRows=150) {
  const tbl = document.getElementById(tableId); if (!tbl) return;
  const display = rows.slice(0, maxRows);
  let h = '<thead><tr>' + columns.map(c=>`<th>${c}</th>`).join('') + '</tr></thead><tbody>';
  display.forEach(row => {
    h += '<tr>' + columns.map(c => {
      const v = row[c];
      return `<td>${v===null||v===undefined?'<span style="color:var(--text4)">—</span>':v}</td>`;
    }).join('') + '</tr>';
  });
  h += '</tbody>';
  if (rows.length > maxRows)
    h += `<tfoot><tr><td colspan="${columns.length}" class="table-footer">Showing ${maxRows} of ${rows.length.toLocaleString()} rows</td></tr></tfoot>`;
  tbl.innerHTML = h;
}

function toggleDataPreview(force) {
  const panel = document.getElementById('upload-preview');
  if (!panel) return;
  const expanded = typeof force === 'boolean' ? force : !panel.classList.contains('expanded');
  panel.classList.toggle('expanded', expanded);
  const header = panel.querySelector('.preview-header');
  if (header) header.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  const label = document.getElementById('preview-toggle-label');
  if (label) label.textContent = expanded ? 'Collapse preview' : 'Show full preview';
}
window.toggleDataPreview = toggleDataPreview;

function refreshColumnDropdowns() {
  const cols = STATE.columns;
  ['rename-old','delete-col','plot-single-col','plot-x-col','plot-y-col',
   'plot-color-col','plot-facet-col','plot-size-col','plot-ws-col','plot-wd-col',
   'plot-adv-group','plot-adv-value','plot-lat-col','plot-lon-col',
   'eval-obs-col','eval-mod-col','ext-col','out-col']
  .forEach(id => {
    const el = document.getElementById(id); if (!el) return;
    const isOpt = ['plot-color-col'].includes(id);
    el.innerHTML = (isOpt ? '<option value="">None</option>' : '<option value="">— select —</option>') +
      cols.map(c=>`<option value="${c}">${c}</option>`).join('');
  });
  buildColCheckboxes(cols);
  buildColumnsOverview(cols);
  buildAirnoteMapping(cols);
  ['compliance-col','ml-target'].forEach(id => {
    const el = document.getElementById(id); if (!el) return;
    el.innerHTML = '<option value="">— select —</option>' + cols.map(c => `<option value="${c}">${c}</option>`).join('');
    const preferred = ['pm2.5','pm10','O3','NO2'].find(c => cols.includes(c));
    if (preferred) el.value = preferred;
  });
    const featureBox = document.getElementById('ml-features');
    if (featureBox) featureBox.innerHTML = cols.filter(c => c !== 'datetime').map(c => `<label style="font-size:12px;color:var(--text2)"><input type="checkbox" class="ml-feature" value="${c}" ${['temperature','RH','pm10','NO2'].includes(c)?'checked':''}/> ${c}</label>`).join('');
  // Extract year list from "year" column presence in preview data
  if (STATE._previewRows && cols.includes("year")) {
    const ys = [...new Set(STATE._previewRows.map(r=>r.year).filter(y=>y!=null&&!isNaN(y)))].sort();
    STATE._yearList = ys;
  }
  refreshCalendarYears();
  refreshScatterColorBy();
  refreshAdvancedPlotDefaults();
}

function buildAirnoteMapping(cols) {
  const card = document.getElementById('airnote-mapping-card');
  const grid = document.getElementById('airnote-mapping-grid');
  if (!card || !grid) return;
  renderColumnMapping(grid, cols, 'airnote-map');
  card.style.display = 'block';
}

function getAirnoteMapping() {
  return getColumnMapping('airnote-mapping-grid');
}

function renderColumnMapping(grid, cols, className) {
  const fields = [
    ['datetime','Datetime'], ['pm2.5','PM2.5'], ['pm1','PM1'], ['pm10','PM10'],
    ['temperature','Temperature'], ['RH','Relative humidity'], ['pressure','Pressure'],
    ['CO2','CO2'], ['VOC','VOC'], ['NO2','NO2'], ['NOx','NOx'], ['NH3','NH3'],
    ['CO','CO'], ['O3','O3'], ['SO2','SO2'], ['rainfall','Rainfall'],
    ['windspeed','Wind speed'], ['wind_direction','Wind direction'],
    ['lat','Latitude'], ['lon','Longitude']
  ];
  grid.innerHTML = fields.map(([key, label]) => {
    const preferred = key === 'pm1' ? ['pm1','pm1.0'] : [key];
    const selected = preferred.find(col => cols.includes(col)) || '';
    const options = '<option value="">Auto / none</option>' + cols.map(col =>
      `<option value="${col}"${col === selected ? ' selected' : ''}>${col}</option>`).join('');
    return `<div class="form-group">
      <label class="form-label">${label}</label>
      <select class="form-select ${className}" data-target="${key}" style="font-size:12px">${options}</select>
    </div>`;
  }).join('');
}

function getColumnMapping(gridId) {
  const grid = document.getElementById(gridId);
  if (!grid) return {};
  return Object.fromEntries([...grid.querySelectorAll('select[data-target]')]
    .filter(el => el.value).map(el => [el.dataset.target, el.value]));
}

function buildColCheckboxes(cols) {
  const box = document.getElementById('col-checkboxes'); if (!box) return;
  const sel = ['pm2.5'];
  box.innerHTML = cols.map(c => `
    <button class="col-chip ${sel.includes(c)?'active':''}" onclick="toggleColChip(this,'${c}')"
      style="background:${sel.includes(c)?'var(--blue)':'var(--bg4)'};
      border:1px solid ${sel.includes(c)?'var(--blue)':'var(--border)'};
      border-radius:20px;padding:3px 12px;font-size:12px;
      color:${sel.includes(c)?'#fff':'var(--text3)'};cursor:pointer;transition:all .15s" data-col="${c}">${c}</button>
  `).join('');
}

function toggleColChip(btn, col) {
  const on = btn.classList.toggle('active');
  btn.style.background = on ? 'var(--blue)' : 'var(--bg4)';
  btn.style.color = on ? '#fff' : 'var(--text3)';
  btn.style.borderColor = on ? 'var(--blue)' : 'var(--border)';
}

function getSelectedChips() {
  return [...document.querySelectorAll('.col-chip.active')].map(b=>b.dataset.col);
}

function buildColumnsOverview(cols) {
  const grid = document.getElementById('columns-list'); if (!grid) return;
  const ICONS = { float64:'🔢', int64:'🔢', object:'📝', datetime64:'📅', bool:'☑' };
  grid.innerHTML = cols.map(c => {
    const dtype = STATE.dtypes?.[c] || 'unknown';
    const icon = ICONS[dtype.split('[')[0]] || '❓';
    return `<div style="background:var(--bg);border:1px solid var(--bg4);border-radius:8px;
      padding:8px 12px;display:flex;align-items:center;gap:8px">
      <div style="width:8px;height:8px;border-radius:50%;background:var(--blue);flex-shrink:0"></div>
      <span style="color:var(--text2);font-size:12px;overflow:hidden;text-overflow:ellipsis;flex:1">${c}</span>
      <span style="font-size:11px;background:var(--bg4);padding:1px 6px;border-radius:10px;
        color:var(--text4);flex-shrink:0">${dtype.split('(')[0]}</span>
    </div>`;
  }).join('');
  document.getElementById('columns-grid').style.display = 'block';
}

/* ─────────────────────────────────────────────────────────
   Tab switching
   ───────────────────────────────────────────────────────── */
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  const btn = [...document.querySelectorAll('.tab-btn')]
    .find(b=>b.getAttribute('onclick')===`switchTab('${name}')`);
  if (btn) btn.classList.add('active');
  const panel = document.getElementById(`panel-${name}`);
  if (panel) panel.classList.add('active');
  if (name==='map' && STATE.sessionId) setTimeout(() => {
    initMap();
  }, 100);
  if (name==='explore' && STATE.sessionId) loadStats();
}

function enableTabs() {
  ['clean','explore','plots','map','extreme','compliance','machinelearning','modeleval'].forEach(id => {
    const btn = document.getElementById(`tab-${id}`);
    if (btn) btn.disabled = false;
  });
}

/* ─────────────────────────────────────────────────────────
   Toggle helpers
   ───────────────────────────────────────────────────────── */
function toggleOpt(key) {
  STATE.cleanToggles[key] = !STATE.cleanToggles[key];
  document.getElementById(`tog-${key}`)?.classList.toggle('on', STATE.cleanToggles[key]);
}

function toggleOpt2(key) {
  STATE.extremeToggles[key] = !STATE.extremeToggles[key];
  document.getElementById(`tog2-${key}`)?.classList.toggle('on', STATE.extremeToggles[key]);
}

function toggleMSOpt(key) {
  STATE.msToggles[key] = !STATE.msToggles[key];
  const idMap = { apply_pm25_correction:'ms-tog-pm25', resample_hourly:'ms-tog-hourly' };
  document.getElementById(idMap[key])?.classList.toggle('on', STATE.msToggles[key]);
}

function toggleMapLayer(layer) {
  if (layer==='heat') {
    STATE.showHeat = !STATE.showHeat;
    document.getElementById('tog-heat').classList.toggle('on', STATE.showHeat);
  } else {
    STATE.showMarkers = !STATE.showMarkers;
    document.getElementById('tog-markers').classList.toggle('on', STATE.showMarkers);
  }
  if (STATE.sessionId) renderMapLayers(STATE._mapData || []);
}

function onDataUpdated(data, msg) {
  STATE.columns      = data.columns;
  STATE.shape        = data.shape;
  STATE.dtypes       = data.dtypes;
  STATE._previewRows = data.preview || [];
  STATE.isCleaned    = true;
  document.getElementById('cleaned-dot').style.display = 'inline';
  refreshColumnDropdowns();
  if (msg) toast(msg);
}

/* ─────────────────────────────────────────────────────────
   Drag / Drop helpers (shared)
   ───────────────────────────────────────────────────────── */
function handleDragOver(e, zoneId) {
  e.preventDefault();
  document.getElementById(zoneId)?.classList.add('drag-over');
}

function handleDragLeave(zoneId) {
  document.getElementById(zoneId)?.classList.remove('drag-over');
}

function handleDrop(e, mode) {
  e.preventDefault();
  const zoneId = mode==='upload'?'drop-zone':mode==='batch'?'batch-drop-zone':'ms-drop-zone';
  document.getElementById(zoneId)?.classList.remove('drag-over');
  addFilesToQueue([...e.dataTransfer.files], mode);
}

function handleFileSelect(e, mode) { addFilesToQueue([...e.target.files], mode); }

function addFilesToQueue(files, mode='upload') {
  const allowed = ['csv','xlsx','xls','txt'];
  const valid = files.filter(f => allowed.includes(f.name.split('.').pop().toLowerCase()));
  if (!valid.length) { toast('No supported files (CSV, XLSX, XLS, TXT)', 'warn'); return; }
  if (mode === 'upload')  { STATE.queuedFiles = [...STATE.queuedFiles, ...valid]; renderFileList('upload'); }
  if (mode === 'batch')   { STATE.batchFiles  = [...STATE.batchFiles,  ...valid]; renderFileList('batch'); }
  if (mode === 'ms')      { STATE.msFiles     = [...STATE.msFiles,     ...valid]; renderMsFileList(); }
}

function renderFileList(mode) {
  if (mode === 'upload') {
    const list = document.getElementById('file-list'), items = document.getElementById('file-items');
    if (!STATE.queuedFiles.length) { list.style.display='none'; return; }
    list.style.display='block';
    items.innerHTML = STATE.queuedFiles.map((f,i)=>`
      <div class="file-item">
        <span class="file-icon">📄</span><span class="file-name">${f.name}</span>
        <span class="file-size">${(f.size/1024).toFixed(1)} KB</span>
        <button class="file-remove" onclick="removeFile(${i},'upload')">✕</button>
      </div>`).join('');
  } else {
    const list = document.getElementById('batch-file-list'), items = document.getElementById('batch-file-items');
    if (!STATE.batchFiles.length) { list.style.display='none'; return; }
    list.style.display='block';
    document.getElementById('batch-file-count').textContent = `Daily Files (${STATE.batchFiles.length})`;
    items.innerHTML = STATE.batchFiles.map((f,i)=>`
      <div class="file-item">
        <span class="file-icon">📅</span><span class="file-name">${f.name}</span>
        <span class="file-size">${(f.size/1024).toFixed(1)} KB</span>
        <button class="file-remove" onclick="removeFile(${i},'batch')">✕</button>
      </div>`).join('');
  }
}

function renderMsFileList() {
  const editor = document.getElementById('ms-station-editor');
  editor.style.display = 'block';
  const labels = document.getElementById('ms-station-labels');
  labels.innerHTML = STATE.msFiles.map((f,i)=>`
    <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin-bottom:8px;align-items:center">
      <span style="font-size:12px;color:var(--text3);overflow:hidden;text-overflow:ellipsis">${f.name}</span>
      <input type="text" class="form-input" id="ms-label-${i}" value=""
        placeholder="Optional · defaults to file name" style="font-size:12px"/>
      <button class="file-remove" onclick="removeMsFile(${i})">✕</button>
    </div>`).join('');
}

function removeFile(idx, mode) {
  if (mode==='upload') { STATE.queuedFiles.splice(idx,1); renderFileList('upload'); }
  else { STATE.batchFiles.splice(idx,1); renderFileList('batch'); }
}

function removeMsFile(idx) {
  STATE.msFiles.splice(idx,1); renderMsFileList();
}

/* ─────────────────────────────────────────────────────────
   UPLOAD TAB
   ───────────────────────────────────────────────────────── */
async function uploadFiles() {
  if (!STATE.queuedFiles.length) { toast('Add at least one file first.', 'warn'); return; }
  setLoading('upload-btn', true);
  document.getElementById('upload-progress').style.display='block';
  const fill = document.getElementById('progress-fill'), txt = document.getElementById('progress-text');
  const fd = new FormData();
  STATE.queuedFiles.forEach(f => fd.append('files', f));
  try {
    let prog=0;
    const iv = setInterval(()=>{ prog=Math.min(prog+15,85); fill.style.width=prog+'%'; txt.textContent=`Uploading… ${prog}%`; }, 200);
    const res = await fetch('/api/upload', { method:'POST', body:fd });
    clearInterval(iv); fill.style.width='100%'; txt.textContent='Upload complete!';
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    STATE.sessionId    = data.session_id;
    STATE.columns      = data.columns;
    STATE.shape        = data.shape;
    STATE.dtypes       = data.dtypes;
    STATE._previewRows = data.preview || [];
    renderColumnMapping(document.getElementById('batch-mapping-grid'), data.columns, 'batch-map');
    document.getElementById('session-badge').style.display='flex';
    document.getElementById('session-id-display').textContent=data.session_id;
    enableTabs();
    document.getElementById('preview-rows-badge').textContent=`${data.shape[0].toLocaleString()} rows`;
    document.getElementById('preview-cols-badge').textContent=`${data.shape[1]} columns`;
    toggleDataPreview(false);
    buildTable('preview-table', data.columns, data.preview);
    document.getElementById('upload-preview').style.display='block';
    refreshColumnDropdowns();
    toast(`Loaded ${data.shape[0].toLocaleString()} rows × ${data.shape[1]} columns ✓`);
    setTimeout(()=>switchTab('clean'), 800);
  } catch(e) { toast(`Upload failed: ${e.message}`, 'error'); }
  finally {
    setLoading('upload-btn', false);
    setTimeout(()=>{ document.getElementById('upload-progress').style.display='none'; fill.style.width='0%'; }, 2000);
  }
}

/* ─────────────────────────────────────────────────────────
   BATCH STACK TAB
   ───────────────────────────────────────────────────────── */
async function batchUpload() {
  if (!STATE.batchFiles.length) { toast('Add daily files first.', 'warn'); return; }
  setLoading('batch-btn', true);
  document.getElementById('batch-progress').style.display='block';
  const fill = document.getElementById('batch-progress-fill'), txt = document.getElementById('batch-progress-text');
  const fd = new FormData();
  STATE.batchFiles.forEach(f => fd.append('files', f));
  fd.append('provider', document.getElementById('batch-provider').value);
  const stationName = document.getElementById('batch-station-name').value.trim();
  if (stationName) fd.append('station_name', stationName);
  try {
    let prog=0;
    const iv = setInterval(()=>{ prog=Math.min(prog+12,85); fill.style.width=prog+'%';
      txt.textContent=`Stacking ${STATE.batchFiles.length} files… ${prog}%`; }, 200);
    const res = await fetch('/api/batch_upload', { method:'POST', body:fd });
    clearInterval(iv); fill.style.width='100%'; txt.textContent='Stacking complete!';
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    // Load into main session
    STATE.sessionId    = data.session_id;
    STATE.columns      = data.columns;
    STATE.shape        = data.shape;
    STATE.dtypes       = data.dtypes;
    STATE._previewRows = data.preview || [];
    document.getElementById('session-badge').style.display='flex';
    document.getElementById('session-id-display').textContent=data.session_id;
    enableTabs();
    toggleDataPreview(false);
    refreshColumnDropdowns();
    // Show result
    document.getElementById('batch-stacked-badge').textContent=`📦 ${data.stacked_count} files stacked`;
    document.getElementById('batch-rows-badge').textContent=`${data.shape[0].toLocaleString()} rows`;
    document.getElementById('batch-cols-badge').textContent=`${data.shape[1]} columns`;
    if (data.failed_files?.length) {
      const warn = document.getElementById('batch-warnings');
      warn.style.display='block';
      warn.innerHTML=`<div style="background:#2a1d0d;border:1px solid var(--yellow);border-radius:8px;padding:10px;font-size:12px;color:var(--yellow)">
        ⚠️ ${data.failed_files.length} file(s) could not be read: ${data.failed_files.slice(0,3).join(', ')}${data.failed_files.length>3?'…':''}</div>`;
    }
    buildTable('batch-preview-table', data.columns, data.preview);
    document.getElementById('batch-result').style.display='block';
    toast(`Stacked ${data.stacked_count} files → ${data.shape[0].toLocaleString()} rows ✓`, 'success');
    // Auto-fill provider in clean tab
    const cp = document.getElementById('clean-provider');
    if (cp && data.provider) cp.value = data.provider;
    setTimeout(()=>switchTab('clean'), 1000);
  } catch(e) { toast(`Batch stack failed: ${e.message}`, 'error'); }
  finally {
    setLoading('batch-btn', false);
    setTimeout(()=>{ document.getElementById('batch-progress').style.display='none'; fill.style.width='0%'; }, 2500);
  }
}

/* ─────────────────────────────────────────────────────────
   MULTI-STATION TAB
   ───────────────────────────────────────────────────────── */
async function msUpload() {
  if (!STATE.msFiles.length) { toast('Add station files first.', 'warn'); return; }
  if (STATE.msFiles.some((_, i) => !document.getElementById(`ms-label-${i}`)?.value.trim())) {
    toast('Enter a Station Name for every file before uploading.', 'warn'); return;
  }
  setLoading('ms-upload-btn', true);
  document.getElementById('ms-upload-progress').style.display='block';
  const fill = document.getElementById('ms-progress-fill'), txt = document.getElementById('ms-progress-text');
  const fd = new FormData();
  STATE.msFiles.forEach((f,i) => {
    fd.append('files', f);
    const label = document.getElementById(`ms-label-${i}`).value.trim();
    fd.append('station_labels', label);
  });
  fd.append('provider', document.getElementById('ms-provider').value);
  try {
    let prog=0;
    const iv = setInterval(()=>{ prog=Math.min(prog+15,85); fill.style.width=prog+'%';
      txt.textContent=`Uploading ${STATE.msFiles.length} station files… ${prog}%`; }, 200);
    const res = await fetch('/api/multistation_upload', { method:'POST', body:fd });
    clearInterval(iv); fill.style.width='100%'; txt.textContent='Upload complete!';
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    STATE.batchId = data.batch_id;
    const sourceColumns = [...new Set(Object.values(data.columns_by_station || {}).flat())];
    renderColumnMapping(document.getElementById('ms-mapping-grid'), sourceColumns, 'ms-map');
    document.getElementById('ms-mapping-card').style.display='block';
    document.getElementById('ms-clean-card').style.display='block';
    toast(`Uploaded ${data.station_count} stations: ${data.station_names.join(', ')} ✓`);
  } catch(e) { toast(`Upload failed: ${e.message}`, 'error'); }
  finally {
    setLoading('ms-upload-btn', false);
    setTimeout(()=>{ document.getElementById('ms-upload-progress').style.display='none'; fill.style.width='0%'; }, 2000);
  }
}

async function msClean() {
  if (!STATE.batchId) { toast('Upload stations first.', 'warn'); return; }
  setLoading('ms-clean-btn', true);
  document.getElementById('ms-clean-loader').style.display='flex';
  try {
    const data = await api('/api/multistation_clean', {
      batch_id: STATE.batchId,
      provider: document.getElementById('ms-provider').value,
      options: {
        ...STATE.msToggles,
        column_mapping: getColumnMapping('ms-mapping-grid'),
        start_date: document.getElementById('ms-start').value || null,
        end_date:   document.getElementById('ms-end').value   || null,
      }
    });
    const result = document.getElementById('ms-clean-result');
    result.style.display='block';
    const errCount = Object.keys(data.errors||{}).length;
    result.innerHTML = `<div style="color:var(--green);font-size:13px">
      ✅ Cleaned ${data.cleaned_stations.length} stations: ${data.cleaned_stations.join(', ')}
      ${errCount?`<br>⚠️ ${errCount} station(s) failed: ${Object.entries(data.errors).map(([k,v])=>`${k}: ${v}`).join('; ')}`:''}  
    </div>`;
    // Populate column select with actual cleaned columns
    const firstPreview = Object.values(data.previews||{})[0];
    if (firstPreview) {
      const EXCLUDE = new Set(['datetime','year','month','day','hour','season',
        'location','lon','lat','altitude','_station','_source_file','AQI',
        'AQI_category','AQI_color','wind_direction','windspeed','rainfall']);
      const numCols = firstPreview.columns.filter(c => !EXCLUDE.has(c) &&
        !['string','object'].includes(typeof firstPreview.preview?.[0]?.[c]));
      const sel = document.getElementById('ms-col');
      const preferred = ['pm2.5','pm10','NO2','O3','CO','SO2','CO2','VOC','temperature','RH'];
      // Sort: preferred columns first, then alphabetical
      numCols.sort((a,b)=>{
        const ia = preferred.indexOf(a), ib = preferred.indexOf(b);
        if (ia>=0 && ib>=0) return ia-ib;
        if (ia>=0) return -1;
        if (ib>=0) return 1;
        return a.localeCompare(b);
      });
      sel.innerHTML = numCols.map(c=>`<option value="${c}"${c==='pm2.5'?' selected':''}>${c}</option>`).join('');
    }
    document.getElementById('ms-analysis-card').style.display='block';
    document.getElementById('ms-load-plot-btn').style.display='inline-flex';
    toast(`All stations cleaned ✓`);
  } catch(e) { toast(e.message, 'error'); }
  finally { setLoading('ms-clean-btn', false); document.getElementById('ms-clean-loader').style.display='none'; }
}

async function runComparative() {
  if (!STATE.batchId) { toast('Upload and clean stations first.', 'warn'); return; }
  document.getElementById('comp-loader').style.display='flex';
  document.getElementById('comp-results').style.display='none';
  setLoading('comp-run-btn', true);
  try {
    const data = await api('/api/comparative', {
      batch_id: STATE.batchId,
      column:   document.getElementById('ms-col').value,
      params: {
        resample_freq: document.getElementById('ms-freq').value,
        who_24h: parseFloat(document.getElementById('ms-who24').value) || null,
        epa_24h: parseFloat(document.getElementById('ms-epa24').value) || null,
      }
    });
    renderComparativeResults(data);
  } catch(e) { toast(e.message, 'error'); }
  finally { document.getElementById('comp-loader').style.display='none'; setLoading('comp-run-btn', false); }
}

async function msLoadMerged() {
  if (!STATE.batchId) { toast('Upload stations first.', 'warn'); return; }
  try {
    const data = await api('/api/multistation_merge', { batch_id: STATE.batchId });
    STATE.sessionId = data.session_id;
    document.getElementById('session-badge').style.display='flex';
    document.getElementById('session-id-display').textContent=data.session_id;
    enableTabs();
    onDataUpdated(data, 'Merged station data loaded into the plot dashboard.');
    switchTab('plots');
  } catch(e) { toast(e.message, 'error'); }
}

const STATION_COLORS = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#f97316','#84cc16','#6366f1'];

function renderComparativeResults(r) {
  const names = r.station_names;
  const col   = r.column;

  // Rankings
  document.getElementById('comp-ranking').innerHTML = (r.ranking||[]).map(({station,mean,rank})=>`
    <div class="stat-card">
      <div class="comp-rank comp-rank-${rank}">#${rank}</div>
      <div class="stat-label">${station}</div>
      <div class="stat-value" style="color:${STATION_COLORS[(rank-1)%STATION_COLORS.length]};font-size:18px">${mean.toFixed(2)}</div>
      <div class="stat-unit">${col}</div>
    </div>`).join('');

  // Descriptive table
  const descKeys = ['n','mean','median','std','min','max','skewness','kurtosis','cv_pct','date_start','date_end'];
  let tHtml = '<thead><tr><th>Metric</th>' + names.map(n=>`<th>${n}</th>`).join('') + '</tr></thead><tbody>';
  descKeys.forEach(k => {
    const row = names.map(n => {
      const v = r.descriptive?.[n]?.[k];
      return v===null||v===undefined ? '—' : typeof v==='number' ? v.toFixed(3) : v;
    });
    tHtml += `<tr><td style="font-weight:600;color:var(--text3)">${k}</td>${row.map(v=>`<td style="font-family:monospace">${v}</td>`).join('')}</tr>`;
  });
  tHtml += '</tbody>';
  document.getElementById('comp-desc-table').innerHTML = tHtml;

  // Time Series
  if (r.timeseries) {
    const traces = names.map((n,i) => ({
      type:'scatter', mode:'lines', name:n,
      x: r.timeseries[n]?.dates || [],
      y: r.timeseries[n]?.values || [],
      line:{ color:STATION_COLORS[i%STATION_COLORS.length], width:2 }
    }));
    plotlyReady(()=>Plotly.react('comp-ts-plot', traces,
      applyCelestialLayout({ title:`${col} — Time Series by Station`, xaxis:{title:'Date'}, yaxis:{title:col}, height:380 }),
      PLOT_CONFIG));
  }

  // Diurnal
  if (r.diurnal) {
    const traces = names.map((n,i) => ({
      type:'scatter', mode:'lines+markers', name:n,
      x: r.diurnal[n]?.hours || [],
      y: r.diurnal[n]?.mean  || [],
      line:{ color:STATION_COLORS[i%STATION_COLORS.length], width:2 }
    }));
    plotlyReady(()=>Plotly.react('comp-diurnal-plot', traces,
      applyCelestialLayout({ title:'Diurnal Profile', xaxis:{title:'Hour of Day'}, yaxis:{title:`Mean ${col}`}, height:300 }),
      PLOT_CONFIG));
  }

  // Monthly
  if (r.monthly) {
    const traces = names.map((n,i) => ({
      type:'bar', name:n,
      x: r.monthly[n]?.months || [],
      y: r.monthly[n]?.values || [],
      marker:{ color:STATION_COLORS[i%STATION_COLORS.length] }
    }));
    plotlyReady(()=>Plotly.react('comp-monthly-plot', traces,
      applyCelestialLayout({ title:'Monthly Means', barmode:'group', xaxis:{title:'Month'}, yaxis:{title:`Mean ${col}`}, height:300 }),
      PLOT_CONFIG));
  }

  // Seasonal
  if (r.seasonal) {
    const seasons = ['DJF','MAM','JJA','SON'];
    const traces = names.map((n,i) => ({
      type:'bar', name:n,
      x: seasons,
      y: seasons.map(s=>r.seasonal[n]?.[s]||null),
      marker:{ color:STATION_COLORS[i%STATION_COLORS.length] }
    }));
    plotlyReady(()=>Plotly.react('comp-seasonal-plot', traces,
      applyCelestialLayout({ title:'Seasonal Means', barmode:'group', xaxis:{title:'Season'}, yaxis:{title:`Mean ${col}`}, height:300 }),
      PLOT_CONFIG));
  }

  // Pairwise correlation heatmap
  if (r.pairwise_corr?.matrix) {
    plotlyReady(()=>Plotly.react('comp-corr-plot',
      [{ type:'heatmap', z:r.pairwise_corr.matrix, x:r.pairwise_corr.names, y:r.pairwise_corr.names,
         colorscale:'RdBu', zmid:0, text:r.pairwise_corr.matrix.map(row=>row.map(v=>v!=null?v.toFixed(3):'—')),
         texttemplate:'%{text}', colorbar:{title:'r'} }],
      applyCelestialLayout({ title:'Pairwise Pearson r', height:300 }),
      PLOT_CONFIG));
  }

  // Percentile comparison
  if (r.percentile_comparison) {
    const pKeys = Object.keys(Object.values(r.percentile_comparison)[0]||{});
    const traces = names.map((n,i) => ({
      type:'scatter', mode:'lines+markers', name:n,
      x: pKeys, y: pKeys.map(k=>r.percentile_comparison[n]?.[k]||null),
      line:{ color:STATION_COLORS[i%STATION_COLORS.length], width:2 },
      marker:{ size:7 }
    }));
    plotlyReady(()=>Plotly.react('comp-pct-plot', traces,
      applyCelestialLayout({ title:'Percentile Comparison', xaxis:{title:'Percentile'}, yaxis:{title:col}, height:300 }),
      PLOT_CONFIG));
  }

  // Exceedance
  const hasExc = Object.values(r.exceedance||{}).some(e=>Object.keys(e).length>0);
  if (hasExc) {
    document.getElementById('comp-exceedance-card').style.display='block';
    document.getElementById('comp-exceedance-grid').innerHTML = names.flatMap(n => {
      const exc = r.exceedance[n]||{};
      return Object.entries(exc).map(([k,v])=>`
        <div class="stat-card">
          <div class="stat-label">${n} — ${k.replace(/_/g,' ')}</div>
          <div class="stat-value" style="color:${typeof v==='number'&&k.includes('pct')&&v>50?'var(--red)':'var(--yellow)'};font-size:18px">
            ${typeof v==='number'?v.toFixed(1):v}${k.includes('pct')?'%':''}
          </div>
        </div>`);
    }).join('');
  }

  // ANOVA
  if (r.anova) {
    document.getElementById('comp-anova-card').style.display='block';
    document.getElementById('comp-anova-body').innerHTML = `
      <div class="grid3" style="margin-top:8px">
        <div class="stat-card"><div class="stat-label">F Statistic</div>
          <div class="stat-value">${r.anova.F_statistic}</div></div>
        <div class="stat-card"><div class="stat-label">p-value</div>
          <div class="stat-value" style="color:${r.anova.significant?'var(--red)':'var(--green)'}">${r.anova.p_value}</div></div>
        <div class="stat-card"><div class="stat-label">Result</div>
          <div class="stat-value" style="font-size:14px;color:${r.anova.significant?'var(--red)':'var(--green)'}">
            ${r.anova.significant?'Significantly different (p<0.05)':'No significant difference'}</div></div>
      </div>`;
  }

  document.getElementById('comp-results').style.display='block';
  toast('Comparative analysis complete ✓');
}

async function msExport(fmt) {
  if (!STATE.batchId) { toast('No batch session.', 'warn'); return; }
  const res = await fetch('/api/multistation_export', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ batch_id:STATE.batchId, format:fmt })
  });
  if (!res.ok) { toast('Export failed', 'error'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href=url; a.download=`multistation_data.${fmt}`; a.click();
  URL.revokeObjectURL(url);
}

/* ─────────────────────────────────────────────────────────
   CLEAN TAB
   ───────────────────────────────────────────────────────── */
async function cleanData() {
  if (!STATE.sessionId) { toast('Upload data first', 'warn'); return; }
  const location = document.getElementById('clean-location').value.trim();
  const stationName = document.getElementById('clean-station-name').value.trim();
  setLoading('clean-btn', true);
  try {
    const data = await api('/api/clean', {
      session_id: STATE.sessionId,
      provider:   document.getElementById('clean-provider').value,
      options: {
        ...STATE.cleanToggles,
        drop_empty_columns: document.getElementById('clean-drop-empty-columns').checked,
        location,
        station_name: stationName,
        column_mapping: {
          ...getAirnoteMapping(),
          ...getColumnMapping('batch-mapping-grid'),
        },
        start_date: document.getElementById('clean-start').value || null,
        end_date:   document.getElementById('clean-end').value   || null,
      }
    });
    const removedEmpty = data.removed_columns || [];
    onDataUpdated(data, removedEmpty.length
      ? `Data cleaned; removed ${removedEmpty.length} fully missing column(s) ✓`
      : 'Data cleaned successfully ✓');
    document.getElementById('export-card').style.display='block';
    document.getElementById('cleaned-preview').style.display='block';
    document.getElementById('clean-rows-badge').textContent=`${data.shape[0].toLocaleString()} rows`;
    document.getElementById('clean-cols-badge').textContent=`${data.shape[1]} columns`;
    buildTable('cleaned-table', data.columns, data.preview);
  } catch(e) { toast(e.message, 'error'); }
  finally { setLoading('clean-btn', false); }
}

document.getElementById('clean-provider')?.addEventListener('change', function() {
  const card = document.getElementById('airnote-mapping-card');
  if (card) card.style.display = 'block';
});

async function aggregateData() {
  if (!STATE.sessionId) return;
  try {
    const data = await api('/api/aggregate', { session_id:STATE.sessionId,
      freq:document.getElementById('agg-freq').value, agg_func:document.getElementById('agg-func').value });
    onDataUpdated(data, `Aggregated ✓`);
    buildTable('cleaned-table', data.columns, data.preview);
    document.getElementById('cleaned-preview').style.display='block';
    document.getElementById('clean-rows-badge').textContent=`${data.shape[0].toLocaleString()} rows`;
    document.getElementById('clean-cols-badge').textContent=`${data.shape[1]} columns`;
  } catch(e) { toast(e.message, 'error'); }
}

async function interpolateData() {
  if (!STATE.sessionId) return;
  try {
    const lv = document.getElementById('interp-limit').value;
    const data = await api('/api/interpolate', { session_id:STATE.sessionId,
      method:document.getElementById('interp-method').value, limit:lv?parseInt(lv):null });
    onDataUpdated(data, 'Missing values filled ✓');
  } catch(e) { toast(e.message, 'error'); }
}

async function removeOutliers() {
  if (!STATE.sessionId) return;
  try {
    const data = await api('/api/remove_outliers', { session_id:STATE.sessionId,
      column:document.getElementById('out-col').value,
      method:document.getElementById('out-method').value,
      threshold:parseFloat(document.getElementById('out-thresh').value) });
    onDataUpdated(data, `Removed ${data.removed_rows} outlier rows ✓`);
  } catch(e) { toast(e.message, 'error'); }
}

async function renameColumn() {
  if (!STATE.sessionId) return;
  const old=document.getElementById('rename-old').value, nw=document.getElementById('rename-new').value;
  if (!old||!nw) { toast('Fill both fields', 'warn'); return; }
  try {
    const data = await api('/api/columns/rename', { session_id:STATE.sessionId, old_name:old, new_name:nw });
    onDataUpdated(data, `Renamed '${old}' → '${nw}' ✓`);
  } catch(e) { toast(e.message, 'error'); }
}

async function deleteColumn() {
  if (!STATE.sessionId) return;
  const col = document.getElementById('delete-col').value;
  if (!col) return;
  try {
    const data = await api('/api/columns/delete', { session_id:STATE.sessionId, column:col });
    onDataUpdated(data, `Deleted column '${col}' ✓`);
  } catch(e) { toast(e.message, 'error'); }
}

async function dropFullyMissingColumns() {
  if (!STATE.sessionId) { toast('Load data first.', 'warn'); return; }
  setLoading('drop-empty-cols-btn', true);
  try {
    const data = await api('/api/columns/drop_empty', { session_id: STATE.sessionId });
    if (!data.removed_columns?.length) {
      toast('No columns are 100% missing.', 'success');
      return;
    }
    onDataUpdated(data, `Removed ${data.removed_columns.length} entirely missing column(s).`);
    toast(`Removed: ${data.removed_columns.join(', ')} · Undo is available.`, 'success', 6000);
  } catch (error) { toast(error.message, 'error'); }
  finally { setLoading('drop-empty-cols-btn', false); }
}

async function addColumn() {
  if (!STATE.sessionId) return;
  const name=document.getElementById('add-col-name').value, expr=document.getElementById('add-col-expr').value;
  if (!name||!expr) { toast('Fill name and expression', 'warn'); return; }
  try {
    const data = await api('/api/columns/add', { session_id:STATE.sessionId, column:name, expression:expr });
    onDataUpdated(data, `Column '${name}' added ✓`);
  } catch(e) { toast(e.message, 'error'); }
}

async function exportData(fmt) {
  if (!STATE.sessionId) return;
  const res = await fetch('/api/export', { method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ session_id:STATE.sessionId, format:fmt }) });
  if (!res.ok) { toast('Export failed', 'error'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download=`cleaned_data.${fmt}`; a.click();
  URL.revokeObjectURL(url);
}

/* ─────────────────────────────────────────────────────────
   EXPLORE TAB
   ───────────────────────────────────────────────────────── */
function renderSimpleExplore(data) {
  const host = document.getElementById('explore-simple-content');
  if (!host) return;
  const describe = data.describe || {};
  const statKeys = Object.keys(describe);
  const numCols = STATE.columns.filter(c => describe[statKeys[0]]?.[c] !== undefined);
  const fmt = v => typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : (v ?? '—');
  const firstCol = numCols[0];
  const summary = firstCol ? describe[statKeys[0]]?.[firstCol] : null;
  const missing = Object.entries(data.missing_pct || {})
    .filter(([, pct]) => pct > 0).sort((a, b) => b[1] - a[1]);
  const worstMissing = missing[0];
  const totalMissing = missing.reduce((acc, [, pct]) => acc + pct, 0);
  const quality = totalMissing === 0
    ? 'No missing values were detected in the loaded dataset.'
    : `Most readings are present. The largest gap is in ${worstMissing[0]} (${worstMissing[1]}% missing).`;

  let rangeText = 'Numeric ranges are shown in the technical view.';
  if (firstCol) {
    const min = describe.min?.[firstCol] ?? describe['min']?.[firstCol];
    const max = describe.max?.[firstCol] ?? describe['max']?.[firstCol];
    if (min !== undefined && max !== undefined) {
      rangeText = `${firstCol} ranges from ${fmt(min)} to ${fmt(max)}.`;
    }
  }

  host.innerHTML = `
    <div class="simple-summary">
      <h3>Plain-language summary</h3>
      This dataset contains <strong>${STATE.shape[0].toLocaleString()} readings</strong> across
      <strong>${STATE.shape[1]} columns</strong>${STATE.isCleaned ? ' and has already been cleaned' : ''}.
      ${rangeText} ${quality}
    </div>
    <div class="simple-metrics">
      <div class="simple-metric"><span>Readings</span><strong>${STATE.shape[0].toLocaleString()}</strong></div>
      <div class="simple-metric"><span>Measurements</span><strong>${STATE.shape[1]}</strong></div>
      <div class="simple-metric"><span>Complete columns</span><strong>${Math.max(0, STATE.shape[1] - missing.length)}</strong></div>
      <div class="simple-metric"><span>Status</span><strong>${STATE.isCleaned ? 'Cleaned' : 'Raw'}</strong></div>
    </div>
    <div class="simple-note">
      <strong>How to read this:</strong> the technical side gives exact counts, percentiles, and quality indicators.
      This side summarises the same information without changing any values. Use the insight cards above for prioritised findings,
      each with its own technical and simple explanation.
    </div>`;
}

async function loadStats() {
  if (!STATE.sessionId) return;
  document.getElementById('explore-loader').style.display='flex';
  document.getElementById('explore-content').style.display='none';
  try {
    const data = await api('/api/stats', { session_id:STATE.sessionId });
    // Shape grid
    document.getElementById('explore-shape-grid').innerHTML = [
      {label:'Rows',    value:STATE.shape[0].toLocaleString(), color:'var(--blue)'},
      {label:'Columns', value:STATE.shape[1], color:'var(--purple)'},
      {label:'Cleaned', value:STATE.isCleaned?'Yes':'No', color:STATE.isCleaned?'var(--green)':'var(--red)'},
      {label:'Provider',value:document.getElementById('clean-provider')?.value||'—', color:'var(--teal)'},
    ].map(s=>`<div class="stat-card"><div class="stat-label">${s.label}</div>
      <div class="stat-value" style="color:${s.color};font-size:18px">${s.value}</div></div>`).join('');

    // Missing
    const mp = data.missing_pct;
    const topMissing = Object.entries(mp).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]).slice(0,20);
    document.getElementById('missing-grid').innerHTML = topMissing.length ?
      topMissing.map(([col,pct])=>`
        <div class="missing-card">
          <div style="font-size:12px;color:var(--text2);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis">${col}</div>
          <div class="missing-pct" style="color:${pct>50?'var(--red)':pct>20?'var(--yellow)':'var(--green)'}">${pct}%</div>
          <div style="font-size:11px;color:var(--text4)">missing</div>
          <div class="missing-bar" style="background:${pct>50?'var(--red)':pct>20?'var(--yellow)':'var(--green)'};width:${pct}%"></div>
        </div>`).join('') :
      '<div style="color:var(--green);font-size:13px">✅ No missing values</div>';

    // Descriptive table
    const desc = data.describe;
    const statKeys = Object.keys(desc);
    const numCols  = STATE.columns.filter(c => desc[statKeys[0]]?.[c] !== undefined);
    if (numCols.length) {
      let tHtml = '<thead><tr><th>Stat</th>' + numCols.map(c=>`<th>${c}</th>`).join('') + '</tr></thead><tbody>';
      statKeys.forEach(k => {
        tHtml += `<tr><td style="font-weight:600;color:var(--text3)">${k}</td>` +
          numCols.map(c => {
            const v = desc[k][c];
            return `<td style="font-family:monospace">${v===null?'—':typeof v==='number'?v.toFixed(3):v}</td>`;
          }).join('') + '</tr>';
      });
      tHtml += '</tbody>';
      document.getElementById('desc-table').innerHTML = tHtml;
    }

    buildColumnsOverview(STATE.columns);
    renderSimpleExplore(data);
    document.getElementById('explore-loader').style.display='none';
    document.getElementById('explore-content').style.display='block';
  } catch(e) { toast(e.message,'error'); document.getElementById('explore-loader').style.display='none'; }
}

/* ─────────────────────────────────────────────────────────
   PLOTS TAB
   ───────────────────────────────────────────────────────── */
const PLOT_GROUPS = {
  multi:      ['timeseries','area','correlation','diurnal','monthly','seasonal_box','radar','pm_size_dist',
               'ridgeline','density','ecdf','pairplot','correlogram','streamgraph','rolling_trend',
               'treemap','sunburst','sensor_qc'],
  single:     ['boxplot','histogram','calendar','time_of_week','aqi_bar','spatial','violin','exceedance','nc_timeseries','co2_met','trend','timevariation','percentile','polar','pollutionrose','annulus','cpf','bivariate_polar',
               'beeswarm','ordered_bar','lollipop','cleveland_dot','density_map','cluster_map','heatmap',
               'temporal_geospatial','uncertainty_bands','confidence_intervals','anomaly_detection',
               'residual_diagnostics','forecast_verification'],
  xy:         ['scatter','bubble','hexbin','connected_scatter','heatmap'],
  comparison: ['timeseries','area','boxplot','violin','histogram','scatter','bubble'],
};

const EXPANDED_PLOT_TYPES = [
  'ridgeline','density','ecdf','beeswarm','pairplot','correlogram','hexbin','connected_scatter',
  'streamgraph','rolling_trend','seasonal_decomposition','treemap','sunburst','waffle',
  'ordered_bar','lollipop','cleveland_dot','density_map','cluster_map','heatmap',
  'temporal_geospatial','uncertainty_bands','confidence_intervals','anomaly_detection',
  'residual_diagnostics','forecast_verification','sensor_qc',
];
const PLOT_BY_GROUP = ['density','ecdf','beeswarm','waffle','ordered_bar','lollipop',
  'cleveland_dot','confidence_intervals','uncertainty_bands','connected_scatter','cluster_map'];
const PLOT_BY_VALUE = ['treemap','sunburst','forecast_verification','cluster_map','density_map',
  'heatmap','ordered_bar','lollipop','cleveland_dot'];
const PLOT_BY_LATLON = ['density_map','cluster_map','temporal_geospatial'];
const PLOT_BY_PERIOD = ['seasonal_decomposition','temporal_geospatial'];
const PLOT_BY_WINDOW = ['rolling_trend','uncertainty_bands','anomaly_detection','residual_diagnostics'];
const PLOT_BY_TOPN = ['waffle','ordered_bar','lollipop','cleveland_dot'];
const PLOT_BY_AGG = ['treemap','sunburst','ordered_bar','lollipop','cleveland_dot','density_map','heatmap'];
const PLOT_BY_BINS = ['histogram','hexbin','density_map','heatmap'];

function refreshAdvancedPlotDefaults() {
  const cols = STATE.columns || [];
  const group = document.getElementById('plot-adv-group');
  if (group) {
    const previous = group.value;
    const temporal = ['hour','weekday','day','week','month','season','year']
      .filter(key => !cols.includes(key));
    group.innerHTML = '<option value="">Auto / none</option>' +
      temporal.map(c => `<option value="${c}">${c} (derived)</option>`).join('') +
      cols.map(c => `<option value="${c}">${c}</option>`).join('');
    const preferred = ['location','AQI_Category','station'].find(c => cols.includes(c)) || '';
    group.value = previous && [...group.options].some(o => o.value === previous) ? previous : preferred;
  }
  const setDefault = (id, preferred) => {
    const el = document.getElementById(id);
    if (!el) return;
    const match = preferred.find(c => cols.includes(c) || [...el.options].some(o => o.value === c));
    if (match && [...el.options].some(o => o.value === match)) el.value = match;
  };
  setDefault('plot-adv-value', ['pm2.5','pm10']);
  setDefault('plot-lat-col', ['lat','latitude']);
  setDefault('plot-lon-col', ['lon','longitude','lng']);
  setDefault('plot-single-col', ['pm2.5','pm10']);
}

function applyExpandedPlotDefaults(type) {
  if (!EXPANDED_PLOT_TYPES.includes(type)) return;
  const preferred = {
    ridgeline: ['pm2.5','pm10','temperature','RH'],
    density: ['pm2.5','pm10','temperature'],
    ecdf: ['pm2.5','pm10'],
    pairplot: ['pm2.5','pm10','temperature','RH','CO2','VOC','NO2','O3'],
    correlogram: ['pm2.5','pm10'],
    streamgraph: ['pm2.5','pm10','temperature'],
    rolling_trend: ['pm2.5'],
    treemap: ['location','pm2.5'],
    sunburst: ['location','pm2.5'],
    sensor_qc: ['pm2.5','pm10','temperature','RH','CO2','VOC'],
  }[type] || [];
  if (preferred.length > 1 && getSelectedChips().length <= 1) {
    preferred.forEach(col => {
      const btn = [...document.querySelectorAll('.col-chip')].find(el => el.dataset.col === col);
      if (btn && !btn.classList.contains('active')) toggleColChip(btn, col);
    });
  }
  const x = document.getElementById('plot-x-col');
  const y = document.getElementById('plot-y-col');
  if (type === 'hexbin' && x && y) {
    if (!x.value && (STATE.columns || []).includes('pm2.5')) x.value = 'pm2.5';
    if (!y.value && (STATE.columns || []).includes('pm10')) y.value = 'pm10';
  }
  if (type === 'connected_scatter' && x && (STATE.columns || []).includes('datetime') && !x.value) {
    x.value = 'datetime';
  }
}

function updateAdvancedPlotControls(type) {
  const show = (id, visible) => {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? 'block' : 'none';
  };
  const expanded = EXPANDED_PLOT_TYPES.includes(type);
  show('plot-ctrl-advanced', expanded);
  if (!expanded) return;
  show('plot-adv-group-group', PLOT_BY_GROUP.includes(type));
  show('plot-adv-value-group', PLOT_BY_VALUE.includes(type));
  show('plot-adv-latlon-group', PLOT_BY_LATLON.includes(type));
  show('plot-adv-lon-group', PLOT_BY_LATLON.includes(type));
  show('plot-adv-period-group', PLOT_BY_PERIOD.includes(type));
  show('plot-adv-window-group', PLOT_BY_WINDOW.includes(type));
  show('plot-adv-method-group', type === 'anomaly_detection');
  show('plot-adv-threshold-group', type === 'anomaly_detection');
  show('plot-adv-cluster-group', type === 'cluster_map');
  show('plot-adv-lags-group', type === 'correlogram');
  show('plot-adv-topn-group', PLOT_BY_TOPN.includes(type));
  show('plot-adv-stat-group', PLOT_BY_AGG.includes(type));
  const label = document.getElementById('plot-adv-value-label');
  if (label) {
    label.textContent = type === 'forecast_verification' ? 'Predicted column (optional)'
      : type === 'cluster_map' ? 'Size column (optional)'
      : type === 'treemap' || type === 'sunburst' ? 'Value column'
      : 'Value column (optional)';
  }
}

function updatePlotControls() {
  const t = document.getElementById('plot-type').value;
  document.getElementById('plot-ctrl-multi').style.display         = PLOT_GROUPS.multi.includes(t)  ? 'block' : 'none';
  document.getElementById('plot-ctrl-single').style.display        = PLOT_GROUPS.single.includes(t) ? 'block' : 'none';
  document.getElementById('plot-ctrl-xy').style.display            = PLOT_GROUPS.xy.includes(t)     ? 'block' : 'none';
  const needsWind = ['windrose','polar','pollutionrose','annulus','cpf','bivariate_polar'].includes(t);
  const needsSpeed = ['windrose','polar','bivariate_polar'].includes(t);
  document.getElementById('plot-ctrl-wind').style.display          = needsWind ? 'block' : 'none';
  document.getElementById('plot-ws-group').style.display = needsSpeed ? 'block' : 'none';
  document.getElementById('plot-polar-settings').style.display = ['polar','bivariate_polar'].includes(t) ? 'grid' : 'none';
  document.getElementById('plot-cpf-threshold-group').style.display = t === 'cpf' ? 'block' : 'none';
  document.getElementById('plot-annulus-period-group').style.display = t === 'annulus' ? 'block' : 'none';
  document.getElementById('plot-ctrl-time-aggregate').style.display = ['timeseries','area'].includes(t) ? 'block' : 'none';
  document.getElementById('plot-time-percentile-group').style.display = document.getElementById('plot-time-stat').value === 'percentile' ? 'block' : 'none';
  // New controls
  document.getElementById('plot-ctrl-calendar').style.display      = t === 'calendar'         ? 'block' : 'none';
  document.getElementById('plot-ctrl-boxgroup').style.display      = t === 'boxplot'          ? 'block' : 'none';
  document.getElementById('plot-ctrl-bins').style.display          = PLOT_BY_BINS.includes(t)  ? 'block' : 'none';
  document.getElementById('plot-ctrl-comparison').style.display = PLOT_GROUPS.comparison.includes(t) ? 'block' : 'none';
  // Bubble size selector
  const bsg = document.getElementById('bubble-size-group');
  if (bsg) bsg.style.display = t === 'bubble' ? 'block' : 'none';
  applyExpandedPlotDefaults(t);
  updateAdvancedPlotControls(t);
}

/* Populate the calendar year selector from the cleaned data */
function refreshCalendarYears() {
  const sel = document.getElementById('plot-cal-year');
  if (!sel) return;
  // Keep first "All years" option, then add years present in data
  const yearCol = STATE.columns.includes('year') ? 'year' : null;
  // We'll use the preview rows if available; otherwise just clear extras
  sel.innerHTML = '<option value="">All years (merged)</option>';
  if (STATE._yearList && STATE._yearList.length) {
    STATE._yearList.forEach(y => {
      const opt = document.createElement('option');
      opt.value = y; opt.textContent = y;
      sel.appendChild(opt);
    });
  }
}

/* Populate hue and facet selectors with all columns (numeric + categorical). */
function refreshScatterColorBy() {
  const colorCol = document.getElementById('plot-color-col');
  const facetCol = document.getElementById('plot-facet-col');
  const allCols = STATE.columns || [];
  const options = '<option value="">None</option>' +
    allCols.map(c=>`<option value="${c}">${c}</option>`).join('');
  if (colorCol) colorCol.innerHTML = options;
  if (facetCol) facetCol.innerHTML = options;
}

async function generatePlot() {
  if (!STATE.sessionId) { toast('Upload data first', 'warn'); return; }
  setLoading('plot-btn', true);
  document.getElementById('plot-loader').style.display='flex';
  document.getElementById('plot-area').innerHTML='';
  try {
    const t = document.getElementById('plot-type').value;
    const title = document.getElementById('plot-title').value;
    let params = { title };

    // Multi-column plots
    if (PLOT_GROUPS.multi.includes(t))
      params.columns = getSelectedChips();

    // Single-column plots
    if (PLOT_GROUPS.single.includes(t))
      params.column = document.getElementById('plot-single-col').value;

    // Scatter / Bubble — xy + colour-by
    if (PLOT_GROUPS.xy.includes(t)) {
      params.x_col     = document.getElementById('plot-x-col').value;
      params.y_col     = document.getElementById('plot-y-col').value;
      if (t === 'bubble') params.size_col = document.getElementById('plot-size-col')?.value || 'RH';
    }

    if (PLOT_GROUPS.comparison.includes(t)) {
      params.color_col = document.getElementById('plot-color-col').value || null;
      params.facet_col = document.getElementById('plot-facet-col').value || null;
      params.color_palette = document.getElementById('plot-hue-palette').value || 'Plotly';
    }

    // Wind rose
    if (['windrose','polar','pollutionrose','annulus','cpf','bivariate_polar'].includes(t)) {
      if (['windrose','polar','bivariate_polar'].includes(t)) params.ws_col = document.getElementById('plot-ws-col').value;
      params.wd_col = document.getElementById('plot-wd-col').value;
    }
    if (t === 'cpf') params.threshold = document.getElementById('plot-cpf-threshold').value || null;
    if (t === 'annulus') params.period = document.getElementById('plot-annulus-period').value;
    if (['polar','bivariate_polar'].includes(t)) {
      params.wind_bin = Number(document.getElementById('plot-polar-bin').value || 0.5);
      params.smooth = Number(document.getElementById('plot-polar-smooth').value || 1.4);
    }
    if (['timeseries','area'].includes(t)) {
      params.resample = document.getElementById('plot-time-average').value || null;
      params.statistic = document.getElementById('plot-time-stat').value;
      params.percentile = Number(document.getElementById('plot-time-percentile').value || 95);
    }

    // Calendar-specific parameters
    if (t === 'calendar') {
      const yr = document.getElementById('plot-cal-year')?.value;
      if (yr) params.year = parseInt(yr);
      const cmap = document.getElementById('plot-cal-cmap')?.value || 'YlOrRd';
      const rev  = document.getElementById('plot-cal-reverse')?.checked || false;
      params.colorscale = rev ? cmap + '_r' : cmap;
    }

    // Box plot group-by
    if (t === 'boxplot') {
      params.group_by = document.getElementById('plot-box-group')?.value || 'season';
    }

    // Histogram and binned charts
    if (PLOT_BY_BINS.includes(t)) {
      params.bins = parseInt(document.getElementById('plot-bins')?.value || '40');
    }

    // Expanded data-to-viz chart options
    if (EXPANDED_PLOT_TYPES.includes(t)) {
      const adv = id => document.getElementById(id)?.value ?? '';
      const addIf = (key, value) => {
        if (value !== '' && value !== null && value !== undefined) params[key] = value;
      };
      if (PLOT_BY_GROUP.includes(t)) addIf('group_by', adv('plot-adv-group'));
      if (t === 'treemap' || t === 'sunburst') {
        addIf('values', adv('plot-adv-value'));
      } else if (t === 'forecast_verification') {
        addIf('observed_col', params.column || 'pm2.5');
        addIf('predicted_col', adv('plot-adv-value'));
      } else if (PLOT_BY_VALUE.includes(t)) {
        addIf('value_col', adv('plot-adv-value'));
      }
      if (PLOT_BY_LATLON.includes(t)) {
        addIf('lat_col', adv('plot-lat-col'));
        addIf('lon_col', adv('plot-lon-col'));
      }
      if (PLOT_BY_PERIOD.includes(t)) {
        const period = adv('plot-adv-period');
        addIf('period', period === 'auto' ? '' : period);
      }
      if (PLOT_BY_WINDOW.includes(t)) {
        const windowValue = Number(adv('plot-adv-window') || 24);
        if (Number.isFinite(windowValue)) params.window = Math.max(1, Math.floor(windowValue));
      }
      if (t === 'anomaly_detection') {
        params.method = adv('plot-adv-method') || 'rolling';
        const threshold = adv('plot-adv-threshold');
        if (threshold !== '') params.threshold = Number(threshold);
      }
      if (t === 'cluster_map') {
        const clusters = Number(adv('plot-adv-clusters') || 3);
        if (Number.isFinite(clusters)) params.clusters = Math.max(1, Math.min(12, Math.floor(clusters)));
      }
      if (t === 'correlogram') {
        const lags = Number(adv('plot-adv-lags') || 24);
        if (Number.isFinite(lags)) params.lags = Math.max(1, Math.min(120, Math.floor(lags)));
      }
      if (PLOT_BY_TOPN.includes(t)) {
        const topN = Number(adv('plot-adv-topn') || 15);
        if (Number.isFinite(topN)) params.top_n = Math.max(1, Math.floor(topN));
      }
      if (PLOT_BY_AGG.includes(t)) addIf('aggfunc', adv('plot-adv-stat') || 'mean');
    }

    const data = await api('/api/plot', { session_id:STATE.sessionId, plot_type:t, params });
    if (!data.plot) throw new Error('No plot data returned');
    const plotArea = document.getElementById('plot-area');
    plotArea.innerHTML = '<div id="live-plot" style="min-height:400px"></div>';
    const fig = JSON.parse(data.plot);
    const baseLayout = getCelestialLayout();
    fig.layout = Object.assign({}, baseLayout, fig.layout);
    plotlyReady(() => Plotly.react('live-plot', fig.data, fig.layout, PLOT_CONFIG));
    STATE._lastPlot = { fig, type:t, title };
    STATE.lastFigure = fig;
    STATE.lastPlotType = t;
    STATE.lastPlotTitle = title || (fig.layout && fig.layout.title && fig.layout.title.text) || '';
    window.dispatchEvent(new CustomEvent('aqat:plot', { detail: { type:t, title:STATE.lastPlotTitle } }));
  } catch(e) { toast(e.message, 'error'); }
  finally { setLoading('plot-btn', false); document.getElementById('plot-loader').style.display='none'; }
}


function savePlot() {
  if (!STATE._lastPlot) { toast('Generate a plot first', 'warn'); return; }
  const { fig, type, title } = STATE._lastPlot;
  const id = `gallery-plot-${STATE.galleryCount++}`;
  const section = document.getElementById('gallery-section'), grid = document.getElementById('gallery-grid');
  section.style.display='block';
  const card = document.createElement('div');
  card.className='card'; card.style.padding='8px';
  card.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:12px;color:var(--text3)">${title||type}</span>
    <button class="btn btn-ghost btn-sm" onclick="this.closest('.card').remove();checkGallery()">✕</button></div>
    <div id="${id}" style="min-height:280px"></div>`;
  grid.appendChild(card);
  const baseLayout = getCelestialLayout();
  const layout = Object.assign({}, baseLayout, fig.layout, { height:280, margin:{t:30,r:10,b:40,l:50} });
  plotlyReady(()=>Plotly.react(id, fig.data, layout, PLOT_CONFIG));
  toast('Plot saved to gallery ✓');
}

function checkGallery() {
  if (!document.querySelector('#gallery-grid .card'))
    document.getElementById('gallery-section').style.display='none';
}

/* MAP TAB */
function updateBasemapStatus(text, error) {
  const label = document.getElementById("map-basemap-text");
  const note = document.getElementById("map-basemap-note");
  if (label) label.textContent = text;
  if (note) note.classList.toggle("is-error", !!error);
}

const MAP_BASEMAPS = {
  openfreemap: {
    label: 'OpenFreeMap · Liberty', kind: 'vector',
    style: 'https://tiles.openfreemap.org/styles/liberty',
  },
  dark: {
    label: 'OpenFreeMap · Dark', kind: 'vector',
    style: 'https://tiles.openfreemap.org/styles/dark',
  },
  positron: {
    label: 'OpenFreeMap · Positron', kind: 'vector',
    style: 'https://tiles.openfreemap.org/styles/positron',
  },
  satellite: {
    label: 'Satellite', kind: 'raster',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    maxZoom: 19, maxNativeZoom: 19,
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
  },
  topographic: {
    label: 'Topographic', kind: 'raster',
    url: 'https://tile.opentopomap.org/{z}/{x}/{y}.png',
    maxZoom: 19, maxNativeZoom: 17,
    attribution: 'Map data: © OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)',
  },
  streets: {
    label: 'Streets', kind: 'raster',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    maxZoom: 19, maxNativeZoom: 19,
    attribution: 'Tiles © Esri, HERE, Garmin, OpenStreetMap contributors',
  },
  offline: { label: 'Offline grid', kind: 'offline' },
};

function createOfflineGridLayer() {
  const GridLayer = L.Layer.extend({
    onAdd(map) {
      this._map = map;
      this._container = L.DomUtil.create('div', 'aqat-offline-grid');
      Object.assign(this._container.style, {
        position: 'absolute', left: '0', top: '0', width: '100%', height: '100%',
        pointerEvents: 'none', zIndex: '350',
      });
      map.getPanes().tilePane.appendChild(this._container);
      map.on('move zoom resize', this.redraw, this);
      this.redraw();
    },
    onRemove(map) {
      map.off('move zoom resize', this.redraw, this);
      if (this._container && this._container.parentNode) {
        this._container.parentNode.removeChild(this._container);
      }
    },
    redraw() {
      if (!this._map || !this._container) return;
      const size = this._map.getSize();
      const step = 64;
      const lines = [];
      for (let x = 0; x <= size.x; x += step) {
        lines.push(`<line x1="${x}" y1="0" x2="${x}" y2="${size.y}"/>`);
      }
      for (let y = 0; y <= size.y; y += step) {
        lines.push(`<line x1="0" y1="${y}" x2="${size.x}" y2="${y}"/>`);
      }
      const center = this._map.getCenter();
      this._container.innerHTML =
        `<svg width="${size.x}" height="${size.y}" xmlns="http://www.w3.org/2000/svg">` +
        `<rect width="100%" height="100%" fill="#ece4cf"/>` +
        `<g stroke="#ddd2b6" stroke-width="1">${lines.join('')}</g>` +
        `<text x="18" y="28" fill="#5a5448" font-family="monospace" font-size="11">OFFLINE GRID · ` +
        `${center.lat.toFixed(2)}°, ${center.lng.toFixed(2)}°</text></svg>`;
    },
  });
  return new GridLayer();
}

function useOfflineBasemap(reason) {
  const select = document.getElementById('map-style');
  if (select) select.value = 'offline';
  applyMapStyle('offline');
  if (reason) updateBasemapStatus(`Offline grid · ${reason}`, true);
}

function applyMapStyle(name) {
  const styleName = MAP_BASEMAPS[name] ? name : 'topographic';
  const style = MAP_BASEMAPS[styleName];
  STATE.mapStyle = styleName;
  const select = document.getElementById('map-style');
  if (select && select.value !== styleName) select.value = styleName;
  const map = STATE.mapInstance;
  if (!map) return;

  if (STATE.mapBaseLayer) map.removeLayer(STATE.mapBaseLayer);
  STATE.mapTileLayer = null;
  STATE.mapBaseLayer = null;

  if (style.kind === 'offline' || (typeof navigator !== 'undefined' && navigator.onLine === false)) {
    STATE.mapBaseLayer = createOfflineGridLayer();
    STATE.mapBaseLayer.addTo(map);
    updateBasemapStatus(style.kind === 'offline' ? 'Offline grid · local' : 'Offline grid · browser offline', style.kind !== 'offline');
    return;
  }

  if (style.kind === 'vector') {
    if (typeof L.maplibreGL !== 'function') {
      useOfflineBasemap('vector tiles unavailable');
      return;
    }
    const vectorLayer = L.maplibreGL({ style: style.style, maxZoom: 19 });
    STATE.mapBaseLayer = vectorLayer;
    updateBasemapStatus(`${style.label} · loading…`, false);
    vectorLayer.once('load', () => {
      if (STATE.mapBaseLayer === vectorLayer) updateBasemapStatus(`${style.label} · MapLibre`, false);
    });
    vectorLayer.on('error', () => {
      if (STATE.mapBaseLayer === vectorLayer) useOfflineBasemap('vector tiles unavailable');
    });
    vectorLayer.addTo(map);
    return;
  }

  const tiles = L.tileLayer(style.url, {
    maxZoom: style.maxZoom,
    maxNativeZoom: style.maxNativeZoom,
    attribution: style.attribution,
  });
  STATE.mapTileLayer = tiles;
  STATE.mapBaseLayer = tiles;
  updateBasemapStatus(`${style.label} · loading tiles…`, false);
  tiles.on('tileerror', () => {
    if (STATE.mapBaseLayer === tiles) useOfflineBasemap(`${style.label} unavailable`);
  });
  tiles.once('load', () => {
    if (STATE.mapBaseLayer === tiles) updateBasemapStatus(`${style.label} · Leaflet 1.9.4`, false);
  });
  tiles.addTo(map);
}

function formatLon(lon) {
  const abs = Math.abs(lon);
  return `${abs.toFixed(abs % 1 ? 2 : 0)}°${lon >= 0 ? 'E' : 'W'}`;
}

function formatLat(lat) {
  const abs = Math.abs(lat);
  return `${abs.toFixed(abs % 1 ? 2 : 0)}°${lat >= 0 ? 'N' : 'S'}`;
}

function paramColor(val, param) {
  if (val==null) return '#60a5fa';
  if (param==='AQI'||param==='pm2.5') {
    if (val<=50)  return '#00e400';
    if (val<=100) return '#ffff00';
    if (val<=150) return '#ff7e00';
    if (val<=200) return '#ff0000';
    if (val<=300) return '#8f3f97';
    return '#7e0023';
  }
  // Generic gradient for other params
  return `hsl(${Math.max(0,240-val*2)},70%,55%)`;
}

function initMap() {
  if (typeof L === 'undefined' || !L.map) { setTimeout(initMap, 150); return; }
  if (STATE.mapInstance) {
    STATE.mapInstance.invalidateSize();
    loadMapData();
    return;
  }
  const map = L.map('map', {
    zoomControl: true,
    attributionControl: true,
    preferCanvas: true,
    minZoom: 2,
    maxZoom: 19,
    worldCopyJump: true,
  }).setView([5.55, -0.2], 10);
  map.attributionControl.setPrefix(false);
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
  STATE.mapInstance = map;
  if (typeof navigator !== 'undefined' && navigator.onLine === false) useOfflineBasemap('browser offline');
  else applyMapStyle(STATE.mapStyle);
  window.addEventListener('offline', () => useOfflineBasemap('browser offline'));
  window.addEventListener('online', () => {
    const select = document.getElementById('map-style');
    if (select && select.value === 'offline') applyMapStyle('topographic');
  });
  map.on('mousemove', event => {
    const el = document.getElementById('map-coords');
    if (el) el.textContent = `${formatLat(event.latlng.lat)}  ${formatLon(event.latlng.lng)}`;
  });
  map.on('mouseout', () => {
    const el = document.getElementById('map-coords');
    if (el) el.textContent = 'Lat —  Lon —';
  });
  loadMapData();
}

function reloadMapData() { if (STATE.sessionId) loadMapData(); }

async function loadMapData() {
  if (!STATE.sessionId||!STATE.mapInstance) return;
  document.getElementById('map-pt-count').textContent='Loading…';
  try {
    const data = await api('/api/map_data', {session_id:STATE.sessionId});
    STATE._mapData = data.data;
    renderMapLayers(data.data);
    const rendered = data.data.length;
    document.getElementById('map-pt-count').textContent = rendered < data.data.length
      ? `${rendered.toLocaleString()} of ${data.data.length.toLocaleString()} points rendered`
      : `${data.data.length.toLocaleString()} data points`;
    renderMapStats(data.data);
  } catch(e) { toast(e.message,'error'); }
}

function renderMapLayers(pts) {
  const map = STATE.mapInstance;
  STATE.mapMarkers.forEach(m=>map.removeLayer(m)); STATE.mapMarkers=[];
  STATE.mapLabels.forEach(l=>map.removeLayer(l)); STATE.mapLabels=[];
  if (STATE.mapHeatLayer) { map.removeLayer(STATE.mapHeatLayer); STATE.mapHeatLayer=null; }
  const valid = pts.filter(p=>p.lat!=null&&p.lon!=null&&Number.isFinite(Number(p.lat))&&Number.isFinite(Number(p.lon)));
  if (!valid.length) return;
  const lats=valid.map(p=>p.lat), lons=valid.map(p=>p.lon);
  // Fit once per session so re-opening the tab or changing the colour
  // parameter does not throw away the user's pan/zoom.
  if (STATE.mapFittedFor !== STATE.sessionId) {
    map.fitBounds([[Math.min(...lats),Math.min(...lons)],[Math.max(...lats),Math.max(...lons)]],{padding:[40,40]});
    STATE.mapFittedFor = STATE.sessionId;
  }
  const colorParam = document.getElementById('map-color-param')?.value || 'pm2.5';
  if (STATE.showMarkers) {
    valid.forEach(pt => {
      const val = pt[colorParam];
      const color = paramColor(val, colorParam);
      const icon = L.divIcon({ className:'',
        html:`<div style="width:12px;height:12px;border-radius:50%;background:${color};border:2px solid #ffffff"></div>`,
        iconSize:[12,12], iconAnchor:[6,6] });
      const m = L.marker([pt.lat,pt.lon],{icon}).addTo(map);
      m.bindPopup(`<div style="min-width:180px;font-size:12px;font-family:monospace">
        <b>${pt.location||'—'}</b><br/>
        ${colorParam}: <b style="color:${color}">${val!=null?val.toFixed(2):'—'}</b><br/>
        ${pt['pm2.5']!=null&&colorParam!=='pm2.5'?`PM2.5: ${pt['pm2.5'].toFixed(2)} μg/m³<br/>`:''}
        ${pt.temperature!=null?`Temp: ${pt.temperature.toFixed(1)} °C<br/>`:''}
        ${pt.RH!=null?`RH: ${pt.RH.toFixed(1)}%<br/>`:''}
        Lat/Lon: ${pt.lat.toFixed(5)}, ${pt.lon.toFixed(5)}<br/>
        ${pt.datetime?`Time: ${pt.datetime}`:''}
      </div>`);
      STATE.mapMarkers.push(m);
    });
  }
  // Station labels are plain text nodes, so location names cannot inject HTML.
  const stations = new Map();
  valid.forEach(pt => {
    if (pt.location && !stations.has(pt.location)) stations.set(pt.location, [pt.lat, pt.lon]);
  });
  if (stations.size && stations.size <= 25) {
    stations.forEach(([lat, lon], name) => {
      const text = document.createElement('span');
      text.textContent = name;
      const label = L.tooltip({
        permanent: true,
        direction: 'right',
        className: 'map-station-label',
        offset: [10, 0],
      }).setLatLng([lat, lon]).setContent(text).addTo(map);
      STATE.mapLabels.push(label);
    });
  }
  if (STATE.showHeat && L.heatLayer) {
    const heatData = valid.map(p=>[p.lat,p.lon,Math.min(((p[colorParam]||0)/200),1)]);
    STATE.mapHeatLayer = L.heatLayer(heatData, { radius:25,blur:20,maxZoom:13,
      gradient:{0.2:'green',0.5:'yellow',0.75:'orange',1:'red'} }).addTo(map);
  }
}

function renderMapStats(pts) {
  const vals = pts.map(p=>p['pm2.5']).filter(v=>v!=null);
  const locs  = [...new Set(pts.map(p=>p.location).filter(Boolean))];
  const el = document.getElementById('map-stats'); el.style.display='grid';
  el.innerHTML = [
    {label:'Total Points', value:pts.length,                                     color:'var(--blue)'},
    {label:'Locations',    value:locs.length,                                    color:'var(--purple)'},
    {label:'Avg PM2.5',    value:vals.length?(vals.reduce((a,b)=>a+b)/vals.length).toFixed(1):'-', color:'var(--yellow)', unit:'μg/m³'},
    {label:'Max PM2.5',    value:vals.length?Math.max(...vals).toFixed(1):'-',   color:'var(--red)', unit:'μg/m³'},
  ].map(s=>`<div class="stat-card"><div class="stat-label">${s.label}</div>
    <div class="stat-value" style="color:${s.color}">${s.value}</div>
    ${s.unit?`<div class="stat-unit">${s.unit}</div>`:''}</div>`).join('');
}

/* ─────────────────────────────────────────────────────────
   EXTREME TAB
   ───────────────────────────────────────────────────────── */
function getGuidelineOverrides(prefix) {
  const periods = ['24h','annual','8h','1h'];
  return Object.fromEntries(periods.map(period => {
    const value = document.getElementById(`ext-${prefix}-${period}`)?.value.trim();
    return [period, value === '' ? null : parseFloat(value)];
  }).filter(([, value]) => value !== null && Number.isFinite(value) && value >= 0));
}

async function runExtreme() {
  if (!STATE.sessionId) { toast('Upload data first','warn'); return; }
  document.getElementById('ext-loader').style.display='flex';
  document.getElementById('ext-results').style.display='none';
  try {
    const data = await api('/api/extreme', {
      session_id:STATE.sessionId,
      column:document.getElementById('ext-col').value,
      params:{ block:document.getElementById('ext-block').value,
        who_standard:STATE.extremeToggles.who, epa_standard:STATE.extremeToggles.epa,
        who_overrides:getGuidelineOverrides('who'),
        epa_overrides:getGuidelineOverrides('epa'),
        top_n:parseInt(document.getElementById('ext-topn').value)||10,
        bins:parseInt(document.getElementById('ext-bins').value)||40,
        percentiles:[50,75,90,95,99,99.9] }
    });
    renderExtremeResults(data);
  } catch(e) { toast(e.message,'error'); }
  finally { document.getElementById('ext-loader').style.display='none'; }
}

function renderExtremeResults(r) {
  const col = r.column;
  const desc = r.descriptive;
  document.getElementById('ext-desc-grid').innerHTML = [
    ['Count',desc.count,''],['Mean',desc.mean,'μg/m³'],['Median',desc.median,'μg/m³'],
    ['Std Dev',desc.std,'μg/m³'],['Min',desc.min,'μg/m³'],['Max',desc.max,'μg/m³'],
    ['Skewness',desc.skewness,''],['Kurtosis',desc.kurtosis,''],
  ].map(([label,value,unit])=>`<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value" style="color:${label==='Max'?'var(--red)':label==='Mean'?'var(--blue)':'var(--text2)'}">
      ${typeof value==='number'?value.toFixed(3):value}</div>
    ${unit?`<div class="stat-unit">${unit}</div>`:''}</div>`).join('');

  // WHO
  const w=r.who||{};
  document.getElementById('ext-who-body').innerHTML=Object.keys(w).length?`
    ${w['24h_standard']!=null?`<div style="color:var(--text3);font-size:12px;margin-bottom:6px">24h: ${w['24h_standard']} μg/m³</div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
        <div style="flex:1;background:var(--bg4);border-radius:20px;height:8px;overflow:hidden">
          <div style="width:${Math.min(w['24h_exceed_pct']||0,100)}%;height:100%;background:${(w['24h_exceed_pct']||0)>50?'var(--red)':'var(--yellow)'};border-radius:20px"></div>
        </div><span style="font-weight:700;color:var(--text)">${w['24h_exceed_pct']}%</span>
      </div><div style="font-size:11px;color:var(--text4)">${w['24h_exceed_n']} exceedance days</div>`:''}
    ${w.annual_standard!=null?`<div style="margin-top:10px;font-size:12px;color:var(--text3)">Annual: ${w.annual_standard} μg/m³</div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
        <span style="font-family:monospace;font-size:16px;font-weight:700;color:var(--text)">${(w.annual_mean||0).toFixed(2)}</span>
        <span class="badge" style="background:${w.annual_exceed?'#ef444422':'#10b98122'};color:${w.annual_exceed?'var(--red)':'var(--green)'}">
          ${w.annual_exceed?'EXCEEDS':'COMPLIANT'}</span></div>`:''}
    ${w['8h_standard']!=null?`<div style="margin-top:10px;color:var(--text3);font-size:12px">8h: ${w['8h_standard']} μg/m³</div>`:''}
    ${w['1h_standard']!=null?`<div style="margin-top:10px;color:var(--text3);font-size:12px">1h: ${w['1h_standard']} μg/m³</div>`:''}
  `:'<div style="color:var(--text4);font-size:12px">No WHO standard available</div>';

  // EPA
  const ep=r.epa||{};
  document.getElementById('ext-epa-body').innerHTML=Object.keys(ep).length?`
    ${ep['24h_standard']!=null?`<div style="color:var(--text3);font-size:12px;margin-bottom:6px">24h: ${ep['24h_standard']} μg/m³</div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
        <div style="flex:1;background:var(--bg4);border-radius:20px;height:8px;overflow:hidden">
          <div style="width:${Math.min(ep['24h_exceed_pct']||0,100)}%;height:100%;background:${(ep['24h_exceed_pct']||0)>50?'var(--red)':'var(--yellow)'};border-radius:20px"></div>
        </div><span style="font-weight:700;color:var(--text)">${ep['24h_exceed_pct']}%</span>
      </div><div style="font-size:11px;color:var(--text4)">${ep['24h_exceed_n']} exceedance days</div>`:''}
    ${ep.annual_standard!=null?`<div style="margin-top:10px;font-size:12px;color:var(--text3)">Annual: ${ep.annual_standard} μg/m³</div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
        <span style="font-family:monospace;font-size:16px;font-weight:700;color:var(--text)">${(ep.annual_mean||0).toFixed(2)}</span>
        <span class="badge" style="background:${ep.annual_exceed?'#ef444422':'#10b98122'};color:${ep.annual_exceed?'var(--red)':'var(--green)'}">
          ${ep.annual_exceed?'EXCEEDS':'COMPLIANT'}</span></div>`:''}
    ${ep['8h_standard']!=null?`<div style="margin-top:10px;color:var(--text3);font-size:12px">8h: ${ep['8h_standard']} μg/m³</div>`:''}
    ${ep['1h_standard']!=null?`<div style="margin-top:10px;color:var(--text3);font-size:12px">1h: ${ep['1h_standard']} μg/m³</div>`:''}
  `:'<div style="color:var(--text4);font-size:12px">No EPA standard available</div>';

  // Return levels
  const rl=r.return_levels||{};
  document.getElementById('ext-rl-grid').innerHTML=Object.entries(rl).map(([p,v])=>`
    <div class="stat-card"><div class="stat-label">${p} Return</div>
      <div class="stat-value" style="color:var(--pink)">${v.toFixed(2)}</div>
      <div class="stat-unit">μg/m³</div></div>`).join('');

  // Plots
  if (r.histogram?.counts) {
    const binMids=r.histogram.bin_edges.slice(0,-1).map((v,i)=>((v+r.histogram.bin_edges[i+1])/2).toFixed(2));
    plotlyReady(()=>Plotly.react('ext-hist-plot',
      [{type:'bar',x:binMids,y:r.histogram.counts,marker:{color:'var(--blue)',opacity:.8}}],
      applyCelestialLayout({title:`Distribution — ${col}`,xaxis:{title:col},yaxis:{title:'Count'},height:300}),PLOT_CONFIG));
  }
  if (r.percentiles) {
    const pK=Object.keys(r.percentiles), pV=Object.values(r.percentiles);
    plotlyReady(()=>Plotly.react('ext-pct-plot',
      [{type:'bar',x:pK,y:pV,marker:{color:pK.map((_,i)=>`hsl(${210-i/pK.length*150},70%,55%)`)}}],
      applyCelestialLayout({title:'Percentiles',xaxis:{title:'Percentile'},yaxis:{title:col},height:300}),PLOT_CONFIG));
  }
  if (r.block_maxima?.values?.length) {
    plotlyReady(()=>Plotly.react('ext-bm-plot',
      [{type:'scatter',mode:'lines+markers',x:r.block_maxima.dates,y:r.block_maxima.values,
        line:{color:'var(--yellow)',width:2},marker:{size:6,color:'var(--yellow)'}}],
      applyCelestialLayout({title:'Block Maxima',xaxis:{title:'Date'},yaxis:{title:col},height:300}),PLOT_CONFIG));
  }
  if (Object.keys(rl).length) {
    const rlX=Object.keys(rl).map(k=>parseInt(k)), rlY=Object.values(rl);
    plotlyReady(()=>Plotly.react('ext-rl-plot',
      [{type:'scatter',mode:'lines+markers',x:rlX,y:rlY,
        line:{color:'var(--pink)',width:2},marker:{size:8,color:'var(--pink)'}}],
      applyCelestialLayout({title:'Return Levels',xaxis:{title:'Return Period (yr)',type:'log'},yaxis:{title:`${col}`},height:300}),PLOT_CONFIG));
  }
  if (r.top_events?.length) {
    const evCols=['Rank',...Object.keys(r.top_events[0])];
    let evHtml='<thead><tr>'+evCols.map(c=>`<th>${c}</th>`).join('')+'</tr></thead><tbody>';
    r.top_events.forEach((row,i)=>{
      evHtml+=`<tr><td style="color:var(--yellow);font-weight:700">#${i+1}</td>`+
        Object.values(row).map(v=>`<td style="font-family:monospace">${v==null?'—':typeof v==='number'?v.toFixed(4):v}</td>`).join('')+'</tr>';
    });
    evHtml+='</tbody>';
    document.getElementById('ext-events-table').innerHTML=evHtml;
  }
  document.getElementById('ext-results').style.display='block';
}

/* ─────────────────────────────────────────────────────────
   MODEL EVAL TAB
   ───────────────────────────────────────────────────────── */
function guidelineValue(id) {
  const value = document.getElementById(id)?.value.trim();
  return value === '' ? null : parseFloat(value);
}

async function runCompliance() {
  if (!STATE.sessionId) { toast('Upload data first','warn'); return; }
  const column = document.getElementById('compliance-col').value;
  if (!column) { toast('Select a sensor reading','warn'); return; }
  document.getElementById('compliance-loader').style.display='flex';
  document.getElementById('compliance-results').style.display='none';
  try {
    const data = await api('/api/compliance', {session_id:STATE.sessionId, column,
      params:{who_overrides:{'24h':guidelineValue('compliance-who-24h'), annual:guidelineValue('compliance-who-annual'), '8h':guidelineValue('compliance-who-8h'), '1h':guidelineValue('compliance-who-1h')},
        epa_overrides:{'24h':guidelineValue('compliance-epa-24h'), annual:guidelineValue('compliance-epa-annual'), '8h':guidelineValue('compliance-epa-8h'), '1h':guidelineValue('compliance-epa-1h')}}});
    renderCompliance(data);
  } catch(e) { toast(e.message,'error'); }
  finally { document.getElementById('compliance-loader').style.display='none'; }
}

function renderCompliance(data) {
  document.getElementById('compliance-summary').innerHTML = [
    ['Valid readings',data.readings,'var(--blue)'],['Missing',`${data.missing_percent}%`,'var(--yellow)'],
    ['Sampling interval',data.sampling_interval_hours?`${data.sampling_interval_hours} h`:'Unknown','var(--purple)'],
    ['Period',`${data.first_reading.slice(0,10)} to ${data.last_reading.slice(0,10)}`,'var(--teal)']
  ].map(([label,value,color])=>`<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value" style="font-size:16px;color:${color}">${value}</div></div>`).join('');
  document.getElementById('compliance-standards').innerHTML = data.standards.length ? data.standards.map(item => `
    <div class="card" style="border-top:3px solid ${item.exceedance_count?'var(--red)':'var(--green)'}">
      <div class="card-title">${item.authority} · ${item.period}</div>
      <div class="stat-value" style="color:${item.exceedance_count?'var(--red)':'var(--green)'}">${item.exceedance_percent}%</div>
      <div style="color:var(--text3);font-size:12px;margin-top:6px">${item.exceedance_count} exceedances · threshold ${item.threshold}</div>
      <div style="color:var(--text4);font-size:11px;margin-top:6px">${item.episode_count} episode(s)${item.estimated_exceedance_hours!=null?` · about ${item.estimated_exceedance_hours} hours`:''}</div>
    </div>`).join('') : '<div class="card">No configured standard is available for this reading.</div>';
  document.getElementById('compliance-recommendations').innerHTML = data.recommendations.map(item => `
    <div style="border-left:3px solid ${item.priority==='high'?'var(--red)':item.priority==='medium'?'var(--yellow)':'var(--green)'};padding:10px 12px;margin-bottom:10px">
      <div style="font-weight:700;color:var(--text);margin-bottom:4px">${item.title}</div><div style="color:var(--text2);font-size:12px;line-height:1.5">${item.text}</div>
    </div>`).join('');
  const dailyTraces = data.standards.flatMap((item, index) => {
    const color = ['#ef4444','#3b82f6','#f59e0b','#10b981'][index % 4];
    const rows = item.daily_exceedances || [];
    return [{type:'bar', x:rows.map(row=>row.date), y:rows.map(row=>row.value),
      marker:{color:rows.map(row=>row.exceeded?color:'#64748b')}, name:`${item.authority} ${item.period}`}];
  });
  const hourlyTraces = data.standards.flatMap((item, index) => {
    const color = ['#ef4444','#3b82f6','#f59e0b','#10b981'][index % 4];
    const rows = item.hourly_exceedances || [];
    return [{type:'scatter', mode:'lines+markers', x:rows.map(row=>row.datetime), y:rows.map(row=>row.value),
      line:{color,width:2}, marker:{color:rows.map(row=>row.exceeded?color:'#64748b'),size:5}, name:`${item.authority} ${item.period}`}];
  });
  const thresholdShapes = data.standards.map((item, index) => ({type:'line', xref:'paper', x0:0, x1:1,
    y0:item.threshold, y1:item.threshold, line:{color:['#ef4444','#3b82f6','#f59e0b','#10b981'][index % 4],dash:'dash',width:1}}));
  plotlyReady(()=>Plotly.react('compliance-daily-plot', dailyTraces,
    applyCelestialLayout({title:'Daily maximum readings',xaxis:{title:'Day'},yaxis:{title:data.column},height:340,
      barmode:'group',shapes:thresholdShapes}),PLOT_CONFIG));
  plotlyReady(()=>Plotly.react('compliance-hourly-plot', hourlyTraces,
    applyCelestialLayout({title:'Hourly readings and exceedances',xaxis:{title:'Time'},yaxis:{title:data.column},height:340,
      shapes:thresholdShapes}),PLOT_CONFIG));
  document.getElementById('compliance-results').style.display='block';
}

document.getElementById('ml-model')?.addEventListener('change', function() {
  document.getElementById('ml-z-wrap').style.display = this.value === 'anomaly' ? 'block' : 'none';
});

async function runMachineLearning() {
  if (!STATE.sessionId) { toast('Upload data first','warn'); return; }
  const target=document.getElementById('ml-target').value;
  if (!target) { toast('Select a target reading','warn'); return; }
  document.getElementById('ml-loader').style.display='flex';
  document.getElementById('ml-results').style.display='none';
  try {
    const model=document.getElementById('ml-model').value;
    const features=[...document.querySelectorAll('.ml-feature:checked')].map(el=>el.value);
    const data=await api('/api/machine_learning',{session_id:STATE.sessionId,target,model,
      params:{z_score:parseFloat(document.getElementById('ml-z').value)||3, features}});
    renderMachineLearning(data);
  } catch(e) { toast(e.message,'error'); }
  finally { document.getElementById('ml-loader').style.display='none'; }
}

function renderMachineLearning(data) {
  const summary=[];
  if (data.slope_per_day!=null) summary.push(['Trend/day',data.slope_per_day,'var(--blue)']);
  if (data.holdout) { summary.push(['Holdout RMSE',data.holdout.rmse,'var(--yellow)']); summary.push(['Holdout n',data.holdout.n,'var(--purple)']); }
  if (data.metrics) { summary.push(['Baseline RMSE',data.metrics.rmse,'var(--yellow)']); summary.push(['Baseline MAE',data.metrics.mae,'var(--blue)']); }
  if (data.anomaly_count!=null) summary.push(['Anomalies',data.anomaly_count,'var(--red)']);
  document.getElementById('ml-summary').innerHTML=summary.map(([label,value,color])=>`<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value" style="color:${color}">${typeof value==='number'?value.toFixed(3):value}</div></div>`).join('');
  document.getElementById('ml-message').textContent=data.message;
  const detail=data.feature_importance ? '<strong>Feature importance</strong><br>' + Object.entries(data.feature_importance).sort((a,b)=>b[1]-a[1]).map(([name,value])=>`${name}: ${value}`).join('<br>') : data.anomalies ? data.anomalies.slice(0,20).map(x=>`${x.datetime}: ${x.value}`).join('<br>') : data.hourly_baseline ? Object.entries(data.hourly_baseline).map(([hour,value])=>`${hour}:00 — ${value}`).join('<br>') : `Intercept: ${data.intercept}<br>Holdout bias: ${data.holdout?.bias}`;
  document.getElementById('ml-detail').innerHTML=detail;
  if (data.holdout_series?.length) {
    plotlyReady(()=>Plotly.react('ml-main-plot', [
      {type:'scatter',mode:'lines+markers',x:data.holdout_series.map(row=>row.datetime),y:data.holdout_series.map(row=>row.actual),name:'Actual',line:{color:'#3b82f6'}},
      {type:'scatter',mode:'lines+markers',x:data.holdout_series.map(row=>row.datetime),y:data.holdout_series.map(row=>row.predicted),name:'Predicted',line:{color:'#f59e0b',dash:'dash'}}
    ], applyCelestialLayout({title:`Holdout: ${data.target}`,xaxis:{title:'Time'},yaxis:{title:data.target},height:360}),PLOT_CONFIG));
  } else if (data.seasonal_series?.length) {
    plotlyReady(()=>Plotly.react('ml-main-plot', [{type:'bar',x:data.seasonal_series.map(row=>row.hour),y:data.seasonal_series.map(row=>row.value),marker:{color:'#06b6d4'}}],
      applyCelestialLayout({title:`Hourly seasonal baseline: ${data.target}`,xaxis:{title:'Hour of day'},yaxis:{title:data.target},height:360}),PLOT_CONFIG));
  } else if (data.anomaly_series?.length) {
    const normal=data.anomaly_series.filter(row=>!row.is_anomaly), anomalies=data.anomaly_series.filter(row=>row.is_anomaly);
    plotlyReady(()=>Plotly.react('ml-main-plot', [
      {type:'scatter',mode:'lines',x:normal.map(row=>row.datetime),y:normal.map(row=>row.value),name:'Readings',line:{color:'#3b82f6'}},
      {type:'scatter',mode:'markers',x:anomalies.map(row=>row.datetime),y:anomalies.map(row=>row.value),name:'Anomalies',marker:{color:'#ef4444',size:9}}
    ], applyCelestialLayout({title:`Anomaly detection: ${data.target}`,xaxis:{title:'Time'},yaxis:{title:data.target},height:360}),PLOT_CONFIG));
  }
  document.getElementById('ml-results').style.display='block';
}

const METRIC_INFO = {
  MB:   {label:'Mean Bias',          unit:'',  good:v=>Math.abs(v)<5,  desc:'Systematic over/under-estimation'},
  NMB:  {label:'Norm. Mean Bias',    unit:'%', good:v=>Math.abs(v)<10, desc:'NMB < ±10% = good'},
  RMSE: {label:'RMSE',               unit:'',  good:v=>v<15,           desc:'Root Mean Square Error'},
  NRMSE:{label:'Norm. RMSE',         unit:'%', good:v=>v<30,           desc:'NRMSE < 30% = acceptable'},
  MAE:  {label:'MAE',                unit:'',  good:()=>true,          desc:'Mean Absolute Error'},
  R:    {label:'Correlation (r)',     unit:'',  good:v=>v>0.8,          desc:'Pearson correlation'},
  R2:   {label:'R²',                 unit:'',  good:v=>v>0.64,         desc:'Coefficient of determination'},
  IOA:  {label:'Index of Agreement', unit:'',  good:v=>v>0.7,          desc:'Willmott IOA; 1 = perfect'},
  FAC2: {label:'FAC2',               unit:'%', good:v=>v>75,           desc:'% within factor of 2'},
  PBIAS:{label:'Percent Bias',       unit:'%', good:v=>Math.abs(v)<15, desc:'PBIAS < ±15% = good'},
  KGE:  {label:'KGE',                unit:'',  good:v=>v>0.75,         desc:'Kling-Gupta Efficiency'},
};

async function runModelEval() {
  if (!STATE.sessionId) { toast('Upload data first','warn'); return; }
  const obsCol=document.getElementById('eval-obs-col').value;
  const modCol=document.getElementById('eval-mod-col').value;
  if (!obsCol||!modCol) { toast('Select both columns','warn'); return; }
  if (obsCol===modCol)  { toast('Select different columns','warn'); return; }
  document.getElementById('eval-loader').style.display='flex';
  document.getElementById('eval-results').style.display='none';
  try {
    const data = await api('/api/model_eval', { session_id:STATE.sessionId,
      obs_col:obsCol, mod_col:modCol, params:{max_scatter_pts:2000} });
    renderEvalResults(data, obsCol, modCol);
  } catch(e) { toast(e.message,'error'); }
  finally { document.getElementById('eval-loader').style.display='none'; }
}

function renderEvalResults(r, obsCol, modCol) {
  document.getElementById('eval-metrics-grid').innerHTML=Object.entries(METRIC_INFO).map(([key,info])=>{
    const val=r[key]; if (val==null) return '';
    const good=info.good(val);
    return `<div title="${info.desc}" style="background:var(--bg);border:1px solid ${good?'#10b98133':'#ef444433'};
      border-radius:10px;padding:12px 14px;position:relative">
      <div style="font-size:10px;color:var(--text4);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">${info.label}</div>
      <div style="font-size:20px;font-weight:700;font-family:monospace;color:${good?'var(--green)':'var(--red)'}">
        ${typeof val==='number'?val.toFixed(3):val}${info.unit}</div>
      <div style="position:absolute;top:10px;right:10px;font-size:12px">${good?'✅':'❌'}</div>
      <div style="font-size:10px;color:var(--text4);margin-top:2px">${info.desc}</div>
    </div>`;
  }).join('');

  document.getElementById('eval-equation').innerHTML=
    `<span style="color:var(--text)">${modCol} = ${r.slope.toFixed(4)} × ${obsCol} + ${r.intercept.toFixed(4)}</span>
     <span style="margin-left:16px;color:var(--blue)">R² = ${r.R2.toFixed(4)}</span>
     <span style="margin-left:16px;color:var(--purple)">n = ${r.n.toLocaleString()}</span>`;

  if (r.daily_stats&&Object.keys(r.daily_stats).length) {
    document.getElementById('eval-daily-section').style.display='block';
    document.getElementById('eval-daily-grid').innerHTML=[
      ['Daily n',r.daily_stats.n,'','var(--blue)'],
      ['Daily MB',r.daily_stats.MB,'',Math.abs(r.daily_stats.MB)<5?'var(--green)':'var(--red)'],
      ['Daily RMSE',r.daily_stats.RMSE,'','var(--yellow)'],
      ['Daily R',r.daily_stats.R,'',r.daily_stats.R>0.8?'var(--green)':'var(--red)'],
      ['Daily R²',r.daily_stats.R2,'','var(--purple)'],
    ].map(([label,value,,color])=>`<div class="stat-card"><div class="stat-label">${label}</div>
      <div class="stat-value" style="color:${color}">${typeof value==='number'?value.toFixed(3):value}</div></div>`).join('');
  }

  // Scatter
  const all=[...r.scatter.obs,...r.scatter.mod], mn=Math.min(...all), mx=Math.max(...all);
  plotlyReady(()=>Plotly.react('eval-scatter-plot',[
    {type:'scatter',mode:'markers',x:r.scatter.obs,y:r.scatter.mod,
      marker:{color:'var(--blue)',opacity:.5,size:5},name:'Data Points'},
    {type:'scatter',mode:'lines',x:[mn,mx],y:[r.slope*mn+r.intercept,r.slope*mx+r.intercept],
      line:{color:'var(--red)',width:2,dash:'dash'},name:'Regression'},
    {type:'scatter',mode:'lines',x:[mn,mx],y:[mn,mx],line:{color:'var(--green)',width:1,dash:'dot'},name:'1:1 Line'}
  ], applyCelestialLayout({title:`Scatter: ${obsCol} vs ${modCol}`,
    xaxis:{title:obsCol},yaxis:{title:modCol},height:420}),PLOT_CONFIG));

  // Taylor diagram (simplified as polar scatter)
  plotlyReady(()=>Plotly.react('eval-taylor-plot',[
    {type:'scatterpolar',r:[r.R*2],theta:[Math.acos(r.R)*180/Math.PI],
      mode:'markers',marker:{size:14,color:'var(--blue)'},name:'Model'},
    {type:'scatterpolar',r:[0],theta:[0],mode:'markers',marker:{size:14,color:'var(--green)',symbol:'star'},name:'Reference'},
  ], applyCelestialLayout({title:'Taylor Diagram',polar:{radialaxis:{range:[0,2.5]}},height:300}),PLOT_CONFIG));

  // Bias time series
  if (r.bias_ts?.length) {
    plotlyReady(()=>Plotly.react('eval-bias-plot',[
      {type:'scatter',mode:'lines',x:r.bias_ts.map(d=>d.datetime),y:r.bias_ts.map(d=>d.bias_pct),
        line:{color:'var(--yellow)',width:1.5},name:'% Bias'},
      {type:'scatter',mode:'lines',x:r.bias_ts.map(d=>d.datetime),y:r.bias_ts.map(()=>0),
        line:{color:'var(--border2)',width:1,dash:'dot'},showlegend:false}
    ], applyCelestialLayout({title:'% Bias Over Time',xaxis:{title:'Date'},yaxis:{title:'Bias (%)'},height:300}),PLOT_CONFIG));
  }

  // Rating
  const metCount=Object.keys(METRIC_INFO).filter(k=>r[k]!=null).length;
  const goodCount=Object.entries(METRIC_INFO).filter(([k,info])=>r[k]!=null&&info.good(r[k])).length;
  const score=Math.round(goodCount/metCount*100);
  const rating=score>=80?'Excellent':score>=60?'Good':score>=40?'Acceptable':'Poor';
  const ratingColor=score>=80?'var(--green)':score>=60?'var(--blue)':score>=40?'var(--yellow)':'var(--red)';
  document.getElementById('eval-rating-label').textContent=rating;
  document.getElementById('eval-rating-label').style.color=ratingColor;
  document.getElementById('eval-rating-sub').textContent=`${goodCount} of ${metCount} criteria met`;
  document.getElementById('eval-rating-bar').style.width=score+'%';
  const criteriaKeys=['R','IOA','FAC2','NMB','NRMSE'];
  document.getElementById('eval-criteria-grid').innerHTML=criteriaKeys.map(k=>{
    const info=METRIC_INFO[k], val=r[k];
    if (val==null) return '';
    const met=info.good(val);
    return `<div class="perf-criterion ${met?'met':'fail'}">
      <div class="criterion-icon">${met?'✅':'❌'}</div>
      <div style="font-weight:700;color:${met?'var(--green)':'var(--red)'};font-family:monospace">${val.toFixed(3)}${info.unit}</div>
      <div class="criterion-label">${info.label}</div></div>`;
  }).join('');
  document.getElementById('eval-results').style.display='block';
}

// Init
updatePlotControls();
