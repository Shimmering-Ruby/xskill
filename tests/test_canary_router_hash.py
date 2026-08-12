"""验证 CanaryRouter：在线偏差最小化 + pick_side hash 种子/破平。

不依赖 pytest，可用::

    python -m unittest tests.test_canary_router_hash -v

或在打榜镜像内挂载本文件执行（见 run_canary_router_hash_tests.sh）。
"""
from __future__ import annotations

import hashlib
import itertools
import unittest

from xskill.canary import CanaryRouter, pick_side


def _hash_r(traj_id: str, skill_name: str) -> float:
    h = hashlib.sha256(f"{traj_id}:{skill_name}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") / (1 << 32)


class TestPickSideHashBaseline(unittest.TestCase):
    """旧 pick_side：可复现，但小 N 会饿死一侧。"""

    def test_pick_side_deterministic(self):
        a = pick_side("client-a", "skill-x", 0.5)
        b = pick_side("client-a", "skill-x", 0.5)
        self.assertEqual(a, b)

    def test_pick_side_matches_manual_hash(self):
        cid, skill, p = "w0", "openpyxl-excel-automation", 0.5
        r = _hash_r(cid, skill)
        expected = "staging" if r < p else "main"
        self.assertEqual(pick_side(cid, skill, p), expected)

    def test_pick_side_can_starve_three_workers_p05(self):
        """证明旧算法在 N=3、p=0.5 下确实会出现全 main / 全 staging。"""
        starved_main = False
        starved_staging = False
        # 穷举一批合成 id，足够撞到全偏一侧
        for a, b, c in itertools.product(range(80), repeat=3):
            ids = [f"worker-{a}", f"worker-{b}", f"worker-{c}"]
            # 要求三个 distinct，否则不是 3 worker
            if len(set(ids)) < 3:
                continue
            sides = [pick_side(i, "s", 0.5) for i in ids]
            n_st = sides.count("staging")
            if n_st == 0:
                starved_main = True
            if n_st == 3:
                starved_staging = True
            if starved_main and starved_staging:
                break
        self.assertTrue(
            starved_main,
            "expected to find a 3-client set that pick_side maps all to main",
        )
        self.assertTrue(
            starved_staging,
            "expected to find a 3-client set that pick_side maps all to staging",
        )


class TestCanaryRouterHash(unittest.TestCase):
    """新算法：偏差最小化 + pick_side 作种子/破平。"""

    def test_seed_equals_pick_side(self):
        r = CanaryRouter()
        for cid in ("w0", "alpha", "client-deadbeef", "0"):
            for skill in ("s", "openpyxl-excel-automation"):
                for p in (0.2, 0.3, 0.5, 0.7):
                    rr = CanaryRouter()
                    got = rr.assign(
                        client_id=cid, skill_name=skill,
                        probability=p, staging_sha="sha-seed",
                    )
                    self.assertEqual(
                        got, pick_side(cid, skill, p),
                        msg=f"seed mismatch cid={cid} skill={skill} p={p}",
                    )

    def test_tiebreak_equals_pick_side_half(self):
        """main=1,staging=1 后再来第三人：误差打平，必须用 pick_side(..., 0.5)。"""
        # 构造：先让 c_main 进 main、c_st 进 staging。
        # 选一对 seed，使第一个按 p=0.5 为 main，第二个被偏差最小化补到 staging。
        found = False
        for i in range(500):
            c0, c1, c2 = f"tie-a-{i}", f"tie-b-{i}", f"tie-c-{i}"
            if pick_side(c0, "s", 0.5) != "main":
                continue
            r = CanaryRouter()
            s0 = r.assign(client_id=c0, skill_name="s", probability=0.5,
                          staging_sha="sha-tie")
            s1 = r.assign(client_id=c1, skill_name="s", probability=0.5,
                          staging_sha="sha-tie")
            if s0 != "main" or s1 != "staging":
                continue
            s2 = r.assign(client_id=c2, skill_name="s", probability=0.5,
                          staging_sha="sha-tie")
            self.assertEqual(s2, pick_side(c2, "s", 0.5))
            found = True
            break
        self.assertTrue(found, "could not construct 1:1 ledger for tie-break test")

    def test_sticky(self):
        r = CanaryRouter()
        first = r.assign(client_id="c1", skill_name="s", probability=0.5,
                         staging_sha="sha-X")
        for _ in range(20):
            self.assertEqual(
                r.assign(client_id="c1", skill_name="s", probability=0.5,
                         staging_sha="sha-X"),
                first,
            )

    def test_reset_on_staging_sha_change(self):
        r = CanaryRouter()
        self.assertEqual(
            r.assign(client_id="c1", skill_name="s", probability=1.0,
                     staging_sha="sha-OLD"),
            "staging",
        )
        self.assertEqual(
            r.assign(client_id="c1", skill_name="s", probability=0.0,
                     staging_sha="sha-NEW"),
            "main",
        )

    def test_reset_on_probability_change(self):
        r = CanaryRouter()
        r.assign(client_id="c1", skill_name="s", probability=0.0, staging_sha="sha")
        self.assertEqual(
            r.assign(client_id="c1", skill_name="s", probability=1.0,
                     staging_sha="sha"),
            "staging",
        )

    def test_never_starve_n3_p05(self):
        """相对 pick_side 的核心收益：3 worker @ p=0.5 永不 3:0 / 0:3。"""
        for trial in range(500):
            r = CanaryRouter()
            ids = [f"c{trial}-{i}" for i in range(3)]
            sides = [
                r.assign(client_id=cid, skill_name="s", probability=0.5,
                         staging_sha=f"sha-{trial}")
                for cid in ids
            ]
            n_st = sides.count("staging")
            self.assertGreaterEqual(n_st, 1, msg=sides)
            self.assertLessEqual(n_st, 2, msg=sides)

    def test_router_fixes_known_pick_side_starvation_sets(self):
        """把 pick_side 会全 main / 全 staging 的三元组交给 Router，必须两侧都有。"""
        all_main_set = None
        all_staging_set = None
        for a, b, c in itertools.product(range(60), repeat=3):
            ids = [f"worker-{a}", f"worker-{b}", f"worker-{c}"]
            if len(set(ids)) < 3:
                continue
            sides = [pick_side(i, "s", 0.5) for i in ids]
            if sides.count("staging") == 0 and all_main_set is None:
                all_main_set = ids
            if sides.count("staging") == 3 and all_staging_set is None:
                all_staging_set = ids
            if all_main_set and all_staging_set:
                break
        self.assertIsNotNone(all_main_set)
        self.assertIsNotNone(all_staging_set)

        for label, ids in (("all_main", all_main_set),
                           ("all_staging", all_staging_set)):
            r = CanaryRouter()
            sides = [
                r.assign(client_id=cid, skill_name="s", probability=0.5,
                         staging_sha=f"fix-{label}")
                for cid in ids
            ]
            n_st = sides.count("staging")
            self.assertTrue(
                1 <= n_st <= 2,
                msg=f"{label}: pick_side={ [pick_side(i,'s',0.5) for i in ids] } "
                    f"router={sides}",
            )

    def test_reproducible_across_routers(self):
        ids = ["w0", "w1", "w2"]
        seqs = []
        for _ in range(3):
            r = CanaryRouter()
            seqs.append([
                r.assign(client_id=cid, skill_name="s", probability=0.5,
                         staging_sha="sha-same")
                for cid in ids
            ])
        self.assertEqual(seqs[0], seqs[1])
        self.assertEqual(seqs[1], seqs[2])

    def test_counts_helper(self):
        r = CanaryRouter()
        r.assign(client_id="a", skill_name="s", probability=0.5, staging_sha="sha")
        r.assign(client_id="b", skill_name="s", probability=0.5, staging_sha="sha")
        r.assign(client_id="c", skill_name="s", probability=0.5, staging_sha="sha")
        c = r.counts("s")
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["main"] + c["staging"], 3)
        self.assertGreaterEqual(c["staging"], 1)
        self.assertGreaterEqual(c["main"], 1)

    def test_no_random_in_router_balanced_side(self):
        """实现约束：Router 平衡逻辑不依赖 random.random。"""
        import inspect
        from xskill import canary as canary_mod
        src = inspect.getsource(canary_mod.CanaryRouter._balanced_side)
        self.assertNotIn("random.random", src)
        self.assertIn("pick_side", src)

    def test_p02_five_clients_one_staging(self):
        for trial in range(100):
            r = CanaryRouter()
            sides = [
                r.assign(client_id=f"c{trial}-{i}", skill_name="s",
                         probability=0.2, staging_sha=f"sha-{trial}")
                for i in range(5)
            ]
            self.assertEqual(sides.count("staging"), 1, msg=sides)


class TestManifestWiring(unittest.TestCase):
    """确认 team-CS manifest 接到 CanaryRouter，而不是直接 pick_side。"""

    def test_manifest_exports_router(self):
        from xskill.team.server import skill_manifest as sm
        self.assertTrue(hasattr(sm, "_ROUTER"))
        self.assertIsInstance(sm._ROUTER, CanaryRouter)

    def test_manifest_source_uses_assign(self):
        import xskill.team.server.skill_manifest as sm
        from pathlib import Path
        src = Path(sm.__file__).read_text(encoding="utf-8")
        self.assertIn("_ROUTER.assign", src)
        self.assertIn("CanaryRouter", src)
        # 有 staging 时不应再直接 pick_side(client_id, ...)
        resolve = src.split("def _resolve_slot", 1)[1].split("def build_manifest", 1)[0]
        self.assertNotIn("pick_side(", resolve)


class TestStarvationRateComparison(unittest.TestCase):
    """统计对照：同批三元组上 pick_side vs Router 的饿死率。"""

    def test_starvation_rate_table(self):
        n_trials = 2000
        pick_starve = 0
        router_starve = 0
        for t in range(n_trials):
            ids = [f"stat-{t}-0", f"stat-{t}-1", f"stat-{t}-2"]
            ps = [pick_side(i, "s", 0.5) for i in ids]
            if ps.count("staging") in (0, 3):
                pick_starve += 1
            r = CanaryRouter()
            rs = [
                r.assign(client_id=i, skill_name="s", probability=0.5,
                         staging_sha=f"stat-{t}")
                for i in ids
            ]
            if rs.count("staging") in (0, 3):
                router_starve += 1
        # pick_side 理论约 25%；允许统计波动
        self.assertGreater(pick_starve, n_trials * 0.10)
        self.assertEqual(router_starve, 0)
        # 打印便于人工看报告（unittest 默认会显示）
        print(
            f"\n[starvation @{n_trials} trials p=0.5 N=3] "
            f"pick_side={pick_starve} ({pick_starve/n_trials:.1%}) "
            f"CanaryRouter={router_starve} ({router_starve/n_trials:.1%})"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
