# Change: recommend-version-assignment

每次 sync 现算的推送（skill + side + sha）落库为当前赋值；面板可观测、可用 pin 改到版本级；技能详情展示灰度路由用户。

## Why

- 现状：manifest 现算不存账本；「被推荐」= 历史曝光累计（#167），不是当前推送。
- 灰度：`pick_side` 把 staging 分给谁，面板看不见也改不了。
- pin 只钉 skill 名，钉不住 side/sha。

## What changes

- 新表 `recommendation_assignment`（当前赋值 upsert）
- `skill_prefs` 可选 side/sha 覆盖
- 管理页 / 技能详情 / 我的：版本级可视化 + pin（见 `mockups/index.html`）

## Out of scope

推荐排序算法（#168）；canary 裁决逻辑本身。
