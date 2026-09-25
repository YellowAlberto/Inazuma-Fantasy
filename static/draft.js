const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ELEMENTS = { Fire: '🔥 Fuego', Wind: '🌪 Viento', Forest: '🌲 Bosque', Mountain: '⛰ Montaña' };
const STAT_LABELS = { potencia: 'POT', control: 'CON', tecnica: 'TEC', presion: 'PRE', fisico: 'FIS', agilidad: 'AGI', inteligencia: 'INT' };
let S = null;          // estado actual
let swapFrom = null;   // hueco seleccionado para intercambiar
let swapPreview = null;    // deltas de media/química/puntos para cada posible destino de swapFrom/dragFromIndex
let swapPreviewToken = 0;  // descarta respuestas de preview que ya no corresponden a la selección actual
let dragFromIndex = null;  // hueco que se está arrastrando (independiente de swapFrom, que es para clic)

async function api(path, body) {
  const res = await fetch('/draft' + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok) { toast(data.error || 'Error'); throw new Error(data.error); }
  return data;
}

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), 2600);
}

const tier = ovr => ovr >= 90 ? 'legend' : ovr >= 84 ? 'gold' : ovr >= 78 ? 'silver' : 'bronze';
const sprite = p => `<img class="sprite" src="/static/sprites/${encodeURIComponent(p.sprite)}" alt="" loading="lazy" draggable="false" onerror="this.classList.add('noimg')">`;
const chemDots = c => `<span class="dots">${[0,1,2].map(i => `<i class="${i < c ? 'on' : ''}"></i>`).join('')}</span>`;
const pickAt = i => i < 11 ? S.picks[i] : S.bench[i - 11];

// ---------------------------------------------------------------- swap preview
function deltaSpan(v, arrow = true) {
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'same';
  const sign = v > 0 ? '+' : '';
  const ico = arrow ? (v > 0 ? '▲' : v < 0 ? '▼' : '·') : '';
  return `<b class="${cls}">${ico}${sign}${v}</b>`;
}

// Contenido del efecto de intercambiar fromIndex <-> i (media/química/puntos).
// fromIndex es swapFrom (clic) o dragFromIndex (arrastrar), lo que esté activo.
function swapDeltaContent(i, fromIndex) {
  if (fromIndex === null || i === fromIndex || !swapPreview) return '';
  const d = swapPreview.options[i];
  if (!d) return '';
  return `<span>Media ${deltaSpan(d.rating_delta)}</span>
    <span>Química ${deltaSpan(d.chem_delta)}</span>
    <span>Pts ${deltaSpan(d.score_delta)}</span>`;
}

// Rellena las burbujas .swap-delta ya presentes en el DOM sin recrear las
// cartas — así se puede llamar en pleno arrastre sin cortar el drag nativo.
function applySwapPreview() {
  const fromIndex = swapFrom !== null ? swapFrom : dragFromIndex;
  document.querySelectorAll('#slots .swap-delta, #bench .swap-delta').forEach(el => {
    el.innerHTML = swapDeltaContent(+el.dataset.i, fromIndex);
  });
}

async function loadSwapPreview(a) {
  const myToken = ++swapPreviewToken;
  try {
    const res = await fetch(`/draft/api/swap/preview?a=${a}`);
    const data = await res.json();
    if (!res.ok || myToken !== swapPreviewToken) return; // ya no aplica (cambiaste de selección o cancelaste)
    swapPreview = data;
    applySwapPreview();
  } catch (e) { /* si falla la preview, seguimos sin ella */ }
}

// ---------------------------------------------------------------- render
function render(state) {
  S = state;
  $('day').textContent = state.day.split('-').reverse().join('/');
  $('rating').textContent = state.rating ?? 0;
  $('chem').textContent = `${state.chem ?? 0}/33`;
  $('score').textContent = state.score ?? 0;

  if (!state.formation) {
    $('formation-step').classList.remove('hidden');
    $('draft-step').classList.add('hidden');
    renderFormations();
    return;
  }
  $('formation-step').classList.add('hidden');
  $('draft-step').classList.remove('hidden');
  renderPitch();
  renderManager();

  const complete = state.picks.every(Boolean) && state.bench.every(Boolean) && state.manager;
  const btn = $('finish-btn');
  btn.disabled = !complete || state.finished;
  btn.textContent = state.finished ? `Enviado · ${state.score} pts` : complete ? 'Enviar equipo' : 'Completa el equipo';

  if (state.offer) showOffer(state.offer);
  else if (state.manager_offer) showManagerOffer(state.manager_offer);
  else { $('modal').classList.add('hidden'); $('peek-reopen-btn').classList.add('hidden'); }
}

function renderFormations() {
  $('formation-list').innerHTML = S.formation_options.map(f => `
    <button class="formation-card" data-f="${f.name}">
      <div class="mini-pitch">${f.slots.map(s => `<i class="pos-${s[0]}" style="left:${s[2]}%;top:${s[3]}%"></i>`).join('')}</div>
      <strong>${f.name}</strong>
    </button>`).join('');
  document.querySelectorAll('.formation-card').forEach(b =>
    b.onclick = async () => render(await api('/api/formation', { formation: b.dataset.f })));
}

// Carta pequeña (campo o banquillo)
function miniCard(p, i, label, extra = '', style = '', tipExtra = '') {
  if (!p) return `<button class="slot empty ${i >= 11 ? 'bench-slot' : ''}" data-i="${i}" style="${style}"><span>+</span><b>${label}</b></button>`;
  const ovr = p.ovr_here ?? p.ovr;
  const oop = p.out_of_position;
  const sel = swapFrom === i ? 'selected' : swapFrom !== null ? 'target' : '';
  return `<button class="slot card-mini ${tier(ovr)} ${sel} ${i >= 11 ? 'bench-slot' : ''}" data-i="${i}" style="${style}" draggable="${!S.finished}"
            title="${esc(p.nombre)} · ${p.posicion} natural · ${esc(p.saga)}${p.equipos.length ? ' · ' + esc(p.equipos.join(', ')) : ''}${oop ? ' · FUERA DE POSICIÓN' : ''}\n${p.elemento || ''} · ${esc(p.arquetipo || '')}${tipExtra}">
    <span class="ovr ${oop ? 'oop' : ''}">${ovr}</span><span class="pos">${label}</span>
    ${oop ? `<span class="natpos">${p.posicion}</span>` : ''}
    ${sprite(p)}
    <span class="name">${esc(p.apodo)}</span>
    ${extra}
    <div class="swap-delta" data-i="${i}"></div>
  </button>`;
}

function renderPitch() {
  const lvl = v => v >= 3 ? 'l3' : v === 2 ? 'l2' : v === 1 ? 'l1' : 'l0';
  $('links').innerHTML = S.links.map(l => {
    const a = S.slots[l.a], b = S.slots[l.b];
    const filled = S.picks[l.a] && S.picks[l.b];
    return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" class="${filled ? lvl(l.value) : 'empty'}"/>`;
  }).join('');

  // Insignia en el centro de cada enlace con sus puntos y el motivo al pasar el ratón
  const badges = S.links.filter(l => S.picks[l.a] && S.picks[l.b]).map(l => {
    const a = S.slots[l.a], b = S.slots[l.b];
    const tip = l.reasons.length ? l.reasons.join('\n') : 'Sin nada en común';
    return `<span class="link-badge ${lvl(l.value)}" style="left:${(a.x + b.x) / 2}%;top:${(a.y + b.y) / 2}%" title="${esc(tip)}">${l.value ? '+' + l.value : '0'}</span>`;
  }).join('');

  $('slots').innerHTML = badges + S.slots.map((s, i) =>
    miniCard(S.picks[i], i, s.label, S.picks[i] ? chemDots(S.player_chem[i]) : '', `left:${s.x}%;top:${s.y}%`,
             S.picks[i] ? `\nEnlaces: ${S.player_points[i]} pts → química ${S.player_chem[i]}/3` : '')).join('');
  $('bench').innerHTML = S.bench.map((p, k) =>
    miniCard(p, 11 + k, p ? p.posicion : `SUP ${k + 1}`, p ? '<span class="dots bench-tag">SUP</span>' : '')).join('');

  document.querySelectorAll('#slots .slot, #bench .slot').forEach(el => {
    const i = +el.dataset.i;
    el.onclick = () => onSlot(i);
    el.ondragstart = e => {
      e.dataTransfer.setData('text/plain', i); el.classList.add('dragging');
      if (pickAt(i)) {
        swapFrom = null; // si venías de un clic previo, manda el arrastre actual
        dragFromIndex = i; swapPreview = null; applySwapPreview(); loadSwapPreview(i);
      }
    };
    el.ondragend = () => {
      el.classList.remove('dragging');
      dragFromIndex = null; swapPreview = null; applySwapPreview();
    };
    el.ondragover = e => { if (pickAt(i)) { e.preventDefault(); el.classList.add('drop'); } };
    el.ondragleave = () => el.classList.remove('drop');
    el.ondrop = async e => {
      e.preventDefault(); el.classList.remove('drop');
      const from = +e.dataTransfer.getData('text/plain');
      if (from !== i && pickAt(from) && pickAt(i)) {
        swapFrom = null; dragFromIndex = null; swapPreview = null;
        render(await api('/api/swap', { a: from, b: i }));
      }
    };
  });
  applySwapPreview();
  $('swap-hint').textContent = swapFrom === null
    ? 'Clic en una posición vacía para abrir un sobre. Clic (o arrastra) en dos jugadores para intercambiarlos: verás si mejora la media y la química antes de soltar.'
    : `Moviendo a ${pickAt(swapFrom).apodo}: pasa el ratón por otro jugador para ver cómo cambiarían la media y la química, y haz clic en él para confirmar (clic de nuevo en ${pickAt(swapFrom).apodo} para cancelar).`;
}

function renderManager() {
  const m = S.manager;
  $('manager-slot').innerHTML = m
    ? `<div class="manager-card">${sprite(m)}<div><strong>${esc(m.nombre)}</strong><div class="muted small">${esc(m.saga)}</div></div></div>`
    : `<button class="btn wide" id="manager-btn" ${S.finished ? 'disabled' : ''}>Elegir entrenador</button>`;
  const b = $('manager-btn');
  if (b) b.onclick = async () => render(await api('/api/manager/open', {}));
}

async function onSlot(i) {
  if (S.finished) return;
  if (!pickAt(i)) { swapFrom = null; swapPreview = null; return render(await api(`/api/slot/${i}/open`, {})); }
  if (swapFrom === null) { swapFrom = i; swapPreview = null; renderPitch(); loadSwapPreview(i); return; }
  if (swapFrom === i) { swapFrom = null; swapPreview = null; return renderPitch(); }
  const a = swapFrom; swapFrom = null; swapPreview = null;
  render(await api('/api/swap', { a, b: i }));
}

// ---------------------------------------------------------------- modal
function optionCard(p, isManager) {
  const delta = p.chem_delta > 0 ? `+${p.chem_delta}` : p.chem_delta;
  const stats = isManager ? '' : `<div class="stats">${Object.keys(STAT_LABELS).map(k =>
      `<div><span>${STAT_LABELS[k]}</span><b>${p.stats[k]}</b></div>`).join('')}</div>`;
  return `<button class="option ${isManager ? 'manager' : tier(p.ovr)}" data-id="${p.id}">
    <div class="opt-top">
      ${isManager ? '<span class="ovr small">DT</span>' : `<span class="ovr">${p.ovr}</span><span class="pos">${p.posicion}</span>`}
    </div>
    ${sprite(p)}
    <div class="opt-name">${esc(p.nombre)}</div>
    <div class="tags">
      <span class="tag saga">${esc(p.saga)}</span>
      ${p.arquetipo && p.arquetipo !== 'Unknown' ? `<span class="tag arq">${esc(p.arquetipo)}</span>` : ''}
      ${p.elemento ? `<span class="tag el-${p.elemento}">${ELEMENTS[p.elemento] || p.elemento}</span>` : ''}
    </div>
    <div class="teams">${p.equipos.length ? esc(p.equipos.slice(0, 3).join(' · ')) : '<span class="muted">Sin equipo registrado</span>'}</div>
    ${stats}
    ${p.slot_chem === null && !isManager ? '<div class="chem-preview">Suplente · cualquier posición</div>'
      : `<div class="chem-preview ${p.chem_delta > 0 ? 'up' : ''}">Química ${delta} → ${p.chem_after}/33</div>`}
  </button>`;
}

function showOffer(offer) {
  $('modal-title').textContent = offer.slot < 11 ? `Elige tu ${S.slots[offer.slot].label}` : `Elige tu suplente ${offer.slot - 10}`;
  $('options').innerHTML = offer.options.map(p => optionCard(p, false)).join('');
  $('modal').classList.remove('hidden');
  $('peek-reopen-btn').classList.add('hidden');
  document.querySelectorAll('#options .option').forEach(el => el.onclick = async () =>
    render(await api(`/api/slot/${offer.slot}/pick`, { player_id: +el.dataset.id })));
}

function showManagerOffer(offer) {
  $('modal-title').textContent = 'Elige tu entrenador';
  $('options').innerHTML = offer.options.map(p => optionCard(p, true)).join('');
  $('modal').classList.remove('hidden');
  $('peek-reopen-btn').classList.add('hidden');
  document.querySelectorAll('#options .option').forEach(el => el.onclick = async () =>
    render(await api('/api/manager/pick', { player_id: +el.dataset.id })));
}

// ------------------------------------------------------------------ peek
const peekBtn = $('peek-btn');
const peekReopenBtn = $('peek-reopen-btn');
if (peekBtn && peekReopenBtn) {
  peekBtn.onclick = () => {
    $('modal').classList.add('hidden');
    peekReopenBtn.classList.remove('hidden');
  };
  peekReopenBtn.onclick = () => {
    $('modal').classList.remove('hidden');
    peekReopenBtn.classList.add('hidden');
  };
}

// ---------------------------------------------------------------- finish
$('finish-btn').onclick = async () => {
  if (!confirmFinish()) return;
  const st = await api('/api/finish', {});
  render(st);
  showResult(st);
  loadHistory();
};

function confirmFinish() {
  const b = $('finish-btn');
  if (b.dataset.armed) { delete b.dataset.armed; return true; }
  b.dataset.armed = 1; b.textContent = '¿Seguro? Pulsa otra vez';
  setTimeout(() => { if (b.dataset.armed) { delete b.dataset.armed; render(S); } }, 3000);
  return false;
}

function showResult(st) {
  $('res-score').textContent = st.score;
  $('res-rating').textContent = st.rating;
  $('res-chem').textContent = `${st.chem}/33`;
  $('res-rank').textContent = st.rank ? `${st.rank.position}º de ${st.rank.total}` : '—';
  $('result').classList.remove('hidden');
}

async function loadHistory() {
  const h = await fetch('/draft/api/history').then(r => r.json());
  $('history').innerHTML = h.length ? h.slice(0, 7).map(d =>
    `<div class="hist"><span>${d.day.split('-').reverse().slice(0, 2).join('/')}</span><span>${d.chem}/33</span><b>${d.score}</b></div>`).join('')
    : 'Aún no has terminado ningún draft.';
}

// ---------------------------------------------------------------- countdown
function startCountdown(sec) {
  const end = Date.now() + sec * 1000;
  const tick = () => {
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    if (s === 0) return location.reload();
    const f = n => String(n).padStart(2, '0');
    $('countdown').textContent = `${f(Math.floor(s / 3600))}:${f(Math.floor(s / 60) % 60)}:${f(s % 60)}`;
  };
  tick(); setInterval(tick, 1000);
}

// ---------------------------------------------------------------- admin
const adminResetBtn = $('admin-reset-btn');
if (adminResetBtn) {
  adminResetBtn.onclick = async () => {
    if (!confirm('¿Reiniciar tu draft de hoy? Perderás el equipo que llevas hecho.')) return;
    swapFrom = null; swapPreview = null;
    render(await api('/api/admin/reset', {}));
    loadHistory();
    toast('Draft reiniciado.');
  };
}

(async () => {
  const st = await api('/api/state');
  render(st);
  startCountdown(st.next_in);
  loadHistory();
  if (st.finished) toast('Ya has jugado hoy. ¡Vuelve mañana!');
})();
