# PyPI 自动发版 + 元数据同步 — 设计文档

- **日期**: 2026-05-11
- **状态**: Draft（待 review）
- **目标**: 用 GitHub Action 自动化 `xskill` 的 PyPI 发版，并把仓库迁移后的元数据（URLs / 作者 / README）同步到 PyPI

---

## 1. 背景与现状

### 1.1 仓库迁移
- 主仓库已从 `370025263/xskill` 切换到 `SkillNerds/xskill`（新 origin）
- 旧仓库 `370025263/xskill` 保留为备用 remote `stone`

### 1.2 PyPI 包现状
- 包名 `xskill`，PyPI 已有发布版本 `0.0.1` 和 `0.3.0`
- PyPI 上的元数据（Homepage / Repository / Issues / 作者）仍指向旧仓库 `370025263/xskill`
- PyPI 上已发布版本的元数据**不可修改**，元数据更新只能通过发布新版本生效

### 1.3 仓库工程现状
- `pyproject.toml` 里 `version = "0.3.0"` 是硬编码
- 没有任何 GitHub Actions workflow（`.github/workflows/` 不存在）
- 没有 git tag
- 有完整的 pytest 套件（`tests/test_*.py` 11 个），但 pyproject 未声明 pytest 依赖
- 项目有 PR / 多人协作需求（已有 collaborator `yangzejun0312`）

### 1.4 凭证现状
- PyPI account-wide token 存放在 `~/.pypi`
- GitHub `SkillNerds/xskill` 仓库当前为 private

---

## 2. 设计目标 / 非目标

### 目标
1. **打 tag 即发版**：`git push origin v0.3.1` 触发 PyPI 发布 + GitHub Release 创建
2. **元数据同步**：每次发版自动把仓库最新的 URLs / 作者 / README / classifiers 推到 PyPI
3. **版本号一致性**：杜绝 tag 名与 wheel 内部版本号不一致的情况
4. **PR 阶段质量保障**：push / PR 触发 CI 跑测试，保护 main 分支
5. **跨平台架子**：CI 预留 Windows / macOS job，默认不阻塞 PR，将来一行配置开启
6. **失败可回滚**：发版前的失败都能干净拦截；发版后的失败有清晰的人工补救路径

### 非目标
- **不维护 `CHANGELOG.md`**：GitHub Release notes 自动生成已足够
- **不引入 release-please / commitizen 等**：YAGNI，触发模型已选简单的 tag-based
- **不做覆盖率统计 / lint 门禁**：当前未配置，独立立项再加
- **不做多 Python 多 OS 的发版矩阵**：包是纯 Python，单 wheel 通吃

---

## 3. 架构总览

### 3.1 文件布局
```
.github/
  workflows/
    ci.yml          # push to main, PR 触发，跑测试 + 验证可发版
    release.yml     # push tag v* 触发，跑测试 + 构建 + 发 PyPI + 建 GitHub Release
pyproject.toml      # 改造：dynamic version + setuptools_scm + 新 URLs/作者 + dev 依赖
.gitignore          # 加 src/xskill/_version.py
docs/superpowers/specs/2026-05-11-pypi-release-action-design.md  # 本文档
```

### 3.2 关键设计原则

**两个 workflow 都跑 tests，不抽 reusable workflow。**
- 理由：release.yml 必须"自包含可信"。靠 ci.yml 在另一次 commit 上跑过的绿灯来发版 = 隐式跨提交假设。每次发版独立跑测试（约 +2 分钟），换来 release.yml 单文件可读。
- 用 `actions/setup-python` 自带的 pip 缓存，重跑很快。

**版本号唯一源 = git tag**（通过 `setuptools-scm`）。
- 删除 `pyproject.toml` 里的硬编码 `version`，改 `dynamic = ["version"]`
- 打 `v0.3.1` tag 后，`setuptools-scm` 推导出 `0.3.1`
- release.yml 加版本断言：tag 名（去 `v`）必须等于 `setuptools-scm` 推导值

**PyPI 元数据同步是"构建副产物"，不是 action 职责。**
- pyproject.toml 改一次，下一次发版时构建出的 wheel/sdist METADATA 自动包含新值
- Action 只做"按当前仓库状态发版"，不做额外的"同步"步骤

---

## 4. CI Workflow (`.github/workflows/ci.yml`)

### 4.1 触发
- `push` to `main`
- `pull_request` targeting `main`
- `workflow_dispatch`（手动触发，用于开启跨平台 job）

### 4.2 Concurrency
同一 PR 后续 push 自动取消旧 run（`concurrency.cancel-in-progress: true`）。

### 4.3 Job 1: `test` — Ubuntu 必过
- **Matrix**: `os: [ubuntu-latest]`, `python-version: ["3.11", "3.12"]`
- **步骤**:
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5` with `cache: 'pip'`
  3. `pip install -e .[dev]`
  4. `pytest tests/ -q`
- **门禁地位**: 必过；这个 job 失败则 PR 不可合并

### 4.4 Job 2: `test-cross-platform` — Win/Mac 架子
- **触发条件**: `if: github.event_name == 'workflow_dispatch'`
  - 默认不在 PR/push 上跑
  - 协作者可从 Actions 页面手动 "Run workflow" 触发跨平台测试
  - 将来要正式开启（每个 PR 都跑），删掉此 `if` 即可
- **Matrix**: `os: [windows-latest, macos-latest]`, `python-version: ["3.11"]`
- **步骤**: 与 Job 1 相同

### 4.5 Job 3: `verify-build` — 发版预演
- **运行环境**: `ubuntu-latest`, Python 3.11
- **步骤**:
  1. `actions/checkout@v4` with `fetch-depth: 0`（setuptools_scm 需要完整 git 历史）
  2. `actions/setup-python@v5`
  3. `pip install build twine`
  4. `python -m build`
  5. `twine check dist/*`
- **作用**: 在不上传的前提下验证 "这次 PR 不会破坏发版" — 比如 README 出现 PyPI 不支持的 markdown、setuptools_scm 推导出怪版本号

### 4.6 注释说明
ci.yml 文件头加注释：
```yaml
# To enable Win/Mac on every PR, remove the `if:` on the test-cross-platform job.
```

---

## 5. Release Workflow (`.github/workflows/release.yml`)

### 5.1 触发
```yaml
on:
  push:
    tags: ['v*']   # 含预发版 v0.4.0a1, v0.4.0rc1 等
```

### 5.2 Permissions
```yaml
permissions:
  contents: write   # 建 GitHub Release 需要
```

### 5.3 Job 1: `test`
- 与 ci.yml 的 `test` job **完全相同**
- 这就是 § 3.2 中的"两个 workflow 都跑 tests"
- 此 job 失败 → release 整体停，PyPI 不会被污染

### 5.4 Job 2: `publish` — `needs: test`
**串行步骤**:

#### 步骤 1: Checkout
```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0   # setuptools_scm 必需
```

#### 步骤 2: Setup Python
```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.11'
```

#### 步骤 3: 版本一致性断言
```bash
TAG_VERSION="${GITHUB_REF_NAME#v}"   # v0.3.1 -> 0.3.1
pip install setuptools-scm
BUILD_VERSION=$(python -m setuptools_scm)
if [ "$TAG_VERSION" != "$BUILD_VERSION" ]; then
  echo "Tag version ($TAG_VERSION) != setuptools_scm version ($BUILD_VERSION)"
  echo "Possible cause: dirty working tree or untagged commits ahead"
  exit 1
fi
```
**防的是**：tag 名是 `v0.3.1` 但 git tree 是脏的，setuptools_scm 推导出 `0.3.1.dev3+gabcdef.d20260511` 这种脏版本号被传上 PyPI。

#### 步骤 4: Build
```bash
pip install build
python -m build
# 生成 dist/xskill-<version>-py3-none-any.whl 和 dist/xskill-<version>.tar.gz
```

#### 步骤 5: Twine check
```bash
pip install twine
twine check dist/*
```
- 验证 metadata 合规、README 在 PyPI 上能正确渲染

#### 步骤 6: 发布到 PyPI
```yaml
- uses: pypa/gh-action-pypi-publish@release/v1
  with:
    password: ${{ secrets.PYPI_API_TOKEN }}
```
- 使用官方 action，处理重传幂等、网络重试比手工 `twine upload` 健壮
- 不指定 `repository_url`，走正式 PyPI

#### 步骤 7: 创建 GitHub Release
```bash
gh release create "$GITHUB_REF_NAME" \
  dist/*.whl dist/*.tar.gz \
  --generate-notes \
  --title "$GITHUB_REF_NAME"
```
- 用 GitHub 内置的"Generate release notes"自动从上一 tag → 当前 tag 之间的 PR/commit 生成 notes
- wheel + sdist 作为 release asset 附上

### 5.5 失败语义表

| 失败步骤 | 是否污染 PyPI | 是否需要人工补救 |
|---|---|---|
| Job 1 (test) | 否 | 否，修代码重打 tag |
| 步骤 3 版本断言 | 否 | 否，确保 git tree 干净后重打 tag |
| 步骤 4-5 build / twine check | 否 | 否，修代码重打 tag |
| 步骤 6 PyPI 上传（含版本已存在） | 否（PyPI 拒绝同版本覆盖） | 否，PyPI 失败 = release 停 |
| 步骤 7 GitHub Release 创建 | **是**，PyPI 已传上去 | **是**：手动 `gh release create $TAG dist/* --generate-notes` |

**接受步骤 7 半成功用人工补救**，不在 action 里加复杂的兜底。

### 5.6 Concurrency
不设 `cancel-in-progress`。两个不同 tag 并发推送时，两个都要跑完。

---

## 6. pyproject.toml 改造

### 6.1 改动 diff

```toml
[build-system]
- requires = ["setuptools>=68.0"]
+ requires = ["setuptools>=68.0", "setuptools-scm>=8"]
  build-backend = "setuptools.build_meta"

[project]
  name = "xskill"
- version = "0.3.0"
+ dynamic = ["version"]
  description = "Distill reusable Skills from AI Agent execution trajectories"
  readme = "README.md"
  requires-python = ">=3.11"
  license = { text = "MIT" }
- authors = [{ name = "370025263", email = "370025263@qq.com" }]
+ authors = [{ name = "SkillNerds", email = "370025263@qq.com" }]
  keywords = [...]            # 不变
  classifiers = [...]         # 不变
  dependencies = [...]        # 不变

+ [project.optional-dependencies]
+ dev = ["pytest>=7"]

[project.urls]
- Homepage = "https://github.com/370025263/xskill"
- Repository = "https://github.com/370025263/xskill"
- Issues = "https://github.com/370025263/xskill/issues"
+ Homepage = "https://github.com/SkillNerds/xskill"
+ Repository = "https://github.com/SkillNerds/xskill"
+ Issues = "https://github.com/SkillNerds/xskill/issues"

# [project.scripts], [tool.setuptools.*] 段不变

+ [tool.setuptools_scm]
+ version_file = "src/xskill/_version.py"
+ fallback_version = "0.0.0+unknown"
```

### 6.2 配套小改

1. **`.gitignore`** 加一行 `src/xskill/_version.py`（setuptools_scm 构建时生成，不入库）
2. **`src/xskill/__init__.py`** 暂不导出 `__version__`（YAGNI）
3. **README.md / 其他文件里的旧仓库链接**: 全仓库 grep `370025263/xskill`，把所有引用一并改为 `SkillNerds/xskill`（包括 starhistory badge、codelint badge 等都跟着仓库链接走）
4. **删本地老 wheel**: `dist/xskill-0.3.0-py3-none-any.whl` 和 `dist/xskill-0.3.0.tar.gz` 是旧 metadata 构建的，清理（不影响 PyPI 上已发布的 0.3.0）

---

## 7. 版本号规范

### 7.1 核心原则
**不是每次 merge 到 main 都发版。Tag 是显式动作，不是自动反应。**

### 7.2 什么时候不发版（推 main 就够）
- 文档、README、注释
- 内部重构（不改对外 API）
- 加测试 / 改 CI
- 接新生态但还在调试，没跑通
- 小 bug 修了但不紧急（可以攒）

### 7.3 什么时候发版

| 升哪位 | 触发条件 | 例子 |
|---|---|---|
| `0.X.PATCH` (0.3.0 → 0.3.1) | 用户可感知的 bug fix；元数据修复 | 修了一个会报错的 CLI flag；改 PyPI 显示的仓库 URL |
| `0.MINOR.0` (0.3.0 → 0.4.0) | 新对外 feature、新生态接入跑通 | 接 nexent 跑通；新增 backend；新增 CLI 子命令 |
| `MAJOR.0.0` (0.x → 1.0.0) | 离开 Beta 时一次性升 | 暂时不到 |

**0.x 阶段 API 不算稳定，breaking change 也走 minor**（SemVer 对 0.x 的豁免）。

### 7.4 预发版（开发调试期发小圈子试用）

PyPI 支持 pre-release：
- `v0.4.0a1` / `a2` / `a3`（alpha）
- `v0.4.0rc1`（候选发布）
- `pip install xskill` 默认**不会**拿到预发版；`pip install xskill --pre` 才拿

用法：接新生态、想小圈子测 → 发 `v0.4.0a1`、`v0.4.0a2`；调稳了 → 发 `v0.4.0`。

---

## 8. 凭证 / Secret 配置

### 8.1 一次性人工动作

1. **在 PyPI 上为 `xskill` 项目生成 project-scoped API token**
   - 登录 https://pypi.org → Account → Add API token
   - **Scope: 仅 `xskill` 项目**（不要用 account-wide）
   - 拷贝生成的 token 字符串

2. **在 GitHub `SkillNerds/xskill` 加 Repository Secret**
   - Settings → Secrets and variables → Actions → New repository secret
   - Name: `PYPI_API_TOKEN`
   - Value: 上一步的 token

3. **替换 `~/.pypi`**
   - 把 `~/.pypi` 里现有 account-wide token 替换为新的 project-scoped token
   - 在 PyPI 上撤销旧 account-wide token

---

## 9. 首次发版执行顺序

### 9.1 第一次发版策略: `v0.3.1a1`（alpha 预发版）

理由：
- 这次的实质改动 = URL 同步 + 作者改 SkillNerds + 内部工程化（CI/setuptools_scm），属于 patch 范畴
- 用 alpha 验证整条 release.yml 跑通，**失败也不污染正式 0.3.1 版本号序列**
- 跑通后再发 `v0.3.1` 正式版

### 9.2 步骤

1. 在 PR 上合并 workflow + pyproject 改造，等 ci.yml 跑绿
2. 一次性人工动作（§ 8.1 三步）
3. 本地：
   ```bash
   git tag v0.3.1a1
   git push origin v0.3.1a1
   ```
4. release.yml 触发，端到端验证全流程
5. 验证产物：
   - PyPI 上有 `xskill 0.3.1a1`（pre-release）
   - GitHub Release `v0.3.1a1` 已创建、含 wheel/sdist asset
   - `pip install xskill` 仍拿到 0.3.0（默认排除 pre-release）✓
   - `pip install xskill --pre` 拿到 0.3.1a1 ✓
   - PyPI 页面元数据已显示 `SkillNerds/xskill` 链接、`SkillNerds` 作者
6. 全部 ok → 后续真要修元数据/发新功能时打 `v0.3.1` 正式 tag

---

## 10. 单元边界 / 可独立性

| 单元 | 职责 | 接口 | 依赖 |
|---|---|---|---|
| `ci.yml` | PR/push 阶段保护 | trigger: push/PR/dispatch；output: status check | pyproject (dev deps), pytest |
| `release.yml` | tag 触发发版 | trigger: tag push；output: PyPI release + GitHub Release | pyproject, PYPI_API_TOKEN secret |
| `pyproject.toml` | 包元数据 + 构建配置 | 被 setuptools 和两个 workflow 共同读 | setuptools-scm |
| 版本规范文档 | 人类协作约定 | 写到本 spec + 摘要进 CONTRIBUTING（可选） | 无 |

每个单元可独立 review / 修改：改 ci.yml 不影响 release.yml；改 pyproject 元数据下次发版才生效。

---

## 11. 风险与已知局限

1. **GitHub Release 半成功（PyPI 已发但 Release 没建）**
   - 影响：PyPI 上的版本无配套 release notes
   - 补救：手动 `gh release create $TAG dist/* --generate-notes`，1 行
   - 接受度: 可接受

2. **PyPI 拒绝同版本上传**
   - 影响：误推同名 tag 后 release 失败
   - 补救：升 patch 号重打
   - 接受度: 这是 PyPI 故意设计，不绕过

3. **PR 来自 fork 时 secret 不可访问**
   - 影响：fork 的 PR 不能触发 release.yml（也不该）；ci.yml 的 verify-build 步骤如果触碰 secret 会失败
   - 缓解：ci.yml 不需要 secret，不受影响

4. **未来 Windows/macOS 真开启时可能爆出兼容问题**
   - 影响：包目前只在 Ubuntu 上验证过，跨平台未测
   - 缓解：架子已留，开启路径明确，问题暴露后单独修

---

## 12. 验收清单

实施完成后逐项确认：

- [ ] `.github/workflows/ci.yml` 存在，三个 job 都按设计
- [ ] `.github/workflows/release.yml` 存在，两个 job 按设计
- [ ] `pyproject.toml`: `version` 字段已删，`dynamic = ["version"]` 已加，URLs 已改 SkillNerds，作者已改 SkillNerds，dev 依赖组已加，`[tool.setuptools_scm]` 已加
- [ ] `.gitignore` 含 `src/xskill/_version.py`
- [ ] README 及全仓库 `370025263/xskill` 引用已替换为 `SkillNerds/xskill`
- [ ] 本地老 `dist/` 已清理
- [ ] PyPI 上已建 project-scoped token
- [ ] GitHub Secret `PYPI_API_TOKEN` 已配置
- [ ] `~/.pypi` 已替换为 project-scoped token，旧 token 已撤销
- [ ] 一个示例 PR 跑过 ci.yml 全绿
- [ ] 打 `v0.3.1a1` 后 release.yml 全绿
- [ ] PyPI 页面显示新 URLs 和新作者
- [ ] GitHub Release `v0.3.1a1` 含 wheel/sdist asset 和自动生成的 notes
