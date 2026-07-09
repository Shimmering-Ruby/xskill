## ADDED Requirements

### Requirement: 蒸馏管线进度实时可见

总览页 SHALL 展示蒸馏管线五阶段实时计数（待拆分轨迹 → 拆分中 → 聚类分派中 →
候选累积中 → 蒸馏/灰度中），数据源为 `trajectories.status` 计数与 watcher 在途状态，
轮询刷新。活跃阶段（计数 > 0 的在途阶段）SHALL 有视觉标记。

#### Scenario: 新轨迹推进管线

- **当** 一条新轨迹入库且尚未被 TaskAgent 处理
- **那么** "待拆分轨迹" 计数 +1；进入拆分后该计数 -1、"拆分中" +1

### Requirement: 冷启动屏障与候选孵化进度可见

总览页 SHALL 展示冷启动屏障进度（已收集 N / 目标 M）与候选孵化进度列表：每个候选
skill 的 `.candidates.yml` 累积 weightscore 相对蒸馏阈值的进度条、贡献原子数、baby
状态标注。冷启动未启用时该区块 SHALL 不渲染（而非显示 0/0）。

#### Scenario: 候选逼近阈值

- **当** 某候选 skill 的累积 weightscore 为 8.6、阈值为 10
- **那么** 该候选显示 86% 进度条与 "8.6 / 10" 数字

### Requirement: 用户连接状态看板

用户 & 画像列表页 SHALL 展示每用户连接状态：在线/离线（`clients.last_seen` 距今是否
在 2 个同步周期内）、上次活跃相对时间、累计轨迹·原子数、触发次数、harness 与主力模型
占比。样本不足的占比 SHALL 显式标注"样本不足"。client 版本列在版本上报（P2）落地前
SHALL 不渲染（而非显示 unknown）。

#### Scenario: 心跳驱动在线状态

- **当** 某 client 在 1 个同步周期内调用过任意鉴权端点
- **那么** 该用户在列表页显示为在线（绿点）
