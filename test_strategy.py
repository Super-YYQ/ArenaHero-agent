# -*- coding: utf-8 -*-
"""策略纯函数测试：不联网，直接构造状态验证决策。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategy import (
    StrategyState,
    WorkerTask,
    assign_resources,
    decide_ranger,
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
    """连续换目标应扫到北侧（y 小于 Core），不能永远停在南/东。

    （sweep=False 固定旧的环形航点行为；扫掠覆盖由 test_sweep_* 系列把关。）
    """
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
        assign_explore_targets(workers, {}, core, state, tick, sweep=False)
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


# ---------- Phase 2：混合规划器集成回归 ----------

def _simulate_worker_to_goal(fixture_name: str, max_ticks: int = 300, use_planner: bool = True):
    """单 Worker 沿决策函数走到目标的确定性仿真，返回到达所用 Tick（失败 None）。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture(fixture_name)
    obstacles = set(obstacles)
    planner = HybridPathPlanner() if use_planner else None
    pos, last_pos = start, None
    for tick in range(1, max_ticks + 1):
        if planner:
            planner.begin_tick(0)  # 与 agent 一致：每 Tick 重置预算
        worker = {"id": "w1", "pos": pos, "cargo": 0, "last_pos": last_pos}
        action, args = decide_worker(
            worker, (0, 0), {"w1": goal}, obstacles, {pos}, set(), planner=planner,
        )
        if action == "move":
            dx, dy = DELTA[args[0]]
            last_pos, pos = pos, (pos[0] + dx, pos[1] + dy)
            assert pos not in obstacles, "走进障碍格"
        else:
            assert action in ("wait", "harvest"), f"意外动作 {action}"
        if pos == goal:
            return tick
    return None


def test_planner_reaches_goal_on_complex_fixtures():
    """直墙/L 形/凹形/瓶颈：规划器都能到达，不因局部振荡提前放弃。"""
    for name in ("straight_wall", "l_shape", "concave", "bottleneck"):
        ticks = _simulate_worker_to_goal(name)
        assert ticks is not None, f"{name} 未到达目标"


def test_planner_fast_layer_matches_greedy():
    """近距离无遮挡走快速层，动作与旧贪心一致。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    r = p.next_step("w", (0, 0), (3, 0), obstacles=set(), occupied=set())
    assert r.status == "FOUND" and r.steps == ("RIGHT",)
    assert r.reason == "fast_layer"
    assert p.stats.fast_steps == 1 and p.stats.astar_calls == 0


def test_planner_route_cursor_avoids_replanning():
    """同一路线游标复用，不重复展开 A*；走完即 AT_TARGET。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(0)
    pos, last_pos, arrived = start, None, False
    for tick in range(1, 200):
        r = p.next_step("w", pos, goal, obstacles=obstacles, occupied={pos})
        if tick == 1:
            assert p.stats.astar_calls == 1, "首步应完成一次 A*"
        else:
            assert p.stats.astar_calls == 1, f"tick {tick} 游标复用不应重新 A*"
        if r.status == "AT_TARGET":
            arrived = True
            break
        assert r.steps, f"tick {tick} 无动作"
        dx, dy = DELTA[r.steps[0]]
        last_pos, pos = pos, (pos[0] + dx, pos[1] + dy)
    assert arrived, "长路线应沿游标走完"
    assert pos == goal


def test_planner_dynamic_block_triggers_local_replan():
    """路线游标首步被动态占用阻挡：局部重规划换路，不把动态格写进静态结构。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner, steps_to_cells
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(0)
    r1 = p.next_step("w", start, goal, obstacles=obstacles, occupied={start})
    assert r1.steps
    dx, dy = DELTA[r1.steps[0]]
    blocked_cell = (start[0] + dx, start[1] + dy)
    # Worker 被挡住没有移动，同位置重新决策但首步格被占
    r2 = p.next_step("w", start, goal, obstacles=obstacles,
                     occupied={start, blocked_cell})
    assert r2.steps and r2.steps[0] != r1.steps[0], "应局部重规划换一步"
    assert p.stats.replans >= 1
    # 新路线不得包含被占格
    route_steps = p.routes["w"].steps if "w" in p.routes else r2.steps
    assert blocked_cell not in set(steps_to_cells(start, route_steps))
    assert blocked_cell not in set(steps_to_cells(start, r2.steps))


def test_planner_blocked_records_failed_goal():
    """确认不可达：返回 BLOCKED 并记录失败目标，快速层随后被抑制。"""
    from map_fixtures import get_fixture
    from pathfinding import BLOCKED, HybridPathPlanner
    obstacles, start, goal = get_fixture("enclosed")
    p = HybridPathPlanner(fast_path_distance=0)  # 强制走 A* 层
    p.begin_tick(1)
    r = p.next_step("w", start, goal, obstacles=obstacles, occupied=set())
    assert r.status == BLOCKED
    assert goal in p._failed_goals["w"]
    r2 = p.next_step("w", start, goal, obstacles=obstacles, occupied=set())
    assert r2.status == BLOCKED
    # 换个目标不再被抑制，A* 正常找到路线
    r3 = p.next_step("w", start, (2, 2), obstacles=set(), occupied=set())
    assert r3.status == "FOUND"


def test_planner_tick_budget_shared_and_never_blocked():
    """总预算耗尽：后续 Worker 得到 BUDGET_EXHAUSTED，绝不误报 BLOCKED。"""
    from map_fixtures import get_fixture
    from pathfinding import BUDGET_EXHAUSTED, HybridPathPlanner
    obstacles, _, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner(total_path_budget=10)
    p.begin_tick(0)
    r1 = p.next_step("a", (0, 0), goal, obstacles=obstacles, occupied=set())
    assert p._budget_left == 0
    r2 = p.next_step("b", (0, 0), goal, obstacles=obstacles, occupied=set())
    assert r2.status == BUDGET_EXHAUSTED
    assert r2.reason == "tick_budget"


def test_planner_version_bump_clears_routes():
    """地图版本递增（新增障碍）：全部路线游标丢弃，下一步重规划。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(1)
    p.next_step("w", (0, 0), (40, 5), obstacles=set(), occupied=set())
    assert p.routes
    p.begin_tick(2)
    assert not p.routes
    assert p.stats.invalidated >= 1


def test_planner_core_goal_occupied_still_entered():
    """回 Core：Core 格被占用也不放弃（交付例外），本步只走合法格。"""
    from pathfinding import DELTA, HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    r = p.next_step("w", (3, 0), (0, 0), obstacles=set(),
                    occupied={(0, 0), (2, 0)}, allow_goal_occupied=True)
    assert r.status == "FOUND" and r.steps
    d = r.steps[0]
    nxt = (3 + DELTA[d][0], 0 + DELTA[d][1])
    assert nxt not in {(0, 0), (2, 0)}, "不得走进占用格"
    # 后续 Tick 能把 Core 当目标继续规划（占用格会被 allow_goal_occupied 剔除）
    r2 = p.next_step("w", nxt, (0, 0), obstacles=set(),
                     occupied={(0, 0), (2, 0)}, allow_goal_occupied=True)
    assert r2.status in ("FOUND", "AT_TARGET")


def test_decide_worker_with_planner_matches_legacy_on_simple_maps():
    """简单场景下，规划器开关不改变 Worker 决策结果。"""
    w = {"id": "w1", "pos": (0, 0), "cargo": 0, "visible_resources": {(3, 0)}}
    legacy = decide_worker(w, (0, 0), {"w1": (3, 0)}, set(), set(), set())
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    planned = decide_worker(w, (0, 0), {"w1": (3, 0)}, set(), set(), set(), planner=p)
    assert legacy == planned == ("move", ("RIGHT",))
    # 满载回 Core
    w2 = {"id": "w2", "pos": (3, 3), "cargo": 2}
    assert decide_worker(w2, (0, 0), {}, set(), set(), set()) == ("move", ("LEFT",)) \
        or decide_worker(w2, (0, 0), {}, set(), set(), set()) == ("move", ("UP",))
    p2 = HybridPathPlanner()
    p2.begin_tick(0)
    a1 = decide_worker(w2, (0, 0), {}, set(), set(), set())
    a2 = decide_worker(w2, (0, 0), {}, set(), set(), set(), planner=p2)
    assert a1 == a2


def test_agent_blocked_resource_gets_cooldown():
    """规划器确认资源不可达 → 沿用现有冷却/任务清理规则。"""
    import tempfile

    from agent import Agent
    from map_fixtures import get_fixture
    from memory import MapMemory
    obstacles, start, goal = get_fixture("enclosed")
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    # fast_path_distance=0 强制走 A* 层（否则近距目标会先走快速层）
    agent = Agent({"enable_path_planner": True, "fast_path_distance": 0}, mem=MapMemory(tmp))
    agent.planner.begin_tick(0)
    agent.planner.next_step("w1", start, goal, obstacles=obstacles, occupied=set())
    wdict = {"id": "w1", "pos": start, "cargo": 0}
    agent._handle_route_result(wdict, {"w1": goal}, tick=10)
    assert agent.strat.resource_cooldowns[("w1", goal)] > 10
    assert "w1" not in agent.strat.worker_tasks


def test_agent_planner_toggle_fallback():
    """enable_path_planner=false 时回退旧实现；缺省开启且共享 routes。"""
    import tempfile

    from agent import Agent
    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    off = Agent({"enable_path_planner": False}, mem=MapMemory(tmp))
    assert off.planner is None
    on = Agent({}, mem=MapMemory(tmp))
    assert on.planner is not None
    assert on.planner.routes is on.strat.routes


def test_planner_config_defaults_and_validation():
    """旧 config 缺字段可启动；非法数值回退默认。"""
    from agent import planner_config
    cfg = planner_config({})
    assert cfg["enable_path_planner"] is True
    assert cfg["astar_max_expansions"] == 800
    bad = planner_config({
        "astar_max_expansions": -5,
        "route_cache_size": "x",
        "fast_path_distance": 10 ** 9,
        "total_path_budget": True,
        "unknown_cell_penalty": 3,
    })
    assert bad["astar_max_expansions"] == 800
    assert bad["route_cache_size"] == 256
    assert bad["fast_path_distance"] == 12
    assert bad["total_path_budget"] == 5000
    assert bad["unknown_cell_penalty"] == 3


def test_memory_obstacle_revision_increments():
    """新增障碍版本号递增；重复观察与资源变化不递增。"""
    import tempfile

    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    mem = MapMemory(tmp)
    mem.observe(1, [(2, 0)], [], (0, 0))
    assert mem.obstacle_revision == 1
    mem.observe(2, [(2, 0)], [], (0, 0))
    assert mem.obstacle_revision == 1
    mem.observe(3, [(3, 0)], [], (0, 0))
    assert mem.obstacle_revision == 2
    mem.observe(4, [], [(9, 9)], (0, 0))
    assert mem.obstacle_revision == 2
    assert mem.obstacle_revision > 0 and mem.resource_seen[(9, 9)] == 4


# ---------- Phase 3：路线缓存与动态占用校验 ----------

def test_route_cache_hit_avoids_reexpansion():
    """同起点同目标（地图未变）：第二个 Worker 命中缓存，不重复展开 A*。"""
    from map_fixtures import get_fixture
    from pathfinding import HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(0)
    r1 = p.next_step("a", start, goal, obstacles=obstacles, occupied={start})
    assert r1.status == "FOUND" and p.stats.astar_calls == 1
    r2 = p.next_step("b", start, goal, obstacles=obstacles, occupied={start})
    assert r2.steps == r1.steps
    assert r2.reason == "cache_hit"
    assert p.stats.astar_calls == 1, "缓存命中不得重新展开 A*"
    assert p.stats.cache_hits == 1 and p.stats.cache_misses == 1


def test_cache_not_polluted_by_dynamic_occupancy():
    """动态占用只触发局部重规划；静态缓存内容保持不变，可被后续命中。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(0)
    r1 = p.next_step("a", start, goal, obstacles=obstacles, occupied={start})
    dx, dy = DELTA[r1.steps[0]]
    blocked_cell = (start[0] + dx, start[1] + dy)
    # 另一 Worker 的占用挡住缓存路线首步：走局部重规划，不写缓存
    r2 = p.next_step("b", start, goal, obstacles=obstacles,
                     occupied={start, blocked_cell})
    assert r2.steps[0] != r1.steps[0]
    assert p.stats.replans >= 1
    # 动态阻塞解除后，原静态路线仍可命中
    r3 = p.next_step("c", start, goal, obstacles=obstacles, occupied={start})
    assert r3.reason == "cache_hit"
    assert r3.steps == r1.steps, "缓存被动态占用污染"
    assert len(p.cache) == 1, "replan 结果不得写入静态缓存"


def test_route_cache_lru_capacity():
    """缓存有上限：超出容量淘汰最旧条目，计数正确。"""
    from pathfinding import FOUND, PathResult, RouteCache, RouteCacheKey
    cache = RouteCache(capacity=2)
    ka = RouteCacheKey((0, 0), (1, 0), 0, "astar")
    kb = RouteCacheKey((0, 0), (2, 0), 0, "astar")
    kc = RouteCacheKey((0, 0), (3, 0), 0, "astar")
    result = PathResult(FOUND, ("RIGHT",), (1, 0), 1, 1, 0)
    cache.put(ka, result)
    cache.put(kb, result)
    assert cache.get(ka) is not None  # 刷新 ka，kb 成为最旧
    cache.put(kc, result)
    assert cache.get(kb) is None, "最旧条目应被淘汰"
    assert cache.get(ka) is not None and cache.get(kc) is not None
    assert len(cache) == 2
    assert cache.hits == 3 and cache.misses == 1 and cache.evictions == 1
    assert cache.clear() == 2 and cache.invalidated == 2


def test_cache_cleared_on_version_bump():
    """地图版本递增：静态缓存整体失效，下一步重新规划。"""
    from map_fixtures import get_fixture
    from pathfinding import HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(1)
    p.next_step("a", start, goal, obstacles=obstacles, occupied={start})
    assert len(p.cache) == 1
    p.begin_tick(2)
    assert len(p.cache) == 0
    assert p.cache.invalidated == 1
    r = p.next_step("a", start, goal, obstacles=obstacles, occupied={start})
    assert r.reason != "cache_hit"


def test_cache_hit_then_cursor_walks_to_goal():
    """缓存命中后建立路线游标，能一路走到目标。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    p = HybridPathPlanner()
    p.begin_tick(0)
    assert p.next_step("a", start, goal, obstacles=obstacles, occupied={start}).steps
    # 另一 Worker 命中缓存后沿游标行进
    pos, last_pos, arrived = start, None, False
    for _ in range(200):
        r = p.next_step("b", pos, goal, obstacles=obstacles, occupied={pos})
        if r.status == "AT_TARGET":
            arrived = True
            break
        assert r.steps
        dx, dy = DELTA[r.steps[0]]
        last_pos, pos = pos, (pos[0] + dx, pos[1] + dy)
    assert arrived and pos == goal


# ---------- Phase 4：BFS 前沿与渐进降级 ----------

_POCKET_WALL = {(1, y) for y in range(-15, 16)}


def test_planner_astar_budget_falls_back_to_bfs_frontier():
    """A* 预算内无进展（死路口袋）→ BFS approach 前沿推进，最终到达。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, FRONTIER, HybridPathPlanner
    obstacles, start, goal = get_fixture("straight_wall")  # 直墙 8 格，先用它验证快速层外路径
    p = HybridPathPlanner(astar_max_expansions=50, frontier_max_expansions=4000,
                          fast_path_distance=0)
    p.begin_tick(0)
    r = p.next_step("w", (0, 0), (10, 0), obstacles=_POCKET_WALL, occupied={(0, 0)})
    assert r.status == FRONTIER
    assert r.reason == "approach_frontier"
    assert p.stats.bfs_calls == 1
    # 沿前沿逐 Tick 推进最终到达（每 Tick 重置预算，与 agent 一致）
    pos, arrived = (0, 0), False
    for _ in range(300):
        p.begin_tick(0)
        r = p.next_step("w", pos, (10, 0), obstacles=_POCKET_WALL, occupied={pos})
        if r.status == "AT_TARGET":
            arrived = True
            break
        assert r.steps, f"前沿推进中无动作: {r.status}/{r.reason}"
        dx, dy = DELTA[r.steps[0]]
        pos = (pos[0] + dx, pos[1] + dy)
    assert arrived and pos == (10, 0)


def test_scout_blocked_gets_explore_frontier_not_failed():
    """侦察目标被封死：返回探索前沿继续推进，不记录失败目标。"""
    from map_fixtures import get_fixture
    from pathfinding import BLOCKED, FRONTIER, HybridPathPlanner
    obstacles, start, goal = get_fixture("enclosed")
    # 已知区域：墙盒及以西全部已知，盒以东是迷雾
    known = {c for c in ((x, y) for x in range(-5, 13) for y in range(-5, 6))}
    p = HybridPathPlanner(fast_path_distance=0)
    p.begin_tick(1, is_known=lambda c: c in known)
    r = p.next_step("w", start, goal, obstacles=obstacles, occupied={start},
                    goal_kind="scout")
    assert r.status == FRONTIER, f"侦察应有探索前沿, got {r.status}/{r.reason}"
    assert goal not in p._failed_goals.get("w", set())
    # 同一目标作为资源目标：确认 BLOCKED 并记录失败（交给冷却）
    p2 = HybridPathPlanner(fast_path_distance=0)
    p2.begin_tick(1, is_known=lambda c: c in known)
    r2 = p2.next_step("w", start, goal, obstacles=obstacles, occupied={start},
                      goal_kind="harvest")
    assert r2.status == BLOCKED
    assert goal in p2._failed_goals["w"]


def test_budget_exhausted_does_not_cooldown_resource():
    """预算不足绝不触发资源冷却；只有确认 BLOCKED 才进入失败处理。"""
    import tempfile

    from agent import Agent
    from map_fixtures import get_fixture
    from memory import MapMemory
    from pathfinding import BUDGET_EXHAUSTED
    obstacles, start, goal = get_fixture("cross_chunk")
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    agent = Agent({"enable_path_planner": True, "total_path_budget": 1,
                   "fast_path_distance": 0}, mem=MapMemory(tmp))
    agent.planner.begin_tick(0)
    r = agent.planner.next_step("w1", start, goal, obstacles=obstacles, occupied={start})
    assert r.status == BUDGET_EXHAUSTED
    wdict = {"id": "w1", "pos": start, "cargo": 0}
    agent._handle_route_result(wdict, {"w1": goal}, tick=10)
    assert ("w1", goal) not in agent.strat.resource_cooldowns, "预算不足不得冷却资源"


# ---------- Phase 5：增量区块导航摘要 ----------

def test_chunk_of_reexport_negative():
    """pathfinding.chunk_of 与 strategy.chunk_of 同一实现，负坐标语义一致。"""
    import strategy
    from pathfinding import chunk_of as pf_chunk_of
    assert strategy.chunk_of is pf_chunk_of
    assert pf_chunk_of((-1, -1)) == (-1, -1)
    assert pf_chunk_of((-32, 5)) == (-1, 0)
    assert pf_chunk_of((-33, -1)) == (-2, -1)
    assert pf_chunk_of((31, 31)) == (0, 0)
    assert pf_chunk_of((32, 0)) == (1, 0)


def test_chunk_index_openings_and_components():
    """边界开放格与连通分量：只在内容实际变化时重算。"""
    from pathfinding import ChunkNavigationIndex
    idx = ChunkNavigationIndex()
    edge_cells = [(31, y) for y in range(0, 5)]
    inner = [(x, 2) for x in range(28, 31)]
    n = idx.observe(edge_cells + inner, {(31, 2)})
    assert n == 1  # 全部落在区块 (0,0)
    s = idx.summary((0, 0))
    assert s.revision == 1
    assert (31, 2) not in s.boundary_openings["RIGHT"]
    assert set(s.boundary_openings["RIGHT"]) == {(31, 0), (31, 1), (31, 3), (31, 4)}
    # 障碍把东边格列切成两段:内部 (28..30,2) 与 (31,0),(31,1)... 分量数 > 1
    assert len(s.connected_components) >= 2
    # 重复观察不重算
    assert idx.observe(edge_cells + inner, {(31, 2)}) == 0
    assert s.revision == 1
    # 新障碍触发重算
    assert idx.observe([], {(31, 0)}) == 1
    assert s.revision == 2 and (31, 0) not in s.boundary_openings["RIGHT"]
    # is_known:障碍格也是已知格
    assert idx.is_known((31, 0)) and idx.is_known((28, 2))
    assert not idx.is_known((5, 5))


def test_chunk_index_corridor_and_portals_negative():
    """曼哈顿走廊与门户：负坐标区块之间同样成立。"""
    from pathfinding import ChunkNavigationIndex
    idx = ChunkNavigationIndex()
    assert idx.corridor((-1, -1), (1, 0)) == [(-1, -1), (0, -1), (1, -1), (1, 0)]
    # 区块 (-1,0) 与 (0,0) 的东向门户:x=-1 与 x=0 的相邻格对
    cells = []
    for y in (0, 1, 2):
        cells.append((-1, y))
        cells.append((0, y))
    idx.observe(cells, set())
    pairs = idx.portal_cells((-1, 0), (0, 0))
    assert ((-1, 0), (0, 0)) in pairs and ((-1, 2), (0, 2)) in pairs
    # 未知邻居:无门户
    assert idx.portal_cells((0, 0), (1, 0)) == []


def test_chunk_index_persistence_roundtrip():
    """to_dict/from_dict 往返:已知格与门户保持,连通分量加载后重算。"""
    from pathfinding import ChunkNavigationIndex
    idx = ChunkNavigationIndex()
    idx.observe([(0, 0), (1, 0), (2, 0), (31, 0)], {(31, 0)})
    s = idx.summary((0, 0))
    comps_before = s.connected_components
    data = idx.to_dict()
    idx2 = ChunkNavigationIndex()
    idx2.from_dict(data)
    s2 = idx2.summary((0, 0))
    assert s2.known_cells == s.known_cells
    assert s2.known_obstacles == s.known_obstacles
    assert s2.boundary_openings == s.boundary_openings
    assert s2.revision == s.revision
    assert s2.connected_components == comps_before
    # 坏条目只丢该区块
    data["bad-key"] = {"revision": 1}
    data["0,0"]["known_cells"] = "corrupted"
    idx3 = ChunkNavigationIndex()
    idx3.from_dict(data)
    assert len(idx3.chunks) == 0


def test_planner_cross_chunk_uses_portals():
    """跨区块目标：走廊+门户逐段规划；未启用索引时退回普通 A*。"""
    from map_fixtures import get_fixture
    from pathfinding import ChunkNavigationIndex, DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture("cross_chunk")
    idx = ChunkNavigationIndex()
    known = []
    for x in range(0, 46):
        for y in range(-3, 11):
            known.append((x, y))
    idx.observe(known, obstacles)
    p = HybridPathPlanner(fast_path_distance=12, chunk_index=idx)
    p.begin_tick(0, is_known=idx.is_known)
    r = p.next_step("w", start, goal, obstacles=obstacles, occupied={start})
    assert r.reason == "chunk_segment", f"应走跨区块走廊, got {r.reason}"
    # 沿游标推进最终到达
    pos, arrived = start, False
    for _ in range(200):
        p.begin_tick(0, is_known=idx.is_known)
        r = p.next_step("w", pos, goal, obstacles=obstacles, occupied={pos})
        if r.status == "AT_TARGET":
            arrived = True
            break
        assert r.steps, f"无动作: {r.status}/{r.reason}"
        dx, dy = DELTA[r.steps[0]]
        pos = (pos[0] + dx, pos[1] + dy)
    assert arrived and pos == goal
    # 未启用索引：同目标走普通 A*
    p2 = HybridPathPlanner(fast_path_distance=12)
    p2.begin_tick(0, is_known=idx.is_known)
    r2 = p2.next_step("w", start, goal, obstacles=obstacles, occupied={start})
    assert r2.reason != "chunk_segment"


def test_memory_chunk_persistence_and_corruption():
    """memory.json 往返：导航摘要恢复；损坏导航字段只丢摘要。"""
    import json
    import tempfile

    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    mem = MapMemory(tmp)
    mem.observe(1, [(2, 0)], [], (0, 0), visible_cells={(0, 0), (1, 0), (2, 0), (3, 0)})
    mem.save()
    mem2 = MapMemory(tmp)
    assert mem2.obstacles == {(2, 0)}
    assert mem2.chunk_index.is_known((1, 0))
    assert mem2.chunk_index.is_known((2, 0))
    assert not mem2.chunk_index.is_known((9, 9))
    assert mem2.obstacle_revision == 1
    # 导航字段损坏：只丢摘要
    data = json.loads(tmp.read_text(encoding="utf-8"))
    data["chunk_navigation"] = "garbage"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    mem3 = MapMemory(tmp)
    assert mem3.obstacles == {(2, 0)}, "障碍记忆不得丢失"
    assert len(mem3.chunk_index) == 0
    # 旧 schema（无 schema_version / chunk_navigation）可加载
    old = {"obstacles": [[5, 5]], "resources": {"6,6": 3}, "core_position": [0, 0]}
    tmp.write_text(json.dumps(old), encoding="utf-8")
    mem4 = MapMemory(tmp)
    assert mem4.obstacles == {(5, 5)} and mem4.resource_seen == {(6, 6): 3}


def test_agent_chunk_navigation_toggle():
    """enable_chunk_navigation=false 时不给规划器区块索引。"""
    import tempfile

    from agent import Agent
    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    off = Agent({"enable_chunk_navigation": False}, mem=MapMemory(tmp))
    assert off.planner.chunk_index is None
    on = Agent({}, mem=MapMemory(tmp))
    assert on.planner.chunk_index is not None
    assert on.planner.is_known is None  # begin_tick 前未注入


# ---------- Phase 6：侦察接入与统一观测反馈 ----------

def test_decide_worker_planner_scout_moves_and_waits():
    """规划器接管侦察移动；到点停；无航点 wait（不再走 away 兜底）。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    w = {"id": "w1", "pos": (0, 0), "cargo": 0, "explore_target": (5, 0)}
    action, args = decide_worker(w, (0, 0), {}, set(), set(), set(), planner=p)
    assert (action, args) == ("move", ("RIGHT",))
    # 到点停下
    w2 = {"id": "w1", "pos": (5, 0), "cargo": 0, "explore_target": (5, 0)}
    assert decide_worker(w2, (0, 0), {}, set(), set(), set(), planner=p) == ("wait", ())
    # 无航点：wait
    w3 = {"id": "w1", "pos": (0, 0), "cargo": 0}
    assert decide_worker(w3, (0, 0), {}, set(), set(), set(), planner=p) == ("wait", ())


def test_planner_scout_routes_around_wall_to_waypoint():
    """侦察航点在墙后：规划器绕行到达，不再两格振荡。"""
    from map_fixtures import get_fixture
    from pathfinding import DELTA, HybridPathPlanner
    obstacles, start, goal = get_fixture("straight_wall")
    p = HybridPathPlanner()
    pos, last_pos = start, None
    arrived = False
    for tick in range(1, 60):
        p.begin_tick(0)
        w = {"id": "w1", "pos": pos, "cargo": 0, "explore_target": goal, "last_pos": last_pos}
        action, args = decide_worker(w, (0, 0), {}, obstacles, {pos}, set(), planner=p)
        if action == "move":
            dx, dy = DELTA[args[0]]
            last_pos, pos = pos, (pos[0] + dx, pos[1] + dy)
        if pos == goal:
            arrived = True
            break
    assert arrived, "侦察应绕墙到达航点"


def test_agent_scout_blocked_abandons_waypoint():
    """侦察航点确认不可达：记录 waypoint_last_seen 并放弃目标。"""
    import tempfile

    from agent import Agent
    from map_fixtures import get_fixture
    from memory import MapMemory
    obstacles, start, goal = get_fixture("enclosed")
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    agent = Agent({"enable_path_planner": True, "fast_path_distance": 0}, mem=MapMemory(tmp))
    agent.planner.begin_tick(1)
    agent.planner.next_step("w1", start, goal, obstacles=obstacles,
                            occupied={start}, goal_kind="scout")
    wdict = {"id": "w1", "pos": start, "cargo": 0, "explore_target": goal}
    agent._handle_route_result(wdict, {}, tick=1)
    assert agent.strat.waypoint_last_seen[goal] == 1
    assert "w1" not in agent.strat.explore_targets


def test_two_scouts_do_not_crosslock_via_cache():
    """两个侦察共享规划器：目的地不冲突，动态占用互不污染静态路线。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    obstacles = {(3, 0)}
    occupied = {(0, 0), (0, 1)}
    r1 = p.next_step("w1", (0, 0), (6, 0), obstacles=obstacles, occupied=occupied)
    r2 = p.next_step("w2", (0, 1), (0, 6), obstacles=obstacles, occupied=occupied)
    assert r1.steps and r2.steps
    from pathfinding import DELTA
    d1 = (0 + DELTA[r1.steps[0]][0], 0 + DELTA[r1.steps[0]][1])
    d2 = (0 + DELTA[r2.steps[0]][0], 1 + DELTA[r2.steps[0]][1])
    assert d1 != d2, "两个侦察本 Tick 目的地不能相同"
    assert d1 not in occupied and d2 not in occupied


# ---------- Phase 7：性能、观测和稳定性 ----------

def test_planner_prune_workers_cleans_stale_ids():
    """失效 Worker ID 的路线/失败记录/占用计数全部清理。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    p.next_step("alive", (0, 0), (40, 5), obstacles=set(), occupied={(0, 0)})
    p.next_step("dead", (0, 0), (40, 5), obstacles=set(), occupied={(0, 0)})
    p._failed_goals["dead"] = {(5, 5)}
    p._fast_blocks["dead"] = ((5, 5), 3)
    p.prune_workers({"alive"})
    assert "dead" not in p.routes
    assert "dead" not in p._failed_goals
    assert "dead" not in p._fast_blocks
    assert "dead" not in p.last_results
    assert "alive" in p.routes


def test_chunk_index_prune_components():
    """冷区丢弃详细连通分量，内容再次变化时惰性重建。"""
    from pathfinding import ChunkNavigationIndex
    idx = ChunkNavigationIndex()
    idx.observe([(0, 0), (1, 0), (2, 0)], set(), now_tick=10)
    s = idx.summary((0, 0))
    assert s.connected_components
    assert idx.prune_components(1000, cold_after_ticks=512) == 1
    assert s.connected_components == ()
    assert s.boundary_openings  # 冷区仍保留边界摘要
    # 再次活跃（内容变化）→ 重建
    idx.observe([(3, 0)], set(), now_tick=1001)
    assert s.connected_components
    assert idx.prune_components(1002, cold_after_ticks=512) == 0


class _FakeUnit:
    def __init__(self, uid, pos):
        self.id = uid
        self.position = pos
        self.cargo = 0
        self.calls = []

    def move(self, d):
        self.calls.append(("move", str(d)))

    def wait(self):
        self.calls.append(("wait", None))

    def harvest(self):
        self.calls.append(("harvest", None))

    def deposit(self):
        self.calls.append(("deposit", None))


def test_worker_exception_does_not_block_others():
    """单 Worker 决策异常：该 Worker wait，其余 Worker 正常决策。"""
    import tempfile
    import types

    import agent as agent_mod
    from agent import Agent
    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    ag = Agent({"enable_path_planner": True}, mem=MapMemory(tmp))
    real = agent_mod.decide_worker

    def flaky(wdict, *a, **kw):
        if wdict["id"] == "w1":
            raise RuntimeError("boom")
        return real(wdict, *a, **kw)

    agent_mod.decide_worker = flaky
    try:
        w1, w2 = _FakeUnit("u1", (0, 0)), _FakeUnit("u2", (0, 1))
        wd1 = {"id": "w1", "pos": (0, 0), "cargo": 0}
        wd2 = {"id": "w2", "pos": (0, 1), "cargo": 0}
        ag._run_workers(types.SimpleNamespace(workers=[w1, w2]),
                        [wd1, wd2], (0, 0), {"w2": (2, 1)}, set(),
                        {(0, 0), (0, 1)}, set(), set(), 1)
    finally:
        agent_mod.decide_worker = real
    assert w1.calls == [("wait", None)], "异常 Worker 应回退 wait"
    assert w2.calls and w2.calls[0][0] == "move", "其余 Worker 不受影响"
    assert ag.strat.last_pos["w1"] == (0, 0)


def test_reconnect_restores_memory_and_drops_routes():
    """断线重连：静态地图与导航摘要恢复，路线游标丢弃后可重新规划。"""
    import tempfile

    from agent import Agent
    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    ag1 = Agent({"enable_path_planner": True}, mem=MapMemory(tmp))
    ag1.planner.begin_tick(1)
    ag1.planner.next_step("w1", (0, 0), (40, 5), obstacles=set(), occupied={(0, 0)})
    ag1.mem.observe(1, [(2, 0)], [], (0, 0), visible_cells={(0, 0), (1, 0), (2, 0)})
    ag1.mem.save()
    assert ag1.planner.routes
    # 重连：全新 Agent/规划器，加载同一 memory.json
    ag2 = Agent({"enable_path_planner": True}, mem=MapMemory(tmp))
    assert ag2.planner.routes == {}
    assert ag2.mem.obstacles == {(2, 0)}
    assert ag2.mem.chunk_index.is_known((1, 0))
    ag2.planner.begin_tick(1, is_known=ag2.mem.chunk_index.is_known)
    r = ag2.planner.next_step("w1", (0, 0), (40, 5),
                              obstacles=ag2.mem.obstacles, occupied={(0, 0)})
    # 重连后已知地图稀疏：未知惩罚下允许先返回前沿，但必须可推进
    assert r.status in ("FOUND", "FRONTIER")
    assert r.steps


# ---------- 近场逐区块扫掠（配额 2 世界里发现资源的主要手段） ----------

def test_chunk_sweep_points_shape_and_coverage():
    """扫描点都在区块内，行距 ≤7、首尾覆盖首尾列（行走沿线视野无缝）。"""
    from strategy import CHUNK_SIZE, SWEEP_LINE_OFFSETS, chunk_sweep_points
    pts = chunk_sweep_points((1, 2))
    x0, y0 = CHUNK_SIZE, 2 * CHUNK_SIZE
    assert len(pts) == 25
    assert all(x0 <= x < x0 + CHUNK_SIZE and y0 <= y < y0 + CHUNK_SIZE for x, y in pts)
    lines = sorted({y - y0 for _, y in pts})
    assert lines == list(SWEEP_LINE_OFFSETS)
    # 视野 ±3：行带并集必须覆盖 0..31
    covered = set()
    for dy in lines:
        covered.update(range(dy - 3, dy + 4))
    assert set(range(32)) <= covered
    # 首点 ≤3、末点 ≥28，行走方向连续覆盖列
    xs = sorted(x - x0 for x, _ in pts if _ == y0 + lines[0])
    assert xs[0] <= 3 and xs[-1] >= 28


def test_sweep_assigns_distinct_chunks():
    """多个空闲 Worker 认领互不相同的扫掠区块和停留点。"""
    from strategy import StrategyState, assign_explore_targets
    state = StrategyState()
    workers = [
        {"id": "w1", "pos": (0, 0), "cargo": 0},
        {"id": "w2", "pos": (0, 0), "cargo": 0},
        {"id": "w3", "pos": (0, 0), "cargo": 0},
    ]
    assign_explore_targets(workers, {}, (0, 0), state, 1)
    chunks = [state.sweep_assign[w["id"]] for w in workers]
    assert len(set(chunks)) == 3, f"扫掠区块应互不相同: {chunks}"
    targets = [w["explore_target"] for w in workers]
    assert len(set(targets)) == 3


def test_sweep_cursor_advances_and_cycles():
    """停留点逐点推进；一个区块扫完后标记完成并轮转到别的区块。"""
    from strategy import StrategyState, assign_explore_targets, chunk_of, chunk_sweep_points
    state = StrategyState()
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers, {}, (0, 0), state, 1)
    chunk = state.sweep_assign["w1"]
    targets = [workers[0]["explore_target"]]
    for tick in range(2, 40):
        workers[0]["pos"] = workers[0]["explore_target"]  # 到达上一停留点
        assign_explore_targets(workers, {}, (0, 0), state, tick)
        targets.append(workers[0]["explore_target"])
    assert targets[:25] == chunk_sweep_points(chunk), "第一轮应按扫描线顺序推进"
    assert state.chunk_last_swept.get(chunk) is not None
    assert state.sweep_cursor[chunk] == 0, "扫完游标归零等待下一轮"
    # 之后轮转到别的区块
    assert chunk_of(targets[25]) != chunk


def test_sweep_due_refill_chunk_gets_priority():
    """到期复查的资源区块优先被扫掠（整块扫，比稀疏探点更容易撞见补点）。"""
    from strategy import StrategyState, assign_explore_targets, chunk_of
    state = StrategyState()
    state.chunk_next_refill[(1, 0)] = 4
    state.chunk_anchor[(1, 0)] = (40, 5)
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers, {}, (0, 0), state, 4)
    target = workers[0]["explore_target"]
    assert chunk_of(target) == (1, 0), f"应优先扫到期区块, got {target}"
    assert state.chunk_last_probe[(1, 0)] == 4
    # 节流：同一复查周期内不会重复优先
    state2 = StrategyState()
    state2.chunk_next_refill[(1, 0)] = 4
    state2.chunk_last_probe[(1, 0)] = 4
    workers2 = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers2, {}, (0, 0), state2, 4)
    assert chunk_of(workers2[0]["explore_target"]) != (1, 0)


def test_sweep_keeps_active_chunks_spread():
    """多 Worker 同时认领时,活跃区块两两保持切比雪夫距离 ≥2,不再挤在同一片。"""
    from strategy import StrategyState, assign_explore_targets
    state = StrategyState()
    workers = [{"id": f"w{i}", "pos": (0, 0), "cargo": 0} for i in range(1, 8)]
    assign_explore_targets(workers, {}, (0, 0), state, 1)
    chunks = [state.sweep_assign[w["id"]] for w in workers]
    assert len(set(chunks)) == len(chunks)
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            a, b = chunks[i], chunks[j]
            gap = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
            assert gap >= 2, f"活跃区块 {a} 与 {b} 相距 {gap}, 会造成 Worker 重叠"
    # 延续:Worker 未被打断时保持自己的区块
    assign_explore_targets(workers, {}, (0, 0), state, 2)
    assert [state.sweep_assign[w["id"]] for w in workers] == chunks


def test_sweep_skips_obstacle_points():
    """扫描点撞上障碍时游标跳过，绝不停在障碍格上。"""
    from strategy import StrategyState, assign_explore_targets
    state = StrategyState()
    obstacles = {(2, 3), (8, 3)}  # chunk (0,0) 的前两个扫描点
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers, {}, (0, 0), state, 1, obstacles=obstacles)
    assert workers[0]["explore_target"] == (14, 3)
    assert state.sweep_cursor[(0, 0)] == 3


def test_sweep_harvest_duty_releases_chunk():
    """Worker 被派去采集/满载时释放扫掠区块，空出来给别人接力。"""
    from strategy import StrategyState, assign_explore_targets
    state = StrategyState()
    w1 = {"id": "w1", "pos": (0, 0), "cargo": 0}
    assign_explore_targets([w1], {}, (0, 0), state, 1)
    assert "w1" in state.sweep_assign
    # 拿到采集任务 → 释放扫掠
    w1["pos"] = (5, 5)
    assign_explore_targets([w1], {"w1": (9, 9)}, (0, 0), state, 2)
    assert "w1" not in state.sweep_assign
    assert "w1" not in state.explore_targets


# ---------- 敌方感知与规避 ----------

def test_enemy_threat_cells_by_type():
    """Ranger 直线 3 格、Vanguard 相邻、敌方 Worker 无威胁、Core 有规避圈。"""
    from strategy import enemy_threat_cells
    enemies = [
        {"pos": (0, 0), "unit_type": "RANGER"},
        {"pos": (20, 0), "unit_type": "VANGUARD"},
        {"pos": (40, 0), "unit_type": "WORKER"},
        {"pos": (60, 0), "unit_type": None},
    ]
    threat, zones = enemy_threat_cells(enemies, set())
    # Ranger:同行 1..3 格都在射程(含负方向与斜线)
    assert (1, 0) in threat and (3, 0) in threat and (-3, 0) in threat
    assert (2, 2) in threat and (0, -3) in threat
    assert (4, 0) not in threat, "Ranger 射程只有 3 格"
    # Vanguard:相邻 1 格
    assert (21, 0) in threat and (20, 1) in threat and (22, 0) not in threat
    # 敌方 Worker 不能攻击:不产生威胁
    assert (41, 0) not in threat and (40, 0) not in threat
    # 敌方 Core:相邻 1 格是攻击威胁,周边 4 格只是路线规避圈
    assert (61, 0) in threat
    assert (60, 4) not in threat, "Core 规避圈不是攻击威胁"
    assert (60, 4) in zones and (64, 0) in zones and (65, 0) not in zones


def test_enemy_ranger_line_blocked_by_obstacle():
    """Ranger 射线被障碍挡住:障碍之后的格子不再是威胁。"""
    from strategy import enemy_threat_cells
    threat, _ = enemy_threat_cells([{"pos": (0, 0), "unit_type": "RANGER"}],
                                   {(2, 0)})
    assert (1, 0) in threat
    assert (2, 0) not in threat and (3, 0) not in threat
    # 另一侧没被挡,仍然在射程内
    assert (-3, 0) in threat


def test_worker_flees_ranger_line():
    """Worker 站在 Ranger 射线上(相距 2 格)也触发撤退。"""
    from pathfinding import HybridPathPlanner
    from strategy import enemy_threat_cells
    threat, zones = enemy_threat_cells([{"pos": (0, 0), "unit_type": "RANGER"}], set())
    p = HybridPathPlanner()
    p.begin_tick(0)
    w = {"id": "w1", "pos": (-2, 0), "cargo": 0}
    action, args = decide_worker(w, (-5, 0), {}, set(), {(-2, 0)}, threat,
                                 planner=p, threat_zones=zones)
    assert action == "move"
    assert args[0] == "LEFT", "应向远离 Ranger 的方向撤退"


def test_worker_not_threatened_by_enemy_worker():
    """敌方 Worker 不能攻击:相邻也不触发撤退。"""
    w = {"id": "w1", "pos": (0, 0), "cargo": 0, "visible_resources": {(3, 0)}}
    legacy = decide_worker(w, (0, 0), {"w1": (3, 0)}, set(), set(), set())
    assert legacy == ("move", ("RIGHT",))


def test_sweep_skips_enemy_zone_points():
    """敌方基地规避圈内的扫描点被跳过,不当目标。"""
    from strategy import StrategyState, assign_explore_targets
    state = StrategyState()
    enemy_zone = {(2, 3), (8, 3)}  # chunk (0,0) 的前两个扫描点
    workers = [{"id": "w1", "pos": (0, 0), "cargo": 0}]
    assign_explore_targets(workers, {}, (0, 0), state, 1, avoid_zones=enemy_zone)
    assert workers[0]["explore_target"] == (14, 3)
    assert state.sweep_cursor[(0, 0)] == 3


def test_memory_enemy_cores_persist_and_correct():
    """敌方 Core 记忆持久化往返;搬迁旧位由视野校正清除。"""
    import json
    import tempfile

    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    mem = MapMemory(tmp)
    known = mem.observe_enemies(10, [((-50, 60), "RivalPlayer")], visible_cells={(-50, 60)})
    assert known == 1
    # 重复确认不新增
    assert mem.observe_enemies(11, [((-50, 60), "RivalPlayer")]) == 0
    # 迁走:原位置可见但已无敌 Core → 清除
    mem.observe_enemies(12, [], visible_cells={(-50, 60)})
    assert (-50, 60) not in mem.enemy_cores
    # 持久化往返
    mem.observe_enemies(13, [((-40, 70), "RivalPlayer")])
    mem.save()
    mem2 = MapMemory(tmp)
    assert mem2.enemy_cores[(-40, 70)]["owner"] == "RivalPlayer"
    # 旧 schema(无 enemy_cores 字段)可加载
    old = {"obstacles": [[1, 1]], "resources": {}}
    tmp.write_text(json.dumps(old), encoding="utf-8")
    assert MapMemory(tmp).enemy_cores == {}


# ---------- 攒钱模式与生产保留金 ----------
from arena_hero import CoreState, UnitType


class _FakeCoreView:
    state = CoreState.NORMAL
    hp = 5


class _FakeCore:
    def __init__(self):
        self.view = _FakeCoreView()
        self.spawned = []

    def spawn(self, unit_type):
        self.spawned.append(unit_type)


def _fake_turn(resources, population=10, tick=1):
    import types
    return types.SimpleNamespace(
        resources=resources, tick=tick,
        state=types.SimpleNamespace(population=population))


def _hoard_agent(extra_cfg=None):
    import tempfile

    from agent import Agent
    from memory import MapMemory
    tmp = Path(tempfile.mkdtemp()) / "m.json"
    cfg = {"enable_path_planner": False, "max_workers": 10, "max_vanguards": 2}
    cfg.update(extra_cfg or {})
    return Agent(cfg, mem=MapMemory(tmp))


def test_hoard_mode_blocks_all_spawns():
    """攒钱模式：无论资源多少都不生产。"""
    ag = _hoard_agent({"hoard_mode": True})
    core = _FakeCore()
    ag._decide_core(_fake_turn(20), core, n_workers=5, n_vanguards=0)
    assert core.spawned == [], "攒钱模式下不应生产任何 Unit"


def test_hoard_releases_when_target_reached():
    """设置目标库存：达标前暂停，达标后恢复生产并保持。"""
    ag = _hoard_agent({"hoard_mode": True, "hoard_until_resources": 50})
    core = _FakeCore()
    ag._decide_core(_fake_turn(49), core, n_workers=5, n_vanguards=0)
    assert core.spawned == []
    ag._decide_core(_fake_turn(50), core, n_workers=5, n_vanguards=0)
    assert core.spawned == [UnitType.WORKER], "达标后恢复生产"
    ag._decide_core(_fake_turn(8), core, n_workers=6, n_vanguards=0)
    assert len(core.spawned) == 2, "恢复后不再回到攒钱状态"


def test_min_spawn_reserve_keeps_buffer():
    """保留金：只在 resources - price ≥ reserve 时才生产。"""
    ag = _hoard_agent({"min_spawn_reserve": 10})
    core = _FakeCore()
    ag._decide_core(_fake_turn(12), core, n_workers=5, n_vanguards=0)
    assert core.spawned == [], "12-5=7 < 10 不应生产"
    ag._decide_core(_fake_turn(20), core, n_workers=5, n_vanguards=0)
    assert core.spawned == [UnitType.WORKER], "20-5=15 ≥ 10 应生产"


def test_normal_spawn_without_hoard_unchanged():
    """不开攒钱/保留金时生产行为与旧版一致。"""
    ag = _hoard_agent()
    core = _FakeCore()
    ag._decide_core(_fake_turn(5), core, n_workers=5, n_vanguards=0)
    assert core.spawned == [UnitType.WORKER]
    ag2 = _hoard_agent({"min_spawn_reserve": 0})
    core2 = _FakeCore()
    ag2._decide_core(_fake_turn(10), core2, n_workers=10, n_vanguards=0)
    assert core2.spawned == [UnitType.VANGUARD], "Worker 满编后按旧逻辑补 Vanguard"


def test_sweep_prefers_chunks_near_worker():
    """远处 Worker 重新认领时选离自己近的未扫区块,不被派去地图对角。"""
    from strategy import StrategyState, assign_explore_targets, chunk_of
    state = StrategyState()
    # Worker 在东北角远处;核心块与其余块均未扫
    workers = [{"id": "w1", "pos": (-1320, 1575), "cargo": 0}]
    assign_explore_targets(workers, {}, (-1397, 1657), state, 200)
    ch = state.sweep_assign["w1"]
    assert ch == (-42, 49), f"应就近认领东北角块, got {ch}"
    assert chunk_of(workers[0]["explore_target"]) == (-42, 49)


def test_hoard_waits_for_min_population():
    """hoard_min_population：人口未达标先正常扩军，达标后才进入攒钱。"""
    ag = _hoard_agent({"hoard_mode": True, "hoard_until_resources": 95,
                       "hoard_min_population": 19, "max_workers": 19})
    core = _FakeCore()
    # 人口 10 < 19：正常生产（不受攒钱模式影响）
    ag._decide_core(_fake_turn(5), core, n_workers=10, n_vanguards=0)
    assert core.spawned == [UnitType.WORKER]
    # 人口 19 达标：进入攒钱
    ag._decide_core(_fake_turn(5), core, n_workers=19, n_vanguards=0)
    assert len(core.spawned) == 1, "人口达标后应暂停生产"
    ag._decide_core(_fake_turn(94), core, n_workers=19, n_vanguards=0)
    assert len(core.spawned) == 1, "攒钱期间不生产"
    ag._decide_core(_fake_turn(95), core, n_workers=19, n_vanguards=0)
    assert len(core.spawned) == 2, "达标(95)后恢复生产"


# ---------- Core 格进入串行化(修复交付死锁) ----------

def test_core_entry_serialized():
    """同一 Tick 只允许一个 Worker 申报进入 Core 格,其余排队。"""
    from pathfinding import DELTA, HybridPathPlanner
    core = (-1397, 1657)
    p = HybridPathPlanner()
    p.begin_tick(0)
    workers = [
        {"id": "w1", "pos": (-1396, 1657), "cargo": 1},
        {"id": "w2", "pos": (-1398, 1657), "cargo": 1},
        {"id": "w3", "pos": (-1397, 1658), "cargo": 1},
    ]
    occupied = {core} | {w["pos"] for w in workers}
    core_reserved = False
    entries = 0
    for w in workers:
        action, args = decide_worker(w, core, {}, set(), set(occupied), set(),
                                     planner=p, core_cell_reserved=core_reserved)
        if action == "move":
            dx, dy = DELTA[args[0]]
            dest = (w["pos"][0] + dx, w["pos"][1] + dy)
            if dest == core:
                entries += 1
                core_reserved = True
    assert entries <= 1, f"同一 Tick {entries} 个 Worker 申报进 Core,服务端会整批拒绝"


def test_legacy_core_entry_serialized():
    """旧贪心路径同样遵守 Core 格串行申报。"""
    from pathfinding import DELTA
    core = (0, 0)
    w = {"id": "w1", "pos": (1, 0), "cargo": 1}
    action, args = decide_worker(w, core, {}, set(), {core, (1, 0)}, set(),
                                 core_cell_reserved=True)
    if action == "move":
        dx, dy = DELTA[args[0]]
        assert (1 + dx, 0 + dy) != core, "已有人申报进 Core,本 Worker 不得再进"


def test_core_entry_queue_drains():
    """满载 Worker 围 Core:每人隔 Tick 进入交付,队列最终清空。"""
    from pathfinding import DELTA, HybridPathPlanner
    core = (-1397, 1657)
    p = HybridPathPlanner()
    ws = {
        "w1": {"id": "w1", "pos": (-1396, 1657), "cargo": 1, "last_pos": None,
               "explore_target": None},
        "w2": {"id": "w2", "pos": (-1398, 1657), "cargo": 1, "last_pos": None,
               "explore_target": None},
        "w3": {"id": "w3", "pos": (-1397, 1658), "cargo": 1, "last_pos": None,
               "explore_target": None},
    }
    deposits = 0
    for tick in range(1, 60):
        p.begin_tick(0)
        occupied = {core} | {w["pos"] for w in ws.values()}
        core_reserved = False
        for wid, w in ws.items():
            if w["cargo"] == 0 and w["pos"] == core and w["explore_target"] is None:
                w["explore_target"] = (-1500, 1750)  # 交付完立刻派走
            action, args = decide_worker(w, core, {}, set(), set(occupied), set(),
                                         planner=p, core_cell_reserved=core_reserved)
            if action == "deposit":
                w["cargo"] = 0
                deposits += 1
            elif action == "move":
                dx, dy = DELTA[args[0]]
                dest = (w["pos"][0] + dx, w["pos"][1] + dy)
                if dest == core:
                    core_reserved = True
                w["last_pos"] = w["pos"]
                w["pos"] = dest
                if w["pos"] != core:
                    w["explore_target"] = w.get("explore_target")
    assert deposits == 3, f"队列应在 60 Tick 内清空,实际交付 {deposits}"


def test_vanguard_leaves_core_cell():
    """Vanguard 出生在 Core 格上时立刻让出交付口,而不是蹲在上面堵门。"""
    from pathfinding import DELTA
    core = (0, 0)
    v = {"id": "v1", "pos": core}
    action, args = decide_vanguard(v, core, [], set(), {core})
    assert action == "move", "出生在 Core 格上必须挪走"
    dx, dy = DELTA[args[0]]
    assert (dx, dy) != (0, 0)
    dest = (dx, dy)
    assert dest != core
    # 让出后相邻蹲守:无敌不动
    v2 = {"id": "v1", "pos": (1, 0)}
    action2, _ = decide_vanguard(v2, core, [], set(), {(0, 0), (1, 0)})
    assert action2 == "wait"


def test_assign_resources_pauses_when_core_full():
    """Core 满仓:停止派发采集任务(交付不进去),Worker 转入扫掠待命。"""
    from strategy import WorkerTask, assign_resources
    tasks = {"w1": WorkerTask(state="harvest", target=(2, 2))}
    workers = [{"id": "w1", "pos": (2, 2), "cargo": 0}]
    a = assign_resources(workers, [(2, 2)], set(), tasks, tick=10, core_space=0)
    assert a == {} and "w1" not in tasks, "满仓时应清空采集任务"
    # 有空间时恢复分配
    tasks2 = {}
    a2 = assign_resources(workers, [(2, 2)], set(), tasks2, tick=11, core_space=5)
    assert a2.get("w1") == (2, 2)
    # core_space 未提供(None)时保持旧行为
    tasks3 = {}
    a3 = assign_resources(workers, [(2, 2)], set(), tasks3, tick=12)
    assert a3.get("w1") == (2, 2)


# ---------- 建军出征与自动治疗 ----------

def test_pick_raid_target_nearest():
    """出征目标 = 记忆中最近的敌方 Core。"""
    from strategy import pick_raid_target
    enemy_cores = {(-50, 60): {"owner": "a"}, (-1383, 1644): {"owner": "b"},
                   (-1400, 1660): {"owner": "c"}}
    target = pick_raid_target(enemy_cores, (-1397, 1657))
    assert target == (-1400, 1660), "应选最近的目标"


def test_vanguard_raid_march():
    """被指派出征:Vanguard 向目标行军(即使方向背离 Core),与库存无关。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    v = {"id": "v1", "pos": (10, 0), "hp": 4, "hp_max": 4}
    action, args = decide_vanguard(v, (0, 0), [], set(), {(10, 0)},
                                   planner=p, raid_target=(40, 0))
    assert action == "move" and args[0] == "RIGHT", "应向出征目标行军"
    # 已到相邻位:无敌可见时原地待命(下一 Tick 目标入视野即 SWEEP)
    v2 = {"id": "v1", "pos": (39, 0), "hp": 4, "hp_max": 4}
    action2, _ = decide_vanguard(v2, (0, 0), [], set(), {(39, 0)},
                                 planner=p, raid_target=(40, 0))
    assert action2 == "wait"


def test_vanguard_without_raid_still_guards():
    """未被指派出征时保持守家行为。"""
    v = {"id": "v1", "pos": (4, 0)}
    action, args = decide_vanguard(v, (0, 0), [], set(), {(4, 0)})
    assert action == "move" and args[0] == "LEFT"


def test_ranger_shoots_enemy_core_in_range():
    """射程内(直线 ≤3 格)的敌方 Core 是首选射击目标。"""
    r = {"id": "r1", "pos": (0, 0), "hp": 2, "hp_max": 2}
    enemies = [{"pos": (3, 0), "unit_type": None, "hp": 5}]
    action, args = decide_ranger(r, (0, 0), enemies, set(), {(0, 0)})
    assert action == "shoot" and args == (3, 0)


def test_ranger_prefers_core_and_low_hp():
    """同时有 Core 和 Unit 可射时优先 Core;同为 Unit 时优先低 HP。"""
    r = {"id": "r1", "pos": (0, 0), "hp": 2, "hp_max": 2}
    enemies = [
        {"pos": (0, 2), "unit_type": "WORKER", "hp": 2},
        {"pos": (0, 3), "unit_type": None, "hp": 5},
    ]
    action, args = decide_ranger(r, (0, 0), enemies, set(), {(0, 0)})
    assert action == "shoot" and args == (0, 3), "围攻优先打 Core"
    enemies2 = [{"pos": (2, 0), "unit_type": "WORKER", "hp": 2},
                {"pos": (3, 0), "unit_type": "WORKER", "hp": 1}]
    _, args2 = decide_ranger(r, (0, 0), enemies2, set(), {(0, 0)})
    assert args2 == (3, 0), "Unit 优先打低 HP"


def test_ranger_shoot_blocked_by_obstacle():
    """射线被障碍挡住:不射击,转入行军/等待。"""
    r = {"id": "r1", "pos": (0, 0), "hp": 2, "hp_max": 2}
    enemies = [{"pos": (3, 0), "unit_type": None, "hp": 5}]
    action, args = decide_ranger(r, (0, 0), enemies, {(1, 0)}, {(0, 0)},
                                 raid_target=(6, 0), planner=None)
    assert action != "shoot", "射线被挡不得射击"


def test_worker_heals_at_core_when_damaged():
    """带伤 Worker 回到 Core 格自动治疗(HEAL 完整动作,一次回满)。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    w = {"id": "w1", "pos": (0, 0), "cargo": 0, "hp": 1, "hp_max": 2}
    action, _ = decide_worker(w, (0, 0), {}, set(), {(0, 0)}, set(), planner=p)
    assert action == "heal"
    # 满血不治疗
    w2 = {"id": "w1", "pos": (0, 0), "cargo": 0, "hp": 2, "hp_max": 2}
    action2, _ = decide_worker(w2, (0, 0), {}, set(), {(0, 0)}, set(), planner=p)
    assert action2 != "heal"


def test_worker_deposits_before_heal():
    """满载优先交付,治疗让位。"""
    from pathfinding import HybridPathPlanner
    p = HybridPathPlanner()
    p.begin_tick(0)
    w = {"id": "w1", "pos": (0, 0), "cargo": 1, "hp": 1, "hp_max": 2}
    action, _ = decide_worker(w, (0, 0), {}, set(), {(0, 0)}, set(), planner=p)
    assert action == "deposit"


def test_vanguard_heals_at_core_when_damaged():
    """带伤 Vanguard 在 Core 格上治疗(而不是让位)。"""
    v = {"id": "v1", "pos": (0, 0), "hp": 2, "hp_max": 4}
    action, _ = decide_vanguard(v, (0, 0), [], set(), {(0, 0)})
    assert action == "heal"


def test_decide_core_war_gating():
    """war_reserve 门槛:military_ok=False 不补军备,True 恢复补员。"""
    ag = _hoard_agent({"war_mode": True, "war_reserve": 100,
                       "max_workers": 19, "max_vanguards": 2})
    core = _FakeCore()
    ag._decide_core(_fake_turn(50), core, n_workers=19, n_vanguards=0, military_ok=False)
    assert core.spawned == [], "未达保留线不建军"
    ag._decide_core(_fake_turn(100), core, n_workers=19, n_vanguards=0, military_ok=True)
    assert core.spawned == [UnitType.VANGUARD], "达标后补员"


def test_war_mode_lifts_hoard_pause():
    """战争模式下攒钱暂停让位:补员照常,边采集边扫荡。"""
    ag = _hoard_agent({"hoard_mode": True, "hoard_until_resources": 200,
                       "hoard_min_population": 19, "max_workers": 19,
                       "war_mode": True, "war_reserve": 50,
                       "max_vanguards": 2})
    core = _FakeCore()
    ag._decide_core(_fake_turn(60), core, n_workers=19, n_vanguards=0, military_ok=True)
    assert core.spawned == [UnitType.VANGUARD], "战争模式优先于攒钱暂停"


def test_split_home_guard():
    """留家守卫分配:兵数超过留家数的部分出征,确定性不摇摆。"""
    from strategy import split_home_guard
    ids = ["aaa", "bbb", "ccc", "ddd"]
    assert split_home_guard(ids, 1) == {"aaa"}
    assert split_home_guard(ids, 2) == {"aaa", "bbb"}
    assert split_home_guard(ids, 0) == set()
    assert split_home_guard(ids, 10) == set(ids)


def test_military_replenish_gate():
    """补员门槛:库存低于 war_reserve(military_ok=False)时暂缓补员,
    空间/资源恢复后自动补;Worker 生产不受战争门槛影响。"""
    ag = _hoard_agent({"war_mode": True, "war_reserve": 80,
                       "max_workers": 19, "max_vanguards": 2})
    core = _FakeCore()
    # 库存 60 < 80:military_ok=False → 不补 Vanguard;Worker 满编也不会生产
    ag._decide_core(_fake_turn(60), core, n_workers=19, n_vanguards=0, military_ok=False)
    assert core.spawned == []
    # 库存恢复 → 补员
    ag._decide_core(_fake_turn(85), core, n_workers=19, n_vanguards=0, military_ok=True)
    assert core.spawned == [UnitType.VANGUARD]


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
