---
name: full-with-metadata
description: 带完整 metadata 块的合法 frontmatter，必须通过。
metadata:
  version: 3
  created: "2024-11-15"
  last_updated: "2026-06-05T10:30:00"
  source_atoms:
    - atom_traj_x_0001
    - atom_traj_x_0002
  frozen: false
  use_count: 7
---

# 修复 django 迁移冲突

## 检测冲突

1. 跑 `python manage.py showmigrations` 看冲突。
