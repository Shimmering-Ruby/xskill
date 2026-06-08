---
name: multiline-no-block
description:
  触发场景: 当用户要求批量处理轨迹时使用本 skill
  所需工具: bash, python, git
  注意事项: 这段多行描述既没用块标量 | 也没加引号
---

# body 正常

正文有内容，但因为多行 description 没用块标量，YAML 把它解析成了
嵌套 mapping（非字符串），description 残缺 —— 这正是要被拦的核心 case。
