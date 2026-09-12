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


def test_loaded_worker_does_not_reverse():
    """满载回城也不能走进刚离开的格子，否则会和对面友军对撞换位。"""
    w = {"id": "w1", "pos": (2, 0), "cargo": 1, "last_pos": (1, 0)}
    action, args = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "move"
    assert args[0] != "LEFT"


def test_two_loaded_workers_do_not_swap():
    """相邻满载 Worker 回城时，不能互换格子。"""
    from strategy import DELTA
    core = (0, 0)
    a = {"id": "a", "pos": (1, 2), "cargo": 1}
    b = {"id": "b", "pos": (1, 1), "cargo": 1}
    obstacles = {(0, 1), (1, 0)}
    occupied = {core, a["pos"], b["pos"]}
    action_a, args_a = decide_worker(a, core, {}, obstacles, occupied, set())
    assert action_a == "move"
    dest_a = (a["pos"][0] + DELTA[args_a[0]][0], a["pos"][1] + DELTA[args_a[0]][1])
    occupied.add(dest_a)
    action_b, args_b = decide_worker(b, core, {}, obstacles, occupied, set())
    assert action_b == "move"
    dest_b = (b["pos"][0] + DELTA[args_b[0]][0], b["pos"][1] + DELTA[args_b[0]][1])
    assert not (dest_a == b["pos"] and dest_b == a["pos"]), "相邻 Worker 互换了格子"


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
    """近处航点都扫过之后，应去更久未见的外圈区块，而不是反复扫家门口。"""
    from strategy import pick_scout_target, _scout_points, SCOUT_RING_STEP
    core = (0, 0)
    last_seen = {(0, 0): 100}
    waypoints = {}
    for _, cand in _scout_points(core):
        dist = abs(cand[0] - core[0]) + abs(cand[1] - core[1])
        if dist <= SCOUT_RING_STEP + 1:
            waypoints[cand] = 100
    target = pick_scout_target(core, last_seen, set(), slot=0, tick=100, waypoint_last_seen=waypoints)
    assert target not in waypoints, f"近处航点扫完后不应再去, got {target}"
    assert abs(target[0]) + abs(target[1]) > SCOUT_RING_STEP


def test_scout_avoids_claimed_targets():
    from strategy import pick_scout_target, chunk_of
    core = (0, 0)
    claimed = set()
    t1 = pick_scout_target(core, {}, claimed, slot=0, tick=1)
    claimed.add(t1)
    t2 = pick_scout_target(core, {}, claimed, slot=1, tick=1)
    assert t1 != t2


def test_worker_waits_when_on_explore_target():
    """到了侦察点应停下，让下一 Tick 换目标；不能沿远离 Core 方向继续走。"""
    w = {"id": "w1", "pos": (5, 0), "cargo": 0, "explore_target": (5, 0)}
    action, _ = decide_worker(w, (0, 0), {}, set(), set(), set())
    assert action == "wait"


def test_two_idle_workers_scout_different_quadrants():
    """两个空闲 Worker 不能都挤在东/东南近处。"""
    from strategy import assign_explore_targets, StrategyState
    core = (0, 0)
    state = StrategyState()
    workers = [
        {"id": "w1", "pos": (0, 0), "cargo": 0},
        {"id": "w2", "pos": (0, 0), "cargo": 0},
    ]
    assign_explore_targets(workers, {}, core, state, tick=1)
    t1 = workers[0]["explore_target"]
    t2 = workers[1]["explore_target"]
    assert t1 != t2
    q1 = (t1[0] - core[0] >= 0, t1[1] - core[1] >= 0)
    q2 = (t2[0] - core[0] >= 0, t2[1] - core[1] >= 0)
    assert q1 != q2, f"两个 Worker 落在同一象限: {t1} {t2}"


def test_scout_does_not_reassign_cell_just_arrived():
    """到达侦察点后，下一个目标不能还是当前格。"""
    from strategy import assign_explore_targets, StrategyState, chunk_of
    core = (0, 0)
    state = StrategyState()
    here = (10, 0)
    workers = [{"id": "w1", "pos": here, "cargo": 0}]
    state.explore_targets["w1"] = here
    state.scout_slots["w1"] = 0
    state.chunk_last_seen[chunk_of(here)] = 50
    assign_explore_targets(workers, {}, core, state, tick=50)
    assert workers[0]["explore_target"] != here


def test_repeated_scout_picks_cover_north():
    """连续换目标应扫到北侧（y 小于 Core），不能永远停在南/东。"""
    from strategy import assign_explore_targets, StrategyState, chunk_of
    core = (0, 0)
    state = StrategyState()
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    saw_north = False
    for tick in range(1, 9):
        prev = workers[0].get("explore_target")
        if prev is not None:
            workers[0]["pos"] = prev
            state.chunk_last_seen[chunk_of(prev)] = tick
        assign_explore_targets(workers, {}, core, state, tick)
        target = workers[0]["explore_target"]
        if target[1] < core[1]:
            saw_north = True
    assert saw_north, "8 次重选应至少有一次朝北"


def test_repeated_scout_covers_north_when_core_near_chunk_south():
    """Core 贴着区块南沿时（实战常见），也不能只往南/东扫。"""
    from strategy import assign_explore_targets, StrategyState, chunk_of
    core = (11, 25)  # 区块 (0,0) 内距南沿 6 格、距北沿 25 格
    state = StrategyState()
    workers = [{"id": "w1", "pos": core, "cargo": 0}]
    saw_north = False
    for tick in range(1, 9):
        prev = workers[0].get("explore_target")
        if prev is not None:
            workers[0]["pos"] = prev
            state.chunk_last_seen[chunk_of(prev)] = tick
        assign_explore_targets(workers, {}, core, state, tick)
        if workers[0]["explore_target"][1] < core[1]:
            saw_north = True
    assert saw_north, "Core 靠南时 8 次重选也应朝北"


def test_scout_does_not_steal_inflight_target():
    """还在路上的侦察目标不能被刚到点的另一个 Worker 抢走。

    w1 到点后槽位变成 1，偏好朝向 (1,-1) 的最近点是 (5,-5)；
    若不清点就重选，会把 w2 正在走的 (5,-5) 抢走。
    """
    from strategy import assign_explore_targets, StrategyState
    core = (0, 0)
    inflight = (5, -5)
    state = StrategyState()
    state.explore_targets["w1"] = (10, 0)
    state.scout_slots["w1"] = 0
    state.explore_targets["w2"] = inflight
    state.scout_slots["w2"] = 7
    workers = [
        {"id": "w1", "pos": (10, 0), "cargo": 0},
        {"id": "w2", "pos": (0, 0), "cargo": 0},
    ]
    assign_explore_targets(workers, {}, core, state, tick=1)
    assert workers[1]["explore_target"] == inflight
    assert workers[0]["explore_target"] != inflight


def test_scout_prefers_near_unseen_over_far_heading():
    """家区块已扫过时，应先去近处未见（西侧约 20），不能因槽位偏好东边而跑到 30+。"""
    from strategy import pick_scout_target, chunk_of
    core = (11, 25)
    last_seen = {chunk_of(core): 50}
    target = pick_scout_target(core, last_seen, set(), slot=0, tick=50)
    dist = abs(target[0] - core[0]) + abs(target[1] - core[1])
    assert dist <= 20, f"近处未见优先，got {target} dist={dist}"


def test_scout_skips_obstacle_waypoints():
    """障碍格不能当侦察目标。"""
    from strategy import pick_scout_target
    core = (0, 0)
    obstacles = {(10, 0), (5, 5), (0, 10), (-5, 5), (-10, 0), (-5, -5), (0, -10), (5, -5)}
    target = pick_scout_target(core, {}, set(), slot=0, tick=1, obstacles=obstacles)
    assert target not in obstacles


def test_blocked_explore_target_is_abandoned():
    """正在走的侦察点若是障碍，立刻换目标，不要围着墙转。"""
    from strategy import assign_explore_targets, StrategyState
    core = (0, 0)
    blocked = (0, -30)
    state = StrategyState()
    state.explore_targets["w1"] = blocked
    state.scout_slots["w1"] = 6
    workers = [{"id": "w1", "pos": (0, -28), "cargo": 0}]
    assign_explore_targets(workers, {}, core, state, tick=1, obstacles={blocked})
    assert workers[0]["explore_target"] != blocked


def test_no_progress_abandons_explore_target():
    """曼哈顿距离一直不缩短（来回徘徊）应换目标，即使每 Tick 都在 move。"""
    from strategy import assign_explore_targets, StrategyState, SCOUT_NO_PROGRESS_TICKS
    core = (0, 0)
    target = (10, 0)
    state = StrategyState()
    state.explore_targets["w1"] = target
    state.scout_slots["w1"] = 0
    workers = [{"id": "w1", "pos": (5, 0), "cargo": 0}]
    for tick in range(1, SCOUT_NO_PROGRESS_TICKS + 2):
        # 在目标旁等距徘徊：距离不变，不能算作接近
        workers[0]["pos"] = (5, 1 if tick % 2 else -1)
        assign_explore_targets(workers, {}, core, state, tick)
    assert workers[0]["explore_target"] != target


def test_detour_oscillation_abandons_harvest_target():
    """绕墙时越走越远、一直碰不到点，应冷却换目标。"""
    from strategy import assign_resources, WorkerTask, RESOURCE_NO_PROGRESS_TICKS
    tasks = {"w1": WorkerTask(state="harvest", target=(10, 0))}
    workers = [{"id": "w1", "pos": (6, 1), "cargo": 0}]
    cooldowns = {}
    state_progress = {}
    resources = [(10, 0), (0, 10)]
    for tick in range(1, RESOURCE_NO_PROGRESS_TICKS + 3):
        # 绕墙越绕越远：x 逐渐后退，到不了 (10,0)
        workers[0]["pos"] = (6 - (tick // 2), 1 if tick % 2 else 2)
        a = assign_resources(
            workers, resources, {(7, 0), (8, 0), (9, 0)}, tasks,
            tick=tick, cooldowns=cooldowns, progress=state_progress,
        )
    assert cooldowns.get(("w1", (10, 0)), 0) > tick, "绕墙无进展应冷却该点"
    assert a.get("w1") != (10, 0)


def test_hungarian_avoids_worker_collision():
    """两个 Worker、两个点：每人一个，不能都挤最近的那颗。"""
    from strategy import assign_resources
    tasks = {}
    workers = [
        {"id": "w1", "pos": (0, 0), "cargo": 0},
        {"id": "w2", "pos": (1, 0), "cargo": 0},
    ]
    resources = [(2, 0), (20, 0)]
    a = assign_resources(workers, resources, set(), tasks)
    assert set(a.values()) == {(2, 0), (20, 0)}


def test_stale_memory_resource_is_deprioritized():
    """刚看见的点应优先于很久没确认的迷雾记忆点。"""
    from strategy import assign_resources
    tasks = {}
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    resources = [(3, 0), (1, 0)]
    last_seen = {(3, 0): 100, (1, 0): 1}
    a = assign_resources(workers, resources, set(), tasks, tick=100, last_seen=last_seen)
    assert a["w1"] == (3, 0)


def test_line_of_sight_keeps_resource_behind_wall():
    """墙后的记忆点不能因为菱形视野 overlapping 就被清掉。"""
    from memory import MapMemory
    from strategy import visible_from
    from pathlib import Path
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    mem = MapMemory(tmp)
    mem.resource_seen[(5, 0)] = 10
    mem.obstacles.add((2, 0))
    vis = visible_from((0, 0), 5, mem.obstacles)
    assert (5, 0) not in vis
    mem.observe(11, [(2, 0)], [], (0, 0), visible_cells=vis)
    assert (5, 0) in mem.resource_seen


def test_harvested_cell_is_tombstoned_not_reassigned():
    """采过的旧格不能再当目标；应去复查区块而不是死磕原坐标。"""
    from strategy import assign_resources, StrategyState, chunk_of
    state = StrategyState()
    here = (4, 0)
    state.harvested_until[here] = 50
    tasks = {}
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    a = assign_resources(workers, [here], set(), tasks, tick=10, harvested_until=state.harvested_until)
    assert "w1" not in a


def test_due_chunk_recheck_beats_far_ring():
    """到了 4 Tick 补充边界，应先复查刚采过的区块，而不是跑去更远的环。"""
    from strategy import assign_explore_targets, StrategyState, chunk_of
    core = (0, 0)
    state = StrategyState()
    harvested = (5, 0)
    ch = chunk_of(harvested)
    state.chunk_next_refill[ch] = 4
    state.chunk_anchor[ch] = harvested
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers, {}, core, state, tick=4)
    target = workers[0]["explore_target"]
    assert chunk_of(target) == ch, f"应复查刚采过的区块, got {target}"


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


# ---------- Phase 0：固定地图夹具与基线回归 ----------

def test_fixture_sanity_bfs_oracle():
    """夹具自检：除 enclosed 外全部可达，enclosed 确认不可达。"""
    from map_fixtures import bfs_oracle, get_fixture
    for name in ("empty", "straight_wall", "l_shape", "concave",
                 "bottleneck", "negative_quadrant", "cross_chunk"):
        obstacles, start, goal = get_fixture(name)
        assert bfs_oracle(obstacles, start, goal) is not None, f"{name} 应可达"
    obstacles, start, goal = get_fixture("enclosed")
    assert bfs_oracle(obstacles, start, goal) is None, "enclosed 应不可达"


def test_step_direction_regressions_on_fixtures():
    """简单场景的 step_direction 动作保持不变（基线回归）。"""
    from map_fixtures import get_fixture
    obstacles, start, goal = get_fixture("empty")
    assert step_direction(start, goal, obstacles, set()) == "RIGHT"

    obstacles, start, goal = get_fixture("straight_wall")
    assert step_direction(start, goal, obstacles, set()) == "RIGHT"
    # 贴墙时才垂直绕行
    d = step_direction((3, 0), goal, obstacles, set())
    assert d in ("UP", "DOWN"), f"贴墙应垂直绕行, got {d}"

    obstacles, start, goal = get_fixture("bottleneck")
    assert step_direction(start, goal, obstacles, set()) == "RIGHT"

    obstacles, start, goal = get_fixture("negative_quadrant")
    d = step_direction(start, goal, obstacles, set())
    assert d is not None

    obstacles, start, goal = get_fixture("concave")
    d = step_direction(start, goal, obstacles, set())
    assert d is not None and d != "LEFT"


def test_step_direction_negative_coords():
    """负坐标下的方向计算与正坐标一致。"""
    assert step_direction((-4, -4), (-1, -4), set(), set()) == "RIGHT"
    assert step_direction((-4, -4), (-4, -9), set(), set()) == "UP"
    assert step_direction((-4, -4), (-9, -4), set(), set()) == "LEFT"


def load_tests(loader, tests, pattern):
    """让 `python -m unittest discover` 也能执行本文件的普通函数测试。"""
    import unittest
    suite = unittest.TestSuite()
    for name in sorted(globals()):
        if name.startswith("test_"):
            suite.addTest(unittest.FunctionTestCase(globals()[name]))
    return suite


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
