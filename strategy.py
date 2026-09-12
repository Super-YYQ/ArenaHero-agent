# -*- coding: utf-8 -*-
"""寻路与策略决策。

策略函数是纯函数：输入当前状态与记忆，输出要执行的指令序列。
不直接调用 SDK 的动作方法，由 agent.py 把指令翻译成 SDK 调用，
这样策略可以脱离网络单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pathfinding import (
    DELTA,
    DIRECTIONS,
    neighbors,
    step_direction,
)

# 方向工具与贪心单步已移至 pathfinding.py（路线规划基础层），
# 此处 re-export 保持 `from strategy import DELTA / step_direction` 兼容。
__all__ = [
    "DELTA", "DIRECTIONS", "neighbors", "step_direction",
    "WorkerTask", "StrategyState", "assign_explore_targets", "assign_resources",
    "decide_worker", "decide_vanguard", "chunk_of", "chunk_center",
    "pick_scout_target", "refill_tick_at_or_after", "visible_from",
]


# ---------- Worker 状态机 ----------
@dataclass
class WorkerTask:
    """每个 Worker 的当前任务。state: 'harvest' 走向资源点，'deposit' 回 Core。"""

    state: str = "harvest"          # harvest | deposit
    target: tuple[int, int] | None = None  # 目标资源格（deposit 阶段不用，回 Core）


@dataclass
class StrategyState:
    worker_tasks: dict[str, WorkerTask] = field(default_factory=dict)
    # worker_id -> 持久探索目标格（到达或发现资源后清空）
    explore_targets: dict[str, tuple[int, int]] = field(default_factory=dict)
    # worker_id -> 上一 Tick 所在格，用来禁止回头
    last_pos: dict[str, tuple[int, int]] = field(default_factory=dict)
    # worker_id -> 连续停在同一格的 tick 数
    stall_count: dict[str, int] = field(default_factory=dict)
    # (worker_id, cell) -> 冷却解除的 tick
    resource_cooldowns: dict[tuple[str, tuple[int, int]], int] = field(default_factory=dict)
    # 32x32 区块 -> 最后一次被视野覆盖的 tick
    chunk_last_seen: dict[tuple[int, int], int] = field(default_factory=dict)
    # 已分配出去的探索目标，避免两个 Worker 去同一格
    scout_claims: set[tuple[int, int]] = field(default_factory=set)
    # worker_id -> 侦察槽位（稳定，不随列表顺序变）
    scout_slots: dict[str, int] = field(default_factory=dict)
    next_scout_slot: int = 0
    # worker_id -> 当前侦察目标上见过的最短曼哈顿距离
    scout_best_dist: dict[str, int] = field(default_factory=dict)
    # worker_id -> 距离没有缩短的连续 tick 数
    no_progress_count: dict[str, int] = field(default_factory=dict)
    # 侦察航点 -> 最后一次到达/放弃的 tick；按点记，避免 32×32 把近处西侧误判成已扫
    waypoint_last_seen: dict[tuple[int, int], int] = field(default_factory=dict)
    # 采过的旧格 -> 墓碑解除 tick（补充边界之后才允许再当目标）
    harvested_until: dict[tuple[int, int], int] = field(default_factory=dict)
    # 区块 -> 下次补充复查 tick
    chunk_next_refill: dict[tuple[int, int], int] = field(default_factory=dict)
    # 区块 -> 上次确认有资源/采到的锚点
    chunk_anchor: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    # 区块 -> 上次派人复查的 tick
    chunk_last_probe: dict[tuple[int, int], int] = field(default_factory=dict)
    # worker_id -> 当前采点上见过的最短曼哈顿距离
    harvest_best_dist: dict[str, int] = field(default_factory=dict)
    # worker_id -> 采点距离没有缩短的连续 tick
    harvest_no_progress: dict[str, int] = field(default_factory=dict)
    # worker_id -> (target, best_dist, stalled)；绕墙无进展时冷却该点
    harvest_progress: dict[str, tuple[tuple[int, int], int, int]] = field(default_factory=dict)
    # ---- 路线规划状态（与 HybridPathPlanner 共享；不持久化到 memory.json）----
    # worker_id -> 当前路线游标
    routes: dict = field(default_factory=dict)
    # 静态路线缓存（Phase 3 起使用 RouteCacheKey）
    route_cache: dict = field(default_factory=dict)
    # 与 MapMemory.obstacle_revision 同步的地图版本
    map_version: int = 0


# 16 方向 × 4 环，半径 10/20/30/40。社区成熟方案（Drew-Z arena-hero-agent）。
SCOUT_VECTORS = (
    (1, 0), (1, 1), (0, 1), (-1, 1),
    (-1, 0), (-1, -1), (0, -1), (1, -1),
    (2, 1), (1, 2), (-1, 2), (-2, 1),
    (-2, -1), (-1, -2), (1, -2), (2, -1),
)
SCOUT_RING_STEP = 10
SCOUT_RING_COUNT = 4
SCOUT_STALL_TICKS = 3
SCOUT_NO_PROGRESS_TICKS = 6
RESOURCE_COOLDOWN_TICKS = 8
RESOURCE_NO_PROGRESS_TICKS = 6
RESOURCE_MEMORY_TTL = 64
REFILL_TICKS = 4
CHUNK_SIZE = 32
VISION = {"CORE": 5, "WORKER": 3, "VANGUARD": 4, "RANGER": 5}


def chunk_of(cell: tuple[int, int]) -> tuple[int, int]:
    """格子所属 32×32 区块（向下取整，与规则文档一致）。"""
    x, y = cell
    return (x // CHUNK_SIZE if x >= 0 else -((-x - 1) // CHUNK_SIZE) - 1,
            y // CHUNK_SIZE if y >= 0 else -((-y - 1) // CHUNK_SIZE) - 1)


def chunk_center(chunk: tuple[int, int]) -> tuple[int, int]:
    cx, cy = chunk
    return (cx * CHUNK_SIZE + CHUNK_SIZE // 2, cy * CHUNK_SIZE + CHUNK_SIZE // 2)


def _chunk_recheck_points(chunk: tuple[int, int], anchor: tuple[int, int] | None):
    """补充复查：锚点 → 区块中心 → 四角。"""
    seen: set[tuple[int, int]] = set()
    cx, cy = chunk
    center = chunk_center(chunk)
    corners = (
        (cx * CHUNK_SIZE, cy * CHUNK_SIZE),
        (cx * CHUNK_SIZE + CHUNK_SIZE - 1, cy * CHUNK_SIZE),
        (cx * CHUNK_SIZE, cy * CHUNK_SIZE + CHUNK_SIZE - 1),
        (cx * CHUNK_SIZE + CHUNK_SIZE - 1, cy * CHUNK_SIZE + CHUNK_SIZE - 1),
    )
    for cand in ((anchor,) if anchor else ()) + (center,) + corners:
        if cand not in seen:
            seen.add(cand)
            yield cand


def should_abandon_scout(stall: int, stall_limit: int = SCOUT_STALL_TICKS) -> bool:
    return stall >= stall_limit


def _heading_delta(a: int, b: int) -> int:
    d = abs(a - b) % len(SCOUT_VECTORS)
    return min(d, len(SCOUT_VECTORS) - d)


def _scout_points(core_pos: tuple[int, int]):
    """16 方向 × 4 环的全部候选，附带朝向下标。"""
    for hi, heading in enumerate(SCOUT_VECTORS):
        for ring_offset in range(SCOUT_RING_COUNT):
            radius = SCOUT_RING_STEP * (1 + ring_offset)
            scale = max(1, radius // (abs(heading[0]) + abs(heading[1])))
            cand = (core_pos[0] + heading[0] * scale, core_pos[1] + heading[1] * scale)
            yield hi, cand


def pick_scout_target(
    core_pos: tuple[int, int],
    chunk_last_seen: dict[tuple[int, int], int],
    claimed: set[tuple[int, int]],
    slot: int,
    tick: int,
    avoid: set[tuple[int, int]] | None = None,
    obstacles: set[tuple[int, int]] | None = None,
    waypoint_last_seen: dict[tuple[int, int], int] | None = None,
) -> tuple[int, int]:
    """从全部 16 方向 × 4 环里挑：没去过的航点优先，同龄时近的优先。

    槽位只在同龄同距里打散朝向（slot*7 mod 16）。
    障碍格不当目标。avoid 只排除格子本身，不整块丢掉近处西侧。
    chunk_last_seen / tick 保留给调用方，选点按航点记忆。
    """
    avoid = avoid or set()
    obstacles = obstacles or set()
    waypoint_last_seen = waypoint_last_seen or {}
    preferred = (slot * 7) % len(SCOUT_VECTORS)
    blocked = claimed | avoid | obstacles
    candidates: list[tuple[int, tuple[int, int]]] = []
    for hi, cand in _scout_points(core_pos):
        if cand in blocked:
            continue
        candidates.append((hi, cand))
    if not candidates:
        for hi, cand in _scout_points(core_pos):
            if cand not in blocked:
                candidates.append((hi, cand))
                break
    if not candidates:
        fallback = (core_pos[0] + SCOUT_RING_STEP, core_pos[1])
        if fallback not in obstacles:
            return fallback
        for hi, cand in _scout_points(core_pos):
            if cand not in obstacles:
                return cand
        return fallback

    def score(item: tuple[int, tuple[int, int]]) -> tuple:
        hi, cand = item
        wp = waypoint_last_seen.get(cand, -1)
        dist = abs(cand[0] - core_pos[0]) + abs(cand[1] - core_pos[1])
        # 没去过的航点 → 近处优先 → 朝向只打散并列
        return (wp, dist, _heading_delta(hi, preferred))

    return min(candidates, key=score)[1]


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def refill_tick_at_or_after(tick: int) -> int:
    """下一个 4 Tick 资源补充边界（含当前 tick）。"""
    rem = tick % REFILL_TICKS
    return tick if rem == 0 else tick + (REFILL_TICKS - rem)


def _los_clear(frm: tuple[int, int], to: tuple[int, int], obstacles: set[tuple[int, int]]) -> bool:
    """整数 supercover：中间格有障碍则看不见终点（障碍格本身可见）。"""
    x0, y0 = frm
    x1, y1 = to
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x1 >= x0 else -1
    sy = 1 if y1 >= y0 else -1
    x, y = x0, y0
    if dx == 0 and dy == 0:
        return True
    if dx >= dy:
        err = dx
        for _ in range(dx):
            x += sx
            err += 2 * dy
            if err >= 2 * dx:
                y += sy
                err -= 2 * dx
            if (x, y) == (x1, y1):
                return True
            if (x, y) in obstacles:
                return False
    else:
        err = dy
        for _ in range(dy):
            y += sy
            err += 2 * dx
            if err >= 2 * dy:
                x += sx
                err -= 2 * dy
            if (x, y) == (x1, y1):
                return True
            if (x, y) in obstacles:
                return False
    return True


def visible_from(origin: tuple[int, int], radius: int, obstacles: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """曼哈顿半径内、不被障碍挡住的格子（障碍格本身可见）。"""
    ox, oy = origin
    seen: set[tuple[int, int]] = set()
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            cell = (ox + dx, oy + dy)
            if _los_clear(origin, cell, obstacles):
                seen.add(cell)
    return seen


def _minimum_cost_assignment(costs: list[list[int]]) -> tuple[int, ...]:
    """矩形匈牙利（行 <= 列），返回每行选中的列。"""
    if not costs:
        return ()
    rows = len(costs)
    columns = len(costs[0])
    row_potential = [0] * (rows + 1)
    column_potential = [0] * (columns + 1)
    matched_row = [0] * (columns + 1)
    previous = [0] * (columns + 1)
    for row_index in range(1, rows + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum_slack = [10**12] * (columns + 1)
        visited = [False] * (columns + 1)
        while True:
            visited[current_column] = True
            current_row = matched_row[current_column]
            delta = 10**12
            next_column = 0
            for column_index in range(1, columns + 1):
                if visited[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced < minimum_slack[column_index]:
                    minimum_slack[column_index] = reduced
                    previous[column_index] = current_column
                if minimum_slack[column_index] < delta:
                    delta = minimum_slack[column_index]
                    next_column = column_index
            for column_index in range(columns + 1):
                if visited[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum_slack[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = previous[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break
    assignment = [-1] * rows
    for column_index in range(1, columns + 1):
        row_index = matched_row[column_index]
        if row_index:
            assignment[row_index - 1] = column_index - 1
    return tuple(assignment)


def assign_explore_targets(
    workers: list[dict],
    assignment: dict[str, tuple[int, int]],
    core_pos: tuple[int, int],
    state: StrategyState,
    tick: int,
    obstacles: set[tuple[int, int]] | None = None,
) -> None:
    """给没有资源任务的空载 Worker 分配侦察目标。原地改 workers 与 state。"""
    obstacles = obstacles or set()

    def reset_progress(wid: str) -> None:
        state.stall_count[wid] = 0
        state.no_progress_count.pop(wid, None)
        state.scout_best_dist.pop(wid, None)

    def should_drop(wid: str, wdict: dict, target: tuple[int, int] | None) -> bool:
        if target is None:
            return True
        if wdict["pos"] == target or target in obstacles:
            return True
        if should_abandon_scout(state.stall_count.get(wid, 0)):
            return True
        dist = _manhattan(wdict["pos"], target)
        best = state.scout_best_dist.get(wid)
        if best is None:
            return False
        return dist >= best and state.no_progress_count.get(wid, 0) + 1 >= SCOUT_NO_PROGRESS_TICKS

    def track_progress(wid: str, wdict: dict, target: tuple[int, int]) -> None:
        dist = _manhattan(wdict["pos"], target)
        best = state.scout_best_dist.get(wid)
        if best is None or dist < best:
            state.scout_best_dist[wid] = dist
            state.no_progress_count[wid] = 0
        else:
            state.no_progress_count[wid] = state.no_progress_count.get(wid, 0) + 1

    def pick_new(wid: str, avoid: set[tuple[int, int]]) -> tuple[int, int]:
        blocked = obstacles | avoid | state.scout_claims
        due = []
        for ch, ready in state.chunk_next_refill.items():
            if ready > tick:
                continue
            last = state.chunk_last_probe.get(ch, -1)
            if last >= ready and tick - last < REFILL_TICKS:
                continue
            due.append(ch)
        due.sort(key=lambda ch: _manhattan(core_pos, state.chunk_anchor.get(ch, chunk_center(ch))))
        for ch in due:
            for cand in _chunk_recheck_points(ch, state.chunk_anchor.get(ch)):
                if cand in blocked:
                    continue
                state.chunk_last_probe[ch] = tick
                return cand
        return pick_scout_target(
            core_pos,
            state.chunk_last_seen,
            state.scout_claims,
            state.scout_slots[wid],
            tick,
            avoid=avoid,
            obstacles=obstacles,
            waypoint_last_seen=state.waypoint_last_seen,
        )

    state.scout_claims.clear()
    for wdict in workers:
        wid = wdict["id"]
        if wid in assignment or wdict["cargo"] > 0:
            continue
        target = state.explore_targets.get(wid)
        if should_drop(wid, wdict, target):
            continue
        state.scout_claims.add(target)

    for wdict in workers:
        wid = wdict["id"]
        wdict["last_pos"] = state.last_pos.get(wid)
        if wid in assignment or wdict["cargo"] > 0:
            state.explore_targets.pop(wid, None)
            reset_progress(wid)
            continue
        if wid not in state.scout_slots:
            state.scout_slots[wid] = state.next_scout_slot
            state.next_scout_slot += 1
        target = state.explore_targets.get(wid)
        if should_drop(wid, wdict, target):
            if target is not None:
                state.scout_slots[wid] = (state.scout_slots[wid] + 1) % len(SCOUT_VECTORS)
            avoid: set[tuple[int, int]] = set()
            if target is not None:
                avoid.add(target)
                state.waypoint_last_seen[target] = tick
            if target is not None and wdict["pos"] == target:
                avoid.add(wdict["pos"])
            target = pick_new(wid, avoid)
            state.explore_targets[wid] = target
            reset_progress(wid)
            state.scout_best_dist[wid] = _manhattan(wdict["pos"], target)
        else:
            track_progress(wid, wdict, target)
        state.scout_claims.add(target)
        wdict["explore_target"] = target


def assign_resources(
    workers: list[dict],
    resource_cells: list[tuple[int, int]],
    obstacles: set[tuple[int, int]],
    tasks: dict[str, WorkerTask],
    tick: int = 0,
    cooldowns: dict[tuple[str, tuple[int, int]], int] | None = None,
    last_seen: dict[tuple[int, int], int] | None = None,
    harvested_until: dict[tuple[int, int], int] | None = None,
    progress: dict[str, tuple[tuple[int, int], int, int]] | None = None,
) -> dict[str, tuple[int, int]]:
    """空载 Worker 认领资源：匈牙利最小费用，每格最多一人。

    last_seen: 格子最后确认 tick，越旧惩罚越大。
    harvested_until: 墓碑，解除 tick 之前不当目标。
    progress: worker_id -> (target, best_dist, no_progress)；绕圈无进展则冷却。
    """
    cooldowns = cooldowns if cooldowns is not None else {}
    last_seen = last_seen if last_seen is not None else {}
    harvested_until = harvested_until if harvested_until is not None else {}
    progress = progress if progress is not None else {}
    free_workers = [w for w in workers if w["cargo"] == 0]
    resources = [
        cell for cell in resource_cells
        if cell not in obstacles and harvested_until.get(cell, 0) <= tick
    ]

    def cooled(wid: str, cell: tuple[int, int]) -> bool:
        return cooldowns.get((wid, cell), 0) > tick

    # 绕墙无进展：距离长期不缩短则冷却该点
    for w in free_workers:
        t = tasks.get(w["id"])
        if not (t and t.state == "harvest" and t.target is not None):
            progress.pop(w["id"], None)
            continue
        dist = _manhattan(w["pos"], t.target)
        prev = progress.get(w["id"])
        if prev is None or prev[0] != t.target:
            progress[w["id"]] = (t.target, dist, 0)
            continue
        _, best, stalled = prev
        if dist < best:
            progress[w["id"]] = (t.target, dist, 0)
        else:
            stalled += 1
            progress[w["id"]] = (t.target, best, stalled)
            if stalled >= RESOURCE_NO_PROGRESS_TICKS:
                cooldowns[(w["id"], t.target)] = tick + RESOURCE_COOLDOWN_TICKS
                tasks.pop(w["id"], None)
                progress.pop(w["id"], None)

    if not free_workers or not resources:
        for w in free_workers:
            tasks.pop(w["id"], None)
        return {}

    unassigned = 10_000 * (len(free_workers) + 1)
    forbidden = unassigned * 2
    matrix: list[list[int]] = []
    for w in free_workers:
        row: list[int] = []
        sticky = tasks.get(w["id"])
        sticky_cell = sticky.target if sticky and sticky.state == "harvest" else None
        for cell in resources:
            if cooled(w["id"], cell):
                row.append(forbidden)
                continue
            dist = _manhattan(w["pos"], cell)
            age = max(0, tick - last_seen[cell]) if cell in last_seen else 0
            stale = 0 if age == 0 else min(6, 2 + age // 8)
            stick = 2 if sticky_cell == cell else 0
            row.append(max(0, dist + stale - stick))
        row.extend([unassigned] * len(free_workers))
        matrix.append(row)

    assignment: dict[str, tuple[int, int]] = {}
    chosen = _minimum_cost_assignment(matrix)
    for i, w in enumerate(free_workers):
        col = chosen[i]
        if col < 0 or col >= len(resources) or matrix[i][col] >= forbidden:
            tasks.pop(w["id"], None)
            continue
        cell = resources[col]
        assignment[w["id"]] = cell
        tasks[w["id"]] = WorkerTask(state="harvest", target=cell)
    return assignment


def decide_worker(
    worker: dict,
    core_pos: tuple[int, int],
    assignment: dict[str, tuple[int, int]],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    threat_cells: set[tuple[int, int]],
    planner=None,
) -> tuple[str, tuple]:
    """返回 (action, args)，action ∈ {'harvest','deposit','move','wait'}。

    优先级：受威胁撤退 > 满载回 Core > 到位采集 > 按任务移动。
    planner 非空时，撤退/回 Core/去资源的移动统一交给混合规划器；
    planner 为 None 时保持旧的单步贪心行为（回归兼容）。
    """
    pos = worker["pos"]
    last_pos = worker.get("last_pos")
    forbidden = {last_pos} if last_pos else set()

    def move_or_wait(result) -> tuple[str, tuple]:
        if result.steps:
            return ("move", (result.steps[0],))
        return ("wait", ())

    if planner is not None:
        wid = worker["id"]
        # 遭遇敌人：向 Core 撤退（Worker 完全不能攻击）
        if pos in threat_cells:
            r = planner.next_step(wid, pos, core_pos, obstacles=obstacles, occupied=occupied,
                                  forbidden=forbidden, allow_goal_occupied=True,
                                  threat=threat_cells, goal_kind="core")
            return move_or_wait(r)
        if worker["cargo"] > 0:
            if pos == core_pos:
                return ("deposit", ())
            # Core 格是占位实体，交付时必须走进去：allow_goal_occupied=True
            r = planner.next_step(wid, pos, core_pos, obstacles=obstacles, occupied=occupied,
                                  forbidden=forbidden, allow_goal_occupied=True,
                                  goal_kind="core")
            return move_or_wait(r)
        target = assignment.get(wid)
        if target is None:
            # 侦察移动：本阶段保留旧逻辑，Phase 6 接入规划器
            return _scout_move(worker, core_pos, obstacles, occupied, forbidden)
        if pos == target:
            # 只有当前视野确认该格仍有资源才 harvest，否则原地等重新分配
            visible = worker.get("visible_resources")
            if visible is None or target in visible:
                return ("harvest", ())
            return ("wait", ())
        r = planner.next_step(wid, pos, target, obstacles=obstacles, occupied=occupied,
                              forbidden=forbidden, goal_kind="harvest")
        return move_or_wait(r)

    # 遭遇敌人：向 Core 撤退（Worker 完全不能攻击）
    if pos in threat_cells:
        d = step_direction(pos, core_pos, obstacles, occupied, forbidden=forbidden)
        if d:
            return ("move", (d,))
        return ("wait", ())

    if worker["cargo"] > 0:
        if pos == core_pos:
            return ("deposit", ())
        # Core 格是占位实体，交付时必须走进去，所以寻路时不把 Core 格当 occupied
        occupied_for_return = occupied - {core_pos}
        d = step_direction(pos, core_pos, obstacles, occupied_for_return, forbidden=forbidden)
        if d:
            return ("move", (d,))
        return ("wait", ())

    target = assignment.get(worker["id"])
    if target is None:
        return _scout_move(worker, core_pos, obstacles, occupied, forbidden)
    if pos == target:
        # 只有当前视野确认该格仍有资源才 harvest，否则原地等重新分配
        visible = worker.get("visible_resources")
        if visible is None or target in visible:
            return ("harvest", ())
        return ("wait", ())
    d = step_direction(pos, target, obstacles, occupied, forbidden=forbidden)
    if d:
        return ("move", (d,))
    return ("wait", ())


def _scout_move(
    worker: dict,
    core_pos: tuple[int, int],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    forbidden: set[tuple[int, int]],
) -> tuple[str, tuple]:
    """旧侦察移动：走向探索航点；走不通沿远离 Core 的轴再试一次。"""
    pos = worker["pos"]
    explore = worker.get("explore_target")
    if explore is not None and pos == explore:
        # 到点停下，下一 Tick 由 assign_explore_targets 换朝向；不要沿远离 Core 绕圈
        return ("wait", ())
    if explore and pos != explore:
        d = step_direction(pos, explore, obstacles, occupied, forbidden=forbidden)
        if d:
            return ("move", (d,))
    away = (pos[0] * 2 - core_pos[0], pos[1] * 2 - core_pos[1])
    d = step_direction(pos, away, obstacles, occupied, forbidden=forbidden)
    if d:
        return ("move", (d,))
    return ("wait", ())


def decide_vanguard(
    vanguard: dict,
    core_pos: tuple[int, int],
    enemies: list[dict],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
) -> tuple[str, tuple]:
    """Vanguard 自卫：有敌近身先打，否则守在 Core 旁。

    enemies: [{'id','pos','unit_type'}] 当前可见敌方对象。
    SWEEP 不需要目标 UUID，也不伤友军，直接朝敌方所在相邻格打。
    """
    pos = vanguard["pos"]
    adjacent_enemies = [
        e for e in enemies
        if abs(e["pos"][0] - pos[0]) + abs(e["pos"][1] - pos[1]) == 1
    ]
    if adjacent_enemies:
        e = adjacent_enemies[0]
        dx, dy = e["pos"][0] - pos[0], e["pos"][1] - pos[1]
        direction = next(d for d, (ddx, ddy) in DELTA.items() if (ddx, ddy) == (dx, dy))
        return ("sweep", (direction,))

    # 敌人接近 Core（视野内距 Core <= 2）：迎击
    for e in sorted(enemies, key=lambda e: abs(e["pos"][0] - core_pos[0]) + abs(e["pos"][1] - core_pos[1])):
        dist_to_core = abs(e["pos"][0] - core_pos[0]) + abs(e["pos"][1] - core_pos[1])
        if dist_to_core <= 2:
            d = step_direction(pos, e["pos"], obstacles, occupied)
            if d:
                return ("move", (d,))

    # 平时蹲守：回到 Core 相邻的空位（不占 Core 格）
    if abs(pos[0] - core_pos[0]) + abs(pos[1] - core_pos[1]) > 1:
        d = step_direction(pos, core_pos, obstacles, occupied)
        if d:
            return ("move", (d,))
    return ("wait", ())
