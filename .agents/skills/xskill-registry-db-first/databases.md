# 库清单

路径默认在 `XSKILL_HOME`（通常是 `~/.xskill`）下，除非另行说明。

| 库 | 侧 | 干什么 |
| --- | --- | --- |
| `registry.db` | server / 单机 | 管线与看板主库。轨迹、采纳、skill 目录投影、体验分投影、推荐相关记录等，面板多数查询走这里。 |
| `team_clients.db` | server | 团队里各客户端的注册与在线相关状态，以及看板身份一类映射。 |
| `team_profile.db` | server | 推荐用的用户画像与推荐存储。 |
| `client_state.db` | client | 本机上传进度、已传集合、collector 进度。 |
| `installations.sqlite` | client | 本机把 skill 装到各生态的账本。查「装了什么」走账本，不靠盲扫用户 skills 目录。 |

业务读请求选上表中的库。不要为同一类信息再新建平行库。

外部生态自己的数据库或用户目录（例如某些 agent 产品的会话库、用户 skills 目录）是摄入源或安装目标，不是业务热路径里反复全表扫描的对象。摄入后业务仍读上表自有库。
