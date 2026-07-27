'use strict';

const state = {
  data: null,
  pairMap: new Map(),
  selectedDuelPlayer: 0,
  selectedRound: null,
  frameIndex: 0,
  playing: false,
  speed: 1,
  timer: null,
  eventTab: 'kills',
  floorMode: null,
  selectedEconomyRound: null,
  economyMetric: 'equip',
};

const TEAM_COLORS = ['#63b7ff', '#d78cff', '#ff9b54', '#58d8b2'];

// Valve's CS2 SVG silhouettes, mirrored by the community-maintained archive.
// Text labels remain visible when the viewer is offline or an asset is missing.
const CS2_ICON_ROOT = 'https://raw.githubusercontent.com/Juknum/counter-strike-icons/main/cs2/panorama/images/icons/equipment';
const EQUIPMENT_ICON_ALIASES = {
  'ak-47': 'ak47',
  'pp-bizon': 'bizon',
  'desert eagle': 'deagle',
  'dual berettas': 'elite',
  'five-seven': 'fiveseven',
  'galil ar': 'galilar',
  'glock-18': 'glock',
  'p2000': 'hkp2000',
  'm4a4': 'm4a1',
  'm4a1-s': 'm4a1_silencer',
  'mac-10': 'mac10',
  'm9 bayonet': 'knife_m9_bayonet',
  'bayonet': 'bayonet',
  'butterfly knife': 'knife_butterfly',
  'karambit': 'knife_karambit',
  'r8 revolver': 'revolver',
  'sawed-off': 'sawedoff',
  'sg 553': 'sg556',
  'ssg 08': 'ssg08',
  'tec-9': 'tec9',
  'ump-45': 'ump45',
  'usp-s': 'usp_silencer',
  'zeus x27': 'taser',
  'he grenade': 'hegrenade',
  'high explosive grenade': 'hegrenade',
  'smoke grenade': 'smokegrenade',
  'incendiary grenade': 'incgrenade',
  'decoy grenade': 'decoy',
  'kevlar': 'kevlar',
  'kevlar + helmet': 'assaultsuit',
  'defuse kit': 'defuser',
};
const EQUIPMENT_ICON_KEYS = new Set([
  'ak47', 'aug', 'awp', 'bizon', 'cz75a', 'deagle', 'decoy', 'defuser', 'elite',
  'famas', 'fiveseven', 'flashbang', 'g3sg1', 'galilar', 'glock', 'hegrenade',
  'hkp2000', 'incgrenade', 'inferno', 'kevlar', 'm249', 'm4a1', 'm4a1_silencer',
  'mac10', 'mag7', 'molotov', 'mp5sd', 'mp7', 'mp9', 'negev', 'nova', 'p250',
  'p90', 'revolver', 'sawedoff', 'scar20', 'sg556', 'smokegrenade', 'ssg08',
  'taser', 'tec9', 'ump45', 'usp_silencer', 'xm1014', 'armor', 'armor_helmet',
  'assaultsuit', 'helmet', 'vest', 'vesthelm', 'c4', 'planted_c4',
]);
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function fmt(value, digits = 0) {
  const n = Number(value ?? 0);
  return n.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

const sortCollator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' });

function sortValue(cell) {
  const explicit = cell?.dataset.sortValue ?? cell?.querySelector?.('[data-sort-value]')?.dataset.sortValue;
  const text = String(explicit ?? cell?.textContent ?? '')
    .replace(/[\u00a0\u202f]/g, ' ')
    .trim();
  if (!text || /^[—–-]+$/.test(text)) return { empty: true, type: 'text', value: '' };

  const duration = text.match(/^([+-]?\d+):([0-5]?\d)(?:\s*s)?$/i);
  if (duration) {
    return { empty: false, type: 'number', value: Number(duration[1]) * 60 + Number(duration[2]) };
  }

  // Duel cells are displayed as "kills : deaths". Sorting that column by the
  // first number matches the meaning of the row player against the opponent.
  const duelScore = text.match(/^([+-]?[\d\s.,]+)\s*:\s*[+-]?[\d\s.,]+$/);
  const numericText = (duelScore?.[1] ?? text)
    .replace(/[+%\s]/g, '')
    .replace('−', '-')
    .replace(',', '.');
  if (/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(numericText)) {
    return { empty: false, type: 'number', value: Number(numericText) };
  }
  return { empty: false, type: 'text', value: text };
}

function compareSortValues(left, right, direction) {
  if (left.empty !== right.empty) return left.empty ? 1 : -1;
  if (left.empty) return 0;
  let result;
  if (left.type === 'number' && right.type === 'number') result = left.value - right.value;
  else result = sortCollator.compare(String(left.value), String(right.value));
  return direction === 'ascending' ? result : -result;
}

function nextSortDirection(header, tbody, columnIndex) {
  const current = header.getAttribute('aria-sort');
  if (current === 'ascending') return 'descending';
  if (current === 'descending') return 'ascending';
  const sample = [...tbody.rows]
    .map(row => sortValue(row.cells[columnIndex]))
    .find(value => !value.empty);
  // Statistical columns are normally useful from the largest value down;
  // player/name columns start alphabetically.
  return sample?.type === 'number' ? 'descending' : 'ascending';
}

function updateSortHeader(header, direction) {
  header.setAttribute('aria-sort', direction);
  const indicator = $('.sort-indicator', header);
  if (indicator) indicator.textContent = direction === 'ascending' ? '↑' : direction === 'descending' ? '↓' : '↕';
  const button = $('.sort-button', header);
  if (button) {
    const action = direction === 'ascending' ? 'по убыванию' : 'по возрастанию';
    button.title = `Сортировать ${action}`;
  }
}

function sortTable(table, header, columnIndex) {
  const tbody = table.tBodies[0];
  if (!tbody || tbody.rows.length < 2) return;
  const direction = nextSortDirection(header, tbody, columnIndex);
  $$('thead th', table).forEach(item => updateSortHeader(item, item === header ? direction : 'none'));

  const rows = [...tbody.rows].map((row, index) => ({
    row,
    index: Number(row.dataset.originalOrder ?? index),
    value: sortValue(row.cells[columnIndex]),
  }));
  rows.forEach(({ row, index }) => { if (!row.dataset.originalOrder) row.dataset.originalOrder = String(index); });
  rows.sort((a, b) => compareSortValues(a.value, b.value, direction) || a.index - b.index);
  const fragment = document.createDocumentFragment();
  rows.forEach(({ row }) => fragment.appendChild(row));
  tbody.appendChild(fragment);
}

function setupSortableTables(root = document) {
  $$('table', root).forEach(table => {
    if (table.dataset.sortableReady === 'true') return;
    const headers = $$('thead th', table);
    if (!headers.length || !table.tBodies.length) return;
    table.dataset.sortableReady = 'true';
    table.classList.add('sortable-table');
    headers.forEach((header, columnIndex) => {
      const label = header.textContent.trim() || `Столбец ${columnIndex + 1}`;
      header.textContent = '';
      header.classList.add('sortable-column');
      header.setAttribute('aria-sort', 'none');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sort-button';
      button.innerHTML = `<span>${esc(label)}</span><span class="sort-indicator" aria-hidden="true">↕</span>`;
      button.setAttribute('aria-label', `${label}: сортировать таблицу`);
      button.title = 'Сортировать';
      button.addEventListener('click', () => sortTable(table, header, columnIndex));
      header.appendChild(button);
    });
  });
}

function initials(name) {
  const parts = String(name || '?').trim().split(/\s+|[_-]+/).filter(Boolean);
  return (parts.slice(0, 2).map(p => p[0]).join('') || '?').toUpperCase();
}

function equipmentIconKey(value) {
  const raw = String(value || '').trim().toLowerCase().replace(/^weapon_/, '');
  if (!raw || raw === '—' || raw === 'unknown') return '';
  const aliased = EQUIPMENT_ICON_ALIASES[raw];
  if (aliased) return aliased;
  const compact = raw.replaceAll(' ', '_').replaceAll('-', '_');
  if (EQUIPMENT_ICON_KEYS.has(compact)) return compact;
  if (compact.startsWith('knife_') || compact === 'knife') return compact;
  return '';
}

function equipmentIcon(value, className = '') {
  const key = equipmentIconKey(value);
  if (!key) return '';
  const classes = ['equipment-icon', className].filter(Boolean).join(' ');
  return `<img class="${classes}" src="${CS2_ICON_ROOT}/${encodeURIComponent(key)}.svg" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.hidden=true">`;
}

function weaponCell(value) {
  const label = String(value || '—');
  return `<span class="weapon-cell">${equipmentIcon(label, 'weapon-cell-icon')}<span>${esc(label)}</span></span>`;
}

function player(index) { return state.data.players[index]; }
function teamColor(teamName) {
  const index = Math.max(0, state.data.teams.findIndex(team => team.name === teamName));
  return TEAM_COLORS[index % TEAM_COLORS.length];
}
function playerColor(index) { return teamColor(player(index)?.team); }
function roundSideLabel(side) {
  return String(side || '').toLowerCase() === 'ct' ? 'CT' : String(side || '').toLowerCase() === 't' ? 'T' : String(side || '—').toUpperCase();
}
function roundOutcomeType(round) {
  const reason = String(round?.reason || '').toLowerCase();
  if (reason.includes('defus')) return 'defuse';
  if (reason.includes('explod') || reason.includes('bombed')) return 'explosion';
  return 'elimination';
}
function roundOutcomeLabel(round) {
  const outcome = roundOutcomeType(round);
  if (outcome === 'defuse') return 'Раздефуз';
  if (outcome === 'explosion') return 'Взрыв бомбы';
  const side = String(round?.reason || '').toLowerCase();
  if (side === 't_killed') return 'Уничтожены T';
  if (side === 'ct_killed') return 'Уничтожены CT';
  return 'Уничтожение команды';
}
function roundWinnerColor(round) {
  if (round?.winner_team) return teamColor(round.winner_team);
  return String(round?.winner_side || '').toLowerCase() === 't' ? '#d78cff' : '#63b7ff';
}
function roundOutcomeIcon(round) {
  const type = roundOutcomeType(round);
  if (type === 'defuse') {
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="9" width="11" height="8" rx="1.8"></rect><path d="M9 9V7.8a3 3 0 0 1 6 0V9"></path><path d="M12 12.2v2.8"></path><path d="M10.6 13.5h2.8"></path><path d="M18.4 12.1h1.9"></path><path d="M3.7 15.7l3.7-3.7"></path></svg>`;
  }
  if (type === 'explosion') {
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.4" y="8.6" width="8.3" height="9" rx="1.2"></rect><path d="M10.5 8.5V6.1"></path><path d="M10.5 6.1c0-1 1-1.6 1.8-1.1l2.6 1.7"></path><path d="M16 7.3l1.8-1.5"></path><path d="M18.7 8.2l-1 2.3"></path><path d="M18.9 12.1h2"></path><path d="M18.1 15.2l1.7 1.4"></path><path d="M15.5 16.8l.9 2.2"></path></svg>`;
  }
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.2c3.4 0 6 2.7 6 6 0 2.2-1.1 4.1-3 5.1v1.4l1.7 1.2v1.9H7.3V17.9l1.7-1.2v-1.4c-1.9-1-3-2.9-3-5.1 0-3.3 2.6-6 6-6Zm-2.1 5.3c-.9 0-1.6.8-1.6 1.7 0 1 .7 1.7 1.6 1.7.9 0 1.6-.7 1.6-1.7 0-.9-.7-1.7-1.6-1.7Zm4.2 0c-.9 0-1.6.8-1.6 1.7 0 1 .7 1.7 1.6 1.7.9 0 1.6-.7 1.6-1.7 0-.9-.7-1.7-1.6-1.7Zm-5.2 7h6.2"/></svg>`;
}
function roundOutcomeTooltip(round) {
  const winner = round?.winner_team || roundSideLabel(round?.winner_side);
  return `Раунд ${round?.number} · ${winner} · ${roundOutcomeLabel(round)}`;
}

function setupTabs() {
  $$('.tab').forEach(button => {
    button.addEventListener('click', () => {
      $$('.tab').forEach(tab => tab.classList.toggle('active', tab === button));
      $$('.panel-page').forEach(page => page.classList.remove('active'));
      $(`#page-${button.dataset.tab}`).classList.add('active');
      if (button.dataset.tab === 'insights') window.setTimeout(resizeRadar, 20);
    });
  });
}

function renderHero() {
  const data = state.data;
  const teams = data.teams.slice(0, 2);
  while (teams.length < 2) teams.push({ name: 'Неизвестная команда', score: 0, players: [] });
  const maxScore = Math.max(...teams.map(t => t.score));
  const mapLabel = data.match.map.replace(/^de_/, '').toUpperCase();

  $('#topbar-meta').innerHTML = `${esc(mapLabel)} · ${data.match.round_count} раундов · ${data.match.tickrate} tick`;
  $('#score-hero .hero-grid').innerHTML = `
    ${heroTeam(teams[0], 0, teams[0].score === maxScore && teams[0].score !== teams[1].score)}
    <div class="score-center">
      <span class="score-number ${teams[0].score === maxScore ? 'winner' : ''}">${teams[0].score}</span>
      <span class="score-separator">VS</span>
      <span class="score-number ${teams[1].score === maxScore ? 'winner' : ''}">${teams[1].score}</span>
    </div>
    ${heroTeam(teams[1], 1, teams[1].score === maxScore && teams[0].score !== teams[1].score, true)}
  `;
}

function heroTeam(team, index, winner, right = false) {
  const color = TEAM_COLORS[index % TEAM_COLORS.length];
  const best = state.data.players.filter(p => p.team === team.name).sort((a, b) => b.rating - a.rating)[0];
  return `
    <div class="team-hero ${right ? 'right' : ''}" style="--team-color:${color}">
      <div class="team-emblem">${esc(initials(team.name))}</div>
      <div class="team-title">
        <div class="result-label">${winner ? 'ПОБЕДИТЕЛЬ' : 'КОМАНДА'}</div>
        <h1 title="${esc(team.name)}">${esc(team.name)}</h1>
        <p>${best ? `Лучший: ${esc(best.name)} · ${fmt(best.rating, 2)}` : 'Нет данных игроков'}</p>
      </div>
    </div>`;
}

function renderValidation() {
  const failures = state.data.validation_failures || [];
  $('#validation-banner').innerHTML = failures.length ? `
    <div class="validation-banner"><strong>Внимание:</strong> парсер сохранил ${failures.length} замечаний валидации.
    ${esc(failures.slice(0, 2).map(f => `${f.check}: ${f.details}`).join(' · '))}</div>` : '';
}

function renderOverview() {
  const data = state.data;
  const mvp = data.players[0];
  const secondaryAwards = (data.awards || []).filter(award => award.title !== 'MVP').slice(0, 4);
  const awards = secondaryAwards.map(award => {
    const p = player(award.player);
    const digits = Number.isInteger(award.value) ? 0 : 1;
    return `<div class="award"><div class="award-profile"><span class="player-avatar">${esc(initials(p.name))}</span><div>
      <div class="award-player">${esc(p.name)}</div><div class="award-subtitle">${esc(award.subtitle)}</div></div></div>
      <div class="award-value">${fmt(award.value, digits)}</div></div>`;
  }).join('');
  const mvpSummary = mvp ? `<div class="mvp-layout">
    <article class="card mvp-card" style="--team-color:${teamColor(mvp.team)}">
      <div class="mvp-badge">★ MVP МАТЧА</div>
      <div class="mvp-main">
        <div class="mvp-avatar">${esc(initials(mvp.name))}</div>
        <div class="mvp-identity"><div class="mvp-name">${esc(mvp.name)}</div><div class="mvp-team">${esc(mvp.team)}</div></div>
        <div class="mvp-rating"><strong>${fmt(mvp.rating, 2)}</strong><span>Рейтинг</span></div>
      </div>
      <div class="mvp-mini-grid">
        <div class="mini-stat"><strong>${mvp.kills}/${mvp.deaths}/${mvp.assists}</strong><span>K / D / A</span></div>
        <div class="mini-stat"><strong>${mvp.round_swing >= 0 ? '+' : ''}${fmt(mvp.round_swing, 2)}%</strong><span>Swing</span></div>
        <div class="mini-stat"><strong>${fmt(mvp.adr, 1)}</strong><span>ADR</span></div>
        <div class="mini-stat"><strong>${fmt(mvp.kast, 1)}%</strong><span>KAST</span></div>
        <div class="mini-stat"><strong>${mvp.round_mvps}</strong><span>MVP раундов</span></div>
      </div>
    </article>
    <div class="card awards-grid">${awards || '<div class="empty-state">Нет дополнительных наград</div>'}</div>
  </div>` : '<div class="empty-state card">Нет данных player_stats.</div>';

  const tabs = [
    ['general', 'Общее'],
    ['advanced', 'Продвинутый'],
    ['entries', 'Первых'],
    ['trades', 'Размен'],
    ['clutches', 'Клатчей'],
  ];
  const nav = `<div class="stats-subtabs" role="tablist">${tabs.map(([id, label], index) =>
    `<button class="stats-subtab ${index === 0 ? 'active' : ''}" data-stats-tab="${id}">${label}</button>`
  ).join('')}</div>`;
  const panels = tabs.map(([id], index) =>
    `<section class="stats-panel ${index === 0 ? 'active' : ''}" data-stats-panel="${id}">${renderStatsPanel(id)}</section>`
  ).join('');
  $('#overview-content').innerHTML = mvpSummary + nav + panels;
  $$('.stats-subtab').forEach(button => button.addEventListener('click', () => {
    $$('.stats-subtab').forEach(item => item.classList.toggle('active', item === button));
    $$('.stats-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.statsPanel === button.dataset.statsTab));
  }));
}

function renderStatsPanel(kind) {
  return state.data.teams.slice(0, 2).map((team, teamIndex) => {
    const roster = state.data.players.filter(p => p.team === team.name);
    const table = statsTable(kind, roster);
    return `<article class="card team-block faceit-block" style="--team-color:${TEAM_COLORS[teamIndex]}">
      <div class="team-block-head"><div class="team-name"><span class="team-color"></span><strong class="team-score-inline">${team.score}</strong>${esc(team.name)}</div>
      <div class="team-score-small">Средний рейтинг ${fmt(team.avg_rating, 2)} · ${state.data.match.round_count} раундов</div></div>
      <div class="table-wrap">${table}</div></article>`;
  }).join('');
}

function statsTable(kind, roster) {
  const playerHead = '<th>Игрок</th>';
  const configs = {
    general: {
      head: `${playerHead}<th>Рейтинг</th><th>Swing</th><th>K</th><th>D</th><th>A</th><th>СУ/Р</th><th>У/С</th><th>У/Р</th><th>HS</th><th>HS %</th><th>5K</th><th>4K</th><th>3K</th><th>2K</th><th>MVP</th>`,
      row: generalStatsRow,
    },
    advanced: {
      head: `${playerHead}<th>RWS</th><th>KAST</th><th>Acc</th><th>S. точность</th><th>Выстрелы</th><th>Hits</th><th>MK</th><th>MK%</th><th>RP</th><th>RS</th>`,
      row: advancedStatsRow,
    },
    entries: {
      head: `${playerHead}<th>Попыток энтри</th><th>Entry Kills</th><th>Entry Deaths</th><th>Entry Difference</th><th>Попыток энтри %</th><th>Успешность энтри, %</th>`,
      row: entryStatsRow,
    },
    trades: {
      head: `${playerHead}<th>Убийств разменом</th><th>Разменов после смерти</th><th>Traded Entry Kills</th><th>Traded Entry Deaths</th>`,
      row: tradeStatsRow,
    },
    clutches: {
      head: `${playerHead}<th>Побед в клатчах</th><th>Клатч-раундов проиграно</th><th>Успех в клатчах</th><th>Победы 1в5</th><th>Победы 1в4</th><th>Победы 1в3</th><th>Победы 1в2</th><th>Победы 1в1</th>`,
      row: clutchStatsRow,
    },
  };
  const config = configs[kind];
  return `<table class="overview-table faceit-table stats-table-${esc(kind)}"><thead><tr>${config.head}</tr></thead><tbody>${roster.map(config.row).join('')}</tbody></table>`;
}

function playerIdentityCells(p) {
  return `<td><div class="player-cell" data-sort-value="${esc(p.name)}"><span class="player-avatar">${esc(initials(p.name))}</span><span class="player-name">${esc(p.name)}</span></div></td>`;
}

function generalStatsRow(p) {
  const low = p.rating < 1 ? 'low' : '';
  const deltaClass = p.round_swing >= 0 ? 'pos' : 'neg';
  const sign = p.round_swing >= 0 ? '+' : '';
  return `<tr>${playerIdentityCells(p)}
    <td><span class="rating-chip ${low}">${fmt(p.rating, 2)}</span></td><td class="delta ${deltaClass}">${sign}${fmt(p.round_swing, 2)}%</td>
    <td>${p.kills}</td><td>${p.deaths}</td><td>${p.assists}</td><td>${fmt(p.adr, 1)}</td><td>${fmt(p.kd, 2)}</td><td>${fmt(p.kills_per_round, 2)}</td>
    <td>${p.headshots}</td><td>${fmt(p.headshot_pct, 1)}%</td><td>${p.multi_kill_5k}</td><td>${p.multi_kill_4k}</td><td>${p.multi_kill_3k}</td><td>${p.multi_kill_2k}</td><td>${p.round_mvps}</td></tr>`;
}

function advancedStatsRow(p) {
  return `<tr>${playerIdentityCells(p)}<td>${fmt(p.rws, 2)}</td><td>${fmt(p.kast, 1)}%</td><td>${fmt(p.accuracy, 1)}%</td><td>${fmt(p.single_shot_accuracy, 1)}%</td><td>${p.shots}</td><td>${p.hits}</td>
    <td>${p.multi_kill_rounds}</td><td>${fmt(p.multi_kill_pct, 0)}%</td><td>${p.rounds}</td><td>${p.rounds_survived}</td></tr>`;
}

function entryStatsRow(p) {
  return `<tr>${playerIdentityCells(p)}<td>${p.entry_attempts}</td><td>${p.opening_kills}</td><td>${p.opening_deaths}</td>
    <td class="delta ${p.entry_difference >= 0 ? 'pos' : 'neg'}">${p.entry_difference}</td><td>${fmt(p.entry_attempt_pct, 0)}%</td><td>${fmt(p.entry_success_pct, 0)}%</td></tr>`;
}

function tradeStatsRow(p) {
  return `<tr>${playerIdentityCells(p)}<td>${p.trade_kills}</td><td>${p.deaths_traded}</td><td>${p.traded_entry_kills}</td><td>${p.traded_entry_deaths}</td></tr>`;
}

function clutchStatsRow(p) {
  return `<tr>${playerIdentityCells(p)}<td>${p.clutches_won}</td><td>${p.clutch_losses}</td><td>${fmt(p.clutch_success_pct, 0)}%</td>
    <td>${p.clutch_1v5}</td><td>${p.clutch_1v4}</td><td>${p.clutch_1v3}</td><td>${p.clutch_1v2}</td><td>${p.clutch_1v1}</td></tr>`;
}

function buildPairMap() {
  state.pairMap.clear();
  state.data.duels.pairs.forEach(([a, v, count]) => state.pairMap.set(`${a}:${v}`, count));
}
function pair(a, v) { return state.pairMap.get(`${a}:${v}`) || 0; }

function renderDuels() {
  const options = state.data.players.map((p, i) => `<option value="${i}">${esc(p.name)} · ${esc(p.team)}</option>`).join('');
  const headers = state.data.players.map(p => `<th title="${esc(p.name)}">${esc(p.name)}</th>`).join('');
  const rows = state.data.players.map((p, i) => {
    const cells = state.data.players.map((opponent, j) => {
      if (i === j || p.team === opponent.team) return '<td class="duel-cell duel-na">—</td>';
      const wins = pair(i, j); const losses = pair(j, i);
      const intensity = state.data.duels.max_pair ? Math.min(.28, wins / state.data.duels.max_pair * .28) : 0;
      return `<td class="duel-cell" style="background:rgba(37,208,125,${intensity})"><span>${wins}</span> : <small>${losses}</small></td>`;
    }).join('');
    return `<tr><td><div class="player-cell" data-sort-value="${esc(p.name)}"><span class="player-avatar">${esc(initials(p.name))}</span>${esc(p.name)}</div></td>${cells}</tr>`;
  }).join('');

  $('#duels-content').innerHTML = `
    <div class="card duel-controls">
      <div class="field"><label>Выбрать игрока</label><select id="duel-player-select">${options}</select></div>
      <div class="muted">Матрица показывает убийства игрока против каждого соперника.</div>
    </div>
    <div class="duel-summary" id="duel-summary"></div>
    <div class="card duel-matrix"><table><thead><tr><th>Игрок / соперник</th>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`;

  $('#duel-player-select').value = String(state.selectedDuelPlayer);
  $('#duel-player-select').addEventListener('change', event => {
    state.selectedDuelPlayer = Number(event.target.value);
    renderDuelSummary();
  });
  renderDuelSummary();
}

function renderDuelSummary() {
  const index = state.selectedDuelPlayer;
  const p = player(index);
  const opponents = state.data.players
    .map((op, i) => ({ ...op, index: i, wins: pair(index, i), losses: pair(i, index) }))
    .filter(op => op.team !== p.team)
    .sort((a, b) => (b.wins + b.losses) - (a.wins + a.losses));
  const wins = opponents.reduce((sum, op) => sum + op.wins, 0);
  const losses = opponents.reduce((sum, op) => sum + op.losses, 0);
  $('#duel-summary').innerHTML = `
    <article class="card selected-player-card">
      <h3>${esc(p.name)}</h3><p>${esc(p.team)}</p>
      <div class="big-duel-score"><strong>${wins}</strong><span>:</span><strong>${losses}</strong></div>
      <p>суммарный счёт личных дуэлей</p>
    </article>
    <article class="card opponent-list">
      ${opponents.map(op => `<div class="opponent-row"><span>${esc(op.name)}</span><strong>${op.wins} убийств</strong><em>${op.losses} смертей</em></div>`).join('') || '<div class="empty-state">Нет соперников</div>'}
    </article>`;
}

function renderUtility() {
  const teamOrder = new Map(state.data.teams.map((team, index) => [team.name, index]));
  const rows = state.data.utility.players.slice().sort((a, b) => {
    const pa = player(a.player), pb = player(b.player);
    const teamDiff = (teamOrder.get(pa.team) ?? 99) - (teamOrder.get(pb.team) ?? 99);
    return teamDiff || (pb.rating - pa.rating) || pa.name.localeCompare(pb.name, 'ru');
  });
  const totals = rows.reduce((acc, row) => {
    ['total','unused_total','successful_grenades','damage','enemies_flashed','enemy_flash_duration'].forEach(key => acc[key] += Number(row[key] || 0));
    return acc;
  }, {total:0, unused_total:0, successful_grenades:0, damage:0, enemies_flashed:0, enemy_flash_duration:0});
  const teamTotals = new Map();
  rows.forEach(row => {
    const p = player(row.player);
    const current = teamTotals.get(p.team) || {team:p.team,total:0,flash:0,smoke:0,he:0,fire:0,decoy:0};
    ['total','flash','smoke','he','fire','decoy'].forEach(key => current[key] += Number(row[key] || 0));
    teamTotals.set(p.team, current);
  });

  $('#utility-content').innerHTML = `
    <div class="utility-summary">
      <div class="card utility-kpi"><span>Гранат использовано</span><strong>${fmt(totals.total)}</strong></div>
      <div class="card utility-kpi"><span>Не использовано</span><strong>${fmt(totals.unused_total)}</strong></div>
      <div class="card utility-kpi"><span>Успешных гранат</span><strong>${fmt(totals.successful_grenades)}</strong></div>
      <div class="card utility-kpi"><span>Урон снаряжением</span><strong>${fmt(totals.damage)}</strong></div>
    </div>
    <div class="utility-team-summaries">${Array.from(teamTotals.values()).map(utilityTeamCard).join('')}</div>
    <div class="utility-subtabs card">
      <button class="utility-subtab active" data-utility-tab="general">Общее</button>
      <button class="utility-subtab" data-utility-tab="damage">Урон</button>
      <button class="utility-subtab" data-utility-tab="support">Поддержка</button>
    </div>
    <div class="utility-detail active" id="utility-general">${utilityTablesByTeam(rows, utilityGeneralTable)}</div>
    <div class="utility-detail" id="utility-damage">${utilityTablesByTeam(rows, utilityDamageTable)}</div>
    <div class="utility-detail" id="utility-support">${utilityTablesByTeam(rows, utilitySupportTable)}</div>`;

  $$('.utility-subtab').forEach(button => button.addEventListener('click', () => {
    $$('.utility-subtab').forEach(tab => tab.classList.toggle('active', tab === button));
    $$('.utility-detail').forEach(panel => panel.classList.toggle('active', panel.id === `utility-${button.dataset.utilityTab}`));
  }));
}

function utilityTeamCard(team) {
  return `<article class="card utility-team-card"><div><span>Командный итог</span><h3>${esc(team.team)}</h3></div><strong>${fmt(team.total)}</strong>
    <div class="utility-team-breakdown"><span><i class="utility-segment smoke"></i>Smoke ${team.smoke}</span>
    <span><i class="utility-segment flash"></i>Flash ${team.flash}</span><span><i class="utility-segment he"></i>HE ${team.he}</span>
    <span><i class="utility-segment fire"></i>Fire ${team.fire}</span><span><i class="utility-segment decoy"></i>Decoy ${team.decoy}</span></div></article>`;
}

function utilityPlayerCell(row) {
  const p = player(row.player);
  return `<div class="player-cell" data-sort-value="${esc(p.name)}"><span class="player-avatar">${esc(initials(p.name))}</span><span><b>${esc(p.name)}</b><small>${esc(p.team)}</small></span></div>`;
}
function sec(value) {
  // FACEIT's displayed support durations normalize values that land within a
  // few Source-2 ticks of the next whole second (for example 7.962 -> 0:08).
  // Keep ordinary values truncated, but absorb sub-50 ms parser jitter.
  return `${formatTime(Math.floor(Number(value || 0) + 0.05))}`;
}
function utilityTablesByTeam(rows, tableRenderer) {
  return state.data.teams.slice(0, 2).map((team, teamIndex) => {
    const teamRows = rows.filter(row => player(row.player)?.team === team.name);
    const grenades = teamRows.reduce((sum, row) => sum + Number(row.total || 0), 0);
    return `<article class="card team-block utility-team-block" style="--team-color:${TEAM_COLORS[teamIndex % TEAM_COLORS.length]}">
      <div class="team-block-head"><div class="team-name"><span class="team-color"></span>${esc(team.name)}</div>
      <div class="team-score-small">${teamRows.length} игроков · ${fmt(grenades)} гранат использовано</div></div>
      <div class="table-wrap">${tableRenderer(teamRows)}</div></article>`;
  }).join('');
}
function utilityShell(headers, body) {
  return `<table class="utility-table"><thead><tr><th>Игрок</th>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>`;
}
function utilityGeneralTable(rows) {
  const headers = ['Не использовано','Использовано','Успешно','Общий урон','Получено урона','Урон команде','От команды','HE','Flash','Smoke','Fire','Decoy','Врагов осл.','Время осл.','Тиммейтов осл.','Время тимм.'];
  const body = rows.map(r => `<tr><td>${utilityPlayerCell(r)}</td><td>${r.unused_total}</td><td>${r.total}</td><td>${r.successful_grenades}</td>
    <td>${fmt(r.damage)}</td><td>${fmt(r.damage_received)}</td><td>${fmt(r.team_damage)}</td><td>${fmt(r.team_damage_received)}</td>
    <td>${r.he}</td><td>${r.flash}</td><td>${r.smoke}</td><td>${r.fire}</td><td>${r.decoy}</td>
    <td>${r.enemies_flashed}</td><td>${sec(r.enemy_flash_duration)}</td><td>${r.teammates_flashed}</td><td>${sec(r.teammate_flash_duration)}</td></tr>`).join('');
  return utilityShell(headers, body);
}
function utilityDamageTable(rows) {
  const headers = ['HE урон','HE получено','HE команде','HE от команды','Unused HE','HE броски','Успешные HE','Fire урон','Fire получено','Fire команде','Fire от команды','Unused Fire','Fire броски','Успешный Fire'];
  const body = rows.map(r => `<tr><td>${utilityPlayerCell(r)}</td><td>${fmt(r.he_damage)}</td><td>${fmt(r.he_damage_received)}</td><td>${fmt(r.he_team_damage)}</td><td>${fmt(r.he_team_received)}</td>
    <td>${r.unused_he}</td><td>${r.he}</td><td>${r.successful_he}</td><td>${fmt(r.fire_damage)}</td><td>${fmt(r.fire_damage_received)}</td>
    <td>${fmt(r.fire_team_damage)}</td><td>${fmt(r.fire_team_received)}</td><td>${r.unused_fire}</td><td>${r.fire}</td><td>${r.successful_fire}</td></tr>`).join('');
  return utilityShell(headers, body);
}
function utilitySupportTable(rows) {
  const headers = ['Flash броски','Успешные','Flash assists','Blind kills','Врагов осл.','Время врагов','Self','Self time','Осл. врагом','Время врагом','Тиммейтов осл.','Время тимм.','Осл. командой','Время командой','Unused Flash','Smoke','Unused Smoke','Decoy','Unused Decoy'];
  const body = rows.map(r => `<tr><td>${utilityPlayerCell(r)}</td><td>${r.flash}</td><td>${r.successful_flash}</td><td>${r.flash_assists}</td><td>${r.blind_kills}</td>
    <td>${r.enemies_flashed}</td><td>${sec(r.enemy_flash_duration)}</td><td>${r.self_flashed}</td><td>${sec(r.self_flash_duration)}</td>
    <td>${r.flashed_by_enemy}</td><td>${sec(r.flashed_by_enemy_duration)}</td><td>${r.teammates_flashed}</td><td>${sec(r.teammate_flash_duration)}</td>
    <td>${r.flashed_by_team}</td><td>${sec(r.flashed_by_team_duration)}</td><td>${r.unused_flash}</td><td>${r.smoke}</td><td>${r.unused_smoke}</td><td>${r.decoy}</td><td>${r.unused_decoy}</td></tr>`).join('');
  return utilityShell(headers, body);
}


function money(value) {
  return `$${fmt(Number(value || 0))}`;
}

function economyBuyLabel(type) {
  return ({ pistol: 'Пистолетный', eco: 'Эко', force: 'Форс', full: 'Полный закуп' })[String(type || '').toLowerCase()] || 'Не определён';
}

function economyBuyClass(type) {
  const value = String(type || '').toLowerCase();
  return ['pistol', 'eco', 'force', 'full'].includes(value) ? value : 'unknown';
}

function economyRound() {
  const rounds = state.data.economy?.rounds || [];
  return rounds.find(round => round.number === state.selectedEconomyRound) || rounds[0] || null;
}

function economyChartSvg() {
  const rounds = state.data.economy?.rounds || [];
  const teams = state.data.teams.slice(0, 2);
  if (!rounds.length || !teams.length) return '<div class="empty-state">Нет данных экономики по раундам.</div>';
  const metric = state.economyMetric === 'money' ? 'money' : 'equip';
  const width = 1120, height = 330, left = 72, right = 24, top = 24, bottom = 50;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const values = rounds.flatMap(round => round.teams.map(team => Number(team[metric] || 0)));
  const rawMax = Math.max(10000, ...values);
  const step = Math.max(2000, Math.ceil(rawMax / 5 / 5000) * 5000);
  const maxValue = step * 5;
  const x = index => left + (rounds.length === 1 ? plotWidth / 2 : index * plotWidth / (rounds.length - 1));
  const y = value => top + plotHeight - Math.max(0, Math.min(maxValue, Number(value || 0))) / maxValue * plotHeight;
  const grid = Array.from({length: 6}, (_, index) => {
    const value = index * step;
    const yy = y(value);
    return `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="economy-grid-line"></line>
      <text x="${left - 12}" y="${yy + 4}" text-anchor="end" class="economy-axis-label">$${Math.round(value / 1000)}k</text>`;
  }).join('');
  const roundLabels = rounds.map((round, index) => `<text x="${x(index)}" y="${height - 20}" text-anchor="middle" class="economy-round-label">${round.number}</text>`).join('');
  const halfIndex = rounds.findIndex(round => round.number === 13);
  const halftime = halfIndex > 0 ? `<line x1="${(x(halfIndex - 1) + x(halfIndex)) / 2}" y1="${top}" x2="${(x(halfIndex - 1) + x(halfIndex)) / 2}" y2="${top + plotHeight}" class="economy-half-line"></line>` : '';
  const series = teams.map((team, teamIndex) => {
    const points = rounds.map((round, index) => {
      const row = round.teams.find(item => item.name === team.name);
      return { index, round: round.number, value: Number(row?.[metric] || 0), buy: row?.buy_type || 'unknown' };
    });
    const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    const dots = points.map(point => `<circle cx="${x(point.index)}" cy="${y(point.value)}" r="4" style="--series-color:${teamColor(team.name)}">
      <title>Раунд ${point.round}: ${team.name} — ${money(point.value)} · ${economyBuyLabel(point.buy)}</title></circle>`).join('');
    return `<path d="${path}" class="economy-series-line" style="--series-color:${teamColor(team.name)}"></path>${dots}`;
  }).join('');
  return `<svg class="economy-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="График экономики команд">
    ${grid}${halftime}${series}${roundLabels}
    <text x="${left}" y="${height - 3}" class="economy-axis-title">Раунд</text>
  </svg>`;
}

function economySummaryCard(summary, teamIndex) {
  const team = state.data.teams.find(item => item.name === summary.team) || { name: summary.team };
  return `<article class="card economy-summary-card" style="--team-color:${teamColor(team.name)}">
    <header><span class="team-color"></span><strong>${esc(team.name)}</strong></header>
    <div class="economy-summary-values"><div><span>Средний закуп</span><b>${money(summary.avg_equip)}</b></div><div><span>Остаток денег</span><b>${money(summary.avg_money)}</b></div></div>
    <div class="economy-buy-counts"><span class="buy-chip full">Full ${summary.full}</span><span class="buy-chip force">Force ${summary.force}</span><span class="buy-chip eco">Eco ${summary.eco}</span><span class="buy-chip pistol">Pistol ${summary.pistol}</span></div>
  </article>`;
}

function isFreeEconomyItem(item) {
  const value = String(item?.name || item || '').trim().toLowerCase();
  return /(?:knife|bayonet|karambit|daggers?|kukri|stiletto|talon|ursus|navaja|paracord|nomad|bowie|falchion|huntsman)/.test(value);
}

function economyLoadout(items) {
  const paidItems = (items || []).filter(item => !isFreeEconomyItem(item));
  if (!paidItems.length) return '<span class="muted">Нет данных</span>';
  return `<div class="weapon-loadout">${paidItems.map(item => `<span class="weapon-chip ${esc(item.type)}">${equipmentIcon(item.name, 'weapon-chip-icon')}<span>${esc(item.name)}${Number(item.count || 1) > 1 ? ` ×${item.count}` : ''}</span></span>`).join('')}</div>`;
}

function economyTeamTable(team, teamIndex) {
  const rows = team.players || [];
  const body = rows.map(row => {
    const p = player(row.player);
    return `<tr>
      <td><div class="player-cell"><span class="player-avatar">${esc(initials(p?.name || '?'))}</span><span><b>${esc(p?.name || 'Unknown')}</b><small>${esc(team.name)}</small></span></div></td>
      <td>${esc(String(row.side || '—').toUpperCase())}</td>
      <td data-sort-value="${row.money}"><strong>${money(row.money)}</strong></td>
      <td data-sort-value="${row.equip}">${money(row.equip)}</td>
      <td>${weaponCell(row.primary)}</td><td>${weaponCell(row.pistol)}</td>
      <td>${economyLoadout(row.items)}</td>
    </tr>`;
  }).join('');
  return `<article class="card team-block economy-team-block" style="--team-color:${teamColor(team.name)}">
    <div class="team-block-head economy-team-head"><div class="team-name"><span class="team-color"></span>${esc(team.name)} <em>${esc(String(team.side || '').toUpperCase())}</em></div>
      <div class="economy-team-meta"><span class="buy-chip ${economyBuyClass(team.buy_type)}">${economyBuyLabel(team.buy_type)}</span><b>${money(team.equip)} закуп</b><span>${money(team.money)} остаток</span></div></div>
    <div class="table-wrap"><table class="economy-player-table"><thead><tr><th>Игрок</th><th>Сторона</th><th>Деньги</th><th>Стоимость</th><th>Основное</th><th>Пистолет</th><th>Оружие и снаряжение</th></tr></thead><tbody>${body}</tbody></table></div>
  </article>`;
}

function renderEconomyRoundTables() {
  const container = $('#economy-round-tables');
  const round = economyRound();
  if (!container) return;
  if (!round) {
    container.innerHTML = '<article class="card empty-state">Нет покадровых данных экономики.</article>';
    return;
  }
  const orderedTeams = state.data.teams.slice(0, 2).map(team => round.teams.find(item => item.name === team.name)).filter(Boolean);
  container.innerHTML = orderedTeams.map((team, index) => economyTeamTable(team, index)).join('');
  setupSortableTables(container);
}

function renderEconomyChart() {
  const chart = $('#economy-chart-area');
  if (chart) chart.innerHTML = economyChartSvg();
  $$('.economy-metric-button').forEach(button => button.classList.toggle('active', button.dataset.economyMetric === state.economyMetric));
}

function renderEconomy() {
  const model = state.data.economy || { rounds: [], summaries: [] };
  const rounds = model.rounds || [];
  if (!rounds.length) {
    $('#economy-content').innerHTML = '<article class="card empty-state">В демке нет данных balance/current_equip_value для экономики.</article>';
    return;
  }
  if (state.selectedEconomyRound == null || !rounds.some(round => round.number === state.selectedEconomyRound)) state.selectedEconomyRound = rounds[0].number;
  const summaries = state.data.teams.slice(0, 2).map(team => model.summaries.find(summary => summary.team === team.name)).filter(Boolean);
  $('#economy-content').innerHTML = `
    <div class="economy-summary-grid">${summaries.map(economySummaryCard).join('')}</div>
    <article class="card economy-chart-card">
      <div class="economy-chart-head"><div><span class="eyebrow">ПО РАУНДАМ</span><h3>Экономика команд</h3></div>
        <div class="economy-metric-switch"><button class="economy-metric-button active" data-economy-metric="equip">Стоимость закупа</button><button class="economy-metric-button" data-economy-metric="money">Деньги после закупа</button></div></div>
      <div class="economy-chart-legend">${state.data.teams.slice(0, 2).map(team => `<span><i style="background:${teamColor(team.name)}"></i>${esc(team.name)}</span>`).join('')}</div>
      <div id="economy-chart-area">${economyChartSvg()}</div>
    </article>
    <div class="economy-round-toolbar card"><div><span class="eyebrow">СОСТАВ ЗАКУПА</span><h3>Оружие игроков</h3></div>
      <label>Раунд <select id="economy-round-select">${rounds.map(round => `<option value="${round.number}" ${round.number === state.selectedEconomyRound ? 'selected' : ''}>${round.number}</option>`).join('')}</select></label></div>
    <div id="economy-round-tables"></div>`;
  $$('.economy-metric-button').forEach(button => button.addEventListener('click', () => {
    state.economyMetric = button.dataset.economyMetric;
    renderEconomyChart();
  }));
  $('#economy-round-select').addEventListener('change', event => {
    state.selectedEconomyRound = Number(event.target.value);
    renderEconomyRoundTables();
  });
  renderEconomyRoundTables();
}

function radarLevelsModel() {
  const model = state.data.map_levels;
  if (model?.levels?.length) return model;
  return { mode: 'single', default: 'upper', split_z: null, transition_z: 0,
    levels: [{ id: 'upper', label: 'Карта', short_label: 'Карта', radar_file: 'radar.png' }] };
}
function isMultiLevelRadar() { const model = radarLevelsModel(); return model.mode === 'split' && model.levels.length > 1; }
function radarSource(level) {
  const embedded = window.__CS2_RADARS__;
  if (embedded && typeof embedded === 'object' && embedded[level.id]) return embedded[level.id];
  if (level.id === 'upper' && window.__CS2_RADAR__) return window.__CS2_RADAR__;
  return level.radar_file || (level.id === 'upper' ? 'radar.png' : `radar_${level.id}.png`);
}
function levelAlphaForZ(levelId, z) {
  const model = radarLevelsModel();
  if (model.mode !== 'split' || model.levels.length < 2) return 1;
  const split = Number(model.split_z ?? -500), transition = Math.max(0, Number(model.transition_z || 0)), height = Number(z);
  if (!Number.isFinite(height)) return levelId === 'upper' ? 1 : 0;
  if (transition <= 0) return levelId === 'upper' ? (height >= split ? 1 : 0) : (height < split ? 1 : 0);
  if (height >= split + transition) return levelId === 'upper' ? 1 : 0;
  if (height <= split - transition) return levelId === 'lower' ? 1 : 0;
  const upper = Math.max(0, Math.min(1, (height - (split - transition)) / (transition * 2)));
  return levelId === 'upper' ? upper : 1 - upper;
}
function applyFloorMode() {
  const model = radarLevelsModel(), validModes = new Set(model.levels.map(level => level.id));
  if (isMultiLevelRadar()) validModes.add('both');
  if (!state.floorMode || !validModes.has(state.floorMode)) state.floorMode = isMultiLevelRadar() ? (model.default || 'both') : model.levels[0].id;
  const stages = $('#radar-stages'); if (!stages) return;
  stages.dataset.mode = state.floorMode;
  $$('.radar-stage', stages).forEach(stage => { stage.hidden = state.floorMode !== 'both' && stage.dataset.level !== state.floorMode; });
  $$('.floor-button').forEach(button => button.classList.toggle('active', button.dataset.floorMode === state.floorMode));
  window.requestAnimationFrame(resizeRadar);
}

function renderInsights() {
  const rounds = state.data.rounds;
  if (!rounds.length) { $('#insights-content').innerHTML = '<div class="card empty-state">Нет данных раундов.</div>'; return; }
  if (state.selectedRound === null) state.selectedRound = rounds[0].number;
  const buttons = rounds.map(round => `
    <button class="round-button" data-round="${round.number}" style="--round-win-color:${roundWinnerColor(round)}" title="${esc(roundOutcomeTooltip(round))}">
      <span>${round.number}</span>
      <small class="round-icon round-icon-${roundOutcomeType(round)}" aria-label="${esc(roundOutcomeLabel(round))}">${roundOutcomeIcon(round)}</small>
    </button>
  `).join('');
  const noPositions = !state.data.match.has_positions, levelModel = radarLevelsModel(), multiLevel = isMultiLevelRadar();
  if (!state.floorMode) state.floorMode = multiLevel ? (levelModel.default || 'both') : levelModel.levels[0].id;
  const floorControls = multiLevel ? `<div class="radar-floor-toolbar"><div><strong>Этаж Nuke</strong><span>Z-разделение: ${fmt(levelModel.split_z, 0)}</span></div><div class="floor-buttons"><button class="floor-button" data-floor-mode="both">Оба</button>${levelModel.levels.map(level => `<button class="floor-button" data-floor-mode="${esc(level.id)}">${esc(level.short_label || level.label)}</button>`).join('')}</div></div>` : '';
  const radarStages = levelModel.levels.map(level => `<div class="radar-stage" data-level="${esc(level.id)}">${multiLevel ? `<span class="radar-level-label">${esc(level.label)}</span>` : ''}<img class="radar-image" data-level="${esc(level.id)}" src="${esc(radarSource(level))}" alt="${esc(level.label)}"><canvas class="radar-canvas" data-level="${esc(level.id)}"></canvas></div>`).join('');
  $('#insights-content').innerHTML = `${noPositions ? `<div class="radar-warning"><strong>Радар загружен, но траектории игроков отсутствуют.</strong> Этот матч был распарсен без spatial samples.</div>` : ''}<div class="card round-strip">${buttons}</div><div class="insights-grid"><aside class="round-roster-panel" id="round-rosters"></aside><article class="card radar-card ${multiLevel ? 'multi-level-radar' : ''}">${floorControls}<div class="radar-stages" id="radar-stages" data-mode="${esc(state.floorMode)}">${radarStages}</div><div class="playback-controls"><button class="control-button" id="play-button" title="Воспроизведение">▶</button><button class="control-button" id="speed-button" title="Скорость">1×</button><input id="frame-slider" type="range" min="0" max="0" value="0" step="1"><span class="time-label" id="time-label">00:00</span></div></article><aside class="round-side-panel"><article class="card round-summary-card" id="round-summary"></article><article class="card event-card"><div class="event-tabs"><button class="event-tab active" data-event-tab="kills">Убийства</button><button class="event-tab" data-event-tab="utility">Гранаты</button></div><div class="kill-feed" id="event-feed"></div></article></aside></div>`;
  $$('.round-button').forEach(button => button.addEventListener('click', () => selectRound(Number(button.dataset.round))));
  $('#play-button').addEventListener('click', togglePlayback);
  $('#speed-button').addEventListener('click', () => { state.speed = state.speed === 1 ? 2 : state.speed === 2 ? .5 : 1; $('#speed-button').textContent = `${state.speed}×`; if (state.playing) { stopPlayback(); startPlayback(); } });
  $('#frame-slider').addEventListener('input', event => { state.frameIndex = Number(event.target.value); drawCurrentFrame(); });
  $$('.event-tab').forEach(button => button.addEventListener('click', () => { state.eventTab = button.dataset.eventTab; $$('.event-tab').forEach(tab => tab.classList.toggle('active', tab === button)); drawCurrentFrame(); }));
  $$('.floor-button').forEach(button => button.addEventListener('click', () => { state.floorMode = button.dataset.floorMode; applyFloorMode(); }));
  $$('.radar-image').forEach(radarImage => radarImage.addEventListener('error', () => { radarImage.style.display = 'none'; const stage = radarImage.closest('.radar-stage'); stage?.classList.add('radar-load-error'); stage?.setAttribute('data-error', `Не удалось загрузить ${radarImage.dataset.level || ''} радар`); }));
  if (noPositions) { $('#play-button').disabled = true; $('#speed-button').disabled = true; }
  window.addEventListener('resize', resizeRadar, { passive: true }); applyFloorMode(); selectRound(state.selectedRound);
}

function selectedRoundData() { return state.data.rounds.find(r => r.number === state.selectedRound); }
function currentFrames() { return state.data.frames[String(state.selectedRound)] || []; }
function currentKills() { return state.data.kills[String(state.selectedRound)] || []; }
function currentUtility() { return state.data.utility_events?.[String(state.selectedRound)] || []; }
function currentRoundPlayers() { return state.data.round_players?.[String(state.selectedRound)] || { players: [], start_probability: {}, team_swing: {} }; }

function selectRound(roundNumber) {
  stopPlayback();
  state.selectedRound = roundNumber;
  state.frameIndex = 0;
  $$('.round-button').forEach(button => button.classList.toggle('active', Number(button.dataset.round) === roundNumber));
  const frames = currentFrames();
  const slider = $('#frame-slider');
  if (slider) { slider.max = String(Math.max(0, frames.length - 1)); slider.value = '0'; slider.disabled = frames.length === 0; }
  renderRoundSummary();
  renderRoundRosters();
  resizeRadar();
  drawCurrentFrame();
}

function signed(value, digits = 2) {
  const number = Number(value || 0);
  return `${number > 0 ? '+' : ''}${fmt(number, digits)}`;
}

function renderRoundRosters() {
  const container = $('#round-rosters');
  if (!container) return;
  const model = currentRoundPlayers();
  const groups = state.data.teams.slice(0, 2).map(team => ({
    team,
    rows: model.players.filter(row => row.team === team.name),
  })).filter(group => group.rows.length);
  container.innerHTML = groups.map(group => {
    const won = group.team.name === selectedRoundData()?.winner_team;
    const probability = model.start_probability?.[group.team.name];
    return `<article class="card round-team-card ${won ? 'won' : 'lost'}" style="--team-color:${teamColor(group.team.name)}">
      <header><div><span class="round-result">${won ? 'W' : 'L'}</span><strong>${esc(group.team.name)}</strong></div><span>${probability == null ? '—' : `${fmt(probability, 1)}% до раунда`}</span></header>
      <div class="round-player-head"><span>Игрок</span><span>Swing</span><span>DMG</span><span>K/D/A</span></div>
      ${group.rows.map(row => `<div class="round-player-row">
        <span><i style="background:${playerColor(row.player)}"></i>${esc(player(row.player)?.name || 'Unknown')}</span>
        <b class="${row.swing >= 0 ? 'positive' : 'negative'}">${signed(row.swing)}%</b>
        <span>${fmt(row.damage)}</span><span>${row.kills}/${row.deaths}/${row.assists}</span>
      </div>`).join('')}
    </article>`;
  }).join('') || '<article class="card empty-state">Нет покадровой статистики игроков.</article>';
}

function renderRoundSummary() {
  const round = selectedRoundData();
  if (!round) return;
  const noPlant = !round.bomb_site || round.bomb_site === 'not_planted';
  const model = currentRoundPlayers();
  const teamSwing = Object.entries(model.team_swing || {}).map(([team, value]) => `${esc(team)} ${signed(value, 1)}%`).join(' · ');
  $('#round-summary').innerHTML = `
    <span class="eyebrow">ROUND ${round.number}</span><h3>${esc(round.winner_team || roundSideLabel(round.winner_side))}</h3>
    <div class="round-meta-grid">
      <div class="round-meta"><span>Победитель</span><strong>${esc(round.winner_team || '—')} · ${esc(roundSideLabel(round.winner_side))}</strong></div>
      <div class="round-meta"><span>Исход</span><strong>${esc(roundOutcomeLabel(round))}</strong></div>
      <div class="round-meta"><span>Бомба</span><strong>${noPlant ? 'Не установлена' : esc(round.bomb_site)}</strong></div>
      <div class="round-meta"><span>Убийства</span><strong>${round.kills}</strong></div>
      <div class="round-meta"><span>Урон</span><strong>${fmt(round.damage)}</strong></div>
      <div class="round-meta"><span>Длительность</span><strong>${fmt((round.end - round.freeze_end) / state.data.match.tickrate, 1)} сек</strong></div>
    </div>${teamSwing ? `<div class="round-model-note">Оценка win-probability: ${teamSwing}</div>` : ''}`;
}

function resizeRadar() {
  const canvases = $$('.radar-canvas'); if (!canvases.length) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvases.forEach(canvas => { if (!canvas.offsetParent) return; canvas.width = Math.round(1024 * dpr); canvas.height = Math.round(1024 * dpr); canvas.dataset.dpr = String(dpr); });
  drawCurrentFrame();
}

function worldToRadar(x, y) {
  const t = state.data.map_transform;
  if (t.mode === 'overview') return [(x - t.pos_x) / t.scale, (t.pos_y - y) / t.scale];
  if (t.mode === 'fit') {
    return [((x - t.min_x) / (t.max_x - t.min_x)) * 1024, (1 - (y - t.min_y) / (t.max_y - t.min_y)) * 1024];
  }
  return [512, 512];
}

const UTILITY_STYLE = {
  flash: { color: '#f6d84a', label: 'FLASH', glyph: '✦', icon: 'flashbang' },
  smoke: { color: '#b8c3cb', label: 'SMOKE', glyph: '◌', icon: 'smokegrenade' },
  he: { color: '#ff5e69', label: 'HE', glyph: '●', icon: 'hegrenade' },
  fire: { color: '#ff7a38', label: 'FIRE', glyph: '♨', icon: 'molotov' },
  decoy: { color: '#b889ff', label: 'DECOY', glyph: '◇', icon: 'decoy' },
};

function drawPlayerTrails(ctx, frames, frameIndex, levelId) {
  if (!frames.length || frameIndex < 1) return;
  const paths = new Map(), step = Math.max(1, Math.floor(frameIndex / 90));
  for (let i = 0; i <= frameIndex; i += step) for (const position of frames[i].p || []) {
    const index = position[0]; if (!paths.has(index)) paths.set(index, [[]]); const segments = paths.get(index), alpha = levelAlphaForZ(levelId, position[3]);
    if (alpha <= .04) { if (segments[segments.length - 1].length) segments.push([]); continue; }
    const point = worldToRadar(position[1], position[2]); segments[segments.length - 1].push([point[0], point[1], alpha]);
  }
  ctx.save(); ctx.lineWidth = 2;
  for (const [index, segments] of paths.entries()) { ctx.strokeStyle = playerColor(index); for (const points of segments) { if (points.length < 2) continue; ctx.globalAlpha = .22 * points.reduce((sum, point) => sum + point[2], 0) / points.length; ctx.beginPath(); points.forEach(([x,y],i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y)); ctx.stroke(); } }
  ctx.restore();
}

function utilityPointAt(event, currentTick) { const path = event.path || []; if (!path.length) return [event.x,event.y,event.z]; let point = path[0]; for (const candidate of path) { if (candidate[0] > currentTick) break; point = candidate; } return [point[1],point[2],point[3]]; }
function utilityPathSegments(path, levelId) { const segments=[[]]; for (const point of path) { const alpha=levelAlphaForZ(levelId,point[3]); if (alpha<=.04) { if (segments[segments.length-1].length) segments.push([]); continue; } segments[segments.length-1].push([point,alpha]); } return segments.filter(segment=>segment.length); }
function drawUtility(ctx, currentTick, levelId) {
  const showAll = state.eventTab === 'utility';
  for (const event of currentUtility()) { const style=UTILITY_STYLE[event.kind]||UTILITY_STYLE.he, path=event.path||[], visiblePath=showAll?path:path.filter(point=>point[0]<=currentTick);
    for (const segment of utilityPathSegments(visiblePath,levelId)) { if (segment.length<=1) continue; const floorAlpha=segment.reduce((sum,item)=>sum+item[1],0)/segment.length; ctx.save(); ctx.strokeStyle=style.color; ctx.globalAlpha=(currentTick>=event.start?.42:.14)*floorAlpha; ctx.lineWidth=2; ctx.setLineDash([5,4]); ctx.beginPath(); segment.forEach(([point],i)=>{const [x,y]=worldToRadar(point[1],point[2]); i?ctx.lineTo(x,y):ctx.moveTo(x,y);}); ctx.stroke(); ctx.restore(); }
    const activeFlight=currentTick>=event.start&&currentTick<event.land, activeEffect=currentTick>=event.effect_start&&currentTick<=event.end, showLanding=showAll||activeEffect||currentTick>=event.land;
    if (activeFlight) { const [wx,wy,wz]=utilityPointAt(event,currentTick), floorAlpha=levelAlphaForZ(levelId,wz); if (floorAlpha>.04) { const [x,y]=worldToRadar(wx,wy); ctx.save(); ctx.globalAlpha=floorAlpha; ctx.fillStyle=style.color; ctx.shadowColor=style.color; ctx.shadowBlur=12; ctx.beginPath(); ctx.arc(x,y,7,0,Math.PI*2); ctx.fill(); ctx.restore(); } }
    if (!showLanding) continue; const floorAlpha=levelAlphaForZ(levelId,event.z); if (floorAlpha<=.04) continue; const [x,y]=worldToRadar(event.x,event.y), alpha=(activeEffect?.95:showAll?.72:.35)*floorAlpha; ctx.save(); ctx.globalAlpha=alpha;
    if (event.kind==='smoke'||event.kind==='fire') { ctx.fillStyle=event.kind==='smoke'?'rgba(190,202,210,.22)':'rgba(255,112,57,.18)'; ctx.strokeStyle=style.color; ctx.lineWidth=3; ctx.beginPath(); ctx.arc(x,y,event.kind==='smoke'?28:24,0,Math.PI*2); ctx.fill(); ctx.stroke(); }
    ctx.fillStyle='#0a0d11'; ctx.strokeStyle=style.color; ctx.lineWidth=3; ctx.beginPath(); ctx.arc(x,y,15,0,Math.PI*2); ctx.fill(); ctx.stroke(); ctx.fillStyle=style.color; ctx.font='900 15px system-ui'; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillText(style.glyph,x,y+1); ctx.restore();
  }
}

function drawCurrentFrame() {
  const canvases=$$('.radar-canvas').filter(canvas=>canvas.offsetParent); if (!canvases.length) return; const frames=currentFrames(), frame=frames[state.frameIndex], round=selectedRoundData();
  canvases.forEach(canvas=>{ const levelId=canvas.dataset.level||'upper', dpr=Number(canvas.dataset.dpr||1), ctx=canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,1024,1024); if (!frame) { drawUtility(ctx,round?.end||round?.start||0,levelId); ctx.fillStyle='rgba(255,255,255,.75)'; ctx.font='700 22px system-ui'; ctx.textAlign='center'; ctx.fillText(state.data.match.has_positions?'Нет кадров в этом раунде':'Позиции не были сохранены при парсинге',512,510); return; } drawPlayerTrails(ctx,frames,state.frameIndex,levelId); drawUtility(ctx,frame.tick,levelId); frame.p.forEach(position=>drawPlayer(ctx,position,levelId)); });
  if (!frame) { $('#time-label').textContent='—'; renderEventFeed(round?.start||0); return; }
  const baseTick=round.freeze_end||round.start, seconds=Math.max(0,(frame.tick-baseTick)/state.data.match.tickrate); $('#time-label').textContent=`${formatTime(seconds)} · tick ${frame.tick}`; const slider=$('#frame-slider'); if (slider) slider.value=String(state.frameIndex); renderEventFeed(frame.tick);
}

function radarViewVector(x, y, yaw, pitch = 0) {
  // Source yaw is measured in world space: 0° = +X, 90° = +Y.
  // The radar transform flips world Y, so projecting a short world-space
  // direction segment is safer than using yaw as a canvas angle directly.
  const yawRadians = Number(yaw || 0) * Math.PI / 180;
  const pitchRadians = Number(pitch || 0) * Math.PI / 180;
  const [fromX, fromY] = worldToRadar(x, y);
  const [toX, toY] = worldToRadar(
    x + Math.cos(yawRadians) * 64,
    y + Math.sin(yawRadians) * 64,
  );
  const dx = toX - fromX;
  const dy = toY - fromY;
  const magnitude = Math.hypot(dx, dy) || 1;
  // A top-down radar cannot show vertical aim. Shorten the pointer when the
  // player looks steeply up/down instead of pretending yaw is fully certain.
  const horizontalAim = Math.max(.18, Math.abs(Math.cos(pitchRadians)));
  return [dx / magnitude, dy / magnitude, horizontalAim];
}

function drawPlayer(ctx, position, levelId = 'upper') {
  let [index,x,y,z,yaw,pitch,alive,ctSide]=position; if (position.length===7) { [index,x,y,z,yaw,alive,ctSide]=position; pitch=0; }
  const floorAlpha=levelAlphaForZ(levelId,z); if (floorAlpha<=.04) return; const p=player(index), [px,py]=worldToRadar(x,y); if (px<-40||py<-40||px>1064||py>1064) return; const color=ctSide?'#63b7ff':'#d78cff'; ctx.save(); ctx.globalAlpha=(alive?1:.32)*floorAlpha; ctx.translate(px,py); const [lookX,lookY,horizontalAim]=radarViewVector(x,y,yaw,pitch), pointerLength=22*horizontalAim; ctx.strokeStyle=color; ctx.lineWidth=4; ctx.lineCap='round'; ctx.beginPath(); ctx.moveTo(0,0); ctx.lineTo(lookX*pointerLength,lookY*pointerLength); ctx.stroke(); ctx.fillStyle='#090b0e'; ctx.beginPath(); ctx.arc(0,0,14,0,Math.PI*2); ctx.fill(); ctx.strokeStyle=color; ctx.lineWidth=4; ctx.stroke(); if (!alive) { ctx.beginPath(); ctx.moveTo(-8,-8); ctx.lineTo(8,8); ctx.moveTo(8,-8); ctx.lineTo(-8,8); ctx.stroke(); } ctx.font='800 16px system-ui'; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillStyle='#fff'; ctx.fillText(String(index+1),0,0); ctx.font='800 15px system-ui'; ctx.textBaseline='top'; ctx.strokeStyle='rgba(0,0,0,.85)'; ctx.lineWidth=5; ctx.strokeText(p.name,0,19); ctx.fillStyle='#fff'; ctx.fillText(p.name,0,19); ctx.restore();
}

function roundEventSeconds(tick) {
  const round = selectedRoundData();
  const baseTick = round?.freeze_end || round?.start || 0;
  return Math.max(0, (Number(tick || 0) - baseTick) / state.data.match.tickrate);
}

function renderKillFeed(currentTick) {
  const events = currentKills();
  const visible = events.filter(event => event[0] <= currentTick).slice(-12).reverse();
  return visible.length ? visible.map(event => {
    const [tick, a, v, weapon, headshot, trade] = event;
    const eventTime = formatTime(roundEventSeconds(tick));
    return `<div class="kill-row"><span class="event-time">${eventTime}</span><span class="attacker" style="color:${playerColor(a)}">${esc(player(a).name)}</span><span class="weapon">${headshot ? '<b class="headshot-mark" title="Headshot">◆</b>' : ''}${equipmentIcon(weapon, 'kill-weapon-icon')}<span>${esc(weapon)}</span>${trade ? '<b class="trade-mark" title="Trade">↻</b>' : ''}</span><span class="victim">${esc(player(v).name)}</span></div>`;
  }).join('') : '<div class="empty-state">Убийств к этому моменту нет</div>';
}

function renderUtilityFeed(currentTick) {
  const events = currentUtility();
  const visible = state.eventTab === 'utility' ? events : events.filter(event => event.start <= currentTick);
  return visible.length ? visible.map(event => {
    const style = UTILITY_STYLE[event.kind] || UTILITY_STYLE.he;
    const seconds = roundEventSeconds(event.start);
    const result = event.damage > 0 ? `${fmt(event.damage)} dmg` : event.blind > 0 ? `${fmt(event.blind, 1)}s blind` : event.flashed > 0 ? `${event.flashed} flashed` : '—';
    return `<div class="utility-event-row"><span class="utility-glyph" style="--utility-color:${style.color}">${equipmentIcon(style.icon, 'utility-event-icon')}<span class="utility-fallback">${style.glyph}</span></span><span><b style="color:${playerColor(event.player)}">${esc(player(event.player)?.name || 'Unknown')}</b><small>${formatTime(seconds)} · ${style.label}</small></span><strong>${result}</strong></div>`;
  }).join('') : '<div class="empty-state">Гранат в этом раунде нет</div>';
}

function renderEventFeed(currentTick) {
  const feed = $('#event-feed');
  if (!feed) return;
  feed.innerHTML = state.eventTab === 'utility' ? renderUtilityFeed(currentTick) : renderKillFeed(currentTick);
}

function formatTime(seconds) {
  const mins = Math.floor(seconds / 60); const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function togglePlayback() { state.playing ? stopPlayback() : startPlayback(); }
function startPlayback() {
  const frames = currentFrames();
  if (frames.length < 2) return;
  state.playing = true; $('#play-button').textContent = '❚❚';
  const sample = Math.max(1, Number(state.data.match.position_sample || 16));
  const delay = Math.max(25, (sample / state.data.match.tickrate) * 1000 / state.speed);
  state.timer = window.setInterval(() => {
    state.frameIndex += 1;
    if (state.frameIndex >= frames.length) state.frameIndex = 0;
    drawCurrentFrame();
  }, delay);
}
function stopPlayback() {
  state.playing = false;
  if (state.timer) window.clearInterval(state.timer);
  state.timer = null;
  const button = $('#play-button'); if (button) button.textContent = '▶';
}

async function boot() {
  setupTabs();
  try {
    if (window.__CS2_DATA__) {
      state.data = window.__CS2_DATA__;
    } else {
      const response = await fetch('data.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = await response.json();
    }
    buildPairMap();
    renderHero(); renderValidation(); renderOverview(); renderDuels(); renderUtility(); renderEconomy(); renderInsights();
    setupSortableTables();
  } catch (error) {
    document.querySelector('main').innerHTML = `<div class="card empty-state" style="margin-top:30px">Не удалось загрузить data.json: ${esc(error.message)}. Открывайте dashboard через локальный сервер, а не file://.</div>`;
    console.error(error);
  }
}

document.addEventListener('DOMContentLoaded', boot);
