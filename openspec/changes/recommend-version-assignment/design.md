# 推荐版本赋值可视化（recommend version assignment）

## 问题

每次 client `/sync` 现算 manifest（`build_manifest` / `pick_side`），**server 不存当前推送账本**：

- 面板「被推荐」读的是 `recommendation_log` 历史曝光累计（见 #167），不是「此刻推了什么」。
- 灰度侧：某 skill 有 staging 时，谁被 `pick_side` 钉到 staging **面板不可见、也不可改**。
- 现有 pin（`skill_prefs`）只钉到 **skill 名**，钉不住 **side / sha**。

## 目标

1. **每次算出「推什么」都落库**（细到 skill + side + sha + bucket），形成可观测的当前赋值。
2. **管理页**看/改某人当前推送；改动走既有 pin 语义，并扩展到版本侧。
3. **技能详情**看该 skill 灰度版本路由到了哪些用户，可观测；管理员可强制 pin 某用户到 main/staging。
4. **用户**可在「我的」自 pin 想用的 skill；**管理员**可代 pin / 全局 pin，并指定是否推灰度版。

## 数据模型（拟）

```text
recommendation_assignment   -- 当前赋值（每 client × skill 一行，upsert）
  client_id, skill, side, sha, bucket, source(auto|user_pin|admin_pin|global_pin), updated_at

recommendation_log          -- 保留：历史曝光审计（已有）
skill_prefs                 -- 扩展：可选 side / sha 覆盖（缺省=仅钉名，side 仍走 pick_side）
```

sync 路径：算完 manifest → upsert `recommendation_assignment` →（可选）写 `recommendation_log` 曝光。

## UI（本 change 的 mockup）

`mockups/index.html`（静态 mock，无 ⓘ 提示气泡）：

| 页 | 内容 |
| --- | --- |
| 管理 | 用户矩阵「当前推送」列 = 槽位数；点开看分桶列表（含 side/sha）；代 pin 可勾 side |
| 技能库 · 详情 | 「灰度路由」：staging / main 用户列表；可 pin 用户到指定 side |
| 我的 | 推给我的槽位展示 side·sha；自 pin / 取消 |

## 非目标（本 mock / 首 PR）

- 不改推荐排序算法（#168）。
- 不重做 canary 裁决本身。
- mock 不接真 API；落地 PR 再接线 `console.py` / `skill_manifest.py`。

## 关联

- #167 当前推送 vs 历史曝光
- 既有 pin：`skill_prefs` / 管理页代 pin / 「我的」自 pin
