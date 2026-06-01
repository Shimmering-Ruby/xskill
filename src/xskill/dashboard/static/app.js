// xskill 控制台前端取数：启动后并发 fetch 各只读端点，填进 DOM。
async function j(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(u + ' ' + r.status);
  return r.json();
}
function put(sel, val) {
  document.querySelectorAll(`[data-m="${sel}"]`).forEach(e => { e.textContent = val; });
}
function rows(bodyId, html) {
  const tb = document.getElementById(bodyId);
  if (tb) tb.innerHTML = html || '<tr><td colspan="6" class="text-secondary">暂无数据</td></tr>';
}
const money = n => '$' + (Number(n) || 0).toFixed(4);
const tok = n => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : '' + n; };

async function loadOverview() {
  const o = await j('/api/v1/dashboard/overview');
  put('overview.trajs', o.trajs);
  put('overview.atoms', o.atoms);
  put('overview.atoms2', o.atoms);
  put('overview.avg_ux', o.avg_ux);
  put('overview.avg_atoms_per_traj', o.avg_atoms_per_traj);
  put('overview.skill_yield', o.skill_yield + '%');
  put('overview.success_rate', o.success_rate + '%');
  put('overview.retry_rate', o.retry_rate + '%');
  const h = o.price_health, el = document.getElementById('price-warn');
  if (el && h && h.ok === false) {
    const reason = { schema_changed: '上游格式变更', source_moved: '上游地址失效', unreachable: '上游不可达' }[h.kind] || '刷新异常';
    el.innerHTML = `<div class="alert alert-warning mb-0 py-2 px-3 small">⚠ 价格表 ${h.stale_days != null ? h.stale_days + 'd' : '从未'} 未刷新 · ${reason}，沿用旧价</div>`;
  }
}

async function loadRates() {
  const r = await j('/api/v1/dashboard/rates');
  put('rates.trigger', r.trigger.overall + '%');
  put('rates.adoption', r.adoption.rate + '%');
  put('rates.promotion', r.promotion.rate + '%');
  put('rates.promotion2', r.promotion.rate + '%');
  put('promotion.detail', `${r.promotion.promoted}/${r.promotion.decided} 已裁决`);
  rows('trigger-body', r.trigger.by_skill.map(s =>
    `<tr><td>${s.skill}</td><td class="text-end">${s.recommended}</td><td class="text-end">${s.used}</td><td class="text-end">${s.rate}%</td></tr>`).join(''));
}

async function loadDomain() {
  const d = await j('/api/v1/dashboard/by-domain');
  const mk = (arr, key) => arr.map(r =>
    `<tr><td>${r[key]}</td><td class="text-end">${r.trajs}</td><td class="text-end">${r.avg_atoms}</td><td class="text-end">${r.skills}</td><td class="text-end">${r.avg_ux}</td></tr>`).join('');
  rows('eco-body', mk(d.by_ecosystem, 'ecosystem'));
  rows('model-body', mk(d.by_model, 'model'));
}

async function loadCost() {
  const c = await j('/api/v1/dashboard/cost');
  put('cost.today', money(c.today_usd));
  put('cost.today2', money(c.today_usd));
  put('cost.total', money(c.total_usd));
  put('cost.tokens', tok(c.total_tokens));
  put('cost.calls', c.total_calls);
  rows('cost-model-body', (c.by_model || []).map(m =>
    `<tr><td>${m.model}</td><td class="text-end">${tok(m.tokens)}</td><td class="text-end">${m.calls}</td><td class="text-end">${money(m.cost)}</td></tr>`).join(''));
  rows('cost-step-body', (c.by_step || []).map(s =>
    `<tr><td>${s.step}</td><td class="text-end">${tok(s.tokens)}</td><td class="text-end">${money(s.cost)}</td></tr>`).join(''));
}

async function loadModels() {
  const m = await j('/api/v1/dashboard/models');
  rows('profile-model-body', (m.models || []).map(x =>
    `<tr><td>${x.model}</td><td class="text-end">${x.trajs}</td><td class="text-end">${x.pct}%</td></tr>`).join(''));
}

async function loadCanary() {
  const c = await j('/api/v1/dashboard/canary');
  rows('canary-body', (c.sides || []).map(s =>
    `<tr><td>${s.side}</td><td class="text-end">${s.trajs}</td><td class="text-end">${s.avg_ux}</td></tr>`).join(''));
}

async function loadDirs() {
  const d = await j('/api/v1/dashboard/dirs');
  rows('dirs-body', (d.dirs || []).map(x =>
    `<tr><td><span class="badge bg-teal-lt">${x.ecosystem || 'manual'}</span></td><td class="text-end">${x.traj_count}</td><td class="text-end">${x.indexed_count}</td><td class="text-secondary">${x.path}</td></tr>`).join(''));
}

// 分区切换（侧栏）
const NAMES = { overview: '总览', cost: '成本 & 用量', profile: '用户 & 画像', skills: '技能库', canary: '灰度 Canary', eco: '生态目录' };
document.body.addEventListener('click', e => {
  const a = e.target.closest('[data-pg]');
  if (!a) return;
  e.preventDefault();
  let pg = a.dataset.pg;
  if (!document.getElementById('pg-' + pg)) pg = 'overview';
  document.querySelectorAll('.sec-page').forEach(s => s.classList.remove('on'));
  document.getElementById('pg-' + pg).classList.add('on');
  document.querySelectorAll('#nav .nav-link').forEach(n => { n.classList.add('text-white-50'); n.classList.remove('text-white', 'active'); });
  const link = document.querySelector('#nav [data-pg="' + pg + '"]');
  if (link) { link.classList.add('text-white', 'active'); link.classList.remove('text-white-50'); }
  document.getElementById('pgname').textContent = NAMES[pg] || '总览';
  window.scrollTo(0, 0);
});

// 每个端点独立加载，互不阻塞——单个失败不拖垮整页
for (const f of [loadOverview, loadRates, loadDomain, loadCost, loadModels, loadCanary, loadDirs]) {
  f().catch(e => console.error(e));
}
