# -*- coding: utf-8 -*-
"""离线固定地图基准：基线贪心 vs 混合规划器。

运行：python benchmark_pathfinding.py
- 到达率 / 平均无进展 Tick：7 张可达固定地图 + 封闭地图；
- 规划失败误判：非封闭地图上出现 BLOCKED 的次数（必须为 0）；
- 简单直线开销：空旷地图上单步决策耗时（相对量级，非绝对承诺）；
- 多 Worker 吞吐：1/3/10 个 Worker、近距与跨区块目标、5000 节点总预算
  下单 Tick 决策耗时；
- 缓存命中：同路线二走不重新展开 A*。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from map_fixtures import FIXTURES, bfs_oracle, get_fixture
from pathfinding import BLOCKED, DELTA, HybridPathPlanner, manhattan, step_direction

MAX_TICKS = 400
REPS = 20_000


def run(use_planner: bool, fixture: str, astar_max: int = 800):
    """单 Worker 到目标仿真。返回 (arrived, ticks, no_progress, blocked_seen)。"""
    obstacles, start, goal = get_fixture(fixture)
    obstacles = set(obstacles)
    planner = HybridPathPlanner(astar_max_expansions=astar_max) if use_planner else None
    pos, last_pos = start, None
    no_progress = 0
    best_dist = manhattan(start, goal)
    blocked_seen = 0
    for tick in range(1, MAX_TICKS + 1):
        if planner:
            planner.begin_tick(0)
            worker = {"id": "w1", "pos": pos, "cargo": 0, "last_pos": last_pos}
            action, args = _planner_decide(worker, goal, obstacles, planner)
            if action == "move" and planner.last_results["w1"].status == BLOCKED:
                blocked_seen += 1
        else:
            action, args = _greedy_decide(pos, last_pos, goal, obstacles)
        if pos == goal:
            return True, tick, no_progress, blocked_seen
        if action == "move":
            dx, dy = DELTA[args[0]]
            last_pos, pos = pos, (pos[0] + dx, pos[1] + dy)
        dist = manhattan(pos, goal)
        if dist >= best_dist:
            no_progress += 1
        else:
            best_dist = dist
    return pos == goal, MAX_TICKS, no_progress, blocked_seen


def _greedy_decide(pos, last_pos, goal, obstacles):
    """基线：旧版 decide_worker 的移动语义（贪心 + last_pos 禁止回头）。"""
    if pos == goal:
        return "wait", ()
    forbidden = {last_pos} if last_pos else set()
    d = step_direction(pos, goal, obstacles, {pos}, forbidden=forbidden)
    if d:
        return "move", (d,)
    return "wait", ()


def _planner_decide(worker, goal, obstacles, planner):
    """规划器路径：与 decide_worker 的去资源分支一致。"""
    pos = worker["pos"]
    last_pos = worker.get("last_pos")
    forbidden = {last_pos} if last_pos else set()
    result = planner.next_step(
        worker["id"], pos, goal, obstacles=obstacles, occupied={pos},
        forbidden=forbidden,
    )
    if result.steps:
        return "move", (result.steps[0],)
    return "wait", ()


def bench_decision_time(use_planner: bool) -> float:
    """空旷地图 12 格内单步决策平均耗时（秒/次）。"""
    obstacles, start, goal = set(), (0, 0), (12, 0)
    if use_planner:
        planner = HybridPathPlanner()
        planner.begin_tick(0)
        t0 = time.perf_counter()
        for _ in range(REPS):
            planner.next_step("w", start, goal, obstacles=obstacles, occupied={start})
        return (time.perf_counter() - t0) / REPS
    t0 = time.perf_counter()
    for _ in range(REPS):
        step_direction(start, goal, obstacles, set())
    return (time.perf_counter() - t0) / REPS


def bench_multiworkers(n: int, far: bool) -> float:
    """n 个 Worker 同时决策的单 Tick 耗时（秒/Tick），总预算 5000。"""
    obstacles = {(10, y) for y in range(-2, 3)} if far else set()
    goals = [(40 + 3 * i, 5 + i) if far else (5 + 3 * i, 3 + i) for i in range(n)]
    planner = HybridPathPlanner(total_path_budget=5000)
    worst = 0.0
    for round_no in range(30):
        planner.begin_tick(0)
        t0 = time.perf_counter()
        for i in range(n):
            pos = goals[i] if round_no else (0, i)
            # 起点随轮次沿路线推进，模拟行进中的 Worker
            start = (0, i) if round_no == 0 else pos
            planner.next_step(f"w{i}", start, goals[i], obstacles=obstacles,
                              occupied={start})
        worst = max(worst, time.perf_counter() - t0)
    return worst


def bench_cache(planner: HybridPathPlanner) -> tuple[int, int]:
    """同一路线走两遍：统计第二次的 A* 调用与缓存命中。"""
    obstacles = {(10, y) for y in range(-2, 3)}
    goal = (40, 5)
    pos = (0, 0)
    for _ in range(200):
        planner.begin_tick(0)
        r = planner.next_step("w", pos, goal, obstacles=obstacles, occupied={pos})
        if r.status == "AT_TARGET":
            break
        if r.steps:
            dx, dy = DELTA[r.steps[0]]
            pos = (pos[0] + dx, pos[1] + dy)
    astar_after_first_pass = planner.stats.astar_calls
    pos = (0, 0)
    for _ in range(200):
        planner.begin_tick(0)
        r = planner.next_step("w", pos, goal, obstacles=obstacles, occupied={pos})
        if r.status == "AT_TARGET":
            break
        if r.steps:
            dx, dy = DELTA[r.steps[0]]
            pos = (pos[0] + dx, pos[1] + dy)
    second_pass_astar = planner.stats.astar_calls - astar_after_first_pass
    return second_pass_astar, planner.stats.cache_hits


def main() -> None:
    fixtures = list(FIXTURES)
    print("=" * 78)
    print(f"{'地图':<18}{'BFS最短':>8}{'贪心到达':>9}{'规划器到达':>10}"
          f"{'贪心Tick':>9}{'规划Tick':>9}{'贪心无进展':>10}{'规划无进展':>10}")
    print("-" * 78)
    greedy_np_by = {}
    p_np_by = {}
    greedy_arrive = planner_arrive = 0
    misjudge = 0
    for name in fixtures:
        obstacles, start, goal = get_fixture(name)
        optimal = bfs_oracle(obstacles, start, goal)
        g_arr, g_ticks, g_np, _ = run(False, name)
        p_arr, p_ticks, p_np, p_blocked = run(True, name)
        misjudge += p_blocked if optimal is not None else 0
        if optimal is not None:
            greedy_arrive += g_arr
            planner_arrive += p_arr
        greedy_np_by[name] = g_np
        p_np_by[name] = p_np
        print(f"{name:<18}{str(optimal):>8}{('是' if g_arr else '否'):>9}"
              f"{('是' if p_arr else '否'):>10}{g_ticks:>9}{p_ticks:>9}{g_np:>10}{p_np:>10}")
    print("-" * 78)
    reachable = [
        name for name in fixtures
        if bfs_oracle(*get_fixture(name)) is not None
    ]
    g_np_sum = 0
    p_np_sum = 0
    for name in reachable:
        g_np_sum += greedy_np_by[name]
        p_np_sum += p_np_by[name]
    g_np_r = g_np_sum / len(reachable)
    p_np_r = p_np_sum / len(reachable)
    print(f"可达地图到达率: 贪心 {greedy_arrive}/{len(reachable)} "
          f"({100 * greedy_arrive / len(reachable):.0f}%), "
          f"规划器 {planner_arrive}/{len(reachable)} "
          f"({100 * planner_arrive / len(reachable):.0f}%, 要求 >= 90%)")
    print(f"可达地图平均无进展 Tick: 贪心 {g_np_r:.1f}, 规划器 {p_np_r:.1f} "
          f"(相对降低 {100 * (1 - p_np_r / max(1e-9, g_np_r)):.0f}%)")
    print(f"规划失败误判(可达地图报 BLOCKED): {misjudge} (要求 0)")
    print()

    g_fast = bench_decision_time(False)
    p_fast = bench_decision_time(True)
    print(f"简单直线单步决策: 贪心 {g_fast * 1e6:.1f} µs, "
          f"规划器快速层 {p_fast * 1e6:.1f} µs (阈值 1000 µs/Worker)")
    for n in (1, 3, 10):
        t_near = bench_multiworkers(n, far=False)
        t_far = bench_multiworkers(n, far=True)
        print(f"{n:>2} Worker 单 Tick 决策最坏耗时: 近距 {t_near * 1000:.2f} ms, "
              f"跨区块 {t_far * 1000:.2f} ms")
    p = HybridPathPlanner()
    p.begin_tick(0)
    second_astar, hits = bench_cache(p)
    print(f"同路线二走: A* 调用 {second_astar} 次 (要求 0), 缓存命中 {hits} 次")
    print("=" * 78)


if __name__ == "__main__":
    main()
