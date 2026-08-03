# 推荐版本赋值可视化（recommend version assignment）

## 问题

每次 client `/sync` 现算 manifest（`build_manifest` / `pick_side`），**server 不存当前推送账本**：

- 面板「被推荐」读的是 `recommendation_log` 历史曝光累计（见 #167），不是「此刻推了什么」。
- 灰度侧：某 skill 有 staging 时，谁被 `pick_side` 钉到 staging **面板不可见、也不可改**。
- 现有 pin（`skill_prefs`）只钉到 **skill 名**，钉不住 **side / sha**。

## 目标

1. **每次算出「推什么」都落库**（细到 skill + side + sha + bucket），形成可观测的当前赋值。
2. **管理页**看/改某人当前推送；改动走既有 pin 语义，并扩展到版本侧。
3. **技能详情**看该 skill 灰度版本路由到了哪些用户；管理员可强制 pin 某用户到 main/staging。
4. **用户**可在「我的」自 pin；**管理员**可代 pin / 全局 pin，并指定是否推灰度版。

## UI 改动原则（最小）

基于现有 `src/xskill/dashboard/static/{index.html,app.js}`，**不重做壳子**：

| 位置 | 改动 |
| --- | --- |
| 管理表头 | 「被推荐」→「当前推送」；增「灰度」列（`current_slots` / `staging_slots`，缺省回退旧 `exposures`） |
| 管理抽屉 | 在既有偏好区之上列出当前槽位（bucket / side / sha）；有 staging 时可切 side（`prefs` + `side`） |
| 技能详情 | 既有详情卡内插入「灰度路由」块（`GET .../skill/{name}/routing`；404 则隐藏） |
| 我的 · 推给我的 | 既有行上强化 side chip + sha 短码（字段已有 `side`） |
| 提示气泡 | 本 change 去掉面板 ⓘ（审阅时不干扰） |

## 数据模型（拟）

```text
recommendation_assignment   -- 当前赋值（每 client × skill 一行，upsert）
  client_id, skill, side, sha, bucket, source(auto|user_pin|admin_pin|global_pin), updated_at

recommendation_log          -- 保留：历史曝光审计（已有）
skill_prefs                 -- 扩展：可选 side / sha 覆盖（缺省=仅钉名，side 仍走 pick_side）
```

## Mock 审阅

`mockups/` = 官方 `index.html` 壳 + 精简 `app.js`：**数据全部写死**，无 fetch / 无 mock API。
本地：`python3 -m http.server` 于该目录即可。重点看管理「当前推送」、技能详情「灰度路由」、我的槽位 side/sha。

## 非目标

- 不改推荐排序算法（#168）。
- 不重做 canary 裁决本身。
- 本 PR 前端已接线；assignment 落库与 API 可同 PR 或跟随 PR 落地。

## 关联

- #167 当前推送 vs 历史曝光
- 既有 pin：`skill_prefs` / 管理页代 pin / 「我的」自 pin
