# profile-scatter — 画像散点（P3-3.4，Q4/Q5 拍板）

## ADDED Requirements

### Requirement: 原子点向量随画像落盘（Q4）

`ProfileStore` SHALL 在更新画像时顺手落盘原子点向量 `points (n,D)` 与逐点
元数据 `point_meta`（atom_id/summary/ux/tags，与 points 行严格对齐——不对齐
SHALL 在写入侧抛错）。points 与 feature_tensor SHALL 出自同一次计算（散点与
聚类中心不同源会画出撒谎的图）。冷启动（无 atom）两者皆空。

#### Scenario: 对齐校验

- **当** 写入 2 行 points 但只给 1 条 point_meta
- **那么** 写入抛 ValueError，不落半份数据

### Requirement: t-SNE 散点端点（Q5 修订：PCA 线性投影对高维语义簇分离效果差，
升级为 t-SNE 邻域保持投影）

系统 SHALL 提供 `GET /user/{user_key}/scatter`（敏感内容端点，只读公网实例
不注册）：原子点、兴趣中心、skill 向量一次性联合做纯 numpy t-SNE（Van der
Maaten & Hinton 2008，含 PCA 确定性初始化 + early exaggeration + 自适应
gains）2D 投影——三者必须联合投影，t-SNE 没有 PCA 那种线性 basis 可对新点
复用，分开投影会让簇位置互相对不上。返回原子点（按最近兴趣簇着色分组 + 簇
语义名=簇内 top tag，簇归属用高维余弦而非 2D 坐标判定，比投影坐标更可信）、
兴趣中心、skill 向量投影。同一批向量 SHALL 每次调用坐标一致（不依赖随机数,
截图/复现稳定）。skill 向量 SHALL 只用 `.skill_index.pkl` 已缓存的
embedding——dashboard 无 embed_client，算不出的不显示（D6，不现算不造假点）；
维度不匹配（换过 embedding 模型）同样不显示。无画像 → 404；有画像无点（冷
启动）→ 显式 note，SHALL NOT 造假点。

#### Scenario: 簇着色与语义名

- **当** 用户原子分属 git 与 docker 两个兴趣簇
- **那么** 散点按簇二色渲染，簇标注"git"/"docker"（簇内 top tag）

#### Scenario: 无索引不画 skill 三角

- **当** skill_dir 下没有 `.skill_index.pkl`
- **那么** 响应 skills 为空，页面标注索引缺失，不发起任何 embedding 计算

### Requirement: 散点交互

前端 SHALL 在用户 & 画像页点击用户行加载散点：hover 原子点显示预览卡
（atom_id + summary + ux），点击原子点跳 atom 详情，点击 skill ▲ 跳 skill
分析页。
