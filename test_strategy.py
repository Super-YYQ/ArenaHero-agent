# -*- coding: utf-8 -*-
"""策略纯函数测试：不联网，直接构造状态验证决策。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategy import (
    StrategyState,
    WorkerTask,
    assign_resources,
    decide_worker,
    decide_vanguard,
    step_direction,
)


def test_step_toward():
    obs = set()
    assert step_direction((0, 0), (3, 0), obs, set()) == "RIGHT"
    assert step_direction((0, 0), (0, -2), obs, set()) == "UP"
    assert step_direction((0, 0), (2, 5), obs, set()) == "DOWN"
    assert step_direction((3, 3), (3, 3), obs, set()) is None


def test_step_avoids_obstacle():
    obs = {(1, 0)}
    d = step_direction((0, 0), (5, 0), obs, set())
    assert d in ("UP", "DOWN"), f"应绕开障碍，得到 {d}"


def test_step_avoids_occupied():
    occupied = {(1, 0)}
    d = step_direction((0, 0), (5, 0), set(), occupied)
    assert d in ("UP", "DOWN"), f"应绕开占用格，得到 {d}"


def test_assign_nearest_resources():
    tasks = {}
    workers = [
        {"id": "w1", "pos": (0, 0), "cargo": 0},
        {"id": "w2", "pos": (10, 10), "cargo": 0},
    ]
    resources = [(1, 0), (9, 10)]
    a = assign_resources(workers, resources, set(), tasks)
    assert a["w1"] == (1, 0)
    assert a["w2"] == (9, 10)


def test_assign_no_duplicate_claim():
    tasks = {}
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}, {"id": "w2", "pos": (0, 1), "cargo": 0}]
    resources = [(1, 0)]
    a = assign_resources(workers, resources, set(), tasks)
    # 只有一个资源点，只有一个 Worker 认领
    assert len(a) == 1
    assert list(a.values()) == [(1, 0)]


def test_loaded_worker_returns_to_core():
    w = {"id": "w1", "pos": (5, 5), "cargo": 1}
    action, args = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "move"
    assert args[0] in ("UP", "LEFT")


def test_worker_deposits_on_core_cell():
    w = {"id": "w1", "pos": (0, 0), "cargo": 2}
    action, _ = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "deposit"


def test_worker_harvests_when_on_target():
    w = {"id": "w1", "pos": (2, 2), "cargo": 0, "visible_resources": {(2, 2)}}
    tasks = {"w1": WorkerTask(state="harvest", target=(2, 2))}
    a = assign_resources([w], [(2, 2)], set(), tasks)
    action, _ = decide_worker(w, (0, 0), a, set(), set(), set())
    assert action == "harvest"


def test_worker_skips_harvest_if_resource_gone():
    w = {"id": "w1", "pos": (2, 2), "cargo": 0, "visible_resources": set()}
    action, _ = decide_worker(w, (0, 0), {"w1": (2, 2)}, set(), set(), set())
    assert action == "wait"


def test_worker_explores_when_no_target():
    w = {"id": "w1", "pos": (0, 0), "cargo": 0, "explore_target": (5, 0)}
    action, args = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "move"
    assert args[0] == "RIGHT"


def test_worker_does_not_oscillate_when_explore_blocked():
    """主方向被挡时绕行，绝不回头走刚才来的那一格。"""
    w = {
        "id": "w1",
        "pos": (0, 0),
        "cargo": 0,
        "explore_target": (5, 0),
        "last_pos": (-1, 0),  # 刚从左边走过来
    }
    obstacles = {(1, 0)}  # 正右被挡
    action, args = decide_worker(w, (0, 0), {}, obstacles, set(), set())
    assert action == "move"
    assert args[0] != "LEFT", "不应回头走到 last_pos"
    assert args[0] in ("UP", "DOWN")


def test_worker_skips_last_pos_even_if_open():
    w = {
        "id": "w1",
        "pos": (0, 0),
        "cargo": 0,
        "explore_target": (5, 0),
        "last_pos": (-1, 0),
    }
    action, args = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "move"
    assert args[0] == "RIGHT"


def test_chunk_of():
    from strategy import chunk_of
    assert chunk_of((0, 0)) == (0, 0)
    assert chunk_of((31, 31)) == (0, 0)
    assert chunk_of((32, -1)) == (1, -1)
    assert chunk_of((-1, -1)) == (-1, -1)


def test_scout_picks_least_recently_seen_chunk():
    """没有资源时，目标应落在最久没扫过的区块中心附近。"""
    from strategy import pick_scout_target, chunk_of
    core = (0, 0)
    # 原点区块刚看过，东边区块从未看过 → 应选东边
    last_seen = {(0, 0): 100}
    claimed = set()
    target = pick_scout_target(core, last_seen, claimed, slot=0, tick=100)
    assert chunk_of(target) != (0, 0), f"不应再扫刚看过的原点区块, got {target}"
    assert target not in claimed


def test_scout_avoids_claimed_targets():
    from strategy import pick_scout_target, chunk_of
    core = (0, 0)
    claimed = set()
    t1 = pick_scout_target(core, {}, claimed, slot=0, tick=1)
    claimed.add(t1)
    t2 = pick_scout_target(core, {}, claimed, slot=1, tick=1)
    assert t1 != t2


def test_assign_skips_cooled_resource():
    from strategy import assign_resources
    tasks = {}
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    resources = [(1, 0), (5, 0)]
    # (1,0) 对该 worker 冷却中
    cooldowns = {("w1", (1, 0)): 50}
    a = assign_resources(workers, resources, set(), tasks, tick=10, cooldowns=cooldowns)
    assert a["w1"] == (5, 0)


def test_stall_forces_new_explore_target():
    """连续 stall_ticks 次停在同一格 → 应换探索目标。"""
    from strategy import should_abandon_scout
    assert should_abandon_scout(stall=3, stall_limit=3) is True
    assert should_abandon_scout(stall=2, stall_limit=3) is False


def test_worker_flees_threat():
    w = {"id": "w1", "pos": (1, 0), "cargo": 0}
    threat = {(1, 0), (0, 0)}  # 自己所在格受威胁
    action, args = decide_worker(w, (5, 0), {}, set(), set(), threat)
    assert action == "move"
    assert args[0] == "RIGHT"  # 向 Core 方向撤退


def test_vanguard_sweeps_adjacent_enemy():
    v = {"id": "v1", "pos": (4, 4)}
    enemies = [{"id": "e1", "pos": (5, 4), "unit_type": "WORKER"}]
    action, args = decide_vanguard(v, (0, 0), enemies, set(), set())
    assert action == "sweep"
    assert args[0] == "RIGHT"


def test_vanguard_intercepts_near_core():
    v = {"id": "v1", "pos": (4, 4)}
    core = (0, 0)
    enemies = [{"id": "e1", "pos": (1, 0), "unit_type": "RANGER"}]
    action, args = decide_vanguard(v, core, enemies, set(), set())
    assert action == "move"  # 向敌人移动迎击
    assert args[0] in ("UP", "LEFT")


def test_vanguard_guards_core_when_clear():
    v = {"id": "v1", "pos": (4, 4)}
    action, args = decide_vanguard(v, (0, 0), [], set(), set())
    assert action == "move"
    assert args[0] in ("UP", "LEFT")
    # 已在 Core 旁边则待机
    v2 = {"id": "v1", "pos": (1, 0)}
    action2, _ = decide_vanguard(v2, (0, 0), [], set(), set())
    assert action2 == "wait"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
