"""构造 team server 的 ``SkillRecommendEngine``(含 ``ClientRegistry``),供 web
startup 与短命 ``profile-refresh`` 子进程共用——两处构造逻辑收敛到一处,source 唯一。

画像落 ``~/.xskill/team_profile.db``(SQLite);读写两侧只经该库通信,故画像计算可
搬到独立短命子进程,web 进程读同一份库即得最新画像。
"""
from __future__ import annotations

from xskill.canary import CanaryConfig
from xskill.config import (
    XSKILL_HOME,
    get_skill_dir,
    get_team_clients_db_path,
    get_team_trajectories_dir,
)
from xskill.recommend.engine import SkillRecommendEngine
from xskill.team.server.client_registry import ClientRegistry
from xskill.utils.llm import create_embed_client


def build_recommend_engine(config: dict) -> SkillRecommendEngine:
    """构造带 ``ClientRegistry`` 的推荐引擎。

    ``client_registry`` 既用于 ``client_id → 目录名`` 解析读 atom,也用于枚举全部
    client(``registry.list()``)做批量画像刷新。
    """
    return SkillRecommendEngine(
        config=config,
        skill_dir=get_skill_dir(),
        traj_root=get_team_trajectories_dir(),
        embed_client=create_embed_client(config),
        profile_db=XSKILL_HOME / "team_profile.db",
        canary_config=CanaryConfig.from_dict(config.get("canary", {})),
        client_registry=ClientRegistry(get_team_clients_db_path()),
    )
