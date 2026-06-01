// xskill 控制台前端取数：启动后 fetch 真实端点填进 DOM。
async function j(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(u + ' ' + r.status);
  return r.json();
}

function put(sel, val) {
  document.querySelectorAll(`[data-m="${sel}"]`).forEach(e => { e.textContent = val; });
}

function renderDomain(rows, bodyId, key) {
  const tb = document.getElementById(bodyId);
  if (!rows || !rows.length) { tb.innerHTML = '<tr><td colspan="5" class="text-secondary">暂无数据</td></tr>'; return; }
  tb.innerHTML = rows.map(r => `<tr><td>${r[key]}</td>`
    + `<td class="text-end">${r.trajs}</td>`
    + `<td class="text-end">${r.avg_atoms}</td>`
    + `<td class="text-end">${r.skills}</td>`
    + `<td class="text-end">${r.avg_ux}</td></tr>`).join('');
}

async function loadOverview() {
  const o = await j('/api/v1/dashboard/overview');
  put('overview.trajs', o.trajs);
  put('overview.atoms', o.atoms);
  put('overview.avg_atoms_per_traj', o.avg_atoms_per_traj);
  put('overview.success_rate', o.success_rate + '%');
  put('overview.skill_yield', o.skill_yield + '%');
  put('overview.retry_rate', o.retry_rate + '%');
  put('overview.avg_ux', o.avg_ux);

  const d = await j('/api/v1/dashboard/by-domain');
  renderDomain(d.by_ecosystem, 'eco-body', 'ecosystem');
  renderDomain(d.by_model, 'model-body', 'model');
}

async function loadStatus() {
  try {
    const s = await j('/api/v1/stats');
    if (s.role) put('status.role', s.role);
    if (s.cost && typeof s.cost.today_usd === 'number') put('status.today_usd', '$' + s.cost.today_usd.toFixed(4));
  } catch (e) { /* stats 可选，失败不阻塞总览 */ }
}

// 左栏 + 卡片「详情 →」分区切换（事件委托）
const NAMES = { overview: '总览', cost: '成本 & 用量', profile: '用户 & 画像', eco: '生态目录' };
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

loadOverview().catch(e => console.error(e));
loadStatus();
