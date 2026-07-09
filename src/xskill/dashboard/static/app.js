// xskill 控制台前端（P1 重写）：Tailwind 视觉体系 + SPA-lite hash 路由。
// 各端点独立加载互不阻塞；指标算不出（分母 0/无记录）显示 — 而非 0%。

// ── 基础工具 ─────────────────────────────────────────────────────
const _cache = {};
async function j(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(u + ' ' + r.status);
  return r.json();
}
// 同一端点多个渲染方共享一次请求
const jc = u => (_cache[u] ||= j(u));

function put(sel, val) {
  document.querySelectorAll(`[data-m="${sel}"]`).forEach(e => { e.textContent = val; });
}
function rows(bodyId, html, empty) {
  const tb = document.getElementById(bodyId);
  if (tb) tb.innerHTML = html
    || `<tr><td colspan="9" class="py-2 text-slate-400">${empty || '暂无数据'}</td></tr>`;
}
const money = n => '$' + (Number(n) || 0).toFixed(4);
const tok = n => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : '' + n; };
// 任何要塞进 innerHTML 的值一律转义（model 名可能是 `<synthetic>`）
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// ux 是 1–10 分；null/0 = 还没有评分，显示 —
const ux = v => (v == null || Number(v) === 0) ? '—' : v;
// 分母为 0 → —（无数据 ≠ 0%）
const pctOr = (rate, denom) => (denom > 0 ? rate + '%' : '—');
const fdate = ts => esc(String(ts || '').replace('T', ' ').slice(0, 16)) || '—';

// 用户头像圈：名字前 2 字符 + 确定性配色（完整类名字面量，供 Tailwind 扫描）
const AV_COLORS = ['bg-indigo-100 text-indigo-700', 'bg-sky-100 text-sky-700',
  'bg-amber-100 text-amber-700', 'bg-rose-100 text-rose-700',
  'bg-emerald-100 text-emerald-700', 'bg-violet-100 text-violet-700'];
function avatar(name, size) {
  let h = 0;
  for (const ch of String(name || '?')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  const cls = AV_COLORS[h % AV_COLORS.length];
  const sz = size === 'sm' ? 'w-5 h-5 text-[9px]' : 'w-6 h-6 text-[10px]';
  return `<span class="${sz} rounded-full ${cls} inline-flex items-center justify-center font-bold shrink-0">${esc(String(name || '?').slice(0, 2))}</span>`;
}
// 占比条
function bar(pct, color) {
  return `<div class="flex-1 h-1.5 rounded-full bg-slate-100 min-w-[60px]"><div class="h-full rounded-full ${color || 'bg-teal-500'}" style="width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%"></div></div>`;
}

// ── 总览 ─────────────────────────────────────────────────────────
async function loadOverview() {
  const o = await jc('api/v1/dashboard/overview');
  put('overview.trajs', o.trajs);
  put('overview.atoms', o.atoms);
  put('overview.avg_atoms_per_traj', o.trajs > 0 ? o.avg_atoms_per_traj : '—');
  put('overview.avg_ux', (o.ux_n > 0 && o.avg_ux != null) ? o.avg_ux : '—');
  put('overview.ux_n', o.ux_n > 0 ? `${o.ux_n} 份使用打分` : '还没有使用打分');
  put('overview.retry_rate', o.trajs > 0 ? o.retry_rate + '%' : '—');
  put('overview.filtered', o.filtered > 0 ? `filtered ${o.filtered} 条不进分母` : '');
  // 成功率是终态口径：分母 = done+error+filtered。done/error 在 pipeline 端点里。
  try {
    const p = await jc('api/v1/dashboard/pipeline');
    const finished = (p.stages.done || 0) + (p.stages.error || 0) + (o.filtered || 0);
    put('overview.success_rate', finished > 0 ? o.success_rate + '%' : '—');
  } catch (e) {
    put('overview.success_rate', o.trajs > 0 ? o.success_rate + '%' : '—');
  }
  const h = o.price_health, el = document.getElementById('price-warn');
  if (el && h && h.ok === false) {
    const reason = { schema_changed: '上游格式变更', source_moved: '上游地址失效', unreachable: '上游不可达' }[h.kind] || '刷新异常';
    el.innerHTML = `<div class="mt-2 rounded-xl bg-amber-50/70 ring-1 ring-amber-100 px-3.5 py-2 text-[11px] text-amber-700">价格表 ${h.stale_days != null ? h.stale_days + 'd' : '从未'} 未刷新 · ${reason}，沿用旧价</div>`;
  }
}

async function loadRates() {
  const r = await jc('api/v1/dashboard/rates');
  const recsTotal = (r.trigger.by_skill || []).reduce((a, s) => a + (s.recommended || 0), 0);
  put('rates.trigger', pctOr(r.trigger.overall, recsTotal));
  put('rates.adoption', pctOr(r.adoption.rate, r.adoption.total));
  put('rates.promotion', pctOr(r.promotion.rate, r.promotion.decided));
  put('rates.promotion2', pctOr(r.promotion.rate, r.promotion.decided));
  put('promotion.detail', r.promotion.decided > 0
    ? `${r.promotion.promoted}/${r.promotion.decided} 已裁决` : '还没有灰度裁决');
  rows('trigger-body', (r.trigger.by_skill || []).map(s =>
    `<tr><td class="py-2 font-medium text-slate-800">${esc(s.skill)}</td>`
    + `<td class="text-right tabular-nums">${s.recommended}</td>`
    + `<td class="text-right tabular-nums">${s.used}</td>`
    + `<td class="text-right"><div class="flex items-center gap-2 justify-end">${bar(s.rate)}<span class="tabular-nums text-[11px] text-slate-500 w-10 text-right">${pctOr(s.rate, s.recommended)}</span></div></td></tr>`).join(''),
    '还没有推荐曝光记录');
}

const STAGE_DEFS = [
  ['pending_split', '待拆分'], ['splitting', '拆分中'],
  ['clustering', '聚类分派中'], ['done', '已完成'], ['error', '错误'],
];
async function loadPipeline() {
  const p = await jc('api/v1/dashboard/pipeline');
  const cells = STAGE_DEFS.map(([k, label]) => {
    const n = p.stages[k] || 0;
    const active = n > 0 && (k === 'splitting' || k === 'clustering');
    const isErr = k === 'error' && n > 0;
    if (active) return `<div class="flex-1 min-w-[92px] rounded-xl ring-1 ring-teal-200 bg-teal-50/50 px-3.5 py-3">
      <div class="text-[11px] text-teal-600 flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse"></span>${label}</div>
      <div class="mt-1 text-xl font-semibold tabular-nums text-teal-700">${n}</div></div>`;
    if (isErr) return `<div class="flex-1 min-w-[92px] rounded-xl ring-1 ring-rose-200 bg-rose-50/50 px-3.5 py-3">
      <div class="text-[11px] text-rose-600">${label}</div>
      <div class="mt-1 text-xl font-semibold tabular-nums text-rose-700">${n}</div></div>`;
    return `<div class="flex-1 min-w-[92px] rounded-xl ring-1 ring-slate-200 px-3.5 py-3">
      <div class="text-[11px] text-slate-400">${label}</div>
      <div class="mt-1 text-xl font-semibold tabular-nums">${n}</div></div>`;
  }).join('<div class="self-center text-slate-300 shrink-0">→</div>');
  document.getElementById('pipe-stages').innerHTML = cells;
  // 冷启动屏障：signal 不存在（null）整块不渲染
  const cold = document.getElementById('pipe-cold');
  cold.innerHTML = (p.cold_start && p.cold_start.active)
    ? `<div class="mt-4 rounded-xl bg-slate-50 ring-1 ring-slate-100 px-4 py-3 flex items-center gap-2">
        <span class="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse"></span>
        <span class="text-xs font-medium text-slate-600">冷启动屏障激活中</span>
        <span class="text-[11px] text-slate-400">收集满后统一蒸馏，避免碎片化 skill</span></div>`
    : '';
  const cands = document.getElementById('pipe-cands');
  if (!(p.candidates || []).length) { cands.innerHTML = ''; return; }
  cands.innerHTML = `<div class="text-[11px] text-slate-400 mb-2">候选孵化进度 · weightscore 满 ${esc(p.candidates[0].threshold)} 触发蒸馏</div>
    <div class="space-y-3">` + p.candidates.map(c => `
      <div>
        <div class="flex items-baseline justify-between">
          <span class="font-medium text-slate-800 text-xs">${esc(c.skill)}</span>
          <span class="text-[11px] tabular-nums ${c.progress >= 0.8 ? 'text-teal-700' : 'text-slate-600'} font-semibold">${esc(c.weightscore)} <span class="text-slate-300 font-normal">/ ${esc(c.threshold)}</span></span>
        </div>
        <div class="mt-1.5 h-2 rounded-full bg-slate-100 overflow-hidden"><div class="h-full rounded-full bg-teal-500" style="width:${(c.progress * 100).toFixed(0)}%"></div></div>
        <div class="mt-1 text-[10.5px] text-slate-400">${c.atoms} 个原子贡献</div>
      </div>`).join('') + '</div>';
}

function shareBars(elId, arr, key) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!(arr || []).length) { el.innerHTML = '<span class="text-slate-400">暂无数据</span>'; return; }
  const total = arr.reduce((a, r) => a + (r.trajs || 0), 0) || 1;
  const max = Math.max(...arr.map(r => r.trajs || 0)) || 1;
  el.innerHTML = arr.map(r => `
    <div class="flex items-center gap-2.5">
      <span class="w-24 text-slate-600 text-xs text-right truncate" title="${esc(r[key])}">${esc(r[key])}</span>
      ${bar(r.trajs / max * 100)}
      <span class="tabular-nums text-slate-500 w-9 text-right text-[11px]">${Math.round(r.trajs / total * 100)}%</span>
      <span class="tabular-nums text-slate-400 w-20 text-right text-[11px]">${r.trajs} · ${r.atoms} 原子</span>
    </div>`).join('');
}
async function loadDomain() {
  const d = await jc('api/v1/dashboard/by-domain');
  shareBars('eco-bars', d.by_ecosystem, 'ecosystem');
  shareBars('model-bars', d.by_model, 'model');
}

async function loadCost() {
  const c = await jc('api/v1/dashboard/cost');
  put('cost.today', money(c.today_usd));
  put('cost.total', money(c.total_usd));
  put('cost.tokens', tok(c.total_tokens));
  put('cost.calls', c.total_calls);
  rows('cost-model-body', (c.by_model || []).map(m =>
    `<tr><td class="py-2">${esc(m.model)}</td><td class="text-right tabular-nums">${tok(m.tokens)}</td><td class="text-right tabular-nums">${m.calls}</td><td class="text-right tabular-nums">${money(m.cost)}</td></tr>`).join(''),
    '还没有调用记录');
  rows('cost-step-body', (c.by_step || []).map(s =>
    `<tr><td class="py-2">${esc(s.step)}</td><td class="text-right tabular-nums">${tok(s.tokens)}</td><td class="text-right tabular-nums">${money(s.cost)}</td></tr>`).join(''),
    '还没有调用记录');
}

// ── 技能库 ───────────────────────────────────────────────────────
const STATE_BADGE = {
  main: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200',
  staging: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200',
  baby: 'bg-sky-50 text-sky-700 ring-1 ring-sky-200',
  unknown: 'bg-slate-100 text-slate-500',
};
const stateBadge = s =>
  `<span class="px-2 py-0.5 rounded-md text-[11px] font-medium ${STATE_BADGE[s] || STATE_BADGE.unknown}">${esc(s)}</span>`;

async function loadSkills() {
  const d = await jc('api/v1/dashboard/skills');
  const bs = d.by_state || {};
  const parts = Object.keys(bs).sort().map(k => `${k} ${bs[k]}`).join(' · ');
  put('skills.summary', `共 ${d.total} 个${parts ? ' · ' + parts : ''}`);
  rows('skills-body', (d.skills || []).map(s =>
    `<tr class="hover:bg-slate-50 cursor-pointer" data-skill-row="${esc(s.name)}">`
    + `<td class="py-2.5 font-medium text-teal-700">${esc(s.name)}</td>`
    + `<td>${stateBadge(s.state)}</td>`
    + `<td class="text-slate-500 max-w-[480px] truncate" title="${esc(s.description)}">${esc(s.description) || '—'}</td>`
    + `<td class="text-right tabular-nums">v${esc(s.version)}</td>`
    + `<td class="text-right tabular-nums">${s.candidates || 0}</td></tr>`).join(''),
    '技能库还是空的');
}

// 进化路径：git-log 式行视图（mockup ①）。main 泳道 x=22，staging/rejected x=64。
function renderGraph(g) {
  const all = (g.nodes || []).slice();
  // API 按 ts 降序；同秒提交（自动化链路常见）ts 相同,再按父子拓扑深度
  // 决序（子在前）——否则 HEAD 可能排到祖先下面。
  const bySha = new Map(all.map(n => [n.sha, n]));
  const gen = new Map();
  const depth = sha => {
    if (!bySha.has(sha)) return 0;
    if (gen.has(sha)) return gen.get(sha);
    gen.set(sha, 0); // 防环
    const d = 1 + Math.max(0, ...(bySha.get(sha).parents || []).map(depth));
    gen.set(sha, d);
    return d;
  };
  all.forEach(n => depth(n.sha));
  all.sort((a, b) => (b.ts - a.ts) || (gen.get(b.sha) - gen.get(a.sha)));
  const nodes = all.slice(0, 30);
  if (!nodes.length) return '<div class="text-slate-400 text-xs mt-3">还没有提交历史</div>';
  const ROW = 48, top = 24;
  const xOf = n => (n.lanes || []).includes('main') ? 22 : 64;
  const hasStg = nodes.some(n => xOf(n) === 64);
  const laneRows = x => nodes.map((n, i) => xOf(n) === x ? i : -1).filter(i => i >= 0);
  const laneLine = x => {
    const rs = laneRows(x);
    if (rs.length < 2) return '';
    return `<line x1="${x}" y1="${top + rs[0] * ROW}" x2="${x}" y2="${top + rs[rs.length - 1] * ROW}" stroke="#e2e8f0" stroke-width="2"/>`;
  };
  const dots = nodes.map((n, i) => {
    const y = top + i * ROW, x = xOf(n);
    if (n.decision === 'promoted') return `<circle cx="${x}" cy="${y}" r="6.5" fill="#10b981"/>`;
    if (n.decision === 'rejected') return `<circle cx="${x}" cy="${y}" r="6.5" fill="#f43f5e"/>`;
    if (n.is_head_staging) return `<circle cx="${x}" cy="${y}" r="6.5" fill="#fbbf24"/>`;
    return `<circle cx="${x}" cy="${y}" r="6.5" fill="#fff" stroke="#94a3b8" stroke-width="2"/>`;
  }).join('');
  const svg = `<svg width="88" height="${top + (nodes.length - 1) * ROW + 24}" class="shrink-0">
    <text x="22" y="10" font-size="9.5" fill="#94a3b8" text-anchor="middle">main</text>
    ${hasStg ? '<text x="64" y="10" font-size="9.5" fill="#94a3b8" text-anchor="middle">staging</text>' : ''}
    ${laneLine(22)}${laneLine(64)}${dots}</svg>`;
  const rowsHtml = nodes.map(n => {
    let sub = '', subCls = 'text-slate-400', rowCls = '';
    if (n.decision === 'promoted') {
      const d = n.decision_detail || {};
      sub = `晋升${d.staging_avg != null && d.main_avg != null ? ` · ${d.staging_avg} > ${d.main_avg}` : ''}${n.is_head_main ? ' · main HEAD' : ''}`;
      subCls = 'text-emerald-600';
    } else if (n.decision === 'rejected') {
      const d = n.decision_detail || {};
      sub = `回滚${d.staging_avg != null && d.main_avg != null ? ` · ${d.staging_avg} < ${d.main_avg}` : ''}`;
      subCls = 'text-rose-600';
    } else if (n.is_head_staging) {
      sub = '灰度观察中 · staging HEAD'; subCls = 'text-amber-700';
      rowCls = 'bg-amber-50/60 ring-1 ring-amber-100';
    } else if (n.is_head_main) {
      sub = 'main HEAD'; subCls = 'text-slate-500';
    } else {
      sub = (n.lanes || []).includes('main') ? 'main 提交' : 'staging 提交';
    }
    const rej = (n.lanes || []).includes('rejected') && n.decision !== 'rejected'
      ? ' <span class="px-1.5 py-0.5 rounded bg-rose-50 text-rose-600 text-[10px]">rejected</span>' : '';
    return `<div class="h-12 flex items-center justify-between gap-2 rounded-lg px-2 -mx-2 cursor-pointer hover:bg-slate-50 ${rowCls}" data-gnode="${esc(n.sha)}">
      <div class="min-w-0"><div class="font-medium truncate">${esc(n.subject) || '(无提交说明)'}${rej}</div>
        <div class="text-[11px] ${subCls}">${esc(sub)}</div></div>
      <code class="text-[11px] text-slate-400 shrink-0">${esc(n.sha.slice(0, 7))}</code></div>`;
  }).join('');
  const unloc = (g.decisions_unlocated || []).length;
  return `<div class="flex mt-3">${svg}<div class="flex-1 min-w-0" style="padding-top:2px">${rowsHtml}</div></div>
    <div class="flex gap-4 mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500 flex-wrap">
      <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>晋升</span>
      <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span>回滚</span>
      <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>观察中</span>
      <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-white ring-2 ring-slate-300"></span>普通提交</span>
      ${(g.nodes || []).length > 30 ? '<span class="text-slate-400">仅显示最近 30 个节点</span>' : ''}
    </div>
    ${unloc ? `<div class="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-400">${unloc} 条历史裁决无法定位到节点</div>` : ''}`;
}

// 得分趋势：main/staging 双折线（main 实线 blue-600 / staging 虚线 emerald-500）
function renderDual(daily) {
  const pts = (daily || []).filter(d => d.avg_ux != null);
  if (!pts.length) return '<div class="text-slate-400 text-xs mt-3">还没有使用打分</div>';
  const dates = [...new Set(pts.map(p => p.date))].sort();
  const W = 620, H = 200, L = 34, R = 12, T = 16, B = 26;
  const xOf = d => dates.length > 1
    ? L + dates.indexOf(d) / (dates.length - 1) * (W - L - R) : (L + W - R) / 2;
  const yOf = v => T + (10 - v) / 10 * (H - T - B);
  const series = side => pts.filter(p => p.side === side)
    .sort((a, b) => a.date < b.date ? -1 : 1);
  const line = (arr, color, dash) => {
    if (!arr.length) return '';
    const path = arr.map((p, i) => `${i ? 'L' : 'M'}${xOf(p.date).toFixed(1)} ${yOf(p.avg_ux).toFixed(1)}`).join(' ');
    return `<path d="${path}" fill="none" stroke="${color}" stroke-width="2"${dash ? ' stroke-dasharray="5 4"' : ''}/>`
      + arr.map(p => `<circle cx="${xOf(p.date).toFixed(1)}" cy="${yOf(p.avg_ux).toFixed(1)}" r="3.5" fill="${color}" stroke="#fff" stroke-width="1.5"><title>${esc(p.date)} ${esc(p.side)} ${p.avg_ux} · ${p.n} 份</title></circle>`).join('');
  };
  const grid = [2, 4, 6, 8, 10].map(v =>
    `<line x1="${L}" y1="${yOf(v)}" x2="${W - R}" y2="${yOf(v)}" stroke="#f1f5f9"/>`
    + `<text x="${L - 5}" y="${yOf(v) + 3}" font-size="10" fill="#94a3b8" text-anchor="end">${v}</text>`).join('');
  const step = Math.max(1, Math.ceil(dates.length / 6));
  const xlabels = dates.filter((_, i) => i % step === 0 || i === dates.length - 1).map(d =>
    `<text x="${xOf(d).toFixed(1)}" y="${H - 8}" font-size="10" fill="#94a3b8" text-anchor="middle">${esc(d.slice(5))}</text>`).join('');
  return `<div class="flex gap-4 text-[11px] text-slate-500 mt-2">
      <span class="flex items-center gap-1.5"><span class="w-4 h-0.5 bg-blue-600 rounded"></span>main</span>
      <span class="flex items-center gap-1.5"><svg width="16" height="2"><line x1="0" y1="1" x2="16" y2="1" stroke="#10b981" stroke-width="2" stroke-dasharray="4 3"/></svg>staging</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" class="w-full mt-1" style="max-height:220px">${grid}
      <line x1="${L}" y1="${H - B + 4}" x2="${W - R}" y2="${H - B + 4}" stroke="#e2e8f0"/>${xlabels}
      ${line(series('main'), '#2563eb', false)}${line(series('staging'), '#10b981', true)}</svg>`;
}

// 血缘：贡献来源（用户占比条 + 模型 chips）与贡献原子列表
function renderLineage(lin) {
  const byUser = lin.by_user || [];
  const maxU = Math.max(...byUser.map(u => u.atoms), 1);
  const userRows = byUser.map(u => `
    <div class="flex items-center gap-2.5">
      ${avatar(u.user)}<span class="w-16 text-slate-600 truncate" title="${esc(u.user)}">${esc(u.user)}</span>
      ${bar(u.atoms / maxU * 100)}
      <span class="tabular-nums text-slate-700 font-medium w-6 text-right">${u.atoms}</span>
    </div>`).join('') || '<span class="text-slate-400 text-xs">还没有贡献原子</span>';
  const modelChips = (lin.by_model || []).map(m =>
    `<span class="px-2.5 py-1 rounded-lg bg-slate-100 text-xs text-slate-600">${esc(m.model)} <b class="text-slate-800">${m.atoms}</b></span>`).join(' ');
  const atomRows = (lin.atoms || []).map(a => {
    const clickable = !a.source_cleaned && a.traj_id;
    const title = a.source_cleaned
      ? '<span class="text-slate-400">源已清理 <span class="text-[11px]">（原子文件已过期回收，保留记录）</span></span>'
      : `<span class="text-slate-800">${esc(a.intent) || esc(a.atom_id)}</span>`;
    const st = a.state === 'adopted'
      ? '<span class="text-[10.5px] text-emerald-600">已采纳</span>'
      : '<span class="text-[10.5px] text-amber-600">候选中</span>';
    return `<div class="py-2.5 flex items-center justify-between gap-2 ${clickable ? 'cursor-pointer hover:bg-slate-50 rounded-lg px-2 -mx-2' : ''}"
        ${clickable ? `data-atom-jump="${esc(a.traj_id)}/${esc(a.atom_id)}"` : ''}>
      <div class="min-w-0"><div class="truncate">${title}</div>
        <div class="text-[11px] ${a.source_cleaned ? 'text-slate-300' : 'text-slate-400'}">${esc(a.user)} · ${esc(a.model)} ${st}</div></div>
      <span class="px-2 py-0.5 rounded-md ${a.source_cleaned ? 'bg-slate-50 text-slate-400' : 'bg-teal-50 text-teal-700'} text-[11px] font-semibold tabular-nums shrink-0">${a.weightscore != null ? esc(a.weightscore) : '—'}</span>
    </div>`;
  }).join('') || '<div class="text-slate-400 text-xs py-2">还没有贡献原子</div>';
  return { userRows, modelChips, atomRows };
}

function renderDiff(diff) {
  if (!diff) return '<span class="text-slate-400">无 diff</span>';
  return '<pre class="text-[11.5px] leading-relaxed overflow-x-auto">' + diff.split('\n').map(line => {
    const e = esc(line);
    if (line.startsWith('+') && !line.startsWith('+++')) return `<span class="block bg-emerald-50 text-emerald-800">${e}</span>`;
    if (line.startsWith('-') && !line.startsWith('---')) return `<span class="block bg-rose-50 text-rose-800">${e}</span>`;
    if (line.startsWith('@@')) return `<span class="block text-violet-600">${e}</span>`;
    return e;
  }).join('\n') + '</pre>';
}

let _curSkill = null;
async function openSkill(name) {
  _curSkill = name;
  const box = document.getElementById('skill-detail');
  box.innerHTML = `<div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5 text-slate-400">加载 ${esc(name)} …</div>`;
  const [dR, gR, uR, lR, tR] = await Promise.allSettled([
    jc('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/detail'),
    jc('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/graph'),
    jc('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/ux/daily'),
    jc('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/lineage'),
    jc('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/tree'),
  ]);
  if (dR.status === 'rejected') {
    box.innerHTML = `<div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5 text-rose-600">加载失败：${esc(dR.reason)}</div>`;
    return;
  }
  const d = dR.value;
  const g = gR.status === 'fulfilled' ? gR.value : null;
  const daily = uR.status === 'fulfilled' ? uR.value.daily : [];
  const lin = lR.status === 'fulfilled' ? lR.value : { atoms: [], by_user: [], by_model: [], uses: 0, avg_ux: null };
  const tree = tR.status === 'fulfilled' ? tR.value : { files: [] };

  const heads = (g && g.heads) || {};
  const headChips = [
    heads.main ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-xs font-medium text-slate-600">main <code class="text-slate-400">${esc(heads.main.slice(0, 7))}</code></span>` : '',
    heads.staging ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50 ring-1 ring-amber-200 text-xs font-medium text-amber-700"><span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span>staging 灰度中 <code class="opacity-60">${esc(heads.staging.slice(0, 7))}</code></span>` : '',
  ].join(' ');

  const vrows = (d.versions || []).map(v =>
    `<tr><td class="py-2"><code class="text-[11px]">${esc((v.sha || '').slice(0, 8))}</code></td>`
    + `<td class="text-right tabular-nums">${v.triggers}</td>`
    + `<td class="text-right tabular-nums">${ux(v.avg_ux)}</td>`
    + `<td class="text-right tabular-nums">${v.atoms}</td>`
    + `<td class="text-slate-500 pl-4">${fdate(v.first_ts).slice(0, 10)}</td></tr>`).join('')
    || '<tr><td colspan="5" class="py-2 text-slate-400">还没有版本触发数据</td></tr>';
  const byUserRows = (d.by_user || []).map(u =>
    `<tr><td class="py-2"><span class="flex items-center gap-2">${avatar(u.user, 'sm')}${esc(u.user)}</span></td>`
    + `<td class="text-right tabular-nums">${u.triggers}</td>`
    + `<td class="text-right tabular-nums">${ux(u.avg_ux)}</td></tr>`).join('')
    || '<tr><td colspan="3" class="py-2 text-slate-400">还没有触发记录</td></tr>';

  const L = renderLineage(lin);
  const fileItems = (tree.files || []).map(f =>
    `<a href="javascript:void(0)" class="skf block px-2 py-1 rounded hover:bg-slate-50 text-xs text-slate-600" data-skill="${esc(name)}" data-path="${esc(f.path)}">${esc(f.path)} <span class="text-slate-300">(${f.size})</span></a>`).join('')
    || '<span class="text-slate-400 text-xs px-2">空目录</span>';
  const gitItems = (d.versions_git || []).map(v =>
    `<a href="javascript:void(0)" class="skd block px-2 py-1 rounded hover:bg-slate-50 text-xs text-slate-600" data-skill="${esc(name)}" data-sha="${esc(v.sha)}"><code class="text-[11px] text-slate-400">${esc(v.short)}</code> ${esc(v.subject)}</a>`).join('')
    || '<span class="text-slate-400 text-xs px-2">非 git 仓</span>';

  box.innerHTML = `
  <div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5">
    <div class="text-xs text-slate-400 mb-1.5">技能库 <span class="mx-1">/</span> <span class="text-slate-600">${esc(name)}</span></div>
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <h2 class="text-lg font-bold tracking-tight">${esc(name)}</h2>
        <div class="text-slate-500 text-xs mt-1">总触发 <b class="text-slate-800 tabular-nums">${d.total_triggers}</b> 次
          · 贡献原子 <b class="text-slate-800 tabular-nums">${(lin.atoms || []).length}</b> 个
          ${lin.avg_ux != null ? `· 血缘平均 ux <b class="text-slate-800 tabular-nums">${lin.avg_ux}</b>` : ''}</div>
      </div>
      <div class="flex gap-2">${headChips}</div>
    </div>

    <div class="grid grid-cols-12 gap-4 mt-4">
      <div class="col-span-12 lg:col-span-5 rounded-2xl ring-1 ring-slate-200 p-5">
        <div class="flex items-baseline justify-between">
          <h3 class="font-semibold text-sm">进化路径</h3>
          <span class="text-[11px] text-slate-400">点击节点查看该版本 diff</span>
        </div>
        ${g ? renderGraph(g) : '<div class="text-slate-400 text-xs mt-3">非 git 仓，暂无进化路径</div>'}
      </div>
      <div class="col-span-12 lg:col-span-7 space-y-4">
        <div class="rounded-2xl ring-1 ring-slate-200 p-5">
          <h3 class="font-semibold text-sm">得分趋势 <span class="font-normal text-[11px] text-slate-400 ml-2">ux 日均 · 悬停节点看当日样本数</span></h3>
          ${renderDual(daily)}
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="rounded-2xl ring-1 ring-slate-200 p-5">
            <h3 class="font-semibold text-sm">贡献来源 <span class="font-normal text-[11px] text-slate-400 ml-1">${(lin.atoms || []).length} 个原子</span></h3>
            <div class="mt-3 space-y-2.5">${L.userRows}</div>
            ${L.modelChips ? `<div class="mt-4 pt-3 border-t border-slate-100 text-[11px] text-slate-400">来源模型</div><div class="mt-2 flex gap-2 flex-wrap">${L.modelChips}</div>` : ''}
          </div>
          <div class="rounded-2xl ring-1 ring-slate-200 p-5">
            <h3 class="font-semibold text-sm">贡献原子 <span class="font-normal text-[11px] text-slate-400 ml-1">点击跳原子详情</span></h3>
            <div class="mt-1 divide-y divide-slate-100 max-h-72 overflow-y-auto">${L.atomRows}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-12 gap-4 mt-4">
      <div class="col-span-12 lg:col-span-7">
        <h3 class="font-semibold text-sm">版本统计 <span class="font-normal text-[11px] text-slate-400 ml-1">触发 / UX / 去重原子 / 首用</span></h3>
        <div class="overflow-x-auto"><table class="w-full mt-1 text-[12.5px]">
          <thead><tr class="text-[11px] text-slate-400 border-b border-slate-100"><th class="text-left font-medium py-2">版本</th><th class="text-right font-medium">触发</th><th class="text-right font-medium">UX</th><th class="text-right font-medium">原子</th><th class="text-left font-medium pl-4">首用</th></tr></thead>
          <tbody class="divide-y divide-slate-50">${vrows}</tbody></table></div>
      </div>
      <div class="col-span-12 lg:col-span-5">
        <h3 class="font-semibold text-sm">按用户</h3>
        <div class="overflow-x-auto"><table class="w-full mt-1 text-[12.5px]">
          <thead><tr class="text-[11px] text-slate-400 border-b border-slate-100"><th class="text-left font-medium py-2">用户</th><th class="text-right font-medium">触发</th><th class="text-right font-medium">UX</th></tr></thead>
          <tbody class="divide-y divide-slate-50">${byUserRows}</tbody></table></div>
      </div>
    </div>

    <div class="grid grid-cols-12 gap-4 mt-4">
      <div class="col-span-12 md:col-span-4">
        <h3 class="font-semibold text-sm">文件目录</h3>
        <div class="mt-1 max-h-44 overflow-y-auto rounded-xl ring-1 ring-slate-100 py-1">${fileItems}</div>
        <h3 class="font-semibold text-sm mt-3">版本（点击看 diff）</h3>
        <div class="mt-1 max-h-36 overflow-y-auto rounded-xl ring-1 ring-slate-100 py-1">${gitItems}</div>
      </div>
      <div class="col-span-12 md:col-span-8">
        <h3 class="font-semibold text-sm">预览 / diff</h3>
        <div id="skill-preview" class="mt-1 rounded-xl ring-1 ring-slate-100 p-3 max-h-80 overflow-auto"><span class="text-slate-400 text-xs">点左侧文件或版本、或进化路径节点查看</span></div>
      </div>
    </div>

    <div id="skill-trigger" class="mt-4"><div class="text-slate-400 text-xs">加载离线触发评测…</div></div>
  </div>`;
  box.scrollIntoView({ behavior: 'smooth' });
  loadTriggerPanel(name).catch(console.error);
}

// 离线探针触发率面板（描述质量信号；区别于"总触发"的线上真实使用率）
function pctf(x) { return Math.round((Number(x) || 0) * 100) + '%'; }
async function loadTriggerPanel(name) {
  const el = document.getElementById('skill-trigger');
  if (!el) return;
  let hist = { history: [] }, cases = { cases: [], exp: null };
  try { hist = await j('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/trigger'); } catch (e) { /* 空 */ }
  try { cases = await j('api/v1/dashboard/skill/' + encodeURIComponent(name) + '/trigger/cases'); } catch (e) { /* 空 */ }
  const hrows = (hist.history || []).map(h =>
    `<tr><td class="py-2"><code class="text-[11px]">${esc((h.version_sha || '—').slice(0, 8))}</code></td>`
    + `<td class="text-right tabular-nums">${pctf(h.test_score)}</td>`
    + `<td class="text-right tabular-nums">${pctf(h.train_score)}</td>`
    + `<td class="text-right tabular-nums">${h.n_cases}</td>`
    + `<td class="text-right tabular-nums">${h.catalog_size}</td>`
    + `<td class="text-slate-500 pl-4">${fdate(h.ts)}</td></tr>`).join('')
    || '<tr><td colspan="6" class="py-2 text-slate-400">还没有离线触发评测</td></tr>';
  const crows = (cases.cases || []).map(c =>
    `<tr><td class="py-2 max-w-[280px] truncate" title="${esc(c.query)}">${esc(c.query)}</td>`
    + `<td class="text-center">${c.should_trigger ? '是' : '否'}</td>`
    + `<td class="text-center">${c.did_trigger ? '触发' : '未触发'}</td>`
    + `<td class="text-center">${c.passed
      ? '<span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[10.5px] font-medium">通过</span>'
      : '<span class="px-1.5 py-0.5 rounded bg-rose-50 text-rose-600 text-[10.5px] font-medium">未过</span>'}</td>`
    + `<td class="text-slate-400 text-[11px] max-w-[200px] truncate" title="${esc((c.catalog || []).join(', '))}">${esc((c.catalog || []).join(', '))}</td>`
    + `<td class="text-right"><button class="trig-rerun px-2.5 py-1 rounded-lg ring-1 ring-slate-200 text-[11px] text-slate-600 hover:bg-slate-50" data-skill="${esc(name)}" data-query="${esc(c.query)}">重跑</button></td></tr>`).join('')
    || '<tr><td colspan="6" class="py-2 text-slate-400">无 case（该 skill 还没跑过触发优化）</td></tr>';
  el.innerHTML = `<h3 class="font-semibold text-sm">离线探针触发率 <span class="font-normal text-[11px] text-slate-400 ml-1">描述质量信号——真跑代理在语义相关技能清单里抢触发；区别于上方"总触发"的线上真实使用</span></h3>
    <div class="overflow-x-auto"><table class="w-full mt-1 text-[12.5px]">
      <thead><tr class="text-[11px] text-slate-400 border-b border-slate-100"><th class="text-left font-medium py-2">版本</th><th class="text-right font-medium">test 触发率</th><th class="text-right font-medium">train</th><th class="text-right font-medium">cases</th><th class="text-right font-medium">诱饵数</th><th class="text-left font-medium pl-4">时间</th></tr></thead>
      <tbody class="divide-y divide-slate-50">${hrows}</tbody></table></div>
    <h3 class="font-semibold text-sm mt-3">逐 case <span class="font-normal text-[11px] text-slate-400 ml-1">实验 ${esc(cases.exp || '—')} · 点"重跑"用当前描述真跑一轮探针</span></h3>
    <div class="overflow-x-auto"><table class="w-full mt-1 text-[12.5px]">
      <thead><tr class="text-[11px] text-slate-400 border-b border-slate-100"><th class="text-left font-medium py-2">query</th><th class="text-center font-medium">应触发</th><th class="text-center font-medium">实测</th><th class="text-center font-medium">判定</th><th class="text-left font-medium">诱饵清单</th><th></th></tr></thead>
      <tbody class="divide-y divide-slate-50">${crows}</tbody></table></div>`;
}

// ── 轨迹 & 原子 ──────────────────────────────────────────────────
let _curTraj = null;
async function openTraj(trajId, atomId) {
  _curTraj = trajId;
  const box = document.getElementById('traj-detail');
  box.innerHTML = `<div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5 text-slate-400">加载 ${esc(trajId)} …</div>`;
  let meta, atoms;
  try {
    [meta, atoms] = await Promise.all([
      jc('api/v1/dashboard/traj/' + encodeURIComponent(trajId)),
      jc('api/v1/dashboard/traj/' + encodeURIComponent(trajId) + '/atoms'),
    ]);
  } catch (e) {
    box.innerHTML = `<div class="bg-white rounded-2xl ring-1 ring-slate-200 p-5 text-rose-600">轨迹加载失败：${esc(e.message)}</div>`;
    return;
  }
  const list = atoms.atoms || [];
  const steps = list.map((a, i) => {
    const orphan = a.chain === 'orphan';
    const num = String(i + 1).padStart(2, '0');
    return `<div class="flex flex-col items-center w-36 shrink-0 text-center cursor-pointer atom-step" data-atom="${esc(a.atom_id)}" ${orphan ? 'title="链表断裂，按位置排序"' : ''}>
      <div class="w-9 h-9 rounded-full ${orphan ? 'bg-amber-400 ring-4 ring-amber-100 text-white' : 'bg-white ring-2 ring-slate-300 text-slate-500'} flex items-center justify-center text-[11px] font-semibold z-10 atom-dot">${num}</div>
      <div class="mt-2.5 font-medium text-slate-700 text-xs line-clamp-2" title="${esc(a.intent)}">${esc(a.intent) || esc(a.atom_id)}</div>
      <div class="text-[11px] text-slate-400 mt-0.5">${a.ux_score != null ? 'ux ' + esc(a.ux_score) : ''}</div>
    </div>`;
  }).join('');
  box.innerHTML = `
  <div class="bg-white rounded-2xl ring-1 ring-slate-200 p-6">
    <div class="text-xs text-slate-400 mb-1.5">轨迹 &amp; 原子 <span class="mx-1">/</span> <span class="text-slate-600 font-mono">${esc(trajId)}</span></div>
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <h2 class="text-lg font-bold tracking-tight font-mono break-all">${esc(trajId)}</h2>
      <div class="flex gap-2 text-xs flex-wrap">
        <span class="px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-slate-600">${esc(meta.harness) || '?'} · ${esc(meta.model) || '?'}</span>
        <span class="px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-slate-600">${esc(meta.user)}</span>
        <span class="px-2.5 py-1 rounded-lg ${meta.status === 'done' ? 'bg-emerald-50 ring-1 ring-emerald-200 text-emerald-700' : meta.status === 'error' ? 'bg-rose-50 ring-1 ring-rose-200 text-rose-700' : 'bg-slate-100 ring-1 ring-slate-200 text-slate-600'} font-medium">${esc(meta.status)}</span>
        <span class="px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-slate-600">原子 <b class="tabular-nums">${meta.atoms}</b></span>
        <span class="px-2.5 py-1 rounded-lg bg-white ring-1 ring-slate-200 text-slate-600">${fdate(meta.discovered_at)}</span>
      </div>
    </div>
    <h3 class="font-semibold text-sm mt-6">原子时间线 <span class="font-normal text-[11px] text-slate-400 ml-2">按链表序 pre/post_atom_id · 点击节点查看详情</span></h3>
    ${list.length ? `<div class="relative mt-6 overflow-x-auto pb-2">
      <div class="relative flex gap-2 min-w-max px-2">
        <div class="absolute left-6 right-6 top-[17px] h-0.5 bg-slate-200"></div>
        ${steps}
      </div></div>` : '<div class="text-slate-400 text-xs mt-3">该轨迹还没有拆出原子</div>'}
    <div id="atom-detail" class="mt-4"></div>
  </div>`;
  if (atomId) openAtom(trajId, atomId).catch(console.error);
  else if (list.length) openAtom(trajId, list[0].atom_id).catch(console.error);
}

async function openAtom(trajId, atomId) {
  const el = document.getElementById('atom-detail');
  if (!el) return;
  el.innerHTML = '<div class="text-slate-400 text-xs">加载原子…</div>';
  // 高亮选中节点
  document.querySelectorAll('.atom-step .atom-dot').forEach(d => {
    d.classList.remove('bg-teal-600', 'ring-4', 'ring-teal-100', 'text-white');
  });
  const sel = document.querySelector(`.atom-step[data-atom="${CSS.escape(atomId)}"] .atom-dot`);
  if (sel && !sel.classList.contains('bg-amber-400')) {
    sel.classList.remove('bg-white', 'ring-2', 'ring-slate-300', 'text-slate-500');
    sel.classList.add('bg-teal-600', 'ring-4', 'ring-teal-100', 'text-white');
  }
  let a;
  try {
    a = await jc('api/v1/dashboard/traj/' + encodeURIComponent(trajId) + '/atom/' + encodeURIComponent(atomId));
  } catch (e) {
    el.innerHTML = `<div class="text-rose-600 text-xs">原子加载失败:${esc(e.message)}</div>`;
    return;
  }
  const chips = arr => (arr || []).map(t =>
    `<span class="px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[11px]">${esc(t)}</span>`).join(' ') || '<span class="text-slate-300">—</span>';
  const skillChips = (a.used_skills || []).map(s =>
    `<span class="skill-jump px-2 py-0.5 rounded-md bg-teal-50 ring-1 ring-teal-200 text-teal-700 text-[11px] font-medium cursor-pointer" data-skill="${esc(s)}">${esc(s)}</span>`).join(' ') || '<span class="text-slate-300">—</span>';
  const dest = (a.destinations || []).map(d =>
    `<span class="skill-jump text-teal-700 font-medium underline decoration-teal-200 underline-offset-2 cursor-pointer" data-skill="${esc(d.skill)}">${esc(d.skill)}</span>
     <span class="text-slate-500">（weightscore ${d.weightscore != null ? esc(d.weightscore) : '—'} · ${d.state === 'adopted' ? '已采纳' : '候选中'}）</span>`).join('<br>')
    || '<span class="text-slate-400">未进入任何 skill</span>';
  const rawBlock = a.raw_status === 'source_cleaned'
    ? '<div class="rounded-xl bg-slate-900 p-4 font-mono text-[11.5px] text-rose-400">源已清理（轨迹原文已过期回收，保留原子记录）</div>'
    : `<div class="rounded-xl bg-slate-900 p-4 font-mono text-[11.5px] leading-relaxed text-slate-300 whitespace-pre-wrap max-h-80 overflow-auto">${esc(a.raw || '')}${a.raw_total_chars > 8000 ? `\n<span class="text-slate-500">（截取 8000/${a.raw_total_chars} 字符）</span>` : ''}</div>`;
  el.innerHTML = `
  <div class="rounded-2xl ring-1 ring-slate-200 p-5">
    <div class="flex items-center justify-between">
      <h3 class="font-semibold text-sm font-mono break-all">${esc(a.atom_id)}</h3>
      ${a.ux_score != null ? `<span class="px-2 py-0.5 rounded-md bg-teal-50 text-teal-700 text-[11px] font-semibold tabular-nums">ux ${esc(a.ux_score)}</span>` : ''}
    </div>
    <dl class="mt-4 space-y-3">
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">intent</dt><dd class="text-slate-800">${esc(a.intent) || '—'}</dd></div>
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">summary</dt><dd class="text-slate-800">${esc(a.summary) || '—'}</dd></div>
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">tags</dt><dd class="flex gap-1.5 flex-wrap">${chips(a.tags)}</dd></div>
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">used_skills</dt><dd class="flex gap-1.5 flex-wrap">${skillChips}</dd></div>
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">去向</dt><dd class="text-slate-800">${dest}</dd></div>
      <div class="flex gap-4"><dt class="w-20 text-slate-400 shrink-0">offset</dt><dd class="tabular-nums text-slate-600">行 ${a.offset_start} – ${a.offset_end}</dd></div>
    </dl>
    <div class="text-[11px] text-slate-400 mt-5 mb-1.5">原文切片（按 offset 行号定位 · 只读）</div>
    ${rawBlock}
  </div>`;
}

async function loadDirs() {
  const d = await jc('api/v1/dashboard/dirs');
  rows('dirs-body', (d.dirs || []).map(x =>
    `<tr><td class="py-2"><span class="px-2 py-0.5 rounded-md bg-teal-50 text-teal-700 text-[11px] font-medium">${esc(x.ecosystem || 'manual')}</span></td>`
    + `<td class="text-right tabular-nums">${x.traj_count}</td>`
    + `<td class="text-right tabular-nums">${x.indexed_count}</td>`
    + `<td class="pl-6 text-slate-500 font-mono text-[11px]">${esc(x.path)}</td></tr>`).join(''),
    '还没有注册目录');
}

// ── 用户 & 画像 ──────────────────────────────────────────────────
async function loadUsersStatus() {
  const d = await jc('api/v1/dashboard/users/status');
  const users = d.users || [];
  put('users.online', `在线 ${d.online} / ${users.length}`);
  const rEl = document.getElementById('users-reason');
  if (d.reason) rEl.innerHTML = `<div class="mt-2 rounded-xl bg-slate-50 ring-1 ring-slate-100 px-3.5 py-2 text-[11px] text-slate-400">${esc(d.reason)}</div>`;
  rows('ustatus-body', users.map(u => {
    const hs = (u.harness || []).slice(0, 2).map(h =>
      `<span class="px-1.5 py-0.5 rounded bg-teal-50 text-teal-700 text-[10.5px]">${esc(h.harness)} ${h.pct}%</span>`).join(' ') || '<span class="text-slate-300">—</span>';
    const topM = (u.models || [])[0];
    const model = u.trajs <= 1
      ? '<span class="text-slate-400">样本不足</span>'
      : topM ? `${esc(topM.model)} <span class="text-slate-400">${topM.pct}%</span>` : '<span class="text-slate-300">—</span>';
    return `<tr data-uid="${esc(u.user)}" class="cursor-pointer hover:bg-slate-50">
      <td class="py-2.5"><span class="flex items-center gap-2">${avatar(u.user)}<b>${esc(u.user)}</b></span></td>
      <td>${u.online
        ? '<span class="inline-flex items-center gap-1.5 text-emerald-600 font-medium text-xs"><span class="w-2 h-2 rounded-full bg-emerald-500"></span>在线</span>'
        : '<span class="inline-flex items-center gap-1.5 text-slate-400 text-xs"><span class="w-2 h-2 rounded-full bg-slate-300"></span>离线</span>'}</td>
      <td class="text-slate-500 text-xs">${fdate(u.last_seen)}</td>
      <td class="text-right tabular-nums text-slate-600">${u.trajs} · ${u.atoms}</td>
      <td class="pl-6">${hs}</td>
      <td class="text-slate-600 text-xs">${model}</td></tr>`;
  }).join(''), '暂无团队用户（非 team server 或尚无 client 连接）');
}

async function loadTags() {
  const d = await jc('api/v1/dashboard/tags');
  const el = document.getElementById('tagcloud');
  const tags = d.tags || [];
  if (!el) return;
  if (!tags.length) { el.innerHTML = '<span class="text-slate-400">暂无标签（轨迹还没拆出带 tags 的原子）</span>'; return; }
  const max = Math.max(...tags.map(t => t.count)), min = Math.min(...tags.map(t => t.count));
  el.innerHTML = tags.map(t => {
    const sz = (12 + (max > min ? (t.count - min) / (max - min) * 16 : 4)).toFixed(0);
    const users = (t.users || []).map(esc).join(' ');
    return `<span class="tagchip inline-block px-2 py-0.5 rounded-lg bg-teal-50 text-teal-700 mr-2 mb-1" data-users="${users}" title="${esc(t.count)} 次" style="font-size:${sz}px">${esc(t.tag)}</span>`;
  }).join(' ');
}

// 用户 ⇄ 标签联动：悬浮（或点击 pin）用户行 → 高亮其贡献的标签、淡化其余
let _pinnedUid = null;
function highlightUser(uid) {
  document.querySelectorAll('#tagcloud .tagchip').forEach(ch => {
    const us = (ch.dataset.users || '').split(' ').filter(Boolean);
    const on = uid && us.includes(uid);
    ch.classList.toggle('hot', !!on);
    ch.classList.toggle('dim', !!uid && !on);
  });
  document.querySelectorAll('#ustatus-body tr[data-uid]').forEach(tr =>
    tr.classList.toggle('bg-teal-50/40', !!uid && tr.dataset.uid === uid));
}
document.addEventListener('mouseover', e => {
  const tr = e.target.closest('#ustatus-body tr[data-uid]');
  if (tr && !_pinnedUid) highlightUser(tr.dataset.uid);
});
document.addEventListener('mouseout', e => {
  const tr = e.target.closest('#ustatus-body tr[data-uid]');
  if (tr && !_pinnedUid) highlightUser(null);
});

// ── 灰度 Canary ──────────────────────────────────────────────────
async function loadCanary() {
  const c = await jc('api/v1/dashboard/canary');
  rows('canary-body', (c.sides || []).map(s =>
    `<tr><td class="py-2">${s.side === 'staging'
      ? '<span class="px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 ring-1 ring-amber-200 text-[11px] font-medium">staging</span>'
      : `<span class="px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[11px] font-medium">${esc(s.side)}</span>`}</td>`
    + `<td class="text-right tabular-nums">${s.uses}</td>`
    + `<td class="text-right tabular-nums">${ux(s.avg_ux)}</td></tr>`).join(''),
    '还没有灰度使用记录');
}

// ── SPA-lite 路由（hash）─────────────────────────────────────────
const NAMES = { overview: '总览', skills: '技能库', traj: '轨迹 & 原子', users: '用户 & 画像', canary: '灰度 Canary' };
function showPage(pg) {
  if (!document.getElementById('pg-' + pg)) pg = 'overview';
  document.querySelectorAll('.sec-page').forEach(s => s.classList.remove('on'));
  document.getElementById('pg-' + pg).classList.add('on');
  document.querySelectorAll('#nav .nav-link').forEach(n => {
    const on = n.dataset.pg === pg;
    n.classList.toggle('bg-teal-50', on);
    n.classList.toggle('text-teal-800', on);
    n.classList.toggle('font-semibold', on);
    n.classList.toggle('text-slate-500', !on);
  });
  document.getElementById('pgname').textContent = NAMES[pg] || '总览';
  window.scrollTo(0, 0);
}
function route() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ''));
  const parts = h.split('/').filter(Boolean);
  if (parts[0] === 'traj' && parts[1]) {
    showPage('traj');
    openTraj(parts[1], parts[2]).catch(console.error);
    return;
  }
  if (parts[0] === 'skill' && parts[1]) {
    showPage('skills');
    openSkill(parts[1]).catch(console.error);
    return;
  }
  showPage(parts[0] || 'overview');
}
window.addEventListener('hashchange', route);

// ── 全局点击委托 ────────────────────────────────────────────────
document.addEventListener('click', async e => {
  const row = e.target.closest('[data-skill-row]');
  if (row) { location.hash = 'skill/' + encodeURIComponent(row.dataset.skillRow); return; }
  const sj = e.target.closest('.skill-jump');
  if (sj) { location.hash = 'skill/' + encodeURIComponent(sj.dataset.skill); return; }
  const aj = e.target.closest('[data-atom-jump]');
  if (aj) { location.hash = 'traj/' + aj.dataset.atomJump; return; }
  const step = e.target.closest('.atom-step');
  if (step && _curTraj) { openAtom(_curTraj, step.dataset.atom).catch(console.error); return; }
  const gn = e.target.closest('[data-gnode]');
  if (gn && _curSkill) {
    const pv = document.getElementById('skill-preview');
    if (pv) {
      pv.innerHTML = '<span class="text-slate-400 text-xs">加载 diff…</span>';
      try {
        const r = await j('api/v1/dashboard/skill/' + encodeURIComponent(_curSkill) + '/diff?sha=' + encodeURIComponent(gn.dataset.gnode));
        pv.innerHTML = renderDiff(r.diff);
      } catch (err) { pv.innerHTML = `<span class="text-rose-600 text-xs">${esc(err.message)}</span>`; }
      pv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    return;
  }
  const fl = e.target.closest('.skf');
  if (fl) {
    const r = await j('api/v1/dashboard/skill/' + encodeURIComponent(fl.dataset.skill) + '/file?path=' + encodeURIComponent(fl.dataset.path));
    const pv = document.getElementById('skill-preview');
    if (pv) pv.innerHTML = r.content != null
      ? `<pre class="text-[11.5px] whitespace-pre-wrap">${esc(r.content)}</pre>`
      : `<span class="text-rose-600 text-xs">${esc(r.error || 'error')}</span>`;
    return;
  }
  const dl = e.target.closest('.skd');
  if (dl) {
    const r = await j('api/v1/dashboard/skill/' + encodeURIComponent(dl.dataset.skill) + '/diff?sha=' + encodeURIComponent(dl.dataset.sha));
    const pv = document.getElementById('skill-preview');
    if (pv) pv.innerHTML = renderDiff(r.diff);
    return;
  }
  // 逐 case"重跑"：用当前描述真跑一轮探针，结果回填按钮
  const rb = e.target.closest('.trig-rerun');
  if (rb) {
    rb.disabled = true; rb.textContent = '跑…';
    try {
      const resp = await fetch('api/v1/dashboard/skill/' + encodeURIComponent(rb.dataset.skill) + '/trigger/rerun',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: rb.dataset.query }) });
      const data = await resp.json();
      rb.classList.remove('ring-slate-200', 'text-slate-600');
      if (data.error) { rb.textContent = '错误'; rb.classList.add('ring-rose-200', 'text-rose-600'); }
      else if (data.did_trigger) { rb.textContent = '已触发'; rb.classList.add('ring-emerald-200', 'text-emerald-700'); }
      else { rb.textContent = '未触发'; rb.classList.add('ring-slate-200', 'text-slate-400'); }
      rb.title = '诱饵清单: ' + ((data.catalog || []).join(', ') || '空');
    } catch (err) { rb.textContent = '错误'; }
    rb.disabled = false;
    return;
  }
  const pinTr = e.target.closest('#ustatus-body tr[data-uid]');
  if (pinTr) {
    _pinnedUid = (_pinnedUid === pinTr.dataset.uid) ? null : pinTr.dataset.uid;
    highlightUser(_pinnedUid);
  }
});

// 轨迹输入框
document.getElementById('traj-open').addEventListener('click', () => {
  const v = document.getElementById('traj-input').value.trim().replace(/\.md$/, '');
  if (v) location.hash = 'traj/' + encodeURIComponent(v);
});
document.getElementById('traj-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('traj-open').click();
});

// ── 启动：各端点独立加载，单个失败不拖垮整页 ───────────────────
route();
for (const f of [loadOverview, loadRates, loadPipeline, loadDomain, loadCost,
  loadSkills, loadDirs, loadUsersStatus, loadTags, loadCanary]) {
  f().catch(e => console.error(e));
}
