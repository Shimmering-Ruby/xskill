/* mock-api.js — 静态演示用：拦截 dashboard fetch，返回与官方 API 同形的 mock。
 * 不改 app.js 的请求路径；仅供 /display-html-aliyun 审阅。 */
(function () {
  const USERS = ['alice', 'bob', 'carol', 'dave', 'erin', 'frank'];
  const SKILLS = {
    'web-flask': { main: 'a1b2c3d4e5f6789012345678abcdef01', staging: 'f9e8d7c6b5a4938271605f4e3d2c1b0a', hasStaging: true, state: 'canary' },
    'sql-migrate': { main: '11223344556677889900aabbccddeeff', staging: '99887766554433221100ffeeddccbbaa', hasStaging: true, state: 'canary' },
    'git-housekeeping': { main: 'deadbeefcafebabe0123456789abcdef', staging: null, hasStaging: false, state: 'active' },
    'pytest-fixture': { main: 'abcdef0123456789fedcba9876543210', staging: 'c0ffeeabc1234567890fedcba9876543', hasStaging: true, state: 'canary' },
    'docker-compose': { main: '13579bdf02468ace97531eca86420bdf', staging: null, hasStaging: false, state: 'active' },
    'openapi-client': { main: '2468ace013579bdf86420bdf97531eca', staging: null, hasStaging: false, state: 'active' },
    'log-rotate': { main: '31415926535897932384626433832795', staging: null, hasStaging: false, state: 'active' },
    'csv-join': { main: '27182818284590452353602874713526', staging: null, hasStaging: false, state: 'active' },
  };
  const store = {
    globalPins: ['git-housekeeping'],
    userPins: { alice: ['pytest-fixture'], bob: [], carol: ['docker-compose'], dave: [], erin: [], frank: ['web-flask'] },
    blocked: { alice: [], bob: ['log-rotate'], carol: [], dave: [], erin: [], frank: [] },
    sideOverrides: { alice: { 'web-flask': 'staging' }, carol: { 'sql-migrate': 'main' } },
    autoSide: {
      alice: { 'web-flask': 'staging', 'sql-migrate': 'main', 'pytest-fixture': 'main' },
      bob: { 'web-flask': 'main', 'sql-migrate': 'staging', 'pytest-fixture': 'staging' },
      carol: { 'web-flask': 'staging', 'sql-migrate': 'staging', 'pytest-fixture': 'main' },
      dave: { 'web-flask': 'main', 'sql-migrate': 'main', 'pytest-fixture': 'main' },
      erin: { 'web-flask': 'staging', 'sql-migrate': 'main', 'pytest-fixture': 'staging' },
      frank: { 'web-flask': 'main', 'sql-migrate': 'staging', 'pytest-fixture': 'main' },
    },
    hist: { alice: 155, bob: 98, carol: 72, dave: 41, erin: 33, frank: 120 },
    ident: { user: 'admin', role: 'admin' },
  };

  function resolveSide(user, skill) {
    const sk = SKILLS[skill];
    if (!sk || !sk.hasStaging) return 'main';
    const ov = (store.sideOverrides[user] || {})[skill];
    if (ov) return ov;
    return (store.autoSide[user] || {})[skill] || 'main';
  }
  function shaOf(skill, side) {
    const sk = SKILLS[skill];
    return side === 'staging' && sk.staging ? sk.staging : sk.main;
  }
  function buildSlots(user) {
    const blocked = new Set(store.blocked[user] || []);
    const out = [];
    const push = (skill, bucket, source, pin_scope, user_removable) => {
      if (blocked.has(skill) || !SKILLS[skill] || out.some(x => x.skill_name === skill)) return;
      const side = resolveSide(user, skill);
      out.push({
        skill_name: skill, side, sha: shaOf(skill, side), bucket, source: 'native',
        pin_scope, user_removable, my_triggers: bucket === 'recommended' ? 0 : 1,
        side_mutable: !!SKILLS[skill].hasStaging,
        overridden: !!(store.sideOverrides[user] || {})[skill],
      });
    };
    store.globalPins.forEach(g => push(g, 'pinned', 'native', 'global', false));
    (store.userPins[user] || []).forEach(p => {
      const ov = !!(store.sideOverrides[user] || {})[p];
      push(p, 'pinned', 'native', 'user', !ov);
    });
    Object.keys(SKILLS).filter(s => !blocked.has(s) && !out.some(x => x.skill_name === s))
      .slice(0, 3).forEach(s => push(s, 'ranked', 'native'));
    Object.keys(SKILLS).filter(s => !blocked.has(s) && !out.some(x => x.skill_name === s))
      .slice(0, 2).forEach(s => push(s, 'recommended', 'native'));
    return out;
  }

  function json(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function pathOf(url) {
    // app.js 多数用相对路径 api/v1/...（无前导 /）；统一成 /api/v1/...
    let u = String(url);
    const q = u.indexOf('?');
    const query = q >= 0 ? u.slice(q) : '';
    if (q >= 0) u = u.slice(0, q);
    const i = u.indexOf('/api/');
    if (i >= 0) return u.slice(i) + query;
    if (u.startsWith('api/')) return '/' + u + query;
    return u + query;
  }

  async function handle(url, init) {
    const full = pathOf(url);
    const path = full.replace(/\?.*$/, '');
    const method = ((init && init.method) || 'GET').toUpperCase();
    let body = {};
    if (init && init.body) {
      try { body = JSON.parse(init.body); } catch (_e) { body = {}; }
    }

    if (path === '/api/v1/dashboard/me') return json(store.ident);
    if (path === '/api/v1/dashboard/login' && method === 'POST') {
      store.ident = { user: body.user_name || 'admin', role: body.user_name === 'admin' ? 'admin' : 'user' };
      return json(store.ident);
    }
    if (path === '/api/v1/dashboard/logout' && method === 'POST') {
      store.ident = null; return json({ ok: true });
    }

    if (path === '/api/v1/dashboard/overview') {
      return json({ trajs: 1284, atoms: 6120, avg_atoms_per_traj: 4.8, avg_ux: 7.2, success_rate: 0.91, retry_rate: 0.06, skill_yield: null });
    }
    if (path === '/api/v1/dashboard/pipeline') return json({ stages: [] });
    if (path === '/api/v1/dashboard/rates') {
      return json({
        trigger: { overall: 0.42, by_skill: Object.keys(SKILLS).slice(0, 4).map(s => ({ skill: s, recommended: 12, used: 5, rate: 42 })) },
        adoption: { rate: 0.61 },
        promotion: { rate: 0.55, promoted: 11, decided: 20 },
      });
    }
    if (path === '/api/v1/dashboard/by-domain') return json({ by_ecosystem: [], by_model: [] });
    if (path === '/api/v1/dashboard/cost') return json({ total: 12.3, by_model: [], by_step: [] });
    if (path === '/api/v1/dashboard/skills' || path.startsWith('/api/v1/dashboard/skills')) {
      const list = Object.keys(SKILLS).map(n => ({
        name: n,
        state: SKILLS[n].hasStaging ? 'staging' : 'main',
        description: n.replace(/-/g, ' ') + ' — mock skill',
        version: SKILLS[n].hasStaging ? 2 : 1,
        candidates: SKILLS[n].hasStaging ? 4 : 1,
        source: 'native',
      }));
      const by_state = {};
      list.forEach(s => { by_state[s.state] = (by_state[s.state] || 0) + 1; });
      // ?name= 定向查一条（skillSource / 详情用）
      const q = full.includes('?') ? new URLSearchParams(full.slice(full.indexOf('?') + 1)) : null;
      const nameQ = q && q.get('name');
      if (nameQ) {
        const hit = list.filter(s => s.name === nameQ);
        return json({ skills: hit, total: hit.length, by_state });
      }
      const limit = q ? parseInt(q.get('limit') || '0', 10) : 0;
      const offset = q ? parseInt(q.get('offset') || '0', 10) : 0;
      const page = limit > 0 ? list.slice(offset, offset + limit) : list;
      return json({ skills: page, total: list.length, by_state });
    }
    if (path === '/api/v1/dashboard/dirs') return json({ dirs: [] });
    if (path === '/api/v1/dashboard/users/status') {
      return json({
        online: 4,
        users: USERS.map(u => ({
          user: u, status: 'online', last_seen: '2026-07-30T12:00:00',
          client_version: '0.6.29', trajs: 40, atoms: 120, harness: 'claude_code', model: 'deepseek-v4',
        })),
      });
    }
    if (path === '/api/v1/dashboard/tags') return json({ tags: [{ tag: 'flask', n: 40 }, { tag: 'sql', n: 22 }] });
    if (path === '/api/v1/dashboard/canary') {
      return json({ sides: [{ side: 'main', uses: 800, avg_ux: 7.1 }, { side: 'staging', uses: 210, avg_ux: 7.4 }], promotion: { rate: 0.55, promoted: 11, decided: 20 } });
    }
    if (path === '/api/v1/dashboard/pipeline/live') return json({ pools: { split: { seats: [] }, cluster: { seats: [] }, edit: { seats: [] } } });

    // skill detail / graph / etc.
    const mDetail = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/detail$/);
    if (mDetail) {
      const name = decodeURIComponent(mDetail[1]);
      const sk = SKILLS[name] || { main: '0'.repeat(40) };
      return json({
        name, total_triggers: 42,
        versions: [{ sha: sk.main, triggers: 30, avg_ux: 7.2, atoms: 12, first_ts: '2026-07-01T00:00:00' }],
        by_user: USERS.slice(0, 3).map(u => ({ user: u, triggers: 5, avg_ux: 7 })),
        versions_git: [{ sha: sk.main, short: sk.main.slice(0, 7), subject: 'update SKILL.md' }],
      });
    }
    const mGraph = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/graph$/);
    if (mGraph) {
      const name = decodeURIComponent(mGraph[1]);
      const sk = SKILLS[name] || {};
      return json({
        heads: { main: sk.main, staging: sk.staging || null },
        nodes: [
          { sha: sk.main, subject: 'main HEAD', lanes: ['main'], is_head_main: true },
          ...(sk.staging ? [{ sha: sk.staging, subject: 'staging HEAD', lanes: ['staging'], is_head_staging: true }] : []),
        ],
        decisions_unlocated: [],
      });
    }
    const mUx = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/ux\/daily$/);
    if (mUx) return json({ daily: [] });
    const mLin = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/lineage$/);
    if (mLin) return json({ atoms: [], by_user: [], by_model: [], uses: 0, avg_ux: null });
    const mTree = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/tree$/);
    if (mTree) return json({ files: [{ path: 'SKILL.md', size: 1200 }] });
    const mTrig = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/trigger/);
    if (mTrig) return json({ history: [], cases: [] });

    const mRoute = path.match(/^\/api\/v1\/dashboard\/skill\/([^/]+)\/routing$/);
    if (mRoute) {
      const name = decodeURIComponent(mRoute[1]);
      const sk = SKILLS[name];
      if (!sk) return json({ error: 'not found' }, 404);
      const staging = [], main = [];
      USERS.forEach(u => {
        const hit = buildSlots(u).find(s => s.skill_name === name);
        if (!hit) return;
        const row = { user: u, bucket: hit.bucket, sha: hit.sha, overridden: hit.overridden };
        (hit.side === 'staging' ? staging : main).push(row);
      });
      return json({ has_staging: !!sk.hasStaging, staging, main });
    }

    // my
    if (path === '/api/v1/dashboard/my/manifest') {
      const user = (store.ident && store.ident.user === 'admin') ? 'alice' : (store.ident && store.ident.user) || 'alice';
      const slots = buildSlots(user);
      return json({
        slots: slots.map(s => ({
          ...s,
          pin_scope: s.pin_scope || null,
          user_removable: !!s.user_removable,
        })),
        blocked: (store.blocked[user] || []).map(skill_name => ({ skill_name })),
        total_slots: 100,
      });
    }
    if (path === '/api/v1/dashboard/my/contributions') {
      return json({ steps: { trajs: 18, atoms: 64, adopted_atoms: 22, skills: 5 } });
    }
    if (path.startsWith('/api/v1/dashboard/my/contributions/trajs')) {
      return json({ total: 0, trajs: [], skill_meta: {} });
    }
    if (path === '/api/v1/dashboard/my/reco-trigger') {
      return json({ user: 'alice', rows: Object.keys(SKILLS).slice(0, 4).map(skill => ({
        skill, exposures: 8, triggers: 3, rate: 0.375, verdict: '正常',
      })) });
    }
    if (path === '/api/v1/dashboard/my/prefs' && method === 'POST') {
      const user = (store.ident && store.ident.role === 'admin') ? 'alice' : (store.ident && store.ident.user) || 'alice';
      const skill = body.skill_name;
      if (body.action === 'pin') {
        store.userPins[user] = [...new Set([...(store.userPins[user] || []), skill])];
        store.blocked[user] = (store.blocked[user] || []).filter(x => x !== skill);
      } else if (body.action === 'block') {
        store.blocked[user] = [...new Set([...(store.blocked[user] || []), skill])];
        store.userPins[user] = (store.userPins[user] || []).filter(x => x !== skill);
      } else if (body.action === 'clear') {
        store.userPins[user] = (store.userPins[user] || []).filter(x => x !== skill);
        store.blocked[user] = (store.blocked[user] || []).filter(x => x !== skill);
        if (store.sideOverrides[user]) delete store.sideOverrides[user][skill];
      }
      return json({ ok: true });
    }

    // admin
    if (path === '/api/v1/dashboard/admin/users-matrix') {
      return json({
        global_pinned: store.globalPins,
        users: USERS.map(u => {
          const slots = buildSlots(u);
          const prefsPinned = (store.userPins[u] || []).length;
          const prefsBlocked = (store.blocked[u] || []).length;
          return {
            client_id: 'cid-' + u,
            user: u,
            client_version: '0.6.29',
            current_slots: slots.length,
            staging_slots: slots.filter(s => s.side === 'staging').length,
            exposures: store.hist[u],
            triggers: Math.round(store.hist[u] * 0.3),
            rate: 0.3,
            pinned: prefsPinned + (store.globalPins.length ? 1 : 0),
            blocked: prefsBlocked,
            stale_advice: [],
            ingest_paused: false,
          };
        }),
      });
    }
    if (path === '/api/v1/dashboard/admin/skills') {
      return json({
        skills: Object.keys(SKILLS).map(name => ({
          name, state: SKILLS[name].state, usage_30d: 20 + name.length,
        })),
      });
    }
    const mPrefs = path.match(/^\/api\/v1\/dashboard\/admin\/user\/([^/]+)\/prefs$/);
    if (mPrefs) {
      const user = decodeURIComponent(mPrefs[1]);
      const prefs = [
        ...(store.userPins[user] || []).map(skill_name => ({ skill_name, pref: 'pinned', set_by: 'admin' })),
        ...(store.blocked[user] || []).map(skill_name => ({ skill_name, pref: 'blocked', set_by: 'admin' })),
      ];
      return json({
        prefs,
        effective: {
          pinned: [...store.globalPins, ...(store.userPins[user] || [])],
          blocked: store.blocked[user] || [],
        },
      });
    }
    const mAssign = path.match(/^\/api\/v1\/dashboard\/admin\/user\/([^/]+)\/assignment$/);
    if (mAssign) {
      const user = decodeURIComponent(mAssign[1]);
      return json({ slots: buildSlots(user) });
    }
    if (path === '/api/v1/dashboard/admin/prefs' && method === 'POST') {
      const user = body.user_key;
      const skill = body.skill_name;
      if (user === '*global*') {
        if (body.action === 'pin' && !store.globalPins.includes(skill)) store.globalPins.push(skill);
        if (body.action === 'clear') store.globalPins = store.globalPins.filter(x => x !== skill);
      } else if (body.action === 'pin') {
        store.userPins[user] = [...new Set([...(store.userPins[user] || []), skill])];
        store.blocked[user] = (store.blocked[user] || []).filter(x => x !== skill);
        if (body.side) {
          if (!store.sideOverrides[user]) store.sideOverrides[user] = {};
          store.sideOverrides[user][skill] = body.side;
        }
      } else if (body.action === 'block') {
        store.blocked[user] = [...new Set([...(store.blocked[user] || []), skill])];
        store.userPins[user] = (store.userPins[user] || []).filter(x => x !== skill);
      } else if (body.action === 'clear') {
        store.userPins[user] = (store.userPins[user] || []).filter(x => x !== skill);
        store.blocked[user] = (store.blocked[user] || []).filter(x => x !== skill);
        if (store.sideOverrides[user]) delete store.sideOverrides[user][skill];
      } else if (body.action === 'clear_side') {
        if (store.sideOverrides[user]) delete store.sideOverrides[user][skill];
      }
      return json({ ok: true });
    }
    if (path === '/api/v1/dashboard/admin/cluster-graph') return json({ nodes: [], edges: [] });
    if (path === '/api/v1/dashboard/admin/config') return json({ path: 'config.yaml', raw: 'team:\n  skill_slots: 100\n' });
    if (path.startsWith('/api/v1/dashboard/admin/config/')) return json({ ok: true });
    if (path.startsWith('/api/v1/dashboard/events')) return json({ events: [], unread: 0 });
    if (path.startsWith('/api/v1/dashboard/user/') && path.includes('/scatter')) {
      return json({ points: [], method: 'tsne' });
    }

    // file / diff
    if (path.includes('/file') || path.includes('/diff')) {
      return json({ content: '# SKILL.md\n\nmock content\n', diff: null });
    }

    console.warn('[mock] unhandled', method, path);
    return json({ ok: true, mock: true, path });
  }

  const _fetch = window.fetch.bind(window);
  window.fetch = function (url, init) {
    const s = String(url);
    if (s.includes('/api/v1/dashboard') || s.includes('api/v1/dashboard')) {
      return handle(s, init || {});
    }
    return _fetch(url, init);
  };

  // 演示默认进管理页并以 admin 身份渲染
  document.addEventListener('DOMContentLoaded', () => {
    if (!location.hash || location.hash === '#') location.hash = '#admin';
  });
})();
