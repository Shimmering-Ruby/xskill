# xskill lint 门禁存量违规基线（待清零）

生成: 2026-07-16 · 分支: perf/split-heavy-processes · 入口: `make lint`
工具: semgrep(.semgrep/xskill.yml) / ruff 0.15.20 / pylint 4.0.5 / vulture 2.16

此清单是门禁落地当日的存量违规快照。清零工作按类别推进，全部清零前 `make lint` 为红。
清零时以重跑 `make lint` 的实时输出为准，本文件只作规模参考。

## 1. semgrep 自定义规则 (.semgrep/xskill.yml)

| 规则 | 命中数 |
|---|---|
| xskill-no-time-sleep | 6 |
| xskill-no-lambda | 128 |
| xskill-no-private-import | 46 |
| xskill-no-os-system | 0 |
| xskill-subprocess-must-be-windowless | 8 |
| xskill-decode-needs-errors | 37 |
| xskill-subprocess-text-needs-encoding | 13 |
| xskill-no-embedding-in-loop | 0 |

### xskill-no-time-sleep (6)

- src/xskill/cli.py:659
- src/xskill/team/client/service.py:734
- src/xskill/team/client/updater.py:158
- src/xskill/team/client/updater.py:174
- src/xskill/team/server/client_registry.py:539
- src/xskill/utils/llm.py:305

### xskill-no-lambda (128)

- src/xskill/_sqlite_connect.py:110
- src/xskill/_sqlite_connect.py:126
- src/xskill/_sqlite_connect.py:131
- src/xskill/_sqlite_connect.py:136
- src/xskill/_sqlite_connect.py:139
- src/xskill/_sqlite_connect.py:142
- src/xskill/_sqlite_connect.py:150
- src/xskill/_sqlite_connect.py:174
- src/xskill/_sqlite_connect.py:178
- src/xskill/_sqlite_connect.py:182
- src/xskill/_sqlite_connect.py:186
- src/xskill/_sqlite_connect.py:189
- src/xskill/_sqlite_connect.py:192
- src/xskill/_sqlite_connect.py:195
- src/xskill/_sqlite_connect.py:201
- src/xskill/_sqlite_connect.py:208
- src/xskill/_sqlite_connect.py:233
- src/xskill/_sqlite_connect.py:260
- src/xskill/_sqlite_connect.py:263
- src/xskill/_sqlite_connect.py:284
- src/xskill/_sqlite_connect.py:289
- src/xskill/_sqlite_connect.py:295
- src/xskill/_sqlite_connect.py:301
- src/xskill/_sqlite_connect.py:306
- src/xskill/_sqlite_connect.py:311
- src/xskill/_sqlite_connect.py:316
- src/xskill/_sqlite_connect.py:323
- src/xskill/_sqlite_connect.py:330
- src/xskill/_sqlite_connect.py:335
- src/xskill/_sqlite_connect.py:340
- src/xskill/_sqlite_connect.py:347
- src/xskill/_sqlite_connect.py:354
- src/xskill/_sqlite_connect.py:359
- src/xskill/_sqlite_connect.py:364
- src/xskill/_sqlite_connect.py:369
- src/xskill/_sqlite_connect.py:373
- src/xskill/_sqlite_connect.py:378
- src/xskill/_sqlite_connect.py:407
- src/xskill/_sqlite_connect.py:413
- src/xskill/_sqlite_connect.py:419
- src/xskill/_sqlite_connect.py:424
- src/xskill/_sqlite_connect.py:428
- src/xskill/_sqlite_connect.py:432
- src/xskill/_sqlite_connect.py:435
- src/xskill/_sqlite_connect.py:441
- src/xskill/_sqlite_connect.py:450
- src/xskill/_sqlite_connect.py:467
- src/xskill/_sqlite_connect.py:505
- src/xskill/_sqlite_connect.py:510
- src/xskill/_sqlite_connect.py:530
- src/xskill/_sqlite_connect.py:537
- src/xskill/_sqlite_connect.py:543
- src/xskill/_sqlite_connect.py:575
- src/xskill/_sqlite_connect.py:579
- src/xskill/_sqlite_connect.py:583
- src/xskill/_sqlite_connect.py:590
- src/xskill/_sqlite_connect.py:606
- src/xskill/_sqlite_connect.py:609
- src/xskill/_sqlite_connect.py:615
- src/xskill/_sqlite_connect.py:655
- src/xskill/_sqlite_connect.py:660
- src/xskill/_sqlite_connect.py:665
- src/xskill/_sqlite_connect.py:670
- src/xskill/_sqlite_connect.py:693
- src/xskill/_sqlite_connect.py:699
- src/xskill/_sqlite_connect.py:708
- src/xskill/_sqlite_connect.py:716
- src/xskill/agents/agent_tools.py:254
- src/xskill/agents/agno_factory.py:55
- src/xskill/agents/skill_edit_agent.py:561
- src/xskill/agents/skill_edit_agent.py:691
- src/xskill/agents/skill_edit_agent.py:776
- src/xskill/agents/task_agent.py:204
- src/xskill/agents/task_agent.py:435
- src/xskill/canary.py:453
- src/xskill/canary.py:486
- src/xskill/canary.py:507
- src/xskill/canary.py:739
- src/xskill/dashboard/console.py:143
- src/xskill/dashboard/console.py:369
- src/xskill/dashboard/console.py:446
- src/xskill/dashboard/console.py:523
- src/xskill/dashboard/explore.py:73
- src/xskill/dashboard/explore.py:81
- src/xskill/dashboard/explore.py:202
- src/xskill/dashboard/explore.py:210
- src/xskill/dashboard/explore.py:212
- src/xskill/dashboard/explore.py:232
- src/xskill/dashboard/explore.py:277
- src/xskill/dashboard/explore.py:370
- src/xskill/dashboard/explore.py:372
- src/xskill/dashboard/explore.py:374
- src/xskill/dashboard/gitgraph.py:74
- src/xskill/dashboard/metrics.py:160
- src/xskill/dashboard/metrics.py:223
- src/xskill/dashboard/metrics.py:335
- src/xskill/dashboard/metrics.py:348
- src/xskill/dashboard/metrics.py:693
- src/xskill/dashboard/metrics.py:754
- src/xskill/dashboard/metrics.py:793
- src/xskill/dashboard/metrics.py:810
- src/xskill/dashboard/metrics.py:823
- src/xskill/dashboard/router.py:333
- src/xskill/ecosystems/claude_code.py:259
- src/xskill/pipeline/atom.py:268
- src/xskill/pipeline/atom.py:330
- src/xskill/recommend/engine.py:258
- src/xskill/recommend/engine.py:275
- src/xskill/recommend/engine.py:295
- src/xskill/recommend/engine.py:442
- src/xskill/recommend/engine.py:642
- src/xskill/recommend/engine.py:686
- src/xskill/recommend/skill_feature.py:63
- src/xskill/recommend/skillhub.py:183
- src/xskill/recommend/skillhub.py:478
- src/xskill/recommend/skillhub.py:503
- src/xskill/recommend/skillhub.py:557
- src/xskill/recommend/skillhub.py:988
- src/xskill/skill/description_opt.py:618
- src/xskill/skill/git.py:1047
- src/xskill/skill/repo.py:265
- src/xskill/skill/skill.py:282
- src/xskill/team/client/redact.py:121
- src/xskill/team/server/api.py:123
- src/xskill/team/server/profile_reco.py:113
- src/xskill/team/server/profile_refresh.py:174
- src/xskill/team/server/skill_manifest.py:144
- src/xskill/utils/search.py:87

### xskill-no-private-import (46)

- src/xskill/agents/agno_factory.py:23
- src/xskill/agents/agno_factory.py:105
- src/xskill/cli.py:476
- src/xskill/dashboard/console.py:37
- src/xskill/dashboard/console.py:254
- src/xskill/dashboard/explore.py:16
- src/xskill/dashboard/explore.py:165
- src/xskill/dashboard/mount.py:17
- src/xskill/dashboard/router.py:567
- src/xskill/ecosystems/__init__.py:25
- src/xskill/ecosystems/__init__.py:43
- src/xskill/ecosystems/__init__.py:53
- src/xskill/ecosystems/__init__.py:62
- src/xskill/ecosystems/__init__.py:72
- src/xskill/ecosystems/__init__.py:82
- src/xskill/ecosystems/__init__.py:92
- src/xskill/ecosystems/__init__.py:102
- src/xskill/ecosystems/__init__.py:109
- src/xskill/ecosystems/_shared.py:38
- src/xskill/ecosystems/_shared.py:518
- src/xskill/ecosystems/_shared.py:519
- src/xskill/ecosystems/_shared.py:520
- src/xskill/ecosystems/_shared.py:521
- src/xskill/ecosystems/_shared.py:522
- src/xskill/ecosystems/_shared.py:523
- src/xskill/ecosystems/claude_code.py:25
- src/xskill/ecosystems/codex.py:22
- src/xskill/ecosystems/cursor.py:21
- src/xskill/ecosystems/nga3.py:18
- src/xskill/ecosystems/ngagent.py:41
- src/xskill/ecosystems/openclaw.py:32
- src/xskill/ecosystems/opencode.py:38
- src/xskill/ecosystems/opencode.py:41
- src/xskill/ecosystems/trae.py:32
- src/xskill/recommend/profile_store.py:19
- src/xskill/recommend/reco_store.py:11
- src/xskill/skill/git.py:607
- src/xskill/skill/repo.py:21
- src/xskill/team/client/daemon.py:306
- src/xskill/team/client/daemon.py:438
- src/xskill/team/client/daemon.py:459
- src/xskill/team/client/daemon.py:491
- src/xskill/team/client/daemon.py:584
- src/xskill/team/client/daemon.py:614
- src/xskill/team/server/api.py:925
- src/xskill/team/shared/git_bundle.py:24

### xskill-no-os-system (0)

无存量违规（规则已用探针文件验证可命中）。


### xskill-subprocess-must-be-windowless (8)

- src/xskill/ecosystems/_fallback.py:213
- src/xskill/pipeline/scheduler.py:61
- src/xskill/team/client/service.py:381
- src/xskill/team/client/service.py:529
- src/xskill/team/client/service.py:553
- src/xskill/team/client/service.py:584
- src/xskill/team/client/service.py:712
- src/xskill/team/client/updater.py:165

### xskill-decode-needs-errors (37)

- src/xskill/cli.py:576
- src/xskill/dashboard/auth.py:69
- src/xskill/dashboard/gitgraph.py:35
- src/xskill/dashboard/gitgraph.py:67
- src/xskill/dashboard/gitgraph.py:68
- src/xskill/dashboard/gitgraph.py:76
- src/xskill/dashboard/gitgraph.py:77
- src/xskill/dashboard/security.py:46
- src/xskill/pipeline/trajectory.py:135
- src/xskill/prices.py:75
- src/xskill/recommend/skillhub.py:160
- src/xskill/skill/git.py:212
- src/xskill/skill/git.py:260
- src/xskill/skill/git.py:270
- src/xskill/skill/git.py:409
- src/xskill/skill/git.py:594
- src/xskill/skill/git.py:676
- src/xskill/skill/git.py:705
- src/xskill/skill/git.py:709
- src/xskill/skill/git.py:718
- src/xskill/skill/git.py:866
- src/xskill/skill/git.py:928
- src/xskill/skill/git.py:938
- src/xskill/skill/git.py:1050
- src/xskill/skill/git.py:1085
- src/xskill/skill/git.py:1350
- src/xskill/skill/git.py:1357
- src/xskill/skill/git.py:1406
- src/xskill/skill/git.py:1467
- src/xskill/skill/git.py:1496
- src/xskill/skill/git.py:1509
- src/xskill/skill/git.py:1512
- src/xskill/team/client/updater.py:63
- src/xskill/team/client/updater.py:98
- src/xskill/team/server/api.py:491
- src/xskill/team/server/api.py:920
- src/xskill/team/shared/git_bundle.py:113

### xskill-subprocess-text-needs-encoding (13)

- src/xskill/agents/agent_tools.py:1063
- src/xskill/dashboard/router.py:395
- src/xskill/pipeline/scheduler.py:61
- src/xskill/runtime.py:88
- src/xskill/team/client/service.py:84
- src/xskill/team/client/service.py:304
- src/xskill/team/client/service.py:529
- src/xskill/team/client/service.py:553
- src/xskill/team/client/service.py:584
- src/xskill/team/client/updater.py:317
- src/xskill/team/client/updater.py:338
- src/xskill/team/client/updater.py:434
- src/xskill/team/client/updater.py:453

### xskill-no-embedding-in-loop (0)

无存量违规（规则已用探针文件验证可命中）。


## 2. ruff (src/ tests/; F401,F841,ARG,E722,S110,S112; tests 豁免 ARG; BLE 不进门禁——规范禁的是静默吞错不是宽捕获)

总计 130 条。

| code | 命中数 |
|---|---|
| ARG001 | 8 |
| ARG002 | 1 |
| ARG005 | 2 |
| F401 | 82 |
| F841 | 5 |
| S110 | 31 |
| S112 | 1 |

### ARG001 (8)

- src/xskill/cli.py:545
- src/xskill/cli.py:832
- src/xskill/ecosystems/cursor.py:107
- src/xskill/skill/git.py:823
- src/xskill/skill/git.py:1062
- src/xskill/skill/git.py:1400
- src/xskill/team/server/skill_manifest.py:393
- src/xskill/utils/search.py:101

### ARG002 (1)

- src/xskill/recommend/engine.py:284

### ARG005 (2)

- src/xskill/agents/agno_factory.py:55
- src/xskill/agents/agno_factory.py:55

### F401 (82)

- src/xskill/api/sse.py:19
- src/xskill/api/sse.py:24
- src/xskill/api/sse.py:26
- src/xskill/api/sse.py:26
- src/xskill/api/sse.py:26
- src/xskill/api/sse.py:26
- src/xskill/api/sse.py:27
- src/xskill/api/sse.py:27
- src/xskill/ecosystems/__init__.py:39
- src/xskill/ecosystems/__init__.py:41
- src/xskill/ecosystems/__init__.py:49
- src/xskill/ecosystems/__init__.py:50
- src/xskill/ecosystems/__init__.py:51
- src/xskill/ecosystems/__init__.py:58
- src/xskill/ecosystems/__init__.py:59
- src/xskill/ecosystems/__init__.py:60
- src/xskill/ecosystems/__init__.py:67
- src/xskill/ecosystems/__init__.py:68
- src/xskill/ecosystems/__init__.py:69
- src/xskill/ecosystems/__init__.py:70
- src/xskill/ecosystems/__init__.py:77
- src/xskill/ecosystems/__init__.py:78
- src/xskill/ecosystems/__init__.py:79
- src/xskill/ecosystems/__init__.py:80
- src/xskill/ecosystems/__init__.py:88
- src/xskill/ecosystems/__init__.py:89
- src/xskill/ecosystems/__init__.py:90
- src/xskill/ecosystems/__init__.py:98
- src/xskill/ecosystems/__init__.py:99
- src/xskill/ecosystems/__init__.py:100
- src/xskill/ecosystems/__init__.py:107
- src/xskill/ecosystems/__init__.py:113
- src/xskill/ecosystems/__init__.py:114
- src/xskill/ecosystems/_shared.py:40
- src/xskill/ecosystems/trae.py:28
- src/xskill/skill/git.py:31
- src/xskill/skill/git.py:376
- src/xskill/skill/git.py:607
- src/xskill/utils/llm.py:21
- tests/_fake_llm_server.py:28
- tests/e2e/test_smoke.py:28
- tests/live/test_codex_live.py:26
- tests/live/test_opencode_live.py:34
- tests/rate_limit/unit/test_token_bucket.py:6
- tests/test_agent_tools_explore.py:5
- tests/test_agent_trace.py:6
- tests/test_atom_canary.py:6
- tests/test_atom_task_store.py:8
- tests/test_canary.py:13
- tests/test_canary.py:14
- tests/test_canary.py:17
- tests/test_canary_rotation.py:25
- tests/test_candidates_atom.py:4
- tests/test_candidates_atom.py:6
- tests/test_cc_traj_naming.py:22
- tests/test_cluster_retry_dedup.py:19
- tests/test_dashboard_skill_detail.py:6
- tests/test_description_opt.py:19
- tests/test_hybrid_search.py:6
- tests/test_install_fallback_revsync.py:18
- tests/test_install_fallback_revsync.py:19
- tests/test_install_fallback_revsync.py:30
- tests/test_log_setup.py:5
- tests/test_process_atom.py:4
- tests/test_runtime_sync.py:24
- tests/test_sanitize.py:6
- tests/test_search_all.py:6
- tests/test_search_all.py:8
- tests/test_ssl_verify.py:11
- tests/test_task_cluster_agent.py:6
- tests/test_team_protocol.py:6
- tests/test_team_reconnect_identity.py:13
- tests/test_trae_adapter.py:13
- tests/test_traj_meta.py:5
- tests/test_trigger_probe.py:9
- tests/test_user_edit_absorb.py:6
- tests/test_ux_score_atom.py:4
- tests/test_watcher.py:19
- tests/test_watcher.py:20
- tests/test_watcher.py:20
- tests/test_watcher.py:24
- tests/test_watcher.py:24

### F841 (5)

- src/xskill/skill/git.py:854
- src/xskill/skill/git.py:903
- src/xskill/skill/skill.py:348
- tests/test_e2e_xskill_serve_auto.py:745
- tests/test_e2e_xskill_serve_auto.py:778

### S110 (31)

- src/xskill/_sqlite_connect.py:160
- src/xskill/_sqlite_connect.py:209
- src/xskill/_sqlite_connect.py:391
- src/xskill/_sqlite_connect.py:451
- src/xskill/_sqlite_connect.py:556
- src/xskill/_sqlite_connect.py:626
- src/xskill/_sqlite_connect.py:725
- src/xskill/agents/agent_tools.py:628
- src/xskill/agents/agent_tools.py:637
- src/xskill/agents/agent_tools.py:745
- src/xskill/agents/agno_factory.py:279
- src/xskill/agents/skill_edit_agent.py:771
- src/xskill/agents/task_cluster_agent.py:85
- src/xskill/agents/task_cluster_agent.py:97
- src/xskill/api/app.py:1103
- src/xskill/api/sse.py:98
- src/xskill/api/sse.py:108
- src/xskill/cli.py:239
- src/xskill/cli.py:528
- src/xskill/dashboard/metrics.py:317
- src/xskill/dashboard/metrics.py:326
- src/xskill/dashboard/metrics.py:383
- src/xskill/ecosystems/claude_code.py:245
- src/xskill/pipeline/atom.py:443
- src/xskill/runtime.py:67
- src/xskill/skill/git.py:1707
- src/xskill/skill/git.py:1860
- src/xskill/skill/skill.py:416
- tests/test_description_opt.py:78
- tests/test_log_setup.py:100
- tests/test_trigger_probe.py:33

### S112 (1)

- src/xskill/pipeline/runner.py:394


## 3. pylint invalid-name (变量/参数/属性名 <3 字母)

总计 613 条（超过 200，只列前 50，另给按文件分布）。

### 按文件分布

| 文件 | 命中数 |
|---|---|
| src/xskill/dashboard/profile_viz.py | 65 |
| src/xskill/api/app.py | 40 |
| src/xskill/agents/agent_tools.py | 36 |
| src/xskill/dashboard/explore.py | 28 |
| src/xskill/dashboard/metrics.py | 25 |
| src/xskill/pipeline/runner.py | 23 |
| src/xskill/canary.py | 22 |
| src/xskill/pipeline/atom.py | 22 |
| src/xskill/team/client/service.py | 22 |
| src/xskill/cli.py | 21 |
| src/xskill/dashboard/console.py | 17 |
| src/xskill/recommend/engine.py | 16 |
| src/xskill/ecosystems/trae.py | 16 |
| src/xskill/dashboard/router.py | 15 |
| src/xskill/usage.py | 14 |
| src/xskill/agents/agent_trace.py | 14 |
| src/xskill/ecosystems/openclaw.py | 13 |
| src/xskill/prices.py | 12 |
| src/xskill/ecosystems/claude_code.py | 12 |
| src/xskill/config.py | 11 |
| src/xskill/ecosystems/_shared.py | 11 |
| src/xskill/agents/task_agent.py | 10 |
| src/xskill/pipeline/registry.py | 10 |
| src/xskill/utils/search.py | 9 |
| src/xskill/agents/skill_edit_agent.py | 8 |
| src/xskill/ecosystems/nga3.py | 8 |
| src/xskill/ecosystems/codex.py | 7 |
| src/xskill/ecosystems/_history.py | 6 |
| src/xskill/agents/task_cluster_agent.py | 5 |
| src/xskill/agents/context_budget.py | 5 |
| src/xskill/agents/user_edit_absorb_agent.py | 5 |
| src/xskill/pipeline/trajectory.py | 5 |
| src/xskill/recommend/skillhub.py | 5 |
| src/xskill/team/server/api.py | 5 |
| src/xskill/team/client/updater.py | 5 |
| src/xskill/team/client/daemon.py | 5 |
| src/xskill/ecosystems/_fallback.py | 5 |
| src/xskill/core.py | 4 |
| src/xskill/runtime.py | 4 |
| src/xskill/recommend/client_interest.py | 4 |
| src/xskill/ecosystems/cursor.py | 4 |
| src/xskill/dashboard/gitgraph.py | 4 |
| src/xskill/utils/llm.py | 4 |
| src/xskill/events.py | 3 |
| src/xskill/recommend/skill_feature.py | 3 |
| src/xskill/recommend/profile_store.py | 3 |
| src/xskill/utils/logging.py | 3 |
| src/xskill/utils/rate_limit.py | 3 |
| src/xskill/agents/agno_factory.py | 2 |
| src/xskill/team/server/profile_reco.py | 2 |
| src/xskill/team/server/skill_manifest.py | 2 |
| src/xskill/team/client/collector.py | 2 |
| src/xskill/dashboard/auth.py | 2 |
| src/xskill/dashboard/security.py | 2 |
| src/xskill/_sqlite_connect.py | 1 |
| src/xskill/types.py | 1 |
| src/xskill/ecosystems/opencode.py | 1 |
| src/xskill/dashboard/mount.py | 1 |

### 前 50 条

- src/xskill/config.py:285:45: C0103: Variable name "f" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:360:4: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:390:49: C0103: Variable name "f" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:417:49: C0103: Variable name "f" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:427:11: C0103: Variable name "p" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:434:8: C0103: Variable name "e" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:676:4: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:706:4: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:750:4: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:794:4: C0103: Variable name "p" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/config.py:823:4: C0103: Variable name "p" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:101:8: C0103: Variable name "cp" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:115:14: C0103: Argument name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:131:4: C0103: Variable name "M" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:181:20: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:182:20: C0103: Variable name "b" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:206:7: C0103: Argument name "b" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:227:4: C0103: Variable name "p" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:229:4: C0103: Variable name "cp" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:257:16: C0103: Argument name "n" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:276:4: C0103: Variable name "sd" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:304:8: C0103: Variable name "em" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:318:8: C0103: Variable name "mx" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:319:12: C0103: Variable name "s" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/usage.py:329:12: C0103: Variable name "m" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:50:15: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:55:12: C0103: Variable name "ep" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:59:12: C0103: Variable name "ip" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:60:12: C0103: Variable name "op" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:64:12: C0103: Variable name "ch" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:74:64: C0103: Variable name "r" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:137:4: C0103: Variable name "sp" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:143:4: C0103: Variable name "st" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:181:4: C0103: Variable name "cp" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:183:8: C0103: Variable name "st" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:206:8: C0103: Variable name "cp" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/prices.py:209:8: C0103: Variable name "t" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/events.py:81:8: C0103: Variable name "r" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/events.py:203:16: C0103: Variable name "r" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/events.py:204:16: C0103: Variable name "ev" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/core.py:77:12: C0103: Variable name "r" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/core.py:78:12: C0103: Variable name "md" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/core.py:127:8: C0103: Variable name "md" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/core.py:145:8: C0103: Variable name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:66:23: C0103: Argument name "d" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:143:4: C0103: Variable name "s" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:145:8: C0103: Variable name "s" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:317:4: C0103: Variable name "h" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:318:4: C0103: Variable name "r" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)
- src/xskill/canary.py:385:4: C0103: Variable name "p" doesn't conform to '[a-z_][a-z0-9_]{2,}$' pattern (invalid-name)

## 4. vulture (--min-confidence 80)

总计 3 条。

- src/xskill/api/sse.py:24: unused import 'EventSourceResponse' (90% confidence)
- src/xskill/recommend/engine.py:284: unused variable 'task_atom' (100% confidence)
- src/xskill/skill/git.py:607: unused import '_fs_to_tree_path' (90% confidence)
