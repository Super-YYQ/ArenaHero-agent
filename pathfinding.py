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
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

# 四个正方向（服务端只接受这四种）
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
DELTA = {
    "UP": (0, -1),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
    "RIGHT": (1, 0),
}

# 区块边长（与规则文档一致）
CHUNK_SIZE = 32

# 区块边界四边（side -> 相邻区块方向）
CHUNK_SIDES = ("UP", "DOWN", "LEFT", "RIGHT")
# side -> (dx, dy) 相邻区块坐标增量
CHUNK_SIDE_NEIGHBOR = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def chunk_of(cell: tuple[int, int]) -> tuple[int, int]:
    """格子所属 32×32 区块（向下取整，与规则文档一致，支持负坐标）。"""
    x, y = cell
    return (x // CHUNK_SIZE if x >= 0 else -((-x - 1) // CHUNK_SIZE) - 1,
            y // CHUNK_SIZE if y >= 0 else -((-y - 1) // CHUNK_SIZE) - 1)

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


# ---------- 路线缓存（静态路线与动态占用严格分离） ----------

@dataclass(frozen=True)
class RouteCacheKey:
    """静态缓存键：只含起终点、地图版本与规划模式。

    动态 occupied 绝不进键；planner_mode="replan" 的结果不缓存，
    避免本 Tick 的动态占用污染静态路线。
    """
    start: tuple[int, int]
    goal: tuple[int, int]
    map_version: int
    planner_mode: str


class RouteCache:
    """有容量上限的静态路线 LRU 缓存（OrderedDict 实现）。"""

    def __init__(self, capacity: int = 256) -> None:
        self.capacity = max(1, int(capacity))
        self._data: "OrderedDict[RouteCacheKey, PathResult]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.invalidated = 0
        self.evictions = 0

    def get(self, key: RouteCacheKey) -> PathResult | None:
        result = self._data.get(key)
        if result is None:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return result

    def put(self, key: RouteCacheKey, result: PathResult) -> None:
        self._data[key] = result
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)
            self.evictions += 1

    def clear(self) -> int:
        """清空并返回被作废的条目数。"""
        n = len(self._data)
        if n:
            self.invalidated += n
            self._data.clear()
        return n

    def __len__(self) -> int:
        return len(self._data)


def bfs_frontier(
    request: PathRequest,
    *,
    mode: str = "approach",
    unknown_boundary_penalty: int = 0,
    max_expansions: int | None = None,
) -> PathResult:
    """带完整前驱链的 BFS 前沿搜索（渐进降级层）。

    只负责两类任务：
    - mode="approach"：A* 预算耗尽时，找当前可达且更靠近目标的前沿；
    - mode="explore"：目标被已知障碍阻断时，找能推进探索的未知边界前沿。

    评分 frontier_score = 4*Manhattan(cell, goal) + 1*distance_from_start
                        + unknown_boundary_penalty(未知邻接) + threat_penalty，
    越小越好，tie-break (score, y, x) 确定性一致。
    有界性由展开上限保证；截断或无前沿时返回 BUDGET_EXHAUSTED，
    绝不把"没搜完"报告成 BLOCKED。
    """
    start, goal = request.start, request.goal
    h0 = manhattan(start, goal)
    if mode == "approach" and h0 == 0:
        return PathResult(AT_TARGET, (), start, 0, 0, request.map_version)
    max_exp = request.max_expansions if max_expansions is None else max(1, max_expansions)
    is_known = request.is_known
    known_domain = is_known is not None
    threat_penalty = request.threat_penalty

    def frontier_score(cell: tuple[int, int], dist_from_start: int, adjacent_unknown: bool) -> int:
        score = 4 * manhattan(cell, goal) + dist_from_start
        if adjacent_unknown:
            score += unknown_boundary_penalty
        if threat_penalty and cell in request.threat:
            score += threat_penalty
        return score

    def is_candidate(cell: tuple[int, int], adjacent_unknown: bool) -> bool:
        if mode == "approach":
            return manhattan(cell, goal) < h0
        return adjacent_unknown

    def adjacent_to_unknown(cell: tuple[int, int]) -> bool:
        if not known_domain:
            return False
        for _, nxt in neighbors(cell):
            if nxt not in request.obstacles and not is_known(nxt):
                return True
        return False

    dist = {start: 0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    queue = deque([start])
    expansions = 0
    truncated = False
    best = None  # (score, y, x, cell)

    while queue:
        cell = queue.popleft()
        expansions += 1
        if expansions > max_exp:
            truncated = True
            break
        for _, nxt in neighbors(cell):
            if nxt in dist:
                continue
            if nxt in request.obstacles or nxt in request.occupied or nxt in request.forbidden:
                continue
            if known_domain and not is_known(nxt):
                continue  # 未知格不入队，只作前沿参照
            dist[nxt] = dist[cell] + 1
            came_from[nxt] = cell
            adj_unknown = adjacent_to_unknown(nxt)
            if nxt != start and is_candidate(nxt, adj_unknown):
                score = frontier_score(nxt, dist[nxt], adj_unknown)
                if best is None or (score, nxt[1], nxt[0]) < (best[0], best[1], best[2]):
                    best = (score, nxt[1], nxt[0], nxt)
            queue.append(nxt)

    if best is not None:
        steps = _reconstruct_steps(came_from, start, best[3])
        return PathResult(FRONTIER, steps, best[3], expansions, dist[best[3]],
                          request.map_version, reason=f"{mode}_frontier")
    return PathResult(BUDGET_EXHAUSTED, (), None, expansions, 0, request.map_version,
                      reason="frontier_truncated" if truncated else "no_frontier")


# ---------- 增量区块导航摘要 ----------

@dataclass
class ChunkNavigationSummary:
    """单个区块的轻量导航摘要。

    known_cells 是该区块全部已观察格（含障碍格）；known_obstacles ⊆ known_cells。
    boundary_openings: side -> 该边上的已知可通行格（按扫描顺序）。
    connected_components: 已知可通行格的连通分量（内容变化时才重算）。
    """
    chunk: tuple[int, int]
    known_cells: set = field(default_factory=set)
    known_obstacles: set = field(default_factory=set)
    boundary_openings: dict = field(default_factory=dict)
    connected_components: tuple = ()
    revision: int = 0

    def passable(self, cell: tuple[int, int]) -> bool:
        """已知且非障碍。未知格不算可通行（调用方按"需要探索"处理）。"""
        return cell in self.known_cells and cell not in self.known_obstacles


class ChunkNavigationIndex:
    """区块导航索引：只基于已观察信息，不为未知区域伪造距离场。

    - observe() 合并新视野；只在区块内容实际变化时重算边界开放格与连通分量；
    - is_known() 供 A* 的 unknown 惩罚使用（乐观可通行、到达即校正）；
    - corridor()/portal_cells() 供跨区块走廊规划；
    - to_dict()/from_dict() 负责 memory.json 持久化（连通分量不持久化，加载后重算）。
    """

    def __init__(self) -> None:
        self.chunks: dict[tuple[int, int], ChunkNavigationSummary] = {}

    # ---------- 查询 ----------
    def summary(self, chunk: tuple[int, int]) -> ChunkNavigationSummary | None:
        return self.chunks.get(chunk)

    def is_known(self, cell: tuple[int, int]) -> bool:
        s = self.chunks.get(chunk_of(cell))
        return s is not None and cell in s.known_cells

    def corridor(self, start_chunk: tuple[int, int], goal_chunk: tuple[int, int]) -> list:
        """曼哈顿区块走廊（先 x 后 y，确定性）。未知区块只意味着"需要探索"。"""
        cx, cy = start_chunk
        gx, gy = goal_chunk
        path = [(cx, cy)]
        while cx != gx:
            cx += 1 if gx > cx else -1
            path.append((cx, cy))
        while cy != gy:
            cy += 1 if gy > cy else -1
            path.append((cx, cy))
        return path

    def portal_cells(self, chunk: tuple[int, int], neighbor: tuple[int, int]) -> list:
        """共享边界上的已知可通行门户对 [(exit, entry), ...]。

        exit 属于 chunk，entry 属于 neighbor，两格相邻。
        任一侧区块未知（无摘要）时返回空列表。
        """
        cx, cy = chunk
        nx, ny = neighbor
        here = self.chunks.get(chunk)
        there = self.chunks.get(neighbor)
        if here is None or there is None:
            return []
        pairs = []
        if (nx, ny) == (cx + 1, cy):        # 东
            for y in range(cy * CHUNK_SIZE, (cy + 1) * CHUNK_SIZE):
                exit_cell = ((cx + 1) * CHUNK_SIZE - 1, y)
                entry_cell = ((cx + 1) * CHUNK_SIZE, y)
                if here.passable(exit_cell) and there.passable(entry_cell):
                    pairs.append((exit_cell, entry_cell))
        elif (nx, ny) == (cx - 1, cy):      # 西
            for y in range(cy * CHUNK_SIZE, (cy + 1) * CHUNK_SIZE):
                exit_cell = (cx * CHUNK_SIZE, y)
                entry_cell = (cx * CHUNK_SIZE - 1, y)
                if here.passable(exit_cell) and there.passable(entry_cell):
                    pairs.append((exit_cell, entry_cell))
        elif (nx, ny) == (cx, cy + 1):      # 南
            for x in range(cx * CHUNK_SIZE, (cx + 1) * CHUNK_SIZE):
                exit_cell = (x, (cy + 1) * CHUNK_SIZE - 1)
                entry_cell = (x, (cy + 1) * CHUNK_SIZE)
                if here.passable(exit_cell) and there.passable(entry_cell):
                    pairs.append((exit_cell, entry_cell))
        elif (nx, ny) == (cx, cy - 1):      # 北
            for x in range(cx * CHUNK_SIZE, (cx + 1) * CHUNK_SIZE):
                exit_cell = (x, cy * CHUNK_SIZE)
                entry_cell = (x, cy * CHUNK_SIZE - 1)
                if here.passable(exit_cell) and there.passable(entry_cell):
                    pairs.append((exit_cell, entry_cell))
        return pairs

    # ---------- 更新 ----------
    def observe(self, cells, obstacles) -> int:
        """合并新观察（cells 含障碍格）。返回内容实际变化的区块数。"""
        changed: dict[tuple[int, int], ChunkNavigationSummary] = {}
        for cell in cells:
            cell = tuple(cell)
            s = self._summary_for(cell)
            if cell not in s.known_cells:
                s.known_cells.add(cell)
                changed[s.chunk] = s
        for cell in obstacles:
            cell = tuple(cell)
            s = self._summary_for(cell)
            if cell not in s.known_cells or cell not in s.known_obstacles:
                s.known_cells.add(cell)
                s.known_obstacles.add(cell)
                changed[s.chunk] = s
        for s in changed.values():
            self._recompute(s)
        return len(changed)

    def _summary_for(self, cell: tuple[int, int]) -> ChunkNavigationSummary:
        chunk = chunk_of(cell)
        s = self.chunks.get(chunk)
        if s is None:
            s = ChunkNavigationSummary(chunk=chunk)
            self.chunks[chunk] = s
        return s

    def _recompute(self, s: ChunkNavigationSummary) -> None:
        """内容变化后重算边界开放格与连通分量（区块内有限格）。"""
        cx, cy = s.chunk
        x0, y0 = cx * CHUNK_SIZE, cy * CHUNK_SIZE

        openings = {}
        for side in CHUNK_SIDES:
            dx, dy = CHUNK_SIDE_NEIGHBOR[side]
            edge = []
            for i in range(CHUNK_SIZE):
                if side in ("UP", "DOWN"):
                    cell = (x0 + i, y0 if side == "UP" else y0 + CHUNK_SIZE - 1)
                else:
                    cell = (x0 if side == "LEFT" else x0 + CHUNK_SIZE - 1, y0 + i)
                if s.passable(cell):
                    edge.append(cell)
            openings[side] = tuple(edge)
        s.boundary_openings = openings

        passable_cells = s.known_cells - s.known_obstacles
        seen = set()
        components = []
        for cell in sorted(passable_cells):
            if cell in seen:
                continue
            comp = {cell}
            seen.add(cell)
            queue = deque([cell])
            while queue:
                cur = queue.popleft()
                for _, nxt in neighbors(cur):
                    if nxt in passable_cells and nxt not in seen:
                        seen.add(nxt)
                        comp.add(nxt)
                        queue.append(nxt)
            components.append(frozenset(comp))
        s.connected_components = tuple(components)
        s.revision += 1

    # ---------- 持久化 ----------
    def to_dict(self) -> dict:
        data = {}
        for chunk, s in self.chunks.items():
            data[f"{chunk[0]},{chunk[1]}"] = {
                "revision": s.revision,
                "known_cells": [list(c) for c in sorted(s.known_cells)],
                "known_obstacles": [list(c) for c in sorted(s.known_obstacles)],
                "boundary_openings": {
                    side: [list(c) for c in cells]
                    for side, cells in s.boundary_openings.items()
                },
            }
        return data

    def from_dict(self, data: dict) -> None:
        """宽松加载：坏条目只丢该区块摘要，不影响其他区块。"""
        self.chunks = {}
        if not isinstance(data, dict):
            return
        for key, raw in data.items():
            try:
                xs, ys = str(key).split(",")
                chunk = (int(xs), int(ys))
                if not isinstance(raw, dict):
                    continue
                known_raw = raw.get("known_cells")
                if known_raw is not None and not isinstance(known_raw, list):
                    continue  # 该区块摘要损坏：整体丢弃
                known = _parse_cell_set(known_raw)
                obstacles = _parse_cell_set(raw.get("known_obstacles")) & known
                revision = int(raw.get("revision", 0) or 0)
            except (ValueError, TypeError, IndexError):
                continue
            s = ChunkNavigationSummary(chunk=chunk, known_cells=known,
                                       known_obstacles=obstacles, revision=revision)
            openings_raw = raw.get("boundary_openings")
            if isinstance(openings_raw, dict):
                for side, cells in openings_raw.items():
                    if side in CHUNK_SIDES and isinstance(cells, list):
                        parsed = _parse_cell_set(cells)
                        s.boundary_openings[side] = tuple(sorted(parsed))
            try:
                self.chunks[chunk] = s
                self._recompute(s)  # 连通分量不持久化，加载后重算
                s.revision = revision  # 内容未变，保持持久化的版本号
            except (ValueError, TypeError, IndexError):
                self.chunks.pop(chunk, None)

    def __len__(self) -> int:
        return len(self.chunks)


def _parse_cell_set(raw) -> set:
    """严格解析 [[x, y], ...] 坐标列表；任何坏条目直接丢弃。"""
    cells = set()
    if not isinstance(raw, list):
        return cells
    for c in raw:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                cells.add((int(c[0]), int(c[1])))
            except (ValueError, TypeError):
                continue
    return cells


# ---------- 混合规划器（快速层 + 局部 A*） ----------

@dataclass
class WorkerRoute:
    """单个 Worker 的路线游标。只存在于内存，不持久化、不写入共享缓存。

    start 是规划时的起点；cell_at(next_index) 是游标当前应处的格子，
    用来检测 Worker 是否沿路线行进（被挡/被挤偏时错位，需要局部重规划）。
    """
    target: tuple[int, int]
    steps: tuple[str, ...]
    next_index: int
    map_version: int
    planned_endpoint: tuple[int, int]
    status: str
    start: tuple[int, int] = (0, 0)
    replans: int = 0
    blocked_ticks: int = 0

    def cell_at(self, index: int) -> tuple[int, int]:
        x, y = self.start
        for d in self.steps[:index]:
            dx, dy = DELTA[d]
            x, y = x + dx, y + dy
        return (x, y)


@dataclass
class PlannerStats:
    """规划统计。字段含义见 Agent.route_stats；failures 按 reason 计数。"""
    plans: int = 0
    fast_steps: int = 0
    astar_calls: int = 0
    bfs_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    invalidated: int = 0
    replans: int = 0
    expanded_nodes: int = 0
    frontier_returns: int = 0
    budget_exhausted: int = 0
    blocked: int = 0
    arrivals: int = 0
    cooldowns: int = 0
    failures: dict = field(default_factory=dict)

    def note_failure(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1

    def snapshot(self) -> dict:
        data = {k: v for k, v in vars(self).items() if k != "failures"}
        data["failures"] = dict(self.failures)
        return data


class HybridPathPlanner:
    """快速层 + 局部 A* 的混合规划器。

    - 快速层：近距离且无失败记录时沿用贪心单步（常数时间，行为与旧版一致）；
    - A* 层：其余情况做有预算的确定性搜索，路线游标按 Worker 保存；
    - 每 Tick 总预算 total_path_budget，耗尽后剩余 Worker 只能走快速层或 wait；
    - 动态占用只影响当前请求；目标确认不可达时记录失败目标并交给调用方处理。
    """

    def __init__(
        self,
        *,
        astar_max_expansions: int = 800,
        frontier_max_expansions: int = 1200,
        total_path_budget: int = 5000,
        fast_path_distance: int = 12,
        unknown_penalty: int = 1,
        threat_penalty: int = 0,
        routes: dict | None = None,
        cache: RouteCache | None = None,
        chunk_index: ChunkNavigationIndex | None = None,
        stats: PlannerStats | None = None,
    ) -> None:
        self.astar_max_expansions = max(1, astar_max_expansions)
        self.frontier_max_expansions = max(1, frontier_max_expansions)
        self.total_path_budget = max(1, total_path_budget)
        self.fast_path_distance = fast_path_distance
        self.unknown_penalty = unknown_penalty
        self.threat_penalty = threat_penalty
        self.routes: dict[str, WorkerRoute] = routes if routes is not None else {}
        self.cache = cache if cache is not None else RouteCache()
        self.chunk_index = chunk_index
        self.stats = stats if stats is not None else PlannerStats()
        self.map_version = 0
        self.is_known: Callable[[tuple[int, int]], bool] | None = None
        self.last_results: dict[str, PathResult] = {}
        self._budget_left = 0
        # worker_id -> (goal, 连续快速层受阻次数)；达到阈值后跳过快速层
        self._fast_blocks: dict[str, tuple[tuple[int, int], int]] = {}
        # worker_id -> 确认不可达的目标集合（容量受限，版本变化时清空）
        self._failed_goals: dict[str, set] = {}

    # ---------- Tick 生命周期 ----------
    def begin_tick(self, map_version: int, is_known=None) -> None:
        """每 Tick 调用：刷新地图版本与总预算；版本变化即丢弃全部路线。"""
        version_changed = map_version != self.map_version
        self.map_version = map_version
        self.is_known = is_known
        self._budget_left = self.total_path_budget
        if version_changed:
            self.stats.invalidated += len(self.routes) + self.cache.clear()
            self.routes.clear()
            self._failed_goals.clear()
            self._fast_blocks.clear()

    def stats_snapshot(self) -> dict:
        return self.stats.snapshot()

    # ---------- 单 Worker 决策 ----------
    def next_step(
        self,
        worker_id: str,
        start: tuple[int, int],
        goal: tuple[int, int],
        *,
        obstacles,
        occupied,
        forbidden=frozenset(),
        allow_goal_occupied: bool = False,
        threat=frozenset(),
        goal_kind: str = "harvest",
    ) -> PathResult:
        """决定本 Tick 的一步；返回的 PathResult.steps[0] 即当前动作。

        goal_kind: 'harvest' | 'core' | 'scout'，只影响后续阶段的降级策略。
        """
        self.stats.plans += 1
        if start == goal:
            self.stats.arrivals += 1
            self.routes.pop(worker_id, None)
            result = PathResult(AT_TARGET, (), start, 0, 0, self.map_version)
            self.last_results[worker_id] = result
            return result

        obstacles = frozenset(obstacles)
        occupied = frozenset(occupied) - {start}
        if allow_goal_occupied:
            occupied = occupied - {goal}
        forbidden = frozenset(forbidden)

        # 1) 复用现有路线游标（动态占用每次重新验证，不进静态结构）
        replan = False
        route = self.routes.get(worker_id)
        if route is not None:
            if route.target != goal or route.map_version != self.map_version:
                self.routes.pop(worker_id, None)
            else:
                result = self._follow_route(route, worker_id, start, obstacles, occupied, forbidden)
                if result is not None:
                    self.last_results[worker_id] = result
                    return result
                replan = True

        # 2.5) 跨区块走廊：启用区块索引且起终点异区块时，先规划到下一门户
        if (self.chunk_index is not None
                and manhattan(start, goal) > self.fast_path_distance
                and chunk_of(start) != chunk_of(goal)):
            result = self._cross_chunk_step(worker_id, start, goal, obstacles, occupied,
                                            forbidden, allow_goal_occupied, threat,
                                            replan=replan)
            if result is not None:
                self.last_results[worker_id] = result
                return result

        # 2) 快速层：近距离且无失败/受阻记录（replan 时跳过，直接重规划）
        if not replan and self._fast_allowed(worker_id, start, goal):
            d = step_direction(start, goal, obstacles, occupied, forbidden=forbidden)
            if d is not None:
                nx, ny = start[0] + DELTA[d][0], start[1] + DELTA[d][1]
                if (nx, ny) not in forbidden:
                    self.stats.fast_steps += 1
                    self._fast_blocks.pop(worker_id, None)
                    result = PathResult(FOUND, (d,), (nx, ny), 0, 1, self.map_version,
                                        reason="fast_layer")
                    self.last_results[worker_id] = result
                    return result
            prev = self._fast_blocks.get(worker_id, (goal, 0))
            self._fast_blocks[worker_id] = (goal, prev[1] + 1 if prev[0] == goal else 1)

        # 3) 局部 A*（预算内；replan 结果不写入静态缓存）
        result = self._astar_step(worker_id, start, goal, obstacles, occupied, forbidden,
                                  allow_goal_occupied, threat, replan=replan, goal_kind=goal_kind)
        self.last_results[worker_id] = result
        return result

    # ---------- 内部 ----------
    def _follow_route(self, route: WorkerRoute, worker_id: str, start,
                      obstacles, occupied, forbidden) -> PathResult | None:
        """路线游标对齐且首步可执行时发一步；错位/受阻返回 None 走重规划。"""
        expected = route.cell_at(route.next_index)
        if start != expected or route.next_index >= len(route.steps):
            # 游标错位（被挡停/被挤偏）或走完未达：局部重规划
            self.stats.replans += 1
            self.stats.note_failure("cursor_misaligned")
            self.routes.pop(worker_id, None)
            return None
        d = route.steps[route.next_index]
        nx, ny = start[0] + DELTA[d][0], start[1] + DELTA[d][1]
        if (nx, ny) in obstacles or (nx, ny) in occupied or (nx, ny) in forbidden:
            # 首步被动态占用阻挡：局部重规划，不改静态地图、不污染路线
            route.blocked_ticks += 1
            self.stats.replans += 1
            self.stats.note_failure("first_step_blocked")
            self.routes.pop(worker_id, None)
            return None
        route.next_index += 1
        route.blocked_ticks = 0
        if route.next_index >= len(route.steps):
            self.routes.pop(worker_id, None)
        return PathResult(route.status, (d,), route.planned_endpoint, 0,
                          len(route.steps) - route.next_index, self.map_version,
                          reason="route_cursor")

    def _fast_allowed(self, worker_id: str, start, goal) -> bool:
        if goal in self._failed_goals.get(worker_id, ()):
            return False
        blocked = self._fast_blocks.get(worker_id)
        if blocked is not None and blocked[0] == goal and blocked[1] >= 2:
            return False
        return manhattan(start, goal) <= self.fast_path_distance

    def _astar_step(self, worker_id, start, goal, obstacles, occupied, forbidden,
                    allow_goal_occupied, threat, replan: bool = False,
                    goal_kind: str = "harvest") -> PathResult:
        if self._budget_left <= 0:
            self.stats.budget_exhausted += 1
            self.stats.note_failure("tick_budget")
            return PathResult(BUDGET_EXHAUSTED, (), None, 0, 0, self.map_version,
                              reason="tick_budget")

        # 缓存只在"全新规划"时查询；replan 的结果带动态占用，绝不读写静态缓存
        cache_key = None
        if not replan:
            cache_key = RouteCacheKey(start, goal, self.map_version, "astar")
            cached = self.cache.get(cache_key)
            if cached is not None and cached.status == FOUND and cached.steps:
                first = cached.steps[0]
                nx, ny = start[0] + DELTA[first][0], start[1] + DELTA[first][1]
                if (nx, ny) not in obstacles and (nx, ny) not in occupied and (nx, ny) not in forbidden:
                    self.stats.cache_hits += 1
                    self.routes[worker_id] = WorkerRoute(
                        target=goal, steps=cached.steps, next_index=1,
                        map_version=self.map_version, planned_endpoint=cached.endpoint,
                        status=FOUND, start=start,
                    )
                    return PathResult(FOUND, (first,), cached.endpoint, 0,
                                      cached.cost, self.map_version, reason="cache_hit")
                # 首步被当前动态状态挡住：局部重规划，不污染静态缓存
                self.stats.replans += 1
                self.stats.note_failure("cache_first_step_invalid")
                cache_key = None
            else:
                self.stats.cache_misses += 1

        request = PathRequest(
            start=start, goal=goal, obstacles=obstacles, occupied=occupied,
            forbidden=forbidden, allow_goal_occupied=allow_goal_occupied,
            max_expansions=min(self.astar_max_expansions, self._budget_left),
            unknown_penalty=self.unknown_penalty,
            threat_penalty=self.threat_penalty,
            threat=frozenset(threat),
            is_known=self.is_known,
            map_version=self.map_version,
        )
        result = astar_search(request)
        self._budget_left = max(0, self._budget_left - result.expanded)
        self.stats.astar_calls += 1
        self.stats.expanded_nodes += result.expanded
        if result.status == FOUND:
            self._fast_blocks.pop(worker_id, None)
            self._failed_goals.get(worker_id, set()).discard(goal)
            self.routes[worker_id] = WorkerRoute(
                target=goal, steps=result.steps, next_index=1,
                map_version=self.map_version, planned_endpoint=result.endpoint,
                status=result.status, start=start,
            )
            if cache_key is not None:
                self.cache.put(cache_key, result)
            result = PathResult(FOUND, (result.steps[0],), result.endpoint,
                                result.expanded, result.cost, self.map_version,
                                reason="astar")
        elif result.status == FRONTIER and result.steps:
            self.stats.frontier_returns += 1
            self.routes[worker_id] = WorkerRoute(
                target=goal, steps=result.steps, next_index=1,
                map_version=self.map_version, planned_endpoint=result.endpoint,
                status=result.status, start=start,
            )
            result = PathResult(FRONTIER, (result.steps[0],), result.endpoint,
                                result.expanded, result.cost, self.map_version,
                                reason="astar_frontier")
        elif result.status == BLOCKED:
            self.stats.note_failure(result.reason)
            if goal_kind == "scout" and result.reason != "goal_is_known_obstacle":
                # 侦察目标被已知障碍阻断：先找能推进探索的前沿，不清除目标
                fallback = self._frontier_fallback(worker_id, start, goal, obstacles,
                                                   occupied, forbidden, mode="explore")
                if fallback is not None:
                    return fallback
            self.stats.blocked += 1
            self._failed_goals.setdefault(worker_id, set()).add(goal)
            self._cap_failed_goals()
            self.routes.pop(worker_id, None)
        elif result.status == BUDGET_EXHAUSTED:
            self.stats.budget_exhausted += 1
            self.stats.note_failure(result.reason)
            fallback = self._frontier_fallback(worker_id, start, goal, obstacles,
                                               occupied, forbidden, mode="approach")
            if fallback is not None:
                return fallback
        return result

    def _cross_chunk_step(self, worker_id, start, goal, obstacles, occupied, forbidden,
                          allow_goal_occupied, threat, replan: bool = False) -> PathResult | None:
        """跨区块规划：沿曼哈顿区块走廊逐段 A* 到下一边界门户。

        任一步不满足（无门户、预算不足、段内失败）就返回 None，
        退回普通 A*/BFS 前沿链；未知区块不会被标记为不可达。
        """
        index = self.chunk_index
        corridor = index.corridor(chunk_of(start), chunk_of(goal))
        if len(corridor) < 2:
            return None
        portals = index.portal_cells(corridor[0], corridor[1])
        if not portals:
            return None  # 下一区块未知或无已知门户：需要探索，退回普通 A*
        best = min(portals, key=lambda pr: (manhattan(start, pr[0]) + manhattan(pr[1], goal),
                                            pr[0][1], pr[0][0]))
        portal = best[0]
        if self._budget_left <= 0:
            return None
        cache_key = None
        if not replan:
            cache_key = RouteCacheKey(start, portal, self.map_version, "chunk")
            cached = self.cache.get(cache_key)
            if cached is not None and cached.status == FOUND and cached.steps:
                first = cached.steps[0]
                nx, ny = start[0] + DELTA[first][0], start[1] + DELTA[first][1]
                if (nx, ny) not in obstacles and (nx, ny) not in occupied and (nx, ny) not in forbidden:
                    self.stats.cache_hits += 1
                    self.routes[worker_id] = WorkerRoute(
                        target=goal, steps=cached.steps, next_index=1,
                        map_version=self.map_version, planned_endpoint=portal,
                        status=FOUND, start=start,
                    )
                    return PathResult(FOUND, (first,), portal, 0, cached.cost,
                                      self.map_version, reason="chunk_cache_hit")
                self.stats.replans += 1
                cache_key = None
            else:
                self.stats.cache_misses += 1
        request = PathRequest(
            start=start, goal=portal, obstacles=obstacles, occupied=occupied,
            forbidden=forbidden, allow_goal_occupied=allow_goal_occupied,
            max_expansions=min(self.astar_max_expansions, self._budget_left),
            unknown_penalty=self.unknown_penalty,
            threat_penalty=self.threat_penalty,
            threat=frozenset(threat),
            is_known=self.is_known,
            map_version=self.map_version,
        )
        result = astar_search(request)
        self._budget_left = max(0, self._budget_left - result.expanded)
        self.stats.astar_calls += 1
        self.stats.expanded_nodes += result.expanded
        if result.status != FOUND or not result.steps:
            return None
        self.routes[worker_id] = WorkerRoute(
            target=goal, steps=result.steps, next_index=1,
            map_version=self.map_version, planned_endpoint=portal,
            status=FOUND, start=start,
        )
        if cache_key is not None:
            self.cache.put(cache_key, result)
        return PathResult(FOUND, (result.steps[0],), portal, result.expanded,
                          result.cost, self.map_version, reason="chunk_segment")

    def _frontier_fallback(self, worker_id, start, goal, obstacles, occupied,
                           forbidden, mode: str) -> PathResult | None:
        """A* 受阻时的 BFS 前沿降级；成功时返回本 Tick 的一步并建立游标。"""
        budget = min(self.frontier_max_expansions, self._budget_left)
        if budget <= 0:
            return None
        request = PathRequest(
            start=start, goal=goal, obstacles=obstacles, occupied=occupied,
            forbidden=forbidden, max_expansions=budget,
            threat_penalty=self.threat_penalty,
            is_known=self.is_known, map_version=self.map_version,
        )
        result = bfs_frontier(request, mode=mode)
        self.stats.bfs_calls += 1
        self._budget_left = max(0, self._budget_left - result.expanded)
        self.stats.expanded_nodes += result.expanded
        if result.status == FRONTIER and result.steps:
            self.stats.frontier_returns += 1
            self.routes[worker_id] = WorkerRoute(
                target=goal, steps=result.steps, next_index=1,
                map_version=self.map_version, planned_endpoint=result.endpoint,
                status=FRONTIER, start=start,
            )
            return PathResult(FRONTIER, (result.steps[0],), result.endpoint,
                              result.expanded, result.cost, self.map_version,
                              reason=result.reason)
        return None

    def _cap_failed_goals(self, limit: int = 256) -> None:
        """失败目标记录有容量上限，防止长期运行无界增长。"""
        total = sum(len(s) for s in self._failed_goals.values())
        if total <= limit:
            return
        for goals in self._failed_goals.values():
            while goals and total > limit:
                goals.pop()
                total -= 1
