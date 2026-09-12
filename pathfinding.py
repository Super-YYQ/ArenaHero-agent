# -*- coding: utf-8 -*-
"""路线规划基础层：网格类型、明确结果状态与有预算的确定性 A*。

本模块不依赖 strategy.py / agent.py：只接收起点、目标、地图快照与预算，
返回带明确状态的 PathResult。strategy.py 反向导入这里的方向工具，
保证"策略纯函数"与"规划纯函数"都能离线单测。

PathResult.status 的固定语义（禁止用 None 同时表达到达/不可达/没算完）：
- FOUND            已找到目标路线；
- FRONTIER         预算内没到目标，但返回更靠近目标的可达前沿路线；
- BLOCKED          当前已知地图中确认没有可行路径；
- BUDGET_EXHAUSTED 搜索被预算截断，不能当成不可达；
- AT_TARGET        起点已是目标。
"""
from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

# 四个正方向（服务端只接受这四种）
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
DELTA = {
    "UP": (0, -1),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
    "RIGHT": (1, 0),
}

# 区块边长（与规则文档一致），chunk_of 复用
CHUNK_SIZE = 32

FOUND = "FOUND"
FRONTIER = "FRONTIER"
BLOCKED = "BLOCKED"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
AT_TARGET = "AT_TARGET"

Infinity = 1 << 60


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def neighbors(cell: tuple[int, int]):
    x, y = cell
    for d in DIRECTIONS:
        dx, dy = DELTA[d]
        yield d, (x + dx, y + dy)


def step_direction(
    frm: tuple[int, int],
    to: tuple[int, int],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    forbidden: set[tuple[int, int]] | None = None,
) -> str | None:
    """贪心单步：朝 to 走一步，返回方向名；无法走返回 None。

    每格最多 2 个占位实体、Core 占 1，因此 occupied 里的格子不能进。
    forbidden 用于禁止回头（刚走过的格子），打断 2 格振荡；
    只有所有方向都被挡时才允许 fallback 进 forbidden。
    """
    if frm == to:
        return None
    x, y = frm
    tx, ty = to
    dx, dy = tx - x, ty - y
    forbidden = forbidden or set()

    # 主方向（距离更长的轴）优先，被挡时垂直绕行，最后才回头
    main_axis = []
    if abs(dx) >= abs(dy) and dx != 0:
        main_axis.append("RIGHT" if dx > 0 else "LEFT")
        main_axis.append("DOWN" if dy >= 0 else "UP")
        main_axis.append("UP" if dy >= 0 else "DOWN")
        main_axis.append("LEFT" if dx > 0 else "RIGHT")
    else:
        main_axis.append("DOWN" if dy > 0 else "UP")
        main_axis.append("RIGHT" if dx >= 0 else "LEFT")
        main_axis.append("LEFT" if dx >= 0 else "RIGHT")
        main_axis.append("UP" if dy > 0 else "DOWN")

    fallback = None
    for d in main_axis:
        nx, ny = x + DELTA[d][0], y + DELTA[d][1]
        if (nx, ny) in obstacles or (nx, ny) in occupied:
            continue
        if (nx, ny) in forbidden:
            fallback = fallback or d
            continue
        return d
    return fallback


def steps_to_cells(start: tuple[int, int], steps: tuple[str, ...]) -> list[tuple[int, int]]:
    """把方向序列还原为格子序列；用于验证每步方向与相邻格一致。"""
    cells = [start]
    x, y = start
    for d in steps:
        dx, dy = DELTA[d]
        x, y = x + dx, y + dy
        cells.append((x, y))
    return cells


def _reconstruct_steps(came_from: dict, start: tuple[int, int], end: tuple[int, int]) -> tuple[str, ...]:
    cells = [end]
    while cells[-1] != start:
        cells.append(came_from[cells[-1]])
    cells.reverse()
    steps = []
    for a, b in zip(cells, cells[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        steps.append(next(d for d, (ddx, ddy) in DELTA.items() if (ddx, ddy) == (dx, dy)))
    return tuple(steps)


@dataclass(frozen=True)
class PathResult:
    status: str
    steps: tuple[str, ...]
    endpoint: tuple[int, int] | None
    expanded: int
    cost: int
    map_version: int
    reason: str = ""


@dataclass(frozen=True)
class PathRequest:
    """一次路线请求。

    obstacles 是永久地形快照，参与缓存与不可达判断；
    occupied / forbidden / threat 只影响本次请求，不写入静态地图。
    未知格不显式展开成集合：用 is_known(cell) 回调判断，
    返回 False 的格子按可通行计算并加 unknown_penalty（到达视野后校正）。
    """
    start: tuple[int, int]
    goal: tuple[int, int]
    obstacles: frozenset[tuple[int, int]]
    occupied: frozenset[tuple[int, int]] = frozenset()
    forbidden: frozenset[tuple[int, int]] = frozenset()
    allow_goal_occupied: bool = False
    max_expansions: int = 800
    unknown_penalty: int = 1
    threat_penalty: int = 0
    threat: frozenset[tuple[int, int]] = frozenset()
    is_known: Optional[Callable[[tuple[int, int]], bool]] = None
    map_version: int = 0


def astar_search(request: PathRequest) -> PathResult:
    """有预算的确定性 A*。

    - 预算耗尽返回 FRONTIER（更靠近目标的最佳已展开格）或 BUDGET_EXHAUSTED，
      绝不把"没搜完"报告成 BLOCKED；
    - BLOCKED 只在两种确认下返回：起点可达空间穷尽，或从目标侧洪泛证明
      目标被已知障碍封死且与起点不连通（无界网格上单靠正向搜索穷不尽）；
    - forbidden 是软约束：被它挡死/无进展时允许一次带 forbidden 的重搜；
    - 目标格被动态占用且不允许进入时返回 BUDGET_EXHAUSTED（暂时状态）。
    """
    start, goal = request.start, request.goal
    if start == goal:
        return PathResult(AT_TARGET, (), start, 0, 0, request.map_version)
    if goal in request.obstacles:
        return PathResult(BLOCKED, (), None, 0, 0, request.map_version, reason="goal_is_known_obstacle")

    result = _astar_once(request, respect_forbidden=True)
    if result.status in (BLOCKED, BUDGET_EXHAUSTED) and request.forbidden:
        retry = _astar_once(request, respect_forbidden=False)
        if retry.status in (FOUND, FRONTIER):
            return PathResult(
                retry.status, retry.steps, retry.endpoint,
                result.expanded + retry.expanded, retry.cost,
                request.map_version, reason="forbidden_relaxed",
            )
        expanded = result.expanded + retry.expanded
        if retry.status == BLOCKED:
            result = PathResult(BLOCKED, (), None, expanded, 0,
                                request.map_version, reason="no_path_in_known_map")
        else:
            result = PathResult(result.status, result.steps, result.endpoint, expanded,
                                result.cost, request.map_version, result.reason)

    # 无界网格：正向搜索穷不尽时，从目标侧做有限洪泛确认"被已知障碍封死"
    if result.status in (BUDGET_EXHAUSTED, FRONTIER):
        sealed, seal_expanded = _goal_sealed_away_from(request, start)
        if sealed:
            return PathResult(BLOCKED, (), None, result.expanded + seal_expanded, 0,
                              request.map_version, reason="goal_sealed_by_known_obstacles")
    return result


def _goal_sealed_away_from(request: PathRequest, start: tuple[int, int]) -> tuple[bool, int]:
    """从目标洪泛（只避开已知障碍，忽略动态占用）。

    返回 (目标连通域有限且不含起点, 洪泛展开数)。洪泛触顶时无法证明，
    返回 False（保守：绝不因预算把可达误判为不可达）。
    """
    goal = request.goal
    cap = request.max_expansions
    seen = {goal}
    queue = deque([goal])
    expansions = 0
    while queue:
        cell = queue.popleft()
        expansions += 1
        if expansions > cap:
            return False, expansions
        for _, nxt in neighbors(cell):
            if nxt in seen or nxt in request.obstacles:
                continue
            if nxt == start:
                return False, expansions
            seen.add(nxt)
            queue.append(nxt)
    return True, expansions


def _astar_once(request: PathRequest, respect_forbidden: bool) -> PathResult:
    start, goal = request.start, request.goal
    occupied = request.occupied - {start}
    if request.allow_goal_occupied:
        occupied = occupied - {goal}
    elif goal in occupied:
        return PathResult(BUDGET_EXHAUSTED, (), None, 0, 0, request.map_version, reason="goal_occupied")
    forbidden = request.forbidden if respect_forbidden else frozenset()

    is_known = request.is_known
    unknown_penalty = request.unknown_penalty if is_known is not None else 0
    threat_cells = request.threat
    threat_penalty = request.threat_penalty

    h0 = manhattan(start, goal)
    open_heap = [(h0, 0, start[1], start[0])]  # (f, h, y, x) 确定性次序
    g_score = {start: 0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    closed: set[tuple[int, int]] = set()
    expansions = 0
    best = None  # (h, y, x, cell, g)：预算耗尽时的最佳前沿

    while open_heap:
        if expansions >= request.max_expansions:
            if best is not None and best[0] < h0:
                steps = _reconstruct_steps(came_from, start, best[3])
                return PathResult(FRONTIER, steps, best[3], expansions, best[4],
                                  request.map_version, reason="budget_frontier")
            return PathResult(BUDGET_EXHAUSTED, (), None, expansions, 0,
                              request.map_version, reason="expansion_budget")
        f, h, y, x = heapq.heappop(open_heap)
        cell = (x, y)
        if cell in closed:
            continue
        closed.add(cell)
        g = g_score[cell]
        expansions += 1
        if cell != start and (best is None or (h, y, x) < best[:3]):
            best = (h, y, x, cell, g)
        if cell == goal:
            return PathResult(FOUND, _reconstruct_steps(came_from, start, goal),
                              goal, expansions, g, request.map_version)
        for d, nxt in neighbors(cell):
            if nxt in closed or nxt in request.obstacles or nxt in occupied or nxt in forbidden:
                continue
            step_cost = 1
            if unknown_penalty and not is_known(nxt):
                step_cost += unknown_penalty
            if threat_penalty and nxt in threat_cells:
                step_cost += threat_penalty
            ng = g + step_cost
            if ng < g_score.get(nxt, Infinity):
                g_score[nxt] = ng
                came_from[nxt] = cell
                nh = manhattan(nxt, goal)
                heapq.heappush(open_heap, (ng + nh, nh, nxt[1], nxt[0]))

    # 可达空间穷尽仍未到目标：当前已知地图确认不可达
    return PathResult(BLOCKED, (), None, expansions, 0,
                      request.map_version, reason="no_path_in_known_map")
