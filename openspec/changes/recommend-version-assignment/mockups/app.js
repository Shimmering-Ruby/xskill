// 静态演示：数据全部写死，无 fetch / 无前后端交互。
// 壳子与官方 static/index.html 一致，只负责把硬编码内容填进 DOM。

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]));

const AV = ['bg-indigo-100 text-indigo-700', 'bg-sky-100 text-sky-700',
  'bg-amber-100 text-amber-700', 'bg-rose-100 text-rose-700',
  'bg-emerald-100 text-emerald-700', 'bg-violet-100 text-violet-700'];
function avatar(name, size) {
  let h = 0;
  for (const ch of String(name || '?')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  const sz = size === 'sm' ? 'w-5 h-5 text-[9px]' : 'w-6 h-6 text-[10px]';
  return `<span class="${sz} rounded-full ${AV[h % AV.length]} inline-flex items-center justify-center font-bold shrink-0">${esc(String(name || '?').slice(0, 2))}</span>`;
}

const BUCKET = {
  pinned: 'bg-violet-100 text-violet-700',
  ranked: 'bg-teal-50 text-teal-700 ring-1 ring-teal-100',
  recommended: 'bg-sky-100 text-sky-700',
};
const sideChip = side => side === 'staging'
  ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 ring-1 ring-amber-100">staging</span>'
  : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">main</span>';
const bucketChip = b => `<span class="text-[10px] px-1.5 py-0.5 rounded ${BUCKET[b] || 'bg-slate-100 text-slate-500'}">${esc(b)}</span>`;

const PAGE = 5;
const TRI_UP = '<svg width="12" height="8" viewBox="0 0 12 8" fill="currentColor"><path d="M6 0L12 8H0z"/></svg>';
const TRI_DN = '<svg width="12" height="8" viewBox="0 0 12 8" fill="currentColor"><path d="M6 8L0 0h12z"/></svg>';

function pagerCol(pagerId, page, total) {
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const p = Math.min(page, pages - 1);
  return `<div class="flex flex-col items-center gap-0.5 shrink-0 pt-0.5">
    <button type="button" class="feed-tri pg-btn" data-pager="${esc(pagerId)}" data-dir="-1" ${p <= 0 ? 'disabled' : ''} title="上一页" aria-label="上一页">${TRI_UP}</button>
    <span class="text-[10px] text-slate-400 tabular-nums leading-none py-1">${p + 1}/${pages}</span>
    <button type="button" class="feed-tri pg-btn" data-pager="${esc(pagerId)}" data-dir="1" ${p >= pages - 1 ? 'disabled' : ''} title="下一页" aria-label="下一页">${TRI_DN}</button>
  </div>`;
}
function pageSlice(arr, page) {
  const pages = Math.max(1, Math.ceil(arr.length / PAGE));
  const p = Math.max(0, Math.min(page, pages - 1));
  return { page: p, pages, slice: arr.slice(p * PAGE, p * PAGE + PAGE), total: arr.length };
}

// ── 写死数据（用户数 > 5，方便演示翻页） ──────────────────────
const SKILLS = [
  { name: 'web-flask', state: 'staging', description: 'Flask 路由与蓝图排错', version: 2, candidates: 4, main: 'a1b2c3d', staging: 'f9e8d7c' },
  { name: 'sql-migrate', state: 'staging', description: '数据库迁移冲突处理', version: 2, candidates: 3, main: '1122334', staging: '9988776' },
  { name: 'git-housekeeping', state: 'main', description: '仓库垃圾回收与分支清理', version: 1, candidates: 1, main: 'deadbee', staging: null },
  { name: 'pytest-fixture', state: 'staging', description: 'pytest fixture 作用域与依赖', version: 2, candidates: 5, main: 'abcdef0', staging: 'c0ffeea' },
  { name: 'docker-compose', state: 'main', description: '多服务 compose 编排', version: 1, candidates: 0, main: '13579bd', staging: null },
  { name: 'openapi-client', state: 'main', description: 'OpenAPI 客户端生成', version: 1, candidates: 2, main: '2468ace', staging: null },
  { name: 'log-rotate', state: 'main', description: '日志轮转与保留策略', version: 1, candidates: 0, main: '3141592', staging: null },
  { name: 'csv-join', state: 'main', description: '多表 CSV 关联合并', version: 1, candidates: 1, main: '2718281', staging: null },
];

const USER_NAMES = [
  'alice', 'bob', 'carol', 'dave', 'erin', 'frank',
  'grace', 'hank', 'ivy', 'jack', 'kate', 'leo',
];

// 每人当前推送；web-flask 刻意让 staging/main 都超过 5 人
const ASSIGN = {};
USER_NAMES.forEach((u, i) => {
  const flaskSide = i % 2 === 0 ? 'staging' : 'main';
  const flaskSha = flaskSide === 'staging' ? 'f9e8d7c' : 'a1b2c3d';
  ASSIGN[u] = [
    { skill: 'git-housekeeping', bucket: 'pinned', side: 'main', sha: 'deadbee', source: '全局 pin' },
    { skill: 'web-flask', bucket: i < 2 ? 'pinned' : 'ranked', side: flaskSide, sha: flaskSha, source: i < 2 ? '自 pin' : '自动' },
    { skill: 'sql-migrate', bucket: 'ranked', side: i % 3 === 0 ? 'staging' : 'main', sha: i % 3 === 0 ? '9988776' : '1122334', source: '自动' },
    { skill: 'pytest-fixture', bucket: 'recommended', side: i % 2 ? 'staging' : 'main', sha: i % 2 ? 'c0ffeea' : 'abcdef0', source: '自动' },
    { skill: 'docker-compose', bucket: 'recommended', side: 'main', sha: '13579bd', source: '自动' },
    ...(i === 0 ? [{ skill: 'openapi-client', bucket: 'recommended', side: 'main', sha: '2468ace', source: '自动' },
      { skill: 'csv-join', bucket: 'recommended', side: 'main', sha: '2718281', source: '自动' }] : []),
  ];
});

const USERS = USER_NAMES.map((user, i) => ({
  user,
  ver: i === 4 ? '未上报' : '0.6.29',
  rate: `${18 + (i * 3) % 20}%`,
  pinned: `${1 + (i % 3)} · ${i === 1 ? 1 : 0}`,
  hist: 30 + i * 12,
}));

const NAMES = {
  overview: '总览', skills: '技能库', pipeline: '流水线', traj: '轨迹 & 原子',
  users: '用户 & 画像', canary: '灰度 Canary', my: '我的', admin: '管理', settings: '设置',
};

// 翻页状态
let adminUserPage = 0;
let drawerUser = null;
let drawerPage = 0;
let curSkill = null;
let routeStgPage = 0;
let routeMainPage = 0;
let mySlotPage = 0;

// ── 路由 ──────────────────────────────────────────────────────
function showPage(pg) {
  document.querySelectorAll('.sec-page').forEach(el => el.classList.toggle('on', el.id === 'pg-' + pg));
  document.querySelectorAll('.nav-link').forEach(a => {
    const on = a.dataset.pg === pg;
    a.classList.toggle('text-slate-500', !on);
    a.style.background = on ? '#f0fdfa' : '';
    a.style.color = on ? '#0f766e' : '';
    a.style.fontWeight = on ? '600' : '';
  });
  const el = document.getElementById('pgname');
  if (el) el.textContent = NAMES[pg] || pg;
}

function route() {
  const h = (location.hash || '#admin').replace(/^#/, '');
  const parts = h.split('/');
  if (parts[0] === 'skill' && parts[1]) {
    showPage('skills');
    const name = decodeURIComponent(parts[1]);
    if (curSkill !== name) { routeStgPage = 0; routeMainPage = 0; }
    renderSkillDetail(name);
    return;
  }
  const pg = parts[0] && NAMES[parts[0]] ? parts[0] : 'admin';
  showPage(pg);
  if (pg === 'skills') {
    const box = document.getElementById('skill-detail');
    if (box && !curSkill) box.innerHTML = '';
  }
  if (pg === 'admin') renderAdmin();
  if (pg === 'my') renderMy();
}

// ── 管理：用户表（上）翻页 ────────────────────────────────────
function renderAdmin() {
  const gp = document.getElementById('admin-gpins');
  if (gp) {
    gp.innerHTML = '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">git-housekeeping</span>';
  }
  const wrap = document.getElementById('admin-users-body');
  if (!wrap) return;

  let host = document.getElementById('admin-users-pager-host');
  if (!host) {
    const tableWrap = wrap.closest('.overflow-x-auto');
    if (tableWrap && tableWrap.parentNode) {
      host = document.createElement('div');
      host.id = 'admin-users-pager-host';
      host.className = 'mt-2 flex gap-2 items-start';
      const pager = document.createElement('div');
      pager.id = 'admin-users-pager';
      const pane = document.createElement('div');
      pane.className = 'min-w-0 flex-1 overflow-x-auto';
      tableWrap.parentNode.insertBefore(host, tableWrap);
      host.appendChild(pager);
      host.appendChild(pane);
      pane.appendChild(tableWrap);
    }
  }
  const pagerEl = document.getElementById('admin-users-pager');
  const { page, slice } = pageSlice(USERS, adminUserPage);
  adminUserPage = page;
  if (pagerEl) pagerEl.innerHTML = pagerCol('admin-users', adminUserPage, USERS.length);

  wrap.innerHTML = slice.map(u => {
    const slots = ASSIGN[u.user] || [];
    const stg = slots.filter(s => s.side === 'staging').length;
    return `<tr>
      <td class="py-2 font-medium">${esc(u.user)}</td>
      <td>${u.ver === '未上报' ? '<span class="text-slate-300">未上报</span>' : esc(u.ver)}</td>
      <td class="text-right tabular-nums">${slots.length}</td>
      <td class="text-right tabular-nums ${stg ? 'text-amber-700' : ''}">${stg}</td>
      <td class="text-right tabular-nums">${esc(u.rate)}</td>
      <td class="text-right tabular-nums">${esc(u.pinned)}</td>
      <td class="pl-6"><span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">处理中</span></td>
      <td class="pl-6"><span class="text-slate-300">—</span></td>
      <td class="text-right whitespace-nowrap">
        <button class="adm-cfg text-[11px] px-2 py-0.5 rounded ring-1 ring-slate-200 hover:bg-slate-50" data-user="${esc(u.user)}">配置…</button>
      </td></tr>`;
  }).join('');

  const sk = document.getElementById('admin-skills-body');
  if (sk) {
    sk.innerHTML = SKILLS.map(s => {
      const [label, cls] = s.state === 'staging'
        ? ['灰度中', 'bg-amber-100 text-amber-700']
        : ['在役', 'bg-emerald-100 text-emerald-700'];
      return `<tr><td class="py-2 font-medium">${esc(s.name)}</td>
        <td><span class="text-[10px] px-1.5 py-0.5 rounded ${cls}">${label}</span></td>
        <td class="text-right tabular-nums">${20 + s.name.length}</td>
        <td class="text-right"><button class="text-[11px] px-2 py-0.5 rounded ring-1 ring-slate-200 text-slate-400 cursor-not-allowed">下线</button></td></tr>`;
    }).join('');
  }
  if (drawerUser) openDrawer(drawerUser, false);
}

function openDrawer(user, resetPage) {
  drawerUser = user;
  if (resetPage !== false) drawerPage = 0;
  const d = document.getElementById('admin-drawer');
  if (!d) return;
  const slots = ASSIGN[user] || [];
  const { page, slice, total } = pageSlice(slots, drawerPage);
  drawerPage = page;
  d.classList.remove('hidden');
  d.innerHTML = `<div class="flex items-baseline justify-between">
      <h3 class="font-medium text-[12.5px]">${esc(user)} 的当前推送
        <span class="text-[10.5px] text-slate-400 font-normal ml-1">${total} 槽 · 每页 ${PAGE} 条</span></h3>
      <button id="adm-drawer-x" class="text-[11px] text-slate-400 hover:bg-slate-100 px-1.5 rounded">收起</button></div>
    <div class="mt-2 flex gap-2 items-start">
      ${pagerCol('drawer-slots', drawerPage, total)}
      <div class="min-w-0 flex-1 space-y-1.5">${slice.map(s => `
        <div class="flex items-center gap-2 px-2.5 py-2 rounded-lg ring-1 ring-slate-100 bg-white">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5 flex-wrap">
              <a href="#skill/${encodeURIComponent(s.skill)}" class="font-medium text-teal-700 text-[12px]">${esc(s.skill)}</a>
              ${bucketChip(s.bucket)} ${sideChip(s.side)}
              <code class="text-[10px] text-slate-400">${esc(s.sha)}</code>
              <span class="text-[10px] text-slate-400">· ${esc(s.source)}</span>
            </div>
          </div>
        </div>`).join('') || '<span class="text-[11px] text-slate-400">无</span>'}</div>
    </div>`;
}

// ── 技能库 ────────────────────────────────────────────────────
function renderSkills() {
  document.querySelectorAll('[data-m="skills.summary"]').forEach(e => {
    e.textContent = `共 ${SKILLS.length} 个 · main 5 · staging 3`;
  });
  const tb = document.getElementById('skills-body');
  if (!tb) return;
  tb.innerHTML = SKILLS.map(s => {
    const st = s.state === 'staging'
      ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">staging</span>'
      : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">main</span>';
    return `<tr class="hover:bg-slate-50 cursor-pointer" data-skill-row="${esc(s.name)}">
      <td class="py-2.5 font-medium text-teal-700">${esc(s.name)} <span class="ml-2 inline-block px-2 py-0.5 rounded-md text-[11px] font-medium bg-slate-100 text-slate-500">自产</span></td>
      <td>${st}</td>
      <td class="text-slate-500 max-w-[480px] truncate">${esc(s.description)}</td>
      <td class="text-right tabular-nums">v${s.version}</td>
      <td class="text-right tabular-nums">${s.candidates}</td></tr>`;
  }).join('');
}

function routingLists(name) {
  const stg = [], main = [];
  USER_NAMES.forEach(u => {
    const hit = (ASSIGN[u] || []).find(s => s.skill === name);
    if (!hit) return;
    (hit.side === 'staging' ? stg : main).push({ user: u, ...hit });
  });
  return { stg, main };
}

function renderSkillDetail(name) {
  curSkill = name;
  const sk = SKILLS.find(s => s.name === name);
  const box = document.getElementById('skill-detail');
  if (!box) return;
  if (!sk) {
    box.innerHTML = `<div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5 text-slate-400">未知 skill：${esc(name)}</div>`;
    return;
  }
  const { stg, main } = routingLists(name);
  const stgP = pageSlice(stg, routeStgPage); routeStgPage = stgP.page;
  const mainP = pageSlice(main, routeMainPage); routeMainPage = mainP.page;

  const row = u => `
    <div class="flex items-center gap-2 py-2 border-b border-slate-50 last:border-0">
      ${avatar(u.user, 'sm')}
      <div class="min-w-0 flex-1">
        <div class="text-[12.5px] font-medium">${esc(u.user)}</div>
        <div class="text-[10.5px] text-slate-400 flex items-center gap-1.5">
          ${bucketChip(u.bucket)} <code>${esc(u.sha)}</code>
          <span>${esc(u.source)}</span>
        </div>
      </div>
    </div>`;

  const listBlock = (title, titleCls, ringCls, bgCls, pagerId, page, list, sliced) => `
    <div>
      <div class="flex items-center justify-between mb-1">
        <div class="text-[11px] font-medium ${titleCls}">${title}</div>
        <span class="text-[10px] text-slate-400">${list.length} 人 · 每页 ${PAGE}</span>
      </div>
      <div class="flex gap-2 items-start">
        ${pagerCol(pagerId, page, list.length)}
        <div class="min-w-0 flex-1 rounded-xl ring-1 ${ringCls} ${bgCls} px-3">
          ${sliced.map(row).join('') || '<div class="text-[11px] text-slate-400 py-2">无人</div>'}
        </div>
      </div>
    </div>`;

  box.innerHTML = `
  <div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5">
    <div class="text-xs text-slate-400 mb-1.5"><a href="#skills" class="text-teal-700 hover:underline">技能库</a> <span class="mx-1">/</span> <span class="text-slate-600">${esc(name)}</span></div>
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <h2 class="text-lg font-bold tracking-tight">${esc(name)}</h2>
        <div class="text-slate-500 text-xs mt-1">${esc(sk.description)}</div>
      </div>
      <div class="flex gap-2">
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-xs font-medium text-slate-600">main <code class="text-slate-400">${esc(sk.main)}</code></span>
        ${sk.staging ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50 ring-1 ring-amber-200 text-xs font-medium text-amber-700"><span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span>staging <code class="opacity-60">${esc(sk.staging)}</code></span>` : ''}
      </div>
    </div>

    <div class="rounded-2xl ring-1 ring-slate-200 p-5 mt-4">
      <div class="flex items-baseline justify-between">
        <h3 class="font-semibold text-sm">灰度路由</h3>
        <span class="text-[11px] text-slate-400">${sk.staging ? `staging ${stg.length} · main ${main.length}` : '无 staging · 全员 main'}</span>
      </div>
      <div class="grid grid-cols-1 gap-4 mt-3">
        ${listBlock('staging · 用户推荐', 'text-amber-700', 'ring-amber-100', 'bg-amber-50/40', 'route-stg', routeStgPage, stg, stgP.slice)}
        ${listBlock('main · 用户推荐', 'text-slate-500', 'ring-slate-100', '', 'route-main', routeMainPage, main, mainP.slice)}
      </div>
    </div>
  </div>`;
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── 我的（槽位也按 5 翻页） ───────────────────────────────────
function renderMy() {
  const slots = ASSIGN.alice || [];
  const { page, slice, total } = pageSlice(slots, mySlotPage);
  mySlotPage = page;
  const sum = document.getElementById('my-slot-sum');
  if (sum) sum.textContent = `${total}/100 槽位 · 每页 ${PAGE}`;
  const el = document.getElementById('my-slots');
  if (el) {
    el.innerHTML = `<div class="flex gap-2 items-start">
      ${pagerCol('my-slots', mySlotPage, total)}
      <div class="min-w-0 flex-1 space-y-2">${slice.map(s => `
      <div class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl ring-1 ring-slate-100">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <a href="#skill/${encodeURIComponent(s.skill)}" class="font-medium text-teal-700 underline decoration-teal-200 underline-offset-2">${esc(s.skill)}</a>
            ${bucketChip(s.bucket)} ${sideChip(s.side)}
            <code class="text-[10px] text-slate-400">${esc(s.sha)}</code>
          </div>
          <div class="mt-1 text-[10px] text-slate-400">${esc(s.source)}</div>
        </div>
      </div>`).join('')}</div></div>`;
  }
  const blocked = document.getElementById('my-blocked');
  if (blocked) blocked.innerHTML = '<span class="text-[11px] text-slate-400">无</span>';
  const steps = document.getElementById('my-steps');
  if (steps) {
    steps.innerHTML = [['轨迹', 18], ['原子', 64], ['被采纳', 22], ['进入 skill', 5]]
      .map(([k, v], i) => `${i ? '<span class="text-slate-300 text-xs">→</span>' : ''}
        <div class="px-3 py-1.5 rounded-lg bg-slate-50 ring-1 ring-slate-100 text-center min-w-[4.5rem]">
          <div class="text-base font-semibold tabular-nums leading-tight">${v}</div><div class="text-[10px] text-slate-400">${k}</div></div>`).join('');
  }
}

function fillPlaceholders() {
  document.querySelectorAll('[data-m]').forEach(e => {
    if (e.textContent === '—' || e.textContent === '加载中…' || !e.textContent.trim()) {
      const k = e.getAttribute('data-m') || '';
      if (k.includes('trajs')) e.textContent = '1284';
      else if (k.includes('atoms')) e.textContent = '6120';
      else if (k.includes('avg_ux')) e.textContent = '7.2';
      else if (k.includes('rate') || k.includes('promotion') || k.includes('trigger') || k.includes('adoption')) e.textContent = '42%';
      else if (k.includes('online')) e.textContent = '4 在线';
      else e.textContent = '—';
    }
  });
  ['canary-body', 'trigger-body', 'eco-body', 'model-body', 'dirs-body', 'ustatus-body'].forEach(id => {
    const tb = document.getElementById(id);
    if (tb && /加载中/.test(tb.textContent)) {
      tb.innerHTML = '<tr><td colspan="8" class="py-2 text-slate-400">静态演示未填充此表</td></tr>';
    }
  });
  const tag = document.getElementById('tagcloud');
  if (tag && /加载中/.test(tag.textContent)) tag.innerHTML = '<span class="text-slate-400 text-xs">静态演示未填充</span>';
  const whoRole = document.getElementById('who-role');
  if (whoRole && !whoRole.textContent) whoRole.textContent = 'admin';
}

function bumpPager(id, dir) {
  if (id === 'admin-users') {
    adminUserPage += dir;
    renderAdmin();
  } else if (id === 'drawer-slots') {
    drawerPage += dir;
    openDrawer(drawerUser, false);
  } else if (id === 'route-stg') {
    routeStgPage += dir;
    renderSkillDetail(curSkill);
  } else if (id === 'route-main') {
    routeMainPage += dir;
    renderSkillDetail(curSkill);
  } else if (id === 'my-slots') {
    mySlotPage += dir;
    renderMy();
  }
}

document.getElementById('nav')?.addEventListener('click', e => {
  const a = e.target.closest('[data-pg]');
  if (!a) return;
  e.preventDefault();
  location.hash = a.dataset.pg;
});
document.addEventListener('click', e => {
  const pg = e.target.closest('.pg-btn');
  if (pg && !pg.disabled) {
    bumpPager(pg.dataset.pager, parseInt(pg.dataset.dir, 10));
    return;
  }
  const cfg = e.target.closest('.adm-cfg');
  if (cfg) { openDrawer(cfg.dataset.user, true); return; }
  if (e.target.id === 'adm-drawer-x') {
    document.getElementById('admin-drawer')?.classList.add('hidden');
    drawerUser = null;
    return;
  }
  const row = e.target.closest('[data-skill-row]');
  if (row) { location.hash = 'skill/' + encodeURIComponent(row.dataset.skillRow); return; }
});
window.addEventListener('hashchange', route);

if (!location.hash || location.hash === '#') location.hash = '#admin';
renderSkills();
fillPlaceholders();
route();
