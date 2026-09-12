# -*- coding: utf-8 -*-
"""路线规划基础层测试：不联网，直接构造请求验证 A* 与结果状态。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from map_fixtures import bfs_oracle, get_fixture
from pathfinding import (
    AT_TARGET,
    BLOCKED,
    BUDGET_EXHAUSTED,
    DELTA,
    FOUND,
    FRONTIER,
    PathRequest,
    astar_search,
    bfs_frontier,
    manhattan,
    step_direction,
    steps_to_cells,
)


def _request(obstacles, start, goal, **kw) -> PathRequest:
    return PathRequest(start=start, goal=goal, obstacles=frozenset(obstacles), **kw)


def _assert_steps_legal(result, start, goal):
    """FOUND/FRONTIER 的每一步方向必须与相邻格一致，且终点正确。"""
    cells = steps_to_cells(start, result.steps)
    for a, b in zip(cells, cells[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, f"步进不合法: {a}->{b}"
    assert cells[-1] == result.endpoint, f"终点不一致: {cells[-1]} != {result.endpoint}"


def test_astar_empty_map_shortest():
    obstacles, start, goal = get_fixture("empty")
    r = astar_search(_request(obstacles, start, goal))
    assert r.status == FOUND
    assert r.cost == manhattan(start, goal) == bfs_oracle(obstacles, start, goal)
    _assert_steps_legal(r, start, goal)


def test_astar_detours_single_wall():
    obstacles, start, goal = get_fixture("straight_wall")
    r = astar_search(_request(obstacles, start, goal))
    assert r.status == FOUND
    assert r.cost == bfs_oracle(obstacles, start, goal)
    _assert_steps_legal(r, start, goal)


def test_astar_l_shape_and_bottleneck():
    for name in ("l_shape", "bottleneck"):
        obstacles, start, goal = get_fixture(name)
        r = astar_search(_request(obstacles, start, goal))
        assert r.status == FOUND, name
        assert r.cost == bfs_oracle(obstacles, start, goal), name
        _assert_steps_legal(r, start, goal)


def test_astar_concave_pocket():
    """凹形口袋：必须从右侧入口进入，不能局部卡死。"""
    obstacles, start, goal = get_fixture("concave")
    r = astar_search(_request(obstacles, start, goal))
    assert r.status == FOUND
    assert r.cost == bfs_oracle(obstacles, start, goal)
    _assert_steps_legal(r, start, goal)


def test_astar_negative_coordinates():
    obstacles, start, goal = get_fixture("negative_quadrant")
    r = astar_search(_request(obstacles, start, goal))
    assert r.status == FOUND
    assert r.cost == bfs_oracle(obstacles, start, goal)
    _assert_steps_legal(r, start, goal)
    assert all(c[0] < 0 for c in steps_to_cells(start, r.steps))


def test_astar_enclosed_returns_blocked():
    """完全封闭目标：从目标侧洪泛确认封死后返回 BLOCKED。"""
    obstacles, start, goal = get_fixture("enclosed")
    r = astar_search(_request(obstacles, start, goal))
    assert r.status == BLOCKED
    assert r.reason == "goal_sealed_by_known_obstacles"
    assert r.expanded > 0


def test_astar_start_enclosed_returns_blocked():
    """起点被封死在有限区域且目标在外：正向搜索穷尽后确认 BLOCKED。"""
    box = ({(x, -2) for x in range(-2, 3)} | {(x, 2) for x in range(-2, 3)}
           | {(-2, y) for y in range(-2, 3)} | {(2, y) for y in range(-2, 3)})
    r = astar_search(_request(box, (0, 0), (0, 8)))
    assert r.status == BLOCKED
    assert r.reason == "no_path_in_known_map"


def test_astar_budget_exhausted_never_blocked():
    """预算截断必须返回 BUDGET_EXHAUSTED/FRONTIER，绝不误报 BLOCKED。"""
    obstacles, start, goal = get_fixture("cross_chunk")
    r = astar_search(_request(obstacles, start, goal, max_expansions=10))
    assert r.status in (BUDGET_EXHAUSTED, FRONTIER), f"got {r.status}: {r.reason}"
    if r.status == FRONTIER:
        _assert_steps_legal(r, start, goal)
        assert manhattan(r.endpoint, goal) < manhattan(start, goal), "前沿应更接近目标"
    r2 = astar_search(_request(obstacles, start, goal, max_expansions=10))
    assert r2.status == r.status and r2.endpoint == r.endpoint, "相同输入必须确定性一致"


def test_astar_start_equals_goal():
    obstacles, start, _ = get_fixture("empty")
    r = astar_search(_request(obstacles, start, start))
    assert r.status == AT_TARGET
    assert r.steps == () and r.endpoint == start


def test_astar_goal_occupied_transient():
    """目标被动态占用且不允许进入：暂时状态，不能当不可达。"""
    obstacles, start, goal = get_fixture("empty")
    r = astar_search(_request(obstacles, start, goal, occupied=frozenset({goal})))
    assert r.status == BUDGET_EXHAUSTED
    assert r.reason == "goal_occupied"
    # 允许进入（Core 例外）时正常寻路
    r2 = astar_search(_request(obstacles, start, goal, occupied=frozenset({goal}),
                               allow_goal_occupied=True))
    assert r2.status == FOUND
    assert r2.endpoint == goal


def test_astar_core_goal_exception():
    """回 Core 场景：Core 格在 occupied 里也允许走进入。"""
    core = (0, 0)
    r = astar_search(_request(set(), (3, 0), core,
                              occupied=frozenset({core, (2, 0), (1, 1)}),
                              allow_goal_occupied=True))
    assert r.status == FOUND
    assert r.endpoint == core


def test_astar_dynamic_occupied_forces_detour():
    """动态占用挡住直路时应绕行，且只影响当前请求。"""
    obstacles, start, goal = get_fixture("empty")
    r = astar_search(_request(obstacles, start, goal,
                              occupied=frozenset({(1, 0), (1, 1)})))
    assert r.status == FOUND
    _assert_steps_legal(r, start, goal)
    assert r.cost == bfs_oracle(obstacles, start, goal)
    cells = steps_to_cells(start, r.steps)
    assert not ({(1, 0), (1, 1)} & set(cells)), "路线不得穿过占用格"


def test_astar_forbidden_is_soft():
    """forbidden 软约束：能避开就避开；唯一门被它挡死时允许一次重搜。"""
    obstacles, start, goal = get_fixture("empty")
    # 直路旁的格子被禁：绕开它
    r = astar_search(_request(obstacles, (0, 0), (3, 0), forbidden=frozenset({(1, 0)})))
    assert r.status == FOUND
    assert (1, 0) not in steps_to_cells((0, 0), r.steps)
    # 封死房间唯一门是 forbidden：正向搜索 BLOCKED 后软约束放宽
    room = ({(x, -2) for x in range(-2, 3)} | {(x, 2) for x in range(-2, 3)}
            | {(-2, y) for y in range(-2, 3)} | {(2, y) for y in range(-2, 3)})
    room.discard((0, 2))  # 北墙留门 (0,2)
    door = {(0, 2)}
    r2 = astar_search(_request(room, (0, 0), (0, 6), forbidden=door))
    assert r2.status == FOUND
    assert r2.reason == "forbidden_relaxed"
    assert (0, 2) in steps_to_cells((0, 0), r2.steps)


def test_astar_unknown_penalty_prefers_known():
    """unknown 惩罚改变路线偏好，但未知格绝不当障碍。"""
    obstacles, start, goal = get_fixture("empty")
    # x>1 全部未知：重惩罚时仍能到达（未知可通行），但路线贴已知区
    known = {c for c in ((x, y) for x in range(-5, 2) for y in range(-5, 6))}
    r_unknown = astar_search(_request(obstacles, start, goal, unknown_penalty=50,
                                      is_known=lambda c: c in known))
    assert r_unknown.status == FOUND, "未知格不是障碍,必须可达"
    _assert_steps_legal(r_unknown, start, goal)
    # 无惩罚时直接走短路线（穿未知）
    r_plain = astar_search(_request(obstacles, start, goal,
                                    is_known=lambda c: c in known))
    assert r_plain.status == FOUND
    assert r_plain.cost < r_unknown.cost, "重未知惩罚应选择代价更高的已知绕行"


def test_astar_threat_penalty():
    """威胁惩罚抬高受威胁格的代价：默认 0 时路线不受影响。"""
    obstacles, start, goal = get_fixture("empty")
    r0 = astar_search(_request(obstacles, start, goal))
    r1 = astar_search(_request(obstacles, start, goal,
                               threat=frozenset({(1, 0)}), threat_penalty=0))
    assert r0.steps == r1.steps, "threat_penalty=0 不应改变路线"
    r2 = astar_search(_request(obstacles, start, goal,
                               threat=frozenset({(1, 0)}), threat_penalty=100))
    assert r2.status == FOUND
    assert (1, 0) not in steps_to_cells(start, r2.steps) or r2.cost > r0.cost


def test_astar_goal_is_known_obstacle():
    obstacles, start, _ = get_fixture("empty")
    obs = set(obstacles) | {(2, 0)}
    r = astar_search(_request(obs, start, (2, 0)))
    assert r.status == BLOCKED
    assert r.reason == "goal_is_known_obstacle"


def test_astar_deterministic_repeat():
    """相同输入重复运行输出完全相同。"""
    for name in ("straight_wall", "concave", "negative_quadrant"):
        obstacles, start, goal = get_fixture(name)
        r1 = astar_search(_request(obstacles, start, goal))
        r2 = astar_search(_request(obstacles, start, goal))
        assert r1 == r2, name


def test_steps_to_cells_roundtrip():
    cells = steps_to_cells((0, 0), ("RIGHT", "RIGHT", "DOWN"))
    assert cells == [(0, 0), (1, 0), (2, 0), (2, 1)]


def test_step_direction_reexport():
    """strategy 的兼容 re-export 指向同一实现。"""
    import strategy
    assert strategy.step_direction is step_direction
    assert strategy.DELTA is DELTA
    assert step_direction((0, 0), (3, 0), set(), set()) == "RIGHT"


# ---------- Phase 4：BFS 前沿 ----------

def test_bfs_frontier_approach_finds_closer_cell():
    """A* 卡死场景（死路口袋）：BFS approach 找到更接近目标的前沿并可回溯。"""
    wall = {(1, y) for y in range(-15, 16)}
    start, goal = (0, 0), (10, 0)
    r = bfs_frontier(_request(wall, start, goal), mode="approach", max_expansions=2000)
    assert r.status == FRONTIER
    assert r.reason == "approach_frontier"
    _assert_steps_legal(r, start, goal)
    assert manhattan(r.endpoint, goal) < manhattan(start, goal), "前沿应更接近目标"


def test_bfs_frontier_truncation_never_blocked():
    """BFS 预算截断返回 BUDGET_EXHAUSTED，绝不误报 BLOCKED。"""
    wall = {(1, y) for y in range(-15, 16)}
    r = bfs_frontier(_request(wall, (0, 0), (10, 0)), mode="approach", max_expansions=5)
    assert r.status == BUDGET_EXHAUSTED
    assert r.status != BLOCKED


def test_bfs_frontier_explore_uses_known_domain():
    """explore 模式：只扩展已知格，前沿为未知边界；截断安全。"""
    wall = {(1, y) for y in range(-15, 16)}
    known = {c for c in ((x, y) for x in range(-5, 0) for y in range(-5, 6))}
    known |= {(0, y) for y in range(-5, 6)}
    r = bfs_frontier(_request(wall, (0, 0), (10, 0), is_known=lambda c: c in known),
                     mode="explore", max_expansions=500)
    assert r.status == FRONTIER
    _assert_steps_legal(r, (0, 0), (10, 0))
    # 终点必须紧邻未知格
    from pathfinding import DELTA
    assert any((r.endpoint[0] + dx, r.endpoint[1] + dy) not in known
               for dx, dy in DELTA.values())


def test_bfs_frontier_deterministic():
    wall = {(1, y) for y in range(-15, 16)}
    r1 = bfs_frontier(_request(wall, (0, 0), (10, 0)), mode="approach", max_expansions=2000)
    r2 = bfs_frontier(_request(wall, (0, 0), (10, 0)), mode="approach", max_expansions=2000)
    assert r1 == r2


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
