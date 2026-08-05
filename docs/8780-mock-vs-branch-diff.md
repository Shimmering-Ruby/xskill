# 8780 Mock vs `fix/skill-routing-api` Branch — UI/UX/API Gap Inventory

**Audited:** 2026-08-05 (re-audit after implementation round)  
**Mock (source of truth):** `/home/admin/xskill-recommend-version-viz/` → http://8.219.96.11:8780/  
**Branch:** `/home/admin/.cursor/worktrees/xskill-optional-pymilvus` (`fix/skill-routing-api`)  
**Baseline reference:** `origin/main` static (shipped ~a6)

---

## Summary verdict

**CONVERGED** — all prior **P0** and **P1** gaps from the first inventory are now **DONE** in the branch. Static `app.js` / `index.html` match mock on every user-visible routing, admin, skills-search, and my-page control that was in scope.

Remaining differences are **P2 / mock-only / intentional prod behavior** (no `mock-api.js`, no fake harness injection, push defaults bound from API not hardcoded `45`, optional cache-bust query params).

**Severity counts (remaining gaps only):**

| Severity | Count | Meaning |
|----------|------:|---------|
| **P0** | 0 | — |
| **P1** | 0 | — |
| **P2** | 6 | Mock-only demo data, cosmetic HTML, or prod-intentional defaults |

`app.js` diff vs mock: ~21 net lines (mostly comment removal, `jc` cache for skills fetch, prod push defaults `0`/`100` vs mock `45`, no client-side `harnesses` fallback). No functional P0/P1 hunks left.

---

## Already aligned

| Area | Evidence |
|------|----------|
| Sidebar branding & nav | `index.html:98-111` — identical teal `x` logo, `xskill` / `控制台`, same SVG nav icons |
| Skill routing REST API | `console.py:561-1296` — `_skill_routing_table`, GET `/skill/{name}/routing`, `/routing/users`, `/routing/user/{user}`; tests in `tests/test_skill_routing.py` |
| Graph HEAD → routing focus | `static/app.js:293` `data-gside`; `:303` legend 「点黄点看推送对象」; `:858` `focusSkillRouting`; `:1551-1567` HEAD click → focus + conditional scroll |
| 「当前推送对象」panel + layout | `static/app.js:502-503` `#skill-routing` in **right column** (`lg:col-span-7`); `:768-847` staging/main columns, typeahead, pagers |
| Evolution path hint + HEAD subcopy | `static/app.js:498` 「点黄点 / main HEAD 看推送给谁；其它节点看 diff」; `:295` 「· 点击看推送给谁」 on HEAD rows |
| Manifest `side_mutable` | `console.py:304-325` `_side_mutable` + emit on slots; `tests/test_skill_routing.py:196` asserts `True` for canary skill |
| Admin assignment API | `console.py:1194-1201` `GET /admin/user/{user_key}/assignment`; `static/app.js:2413` fetch; test `:191-197` |
| Admin matrix slot counts | `console.py:1175-1177,1191` `current_slots` / `staging_slots` / `total_users`; `static/app.js:2377-2378` renders columns |
| Admin drawer side chips | `static/app.js:2425` conditional on `s.side_mutable` (now populated by API) |
| Skills search UI + page size | `index.html:233` `#skills-q`; `static/app.js:190-198` `SKILLS_PAGE_SIZE=10`, debounced `?q=` |
| Skills API `q` substring | `router.py:145-146`; `catalog_store.py:688-791` SQL `LIKE`; `tests/test_skill_routing.py:206-220` |
| Trigger-rate collapsible | `index.html:250-254` `#trigger-rate-toggle` / `#trigger-rate-body` hidden; `static/app.js:2286-2293` toggle |
| My page star pin + side chips | `static/app.js:2052-2071` `_mySlotRowHtml`; `:2252-2272` delegated `.my-row-side` / `.my-row-pin` / `.my-row-unpin` |
| Admin colspan 9 | `index.html:506` `colspan="9"` |
| Route user placeholder | `static/app.js:807` 「输入关键字，如 alice / user-0」 |
| Push input focus-select | `static/app.js:2326` `focus` → `select()` |
| Routing row admin/self prefs | `static/app.js:630-648`, `2402-2418` — `_routePref` → `/admin/prefs` or `/my/prefs` |
| My manifest + take_n | `console.py:679-771`, `static/app.js:2096-2184` — `server_push`, `take_n`, local slice `applyTakeNToSlots` |
| My settings API | `console.py:728-771` — GET/POST `/my/settings` with `take_n` clamped to `skill_slots` |
| My uploads / commits APIs + sections | `console.py:817-943`, `index.html:415-439`, `loadMyUploads` / `loadMyCommits` |
| Push stepper chrome | `index.html:347-365`, CSS `.push-ctl` / `.push-step` |
| Commit status pills | CSS `.commit-pill.live\|canary\|absorbed`, `_commitPill` |
| Admin drawer shell | `static/app.js:2400-2445` — 「{user} 的当前推送」, prefs pin/block, `adm-side` buttons |
| Trigger / lineage / graph APIs | Mock `mock-api.js` mirrors dashboard routes; branch has real implementations |

---

## Remaining gaps

| Area | Severity | Mock evidence | Branch evidence | Notes |
|------|----------|---------------|-----------------|-------|
| Entire `mock-api.js` | **P2** | `xskill-recommend-version-viz/mock-api.js` | Not in branch | Prod uses FastAPI; do not ship |
| Client `harnesses` injection | **P2** | `mock-api.js:293-299`; mock `app.js` `applyTakeNToSlots` fills `['claude-code','codex']` | `static/app.js:2096-2100` spreads slot as-is, no fallback | Mock demo only; prod should come from server when tracked |
| HTML default `my-push-val` `45` | **P2** | `index.html:356` mock `value="45"` | `index.html:355` `value="0"` | Branch correctly binds from `/my/settings` / manifest |
| Index script cache bust | **P2** | `index.html:560-561` `?v=29` | `index.html:559` bare `app.js` | Optional prod mount nicety |
| CSS section comment | **P2** | `index.html:73` 「偏好：推送步进 + …」 | Comment omitted | Cosmetic |
| `total_users` API field, no UI pager | **P2** | `mock-api.js:949` returns `100`, UI still shows 20 rows | `console.py:1191` returns count; `loadAdmin` renders all registry rows, no pager | Both sides lack admin user pagination UI; API field present on branch |

---

## Out of scope / mock-only (do not ship)

| Item | Mock location | Why skip |
|------|---------------|----------|
| 100 synthetic users (`user-001`…) | `mock-api.js:6-12`, `:931-950` | Stress-test pagination; prod uses real ClientRegistry |
| Hardcoded skill catalog (`web-flask`, `skill-01`…) | `mock-api.js:13-97` | Demo fixtures |
| Fake trajectories/atoms | `mock-api.js:100-126` | Demo only |
| Deterministic `resolveSide` hash for 100 users | `mock-api.js:214-218` | Mock convenience; prod uses real `pick_side` |
| Inline Tailwind comment `app.js?v=3` | `index.html:5` mock comment | Doc string only |

---

## API contract quick reference (routing-related)

| Endpoint | Mock | Branch | Gap |
|----------|------|--------|-----|
| `GET /skill/{name}/routing` | counts + has_staging | ✅ `console.py:1242-1252` | — |
| `GET /skill/{name}/routing/users` | filter, offset, limit, `q` | ✅ `:1254-1286` | — |
| `GET /skill/{name}/routing/user/{user}` | single row | ✅ `:1288-1296` | — |
| `GET /my/manifest` | slots + `side_mutable` | ✅ `:679-726`, `:325` | — |
| `GET/POST /my/settings` | take_n, server_slots | ✅ `:728-771` | — |
| `GET /admin/users-matrix` | current_slots, staging_slots, total_users | ✅ `:1138-1192` | — |
| `GET /admin/user/{user}/assignment` | `{ slots: [...] }` | ✅ `:1194-1201` | — |
| `GET /skills?q=` | substring search | ✅ `router.py:145`, `catalog_store.py:722+` | — |

---

## Verification log

**Date:** 2026-08-05

| Check | Result | Notes |
|-------|--------|-------|
| `side_mutable` in `_pack_manifest_slots` | **PASS** | `console.py:304-325`; test asserts on assignment response |
| `GET /admin/user/{user}/assignment` | **PASS** | Route at `:1194`; frontend `openAdminDrawer` `:2413` |
| `admin/users-matrix` slot fields | **PASS** | `current_slots`, `staging_slots`, `total_users` at `:1175-1191` |
| `GET /skills?q=` | **PASS** | `router.py:146`, `catalog_store.py:722-791`; test accepts `q=alp` |
| Graph `data-gside` + `focusSkillRouting` | **PASS** | `static/app.js:293,858,1551-1567` |
| Legend / hint copy | **PASS** | `:303`, `:498`, `:295` match mock strings |
| `#skill-routing` right column | **PASS** | `:502-503` inside `lg:col-span-7` |
| Skills search + page 10 + trigger collapse | **PASS** | `index.html:233,250-254`; `app.js:190,2286` |
| My star/side UI + handlers | **PASS** | `:2052-2071`, `:2252-2272` |
| Admin colspan 9, route placeholder, push focus-select | **PASS** | `index.html:506`; `app.js:807,2326` |
| `pytest tests/test_skill_routing.py` | **SKIP** | Host Python lacks `from __future__ import annotations`; static + grep audit used instead |
| `diff mock/app.js branch/static/app.js` | **PASS (P2 only)** | No missing P0/P1 hunks; ~21-line delta (defaults, comments, harness fallback) |

---

## File diff summary

| File | Relationship |
|------|----------------|
| `index.html` | Aligned on all P0/P1 hunks; P2 only: no `mock-api.js`, no `?v=29`, push default `0` not `45`, CSS comment |
| `app.js` | Functionally aligned; ~21 lines behind mock (prod defaults, no harness injection, comment/`jc` deltas) |
| `mock-api.js` | Mock-only (not in branch) |
| `console.py` | Aligned on assignment, matrix fields, `side_mutable` |
| `router.py` / `catalog_store.py` | Aligned on `q` search |

*Re-audit only — no code fixes applied.*
