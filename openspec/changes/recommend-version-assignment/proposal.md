# Change: recommend-version-assignment

在**官方 dashboard 壳子**上做最小改动：当前推送（skill+side+sha）可观测、可 pin 到版本；技能详情展示灰度路由。

## Why

- 现状：manifest 现算不存账本；「被推荐」= 历史曝光累计（#167）。
- 灰度：`pick_side` 分流对象面板不可见。
- pin 只钉 skill 名。

## What changes

- `static/index.html` / `static/app.js`：管理列/抽屉、技能详情路由块、我的 side+sha（见 design.md）
- `openspec/.../mockups/`：官方 UI + mock-api 供审阅

## Out of scope

推荐排序（#168）；canary 裁决逻辑。
