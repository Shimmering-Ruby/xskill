# 海量轨迹索引系统设计（中心检索索引）

日期：2026-08-27。关联 PR #357（search traj 与 search atom 命令面）。
本文只动 server 内部的索引与检索实现，#357 定下的命令、卡片、JSON 字段、
错误话术全部保持不变。

## 规模假设与目标

- 千人团队，每人每天 10 到 30 个会话，一年累积百万级轨迹、千万级 Atom。
- 查询目标：top-k 检索 p99 在 50ms 以内，且与语料总量基本无关。
- 新鲜度目标：上传写穿即可搜；watcher 补漏一轮内追平。
- 部署约束：格力内网单台 team server，Python 加 SQLite 技术栈，
  不引入需要独立运维的外部检索服务。
- 环境约束：现网 SQLite 版本较老（本机实测 3.26，FTS5 有、trigram
  分词器没有），设计必须在 3.26 上可跑。

## 现状为什么放大不了

按 #357 现状直接放大会在五个地方撞墙：

1. 查询路径是 O(全库)。session 检索把所有 sidecar 行读进 Python 逐篇算
   BM25；atom 关键词检索每次查询重读全部 atom JSON、现场重建 BM25Okapi；
   向量检索是全量 numpy 点积。语料到十万级，每次查询就是秒级起步。
2. 按人分库带来查询扇出。每个工号一份 sidecar SQLite，一次查询要打开
   N 个库文件；千人就是千次 open 加千次全表读。
3. 分词器 `[\w]+` 把整句中文当一个 token，中文查询等同精确匹配，
   召回接近于零。这在 utils/search.py 的模块注释里已经自己承认了。
4. 会话索引只存首问前 2000 字符，用户在第二轮之后说的内容永远搜不到。
5. watcher 每轮对全部文件 stat 比对 mtime 和 size，文件数越大，
   没有任何变化的一轮也越贵。

## 总体架构

一句话：md 与 atom JSON 仍是事实源，索引收敛为 traj_root 下一个可整体
重建的中心库 .xskill_search_index.sqlite，关键词用 FTS5（C 实现的倒排加 BM25，
查询不再进 Python 循环），向量另存，写入靠上传写穿加对账表驱动的增量，
低频全量对账兜底。这延续仓库既有哲学：JSON 是事实源，SQLite 只是
可重建投影（AtomTaskStore 的既定取舍）。

分层：

- 事实源层（不动）：clients/<工号>/sessions/traj_*.md，
  <dataset>/<traj_id>/tasks/atom_*.json。
- 索引层（新）：中心 SQLite 一个文件（docs 表、docs_fts 虚表、
  files 对账表、meta 表），向量索引文件与之并排。
- 服务层（改实现不改接口）：GET /api/v1/team/trajectories/search 与
  GET /api/v1/team/atoms/search 的 handler 换成对中心库的一条 SQL。

## 数据模型

docs 表统一承载两种粒度（turn 级 chunk 放第二期）：

```sql
CREATE TABLE docs (
  doc_id       INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,          -- 'session' 或 'atom'
  traj_id      TEXT NOT NULL,
  atom_id      TEXT NOT NULL DEFAULT '',
  user         TEXT NOT NULL,          -- 工号目录名
  turns        INTEGER NOT NULL DEFAULT 0,
  offset_start INTEGER,
  offset_end   INTEGER,
  mtime_ns     INTEGER NOT NULL,
  title        TEXT NOT NULL           -- 对外描述字段：session 存首问，atom 存 summary
);
CREATE INDEX idx_docs_user ON docs(kind, user);
CREATE UNIQUE INDEX idx_docs_key ON docs(kind, traj_id, atom_id);
```

全文表用普通 FTS5 表（存转换后的文本，rowid 等于 doc_id）：

```sql
CREATE VIRTUAL TABLE docs_fts USING fts5(
  head,   -- session: 首问；atom: intent
  body,   -- session: 全部 User 轮次拼接（每轮截断）；atom: summary 加 tags
  tokenize = 'unicode61'
);
```

不用 contentless（content=''）：3.26 的 contentless 表不支持按行删除，
而我们必须支持轨迹文件删除后的索引清理。普通 FTS5 表多存一份转换后
文本，百万会话按每篇 2KB 估算约 2GB 量级，单机磁盘可以接受，换来
删改语义干净。

对账表与元数据表：

```sql
CREATE TABLE files (
  path       TEXT PRIMARY KEY,   -- 相对 traj_root
  kind       TEXT NOT NULL,      -- 'session' 或 'atom'
  mtime_ns   INTEGER NOT NULL,
  size       INTEGER NOT NULL,
  indexed_at INTEGER NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version、tokenizer_version、embed_model
```

## 中文分词：写入侧 bigram

3.26 没有 trigram 分词器，所以在写入和查询两侧共用同一个纯 Python
变换函数：拉丁与数字按词切并转小写，CJK 连续段展开为相邻二字组
（例如「内存泄漏」变成「内存 存泄 泄漏」），变换结果才进 docs_fts。
查询词经过同一函数后拼成 FTS5 MATCH 表达式。

- 零新依赖，双端一个函数保证一致；tokenizer_version 写进 meta，
  函数一变就触发后台重建。
- 召回上 bigram 对中文足够（这是中文全文检索最常见的做法），
  精度不如词典分词，但索引侧永远可以换：jieba 放第二期，
  等 bigram 的真实召回表现出来再定。

## 写入路径

1. 上传写穿。POST /api/v1/team/upload 落盘 md 之后，同一请求内解析
   User 段、在单事务里 upsert docs、docs_fts、files 三张表。
   单文件毫秒级，不会拖慢上传。
2. Atom 写入挂现有钩子。拆分代理经 AtomTaskStore.save_many 落 JSON 时
   顺带登记中心库（与现有向量投影的增量登记同一时机），embedding
   走批量队列，禁止循环内逐条（semgrep 规则已有）。
3. watcher 增量。不再每轮全量 stat：正常轮次只消费 files 表没见过
   或者 mtime、size 不一致的路径，批量上限沿用每轮 400。
4. 低频全量对账。与 atom_vector_index 现行 reconcile 同思路，定期
   比对事实源与 files 表，补漏、清删。事实源里消失的文件，
   其 docs 与 docs_fts 行同事务删除。
5. 并发模型：WAL，单写多读。上传线程与 watcher 抢同一把跨进程锁
   （复用 _sqlite_connect.connect_with_lock），查询 handler 只读。

## 查询路径

search traj 变成一条 SQL：

```sql
SELECT d.traj_id, d.user, d.title, d.turns,
       bm25(docs_fts, 3.0, 1.0) AS neg_score
FROM docs_fts JOIN docs d ON d.doc_id = docs_fts.rowid
WHERE docs_fts MATCH :q
  AND d.kind = 'session'
  AND (:no_name_filter OR d.user IN (...))
ORDER BY neg_score, d.mtime_ns DESC
LIMIT :k;
```

- head 权重 3.0，body 权重 1.0：首问命中排前，但后续轮次的内容
  也能召回（补掉现状第 4 条瓶颈）。
- 不读 md，不进 Python 循环，百万行毫秒级；工号过滤是 SQL 谓词，
  没有每人一库的扇出。
- 并列时 mtime 新者在前。显式时间衰减打分放第二期。

search atom 双通道：FTS5 关键词取 top-200 候选，向量通道取 top-200，
RRF（倒数排名融合）合并后截 top-k。对外的 sources 字段照旧标
vector 与 keyword，vector_similarity 与 bm25_score 照旧透出。
说明：utils/search.py 现状是 union 加 dedup 不做融合排序，当时是
按需求定的；语料放大后两路各自的分数不可比，RRF 是标准解法，
此处属于有意变更。

## 向量通道

- 只对 atom 建向量（intent 加 summary），session 不打 embedding，
  与 #357 的约束一致。
- 规模账：一千万 atom 乘 1024 维 float32 是 40GB，单机内存放不下。
  分两档走：
  - 百万级以内：int8 量化矩阵加 numpy 分块点积，实现最简单；
    embedding 模型若支持可截断维度，取 256 维，量化后约 2.5GB。
  - 千万级：换 usearch 的磁盘 HNSW（mmap，不常驻内存），
    key 直接用 doc_id。接口层先把这两档收在同一个类后面，
    切换不动调用方。
- embed_model 写进 meta，换模型走后台重建，重建期间旧向量照常服务。

## 一致性与重建

- 索引永远可从事实源整体重建。新增 xskill index rebuild：写
  .xskill_search_index.sqlite.building，完成后原子 rename 切换，期间旧索引
  照常服务查询。
- schema_version、tokenizer_version、embed_model 任一变化都走这条
  后台重建路径，不做原地迁移。
- 首次升级即一次后台 backfill；落后期间查询照常返回已建部分，
  响应 meta 里带索引落后提示，不阻塞。

## 与 #357 的关系

- 用户面零变化：两条命令、卡片文案、--json 字段、七种错误话术照旧。
- server 内部：traj_search.py 的 load_session_docs 加纯 Python BM25
  路径退役，换成中心库查询；search_session_trajectories 与
  search_indexed_atoms 的函数签名可以保留，便于本机单机模式复用
  同一套索引代码（单机就是 traj_root 换成本机 registry 目录）。
- sidecar 文件 .xskill_traj_session_index.sqlite 保留读兼容一个版本，
  中心库建成后停止写入；文件本身以后再清理，不在本期删。

## 本期不做

- 不引 Elasticsearch、Meilisearch、tantivy 等外部引擎或二进制依赖。
  内网单机加 pip 安装的部署形态下，嵌入式是对的；若未来语料超出
  SQLite FTS5 舒适区（约五千万文档），先评估 tantivy-py，再谈服务化。
- 不做磁盘级按人硬隔离（#357 已写明）。
- turn 级 chunk 索引与行号定位、时间衰减打分、jieba 词典分词：
  第二期，视 bigram 召回与 head 加权的实际表现再定。
- 跨机分片：单机撑不住之前不做；真到那步按月份分库 scatter-gather，
  第二期。

## 验证方案

- 合成语料压测：造 10 万与 100 万会话（中英混合），量建库耗时、
  库文件大小、top-10 查询 p50 与 p99，对照现状 Python BM25。
- 中文召回对照：同一批真实中文查询在 bigram 与现状整句 token 两套
  索引上的命中率对比。
- 一致性：随机删改事实源文件后跑对账，验证索引收敛。
