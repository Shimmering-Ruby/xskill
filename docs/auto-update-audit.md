# 自动更新失效审计报告 / Auto-update failure audit

> 4 路只读 subagent 分头审计(重启拉起 / 静默无窗 / GBK 编码 / 流程分支循环),对齐用户线上日志。
> 结论:Windows 上"所有版本自动更新都不 work"属实,但**不是单一 bug,是一条由多个独立缺陷串成的链**。

## TL;DR

自动更新有两类失效,叠加发生:

- **A 类 · 根本升不上去**:走 PyPI 的 `pip install` 在公司代理/SSL 机器上**必然失败**(检测新版走 urllib 认代理、装却走 pip 不认代理,两条通道不对称),只剩 server wheel 一条路。
- **B 类 · 升了却永久掉线**:重启机制在 Windows 上不可靠——`os._exit()` 后靠 Task Scheduler `RestartOnFailure` 拉起,而它对"进程跑完再非零退出"多半不触发;startup_folder 路径的重启命令又写错了(缺解释器)。退出后拉不回来 = 永久掉线到下次登录。

放大器:`_loop` 无 try/except → 任意异常**静默猝死** updater 线程;GBK 让 pip 输出乱码、甚至把"装成功但有告警"误判成失败。

**证伪(重要)**:曾怀疑"pip 覆盖不了运行中的 `xskill.exe`"——**不成立**。daemon 由 `python -m xskill connect --foreground`(service.py:130-140)启动,跑的是 `pythonw.exe`,pip 升级的是纯 Python 的 `xskill` 包(运行时可覆盖),`xskill.exe` launcher 虽在但不是运行进程、不被锁。文件锁不是"所有版本失效"的根因。

## 用户日志逐帧(0.6.15 → 0.6.17)

```
发现新版本 0.6.17(当前 0.6.15)   ← urllib 认代理,检测成功
pip 升级失败: No matching distribution ... (ssl)  ← pip 不认代理,A 类根因
升级完成，即将重启                 ← 走了 server wheel 回退且装成功 → _restart
schtasks 路径 — 以退出码 1 退出     ← os._exit(1),赌 RestartOnFailure(B 类根因)
（之后再没回来）                    ← RestartOnFailure 未触发 / 或线程猝死
```

## 缺陷清单(按严重度)

### 致命
| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| D1 | **PyPI 检测 vs pip 安装的代理/SSL 不对称**:`_latest_pypi_version` 用 urllib(认 env 代理)检测成功;`_install` shell 出 `pip`,**不传 `-i`、不插桩代理** | updater.py:62 vs 308-315 | 公司代理/MITM 机器上**每个版本的 PyPI 升级都失败**（A 类根因,精确对上日志） |
| D2 | **schtasks `os._exit(1)` 赌 `RestartOnFailure`**;而它语义是"任务*启动失败*时重试",对"动作进程跑完再非零退出"多半**不触发** | updater.py:157-159；service.py:278-281 | 退出后不被拉起,只能等下次 `LogonTrigger`(下次登录）→ **永久掉线**（B 类根因） |
| D3 | **startup_folder 重启用裸 `sys.argv`**(缺 `sys.executable`;execv 分支是对的 `[sys.executable]+sys.argv`)。`-m xskill` 下 `sys.argv[0]` 是 `__main__.py` 路径,`Popen` 它 → `OSError WinError 193` | updater.py:165 vs 178 | 结合 D4:新进程起不来、异常又杀掉线程、老进程继续跑旧码 → **永不再升**。也是 state 文件读不到时的**默认**路径 |
| D4 | **`_loop` 无 try/except**(daemon 线程),任意异常永久杀掉静默 updater 线程 | updater.py:247-252 | 一次异常 = 整个进程生命周期内自动更新**静默死亡**（放大器；对比 daemon._tick 有兜底） |

### 高
| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| D5 | 全流程**无 watchdog / 无"回来了"校验**;`os._exit` 跳过 `run_forever` 的 finally;服务端也无 liveness 回调 | updater.py / daemon.py:358 | 上面每种失败都是静默且默认永久 |
| D6 | **GBK**:`subprocess.run(text=True)` 按 cp936-strict 解码 pip 输出(无 `encoding=`/`errors=`)。UTF-8 输出→乱码;非法字节→`UnicodeDecodeError` 被宽 except 吞,把**可能成功**的安装误报成失败 | updater.py:317-333、434-446 | 日志乱码（用户所见）+ 误判 + 无谓走回退 |
| D7 | 装后只核验 `importlib.metadata.version`,**不校验能否 import** | updater.py:338-343、453-458 | 升成一个缺传递依赖/有 bug 的版本 → 重启即崩(尤其同一坏网络拉不到依赖) |

### 中
| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| D8 | 首检延迟整周期(默认 3600s)+ **每次重启重置时钟** | updater.py:249 | 频繁重启/登录的机器可能长期甚至永远到不了首检 |
| D9 | `Version(v)` 遍历**所有** PyPI release,一个不可解析的坏串毒死整个 PyPI 通道 | updater.py:66-73 | 直到坏 release 被删前,PyPI 通道对所有人失效 |
| D10 | collector `read_text(encoding="utf-8")` **无兜底** | collector.py:218/236/299 | GBK 轨迹文件 → `UnicodeDecodeError` 崩掉采集/上传轮次 |

### 已确认干净
- **静默无窗**:update/connect/daemon 路径**每个** subprocess 都过 `windowless_subprocess_kwargs()`(CREATE_NO_WINDOW);`run_git` 是 dulwich 纯 Python 不起进程;startup `.vbs` 隐藏窗口运行。`src/` 无 powershell。**当前无黑窗 bug**;CLAUDE.md rule 13 已落防退化。
- schtasks 输出解析靠 cp936 恰好匹配 schtasks 的 ANSI 输出,当前正确(但同属"未显式指定编码"的隐患模式)。

## 修复方案(分优先级)

### P0 —— 让它"能升上去 + 升完能回来"
1. **D1**:`_install` 的 pip 与 urllib 检测同源——传 `-i` 指向同一 index 并插桩代理;或**首选 server wheel 通道**(内网可达),PyPI 仅作补充。
2. **D2/D3 统一重启为"主动拉起 + 校验存活"**:
   - schtasks 路径:退出前 spawn 一个短命 relauncher,等老进程真退出后 `schtasks /Run /TN <task>` 起**受管**新实例(不靠 RestartOnFailure、不产生脱管孤儿)。
   - startup_folder 路径:改 `[sys.executable] + sys.argv`,并 `proc.poll()` 校验子进程起来了再退老进程。
3. **D4**:`_loop` 包 try/except(照 `daemon._tick`),updater 线程永不猝死。
4. **D6**:所有解析型 subprocess 加 `encoding=` + `errors="replace"`(CLAUDE.md rule 14)。

### P1
5. **D7**:装后跑一次真 `python -c "import xskill"` 校验可导入,再重启。
6. **D5**:加轻量 watchdog / 服务端 liveness("N 分钟没心跳"告警)。
7. **D9**:`Version()` 逐个 try 跳过坏串。
8. **D10**:collector `read_text` 加 `errors="replace"` / 兜底。

## 已落地(本轮)
- CLAUDE.md **rule 13**(Windows 子进程一律静默无窗)、**rule 14**(Windows GBK 解码,禁硬按 UTF-8)。
- `_install_wheel` 已补"装后版本核验再重启"(缓解 D2 的**无谓**触发,但不解决 D2 本身的拉起不可靠)。
- 客户端孤儿 symlink 收割(与本报告无关的另一 client hygiene 修复,已测)。

> 备注:这些修复涉及真机 Windows 验证,不宜与前述小修草草打包;建议自动更新的重启/网络通道重做作为一个独立、带 Windows 实测的版本发布。
