# Testing Strategy — Cross-Platform × Multi-Agent

> 目标：让 xskill 在 **Linux / macOS / Windows** 三平台 × **Claude Code / Codex / OpenCode** 三 agent 的 3 × 3 = 9 个支持单元上，**每次 PR 都自动验证**，避免回归。

## 1. 测试分层

| 层 | 目的 | 是否调真 LLM | 是否依赖 agent 本体安装 | 在 CI 跑几次 |
| --- | --- | :--: | :--: | --- |
| **UT (unit)** | 函数/类内部逻辑、纯路径、纯 git、stub LLM | ❌ | ❌ | **每平台 × Python 版本**（3 OS × 2 py = 6 jobs） |
| **IT (integration)** | 多模块协作：watcher pipeline、ingester ↔ store、installer ↔ skill_repo；仍用 stub LLM + fixture 轨迹 | ❌ | ❌ | **每平台**（3 OS × 1 py = 3 jobs） |
| **Smoke E2E** | 端到端：起 daemon、丢 fixture traj、看 skill 长出来；stub LLM；无外网 | ❌ | ❌ | **每平台 × 每 agent**（3 OS × 3 agent = 9 jobs） |
| **Real-LLM E2E** | 真实跑通：调 DeepSeek/Anthropic API，验证 prompt / agent 决策；少量样本 | ✅ | ❌（用文件 fixture 模拟 agent 输出） | **仅 Linux**（成本控制） |
| **Live-agent E2E** | 真实装 Claude Code/Codex/OpenCode，端到端联调 | ✅ | ✅ | **手动 workflow_dispatch**（成本 + 配置复杂，不进每次 PR） |

**分层原则**：
- UT/IT 占体量绝大多数（>90% test count），跑得快、回归覆盖密集
- Smoke E2E 是"daemon 真的能在该平台起来 + ingester/installer 跑通"的最小证明
- Real-LLM E2E 是"prompt 没改坏 / agent 真能判断"的样本
- Live-agent E2E 是 release gate，不卡 PR

## 2. CI Matrix 设计

### Trigger 表

| 事件 | UT | IT | Smoke E2E | Real-LLM E2E | Live-agent E2E |
| --- | :--: | :--: | :--: | :--: | :--: |
| `pull_request` to main | ✅ 6 jobs | ✅ 3 jobs | ✅ 9 jobs | ❌ | ❌ |
| `push` to main | ✅ | ✅ | ✅ | ✅ Linux | ❌ |
| `workflow_dispatch` | ✅ | ✅ | ✅ | ✅ | ✅ |
| Nightly cron (`schedule`) | — | — | ✅ | ✅ | ✅ |

### Matrix 维度

```yaml
ut-and-it:
  os:     [ubuntu-latest, macos-latest, windows-latest]
  python: ["3.11", "3.12"]
  # = 6 jobs (cross-product, fail-fast: false)

smoke-e2e:
  os:    [ubuntu-latest, macos-latest, windows-latest]
  agent: [claude_code, codex, opencode]
  # = 9 jobs

real-llm-e2e:
  os: [ubuntu-latest]
  # = 1 job, gated by secret presence
```

### 时间预算（粗估）

| Job | 单次时长 | 并发数 | 总占用 |
| --- | --- | --- | --- |
| UT+IT × 6 | ~3 min | 6 | 3 min |
| Smoke E2E × 9 | ~2 min | 9 | 2 min |
| Real-LLM E2E × 1 | ~5 min | 1 | 5 min |
| 总 wall-clock | | | **~5 min** |

GitHub Actions free tier (2000 min/month) 够用：每 PR ~30 min × 50 PR/月 = 1500 min。

## 3. 跨平台坑点清单

| 坑 | 影响 | 处理 |
| --- | --- | --- |
| **Windows symlink 需 Developer Mode** | `install_to_claude_code` 失败 | `ecosystems.install_to_<agent>` 在 Windows 上 try symlink → 失败 fallback 到 directory junction (`mklink /J`) → 仍失败 fallback 到 copy + 显式 warning。**所有 fallback 都跑 IT 覆盖** |
| **`Path.home()` 在 Windows 是 `C:\Users\<u>`** | `.claude/` 目录在 Windows 是 `C:\Users\<u>\.claude\` —— Claude Code 实际就是用这个，OK | 单测断言 `Path.home() / '.claude'` 等价于 `os.path.expanduser('~/.claude')` |
| **行尾 CRLF vs LF** | SKILL.md frontmatter 解析、git diff 噪声 | `.gitattributes` 锁 `*.md text eol=lf`，CI 第一步 `git config core.autocrlf input` |
| **macOS file mtime 精度** | 已修过的 user_edit_absorb 精度 bug 类似 | 测试断言 mtime 差 ≥1.0s 而非 ==，覆盖 macOS HFS+/APFS 差异 |
| **subprocess 调 git 的 PATH** | Windows runner 自带 git, macOS runner 自带 git, ubuntu runner 自带 git — 都 OK | 单测 `assert shutil.which('git')` 前置检查 |
| **路径分隔符** | 硬编码 `/` 分隔的字符串 | grep 一遍 `src/` 删掉所有硬编码 `/`；强制 `Path()` |
| **sqlite WAL on Windows** | 多线程 watcher 在 Win 上偶发锁错 | 单测加一个 stress（10 线程并发写 traj status），三平台都过 |

## 4. Smoke E2E 设计

### 共用骨架（伪代码）

```python
@pytest.mark.parametrize("agent_name", ["claude_code", "codex", "opencode"])
def test_smoke_e2e(agent_name, tmp_path, monkeypatch):
    # 1. 假装 agent 在 tmp_path 下放了一份 fixture trajectory
    fixture_traj = load_fixture(f"tests/fixtures/{agent_name}/sample_session.{ext}")
    agent_traj_dir = tmp_path / agent_name / TRAJ_SUBPATH[agent_name]
    agent_traj_dir.mkdir(parents=True)
    (agent_traj_dir / "session_001." + ext).write_text(fixture_traj)

    # 2. 起 daemon（--home=tmp_path 隔离）
    daemon = start_daemon(home_root=tmp_path, llm_stub=STUB_LLM, embed_stub=STUB_EMBED)

    # 3. 等一轮 scan + cluster + edit
    wait_until(lambda: skill_repo_has_at_least(1), timeout=30)

    # 4. 断言 skill 被装到该 agent 的发现目录
    installed = tmp_path / agent_name / SKILL_INSTALL_SUBPATH[agent_name]
    assert installed.exists()
    assert (installed / "auto-skill" / "SKILL.md").is_file()
```

### 三平台关键点
- **Linux/macOS**：`symlink_to`，断言是 symlink
- **Windows**：先 try symlink，如果 runner 没启 Dev Mode 走 fallback；测试断言 "skill 文件存在"，不强制 symlink 类型

### Stub 策略
- LLM stub: 给定固定输入返回固定 cluster 决策（"new auto-skill"）、固定 SkillEdit 内容（一份合规的 SKILL.md）
- Embed stub: 返回随机向量但 deterministic seeded
- Agent stub: 不需要装真实 codex/opencode；fixture 文件已经"假装"它在那写过 session

## 5. 真实-LLM E2E

只在 Linux 跑一份，触发条件：
- `push` to main 后跑一次
- `workflow_dispatch` 手动跑
- Nightly cron 跑一次

实现：
- 用 `secrets.DEEPSEEK_API_KEY` 注入真实 API key
- 跑一条短轨迹（< 500 token），验证 TaskAgent → TaskClusterAgent → SkillEditAgent 实际能产出合规 SKILL.md
- 失败不阻塞 main（设为 `continue-on-error: true`），但通过 PushNotification 提醒维护者

## 6. PR 流程

```
feat/p<N>-<topic>  ──pr──►  main
                              ▲
                              │ require: all UT/IT/Smoke jobs pass
                              │ require: ≥1 reviewer approval
                              │
                          merge (squash)
```

主 agent merge 前自动跑：
- `gh pr checks <pr>` 确认绿
- `gh pr diff <pr>` 自查 scope 没失控
- `gh pr review --approve` 后 `gh pr merge --squash`

## 7. 维护

- 此文档与 `.github/workflows/ci.yml` 双向追踪。CI 改动必须先改本文档
- 新增 agent 时：扩 fixture 目录 + 注册到 `_KNOWN_ECOSYSTEMS` + 加 smoke e2e 参数 + 此文档 §4 路径表
- 新增平台时：先看 §3 坑点清单是否有新增项
