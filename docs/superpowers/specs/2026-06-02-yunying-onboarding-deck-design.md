# /yunying 小红书代运营创作简报 · 设计

日期：2026-06-02

## 目标

为受聘的小红书代运营做一个交付页面 `https://xskill.wiki/yunying/`，用交互式横向翻页
slide 把"写好 xskill 小红书笔记所需的一切"一次性交到对方手里。代运营据此**原创**笔记，
发给项目方，项目方发布到自己账号。**核心考核口径 = GitHub star 增量**，附带社区/微信群里程碑。

## 读者

页面读者是代运营机构（不是终端开发者）。所以页面是一份"创作简报 / 素材包 / 协作手册"，
而非产品落地页。语气：专业、可操作、给清单和样例。

## 决策（已与用户确认）

- **交付方式**：直接上线 + 完全公开（无口令）。
- **3 篇现有草稿定位**：参考样例 —— 代运营据其选题角度/语气/封面规格**重写**原创，而非直接排期。
- **考核透明度**：KPI（star 增量）+ 社区/微信群里程碑**写进页面**。
- **微信群二维码**：暂无，页面留明显图片占位槽，后续丢图替换。
- **star 基线**：用 shields.io 实时 star badge（自更新，不写死，避免过期）。
- **协作节奏**：默认"每周 2-3 篇"，可改文本。

## 技术方案

单文件自包含 HTML 横向翻页 slide，与 `/story/` 先例同一套样式系统（沙滩/椰子风
CSS 变量、`#track` flex 滑轨、底部箭头+进度点+计数器、键盘 ←→ / 点击 / 触摸翻页）。
原生 JS，无构建步骤、无外部 JS 依赖（star badge 用 shields.io `<img>`）。

**独立 demo，不进 traj2skill 仓库/发版。**（本 spec 文档进仓库，HTML 本体不进。）

### 文件与部署

- 目录：`/home/admin/xskill-ops/yunying/`
  - `index.html`（页面本体）
  - `assets/`（从 `traj2skill/docs/assets/` 复制 `header.png` `demo.gif` `architecture.svg`，
    以及微信群二维码占位 `wechat-placeholder.png`，便于相对路径引用）
- 端口：`10.255.1.1:8014`（8014 当前空闲）
- `serve_all.sh` BACKENDS 表加一行：`yunying|8014|/home/admin/xskill-ops/yunying||static`
- `nginx.conf` xskill.wiki 443 vhost 加 `location = /yunying { return 301 /yunying/; }`
  与 `location /yunying/ { proxy_pass http://10.255.1.1:8014/; ... }`（照 /story/ 块）
- 生效：原地写 nginx.conf（保 inode）→ `docker exec patentdagger-nginx-1 nginx -s reload`；
  后端 `serve_all.sh up`（幂等）

## Slide 结构（约 12 屏）

1. 封面 · 欢迎：标题 + 一句话定位 + 总目标（涨 star / 建社区）+ 翻页提示
2. 产品一句话：xskill 是什么 + 杀死的痛点（agent 每次从零、团队经验隔离），平实话
3. 写给谁看：目标受众（开发者 / 技术 leader / 用 Claude Code·Cursor·Codex 的人）+ 痛点词库
4. 核心卖点（钩子）：5 个差异化卖点，每个写成可当标题的钩子
5. 三种内容角度：干货教学 / 争议观点 / 人味叙事——一句话策略 + 示例标题，**重写参考样例**，链到完整草稿
6. 语气与禁忌 Do/Don't：真实吐槽·低 AI 味·痛点共鸣 vs 别吹神·别堆术语·别像发布会·不编数据
7. 爆款公式：审查框架精华清单（标题数字+冲突+痛点词 15-25 字 / 开头 3 行 hook / W 型密度 / emoji 分隔 / 600-1200 字 / 评论区预埋）
8. 品牌素材：GitHub repo · 安装命令 · logo/header · demo gif · 可用配图，从哪取
9. 事实清单（不可错）：必须写对的硬事实 + 红线（repo 名、别瞎编模型/数字）
10. 交付物 & 协作流程：每篇交付清单 + 格式 + 节奏 + 怎么发我 + 我来发布
11. 考核口径 & 里程碑：核心 KPI = GitHub star 增量（实时 badge）+ 社区/微信群阶段里程碑（二维码占位）
12. 结语 · 联系：recap + 链接 + 联系方式

## 不做（YAGNI）

- 不做后台 / 表单 / 提交功能——纯只读简报页。
- 不做口令鉴权（用户选完全公开）。
- 不把内部基建细节（服务器 IP、埋点机制、灰度内部实现）写进公开页。
- 不直接排期 3 篇草稿（定位为参考样例）。

## 公开页内容红线

页面完全公开，避免泄露：服务器 IP / 内网端口 / 埋点(instrumentation)内部实现 /
未公开的模型与数字。只放对外口径的产品信息 + 创作指引。
