# -*- coding: utf-8 -*-
"""寻路与策略决策。

策略函数是纯函数：输入当前状态与记忆，输出要执行的指令序列。
不直接调用 SDK 的动作方法，由 agent.py 把指令翻译成 SDK 调用，
这样策略可以脱离网络单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pathfinding import (
    CHUNK_SIZE,
    DELTA,
    DIRECTIONS,
    RouteCache,
    chunk_of,
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
    # ---- 近场逐区块扫掠 ----
    # chunk -> 下一个扫描停留点索引（全局游标，多 Worker 顺序接力）
    sweep_cursor: dict[tuple[int, int], int] = field(default_factory=dict)
    # chunk -> 上次完整扫掠完成的 tick
    chunk_last_swept: dict[tuple[int, int], int] = field(default_factory=dict)
    # worker_id -> 正在扫掠的区块
    sweep_assign: dict[str, tuple[int, int]] = field(default_factory=dict)
    # 本 Tick 已被认领的扫掠区块（每 Tick 重建）
    sweep_claims: set = field(default_factory=set)
    # ---- 路线规划状态（与 HybridPathPlanner 共享；不持久化到 memory.json）----
    # worker_id -> 当前路线游标
    routes: dict = field(default_factory=dict)
    # 静态路线缓存（LRU，容量受配置限制；动态占用永不入缓存）
    route_cache: RouteCache = field(default_factory=RouteCache)
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
# CHUNK_SIZE 与 chunk_of 现于 pathfinding.py（区块导航摘要层），此处 re-export
VISION = {"CORE": 5, "WORKER": 3, "VANGUARD": 4, "RANGER": 5}

# ---- 近场逐区块扫掠 ----
# 配额公式 max(2, floor(128/(8+ring)))：远离原点时每区块仅 2 个资源点，
# 稀疏航点几乎撞不见；扫描线让 Worker 视野(半径 3)无缝覆盖整个区块。
SWEEP_CHUNK_RADIUS = 2            # Core 周边 5×5 个区块纳入扫掠
SWEEP_LINE_OFFSETS = (3, 10, 17, 24, 31)   # 行距 7：视野 ±3 无缝覆盖 32 行
SWEEP_POINT_X_OFFSETS = (2, 8, 14, 20, 29)  # 首点 ≤3、末点 ≥28，行走沿线全覆盖
SWEEP_MIN_CHUNK_GAP = 2           # 活跃区块最小切比雪夫距离：防止多 Worker 挤在同一片
SWEEP_STARVE_TICKS = 1500         # 区块超过该 Tick 未扫则插队，防止远处区块饿死
ENEMY_CORE_ZONE_RADIUS = 4        # 敌方基地的路线规避圈（驻军防御范围）
RANGER_SHOOT_RANGE = 3            # Ranger 射程：横/竖/45°斜线 1~3 格
UNIT_HP_MAX = {"WORKER": 2, "VANGUARD": 4, "RANGER": 2}


def pick_raid_target(
    enemy_cores: dict,
    core_pos: tuple[int, int],
    exclude: frozenset = frozenset(),
) -> tuple[int, int] | None:
    """选最近的敌方 Core 作为出征目标（确定性 tie-break）。"""
    best = None
    for cell in enemy_cores:
        if cell in exclude:
            continue
        d = abs(cell[0] - core_pos[0]) + abs(cell[1] - core_pos[1])
        key = (d, cell[1], cell[0])
        if best is None or key < best[0]:
            best = (key, cell)
    return best[1] if best else None


def ranger_shoot_cell(
    pos: tuple[int, int],
    enemies: list[dict],
    obstacles: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Ranger 的最佳射击格：横/竖/45°斜线 1~3 格、中间无障碍。

    优先敌方 Core（围攻优先），其次 HP 最低的敌方 Unit；平局取最近。
    服务端按格结算：命中该格 HP 最低的敌方对象。
    """
    best = None
    for e in enemies:
        ex, ey = e["pos"]
        dx, dy = ex - pos[0], ey - pos[1]
        if dx != 0 and dy != 0 and abs(dx) != abs(dy):
            continue  # 不在横/竖/45°斜线上
        dist = max(abs(dx), abs(dy))
        if dist < 1 or dist > RANGER_SHOOT_RANGE:
            continue
        sx = (dx > 0) - (dx < 0)
        sy = (dy > 0) - (dy < 0)
        blocked = False
        for s in range(1, dist):
            if (pos[0] + sx * s, pos[1] + sy * s) in obstacles:
                blocked = True
                break
        if blocked:
            continue
        is_core = e.get("unit_type") is None
        hp = e.get("hp")
        key = (0 if is_core else 1,
               hp if hp is not None else 99,
               dist, ey, ex)
        if best is None or key < best[0]:
            best = (key, (ex, ey))
    return best[1] if best else None


def enemy_threat_cells(
    enemies: list[dict],
    obstacles: set[tuple[int, int]],
) -> tuple[set, set]:
    """按单位类型计算威胁格。

    返回 (threat_cells, zones)：
    - threat_cells：能打到你的格子，Worker 站上去就触发撤退。
      Vanguard / 敌方 Core 相邻 1 格；Ranger 八方向直线 1~3 格（障碍挡射线）；
      敌方 Worker 完全不能攻击，不构成威胁。
    - zones：路线惩罚圈 = 上面全部 + 敌方 Core 周边 ENEMY_CORE_ZONE_RADIUS 格。
      传给规划器的 threat_penalty，让路线绕开敌方基地而不是硬穿。
    """
    threat: set[tuple[int, int]] = set()
    zones: set[tuple[int, int]] = set()

    def add(cell: tuple[int, int]) -> None:
        threat.add(cell)
        zones.add(cell)

    for e in enemies:
        x, y = e["pos"]
        ut = e.get("unit_type")
        name = getattr(ut, "name", ut)
        if name is None:
            # 敌方 Core：本体会被 Vanguard SWEEP 打到
            add((x, y))
            for dx, dy in DELTA.values():
                add((x + dx, y + dy))
            r = ENEMY_CORE_ZONE_RADIUS
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) + abs(dy) <= r:
                        zones.add((x + dx, y + dy))
        elif name == "RANGER":
            add((x, y))
            for ddx, ddy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                for sign in (1, -1):
                    for s in (1, 2, 3):
                        cell = (x + ddx * s * sign, y + ddy * s * sign)
                        if cell in obstacles:
                            break
                        add(cell)
        elif name == "VANGUARD":
            add((x, y))
            for dx, dy in DELTA.values():
                add((x + dx, y + dy))
        # WORKER：完全不能攻击，不构成威胁
    return threat, zones


def chunk_sweep_points(chunk: tuple[int, int]) -> list[tuple[int, int]]:
    """区块的扫描线停留点：5 条横线 × 5 点，行走沿线时视野覆盖全部 32×32 格。"""
    x0, y0 = chunk[0] * CHUNK_SIZE, chunk[1] * CHUNK_SIZE
    return [(x0 + dx, y0 + dy) for dy in SWEEP_LINE_OFFSETS for dx in SWEEP_POINT_X_OFFSETS]


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
    sweep: bool = True,
    avoid_zones: set[tuple[int, int]] | None = None,
) -> None:
    """给没有资源任务的空载 Worker 分配侦察目标。原地改 workers 与 state。

    sweep=True 走"近场逐区块扫掠"：扫描线把 Core 周边 5×5 区块全覆盖，
    到期复查的资源区块优先——配额制世界里这是发现资源点的主要手段。
    sweep=False 保留旧的稀疏环形航点行为（回归兼容/fallback）。
    avoid_zones（如敌方基地圈）里的格子不当目标、扫掠时直接跳过。
    """
    obstacles = obstacles or set()
    zones = avoid_zones or set()

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

    def due_refill_chunks() -> list[tuple[int, int]]:
        due = []
        for ch, ready in state.chunk_next_refill.items():
            if ready > tick:
                continue
            last = state.chunk_last_probe.get(ch, -1)
            if last >= ready and tick - last < REFILL_TICKS:
                continue
            due.append(ch)
        return due

    def sweep_next(wid: str, due_set: set, worker_pos: tuple[int, int]) -> tuple[int, int] | None:
        """取下一个扫掠停留点。

        优先延续当前区块（最省路程）；到期复查区块（补点在等）次之，且不受
        间隔约束；新认领区块与所有活跃区块保持切比雪夫距离 ≥
        SWEEP_MIN_CHUNK_GAP。候选按"离 Worker 自己的距离"排序——刚扫完远角
        就近拿下一块，而不是被派去地图对角；超期未扫的区块插队防饿死，
        Worker 距离打平时用 Core 距离兜底（多 Worker 同起点的退化场景）。
        """
        core_chunk = chunk_of(core_pos)
        r = SWEEP_CHUNK_RADIUS
        cands = [(core_chunk[0] + dx, core_chunk[1] + dy)
                 for dx in range(-r, r + 1) for dy in range(-r, r + 1)]

        def starving(ch: tuple[int, int]) -> bool:
            swept = state.chunk_last_swept.get(ch, -1)
            return swept != -1 and tick - swept > SWEEP_STARVE_TICKS

        cands.sort(key=lambda ch: (
            0 if starving(ch) else 1,
            abs(ch[0] * CHUNK_SIZE + 16 - worker_pos[0])
            + abs(ch[1] * CHUNK_SIZE + 16 - worker_pos[1]),
            abs(ch[0] - core_chunk[0]) + abs(ch[1] - core_chunk[1]), ch))

        def advance(ch: tuple[int, int]) -> tuple[int, int] | None:
            points = chunk_sweep_points(ch)
            idx = state.sweep_cursor.get(ch, 0)
            while idx < len(points) and (points[idx] in obstacles or points[idx] in zones):
                idx += 1
            if idx >= len(points):
                # 本轮扫完：记录完成时间、游标归零，等待下一轮轮转
                state.sweep_cursor[ch] = 0
                state.chunk_last_swept[ch] = tick
                if ch in due_set:
                    state.chunk_last_probe[ch] = tick
                return None
            state.sweep_cursor[ch] = idx + 1
            state.sweep_assign[wid] = ch
            state.sweep_claims.add(ch)
            if ch in due_set:
                state.chunk_last_probe[ch] = tick
            return points[idx]

        def fresh(ch: tuple[int, int]) -> bool:
            """本 Tick 刚扫完的区块本轮不再重复认领。"""
            return state.chunk_last_swept.get(ch, -1) != tick

        cur = state.sweep_assign.get(wid)
        if cur is not None and fresh(cur):
            point = advance(cur)
            if point is not None:
                return point
        # 到期复查区块：补点已经生成在等，优先去扫，允许靠近活跃区块
        for ch in cands:
            if ch in due_set and ch not in state.sweep_claims and fresh(ch):
                point = advance(ch)
                if point is not None:
                    return point
        # 新区块：与所有活跃区块保持间隔
        for ch in cands:
            if ch in state.sweep_claims or not fresh(ch):
                continue
            if all(max(abs(ch[0] - c2[0]), abs(ch[1] - c2[1])) >= SWEEP_MIN_CHUNK_GAP
                   for c2 in state.sweep_claims):
                point = advance(ch)
                if point is not None:
                    return point
        # 放宽间隔兜底（Worker 数超过间隔可容纳数时）
        for ch in cands:
            if ch not in state.sweep_claims and fresh(ch):
                point = advance(ch)
                if point is not None:
                    return point
        return None

    def pick_new(wid: str, avoid: set[tuple[int, int]], worker_pos: tuple[int, int]) -> tuple[int, int]:
        if sweep:
            point = sweep_next(wid, set(due_refill_chunks()), worker_pos)
            if point is not None:
                return point
        else:
            blocked = obstacles | zones | avoid | state.scout_claims
            due = due_refill_chunks()
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
            avoid=avoid | zones,
            obstacles=obstacles,
            waypoint_last_seen=state.waypoint_last_seen,
        )

    state.scout_claims.clear()
    state.sweep_claims.clear()
    for wdict in workers:
        wid = wdict["id"]
        if wid in assignment or wdict["cargo"] > 0:
            continue
        target = state.explore_targets.get(wid)
        if should_drop(wid, wdict, target):
            continue
        state.scout_claims.add(target)
        cur_chunk = state.sweep_assign.get(wid)
        if cur_chunk is not None:
            state.sweep_claims.add(cur_chunk)

    for wdict in workers:
        wid = wdict["id"]
        wdict["last_pos"] = state.last_pos.get(wid)
        if wid in assignment or wdict["cargo"] > 0:
            state.explore_targets.pop(wid, None)
            state.sweep_assign.pop(wid, None)
            reset_progress(wid)
            continue
        if wid not in state.scout_slots:
            state.scout_slots[wid] = state.next_scout_slot
            state.next_scout_slot += 1
        target = state.explore_targets.get(wid)
        if should_drop(wid, wdict, target):
            if target is not None:
                state.scout_slots[wid] = (state.scout_slots[wid] + 1) % len(SCOUT_VECTORS)
                state.waypoint_last_seen[target] = tick
            avoid: set[tuple[int, int]] = set()
            if target is not None:
                avoid.add(target)
            if target is not None and wdict["pos"] == target:
                avoid.add(wdict["pos"])
            target = pick_new(wid, avoid, wdict["pos"])
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
    core_space: int | None = None,
) -> dict[str, tuple[int, int]]:
    """空载 Worker 认领资源：匈牙利最小费用，每格最多一人。

    last_seen: 格子最后确认 tick，越旧惩罚越大。
    harvested_until: 墓碑，解除 tick 之前不当目标。
    progress: worker_id -> (target, best_dist, no_progress)；绕圈无进展则冷却。
    core_space: Core 剩余容量。满仓(<=0)时停止派发采集——交付不进去，
    采了也背在身上，只会把交付口堵死；Worker 全部转入扫掠待命。
    """
    cooldowns = cooldowns if cooldowns is not None else {}
    last_seen = last_seen if last_seen is not None else {}
    harvested_until = harvested_until if harvested_until is not None else {}
    progress = progress if progress is not None else {}
    free_workers = [w for w in workers if w["cargo"] == 0]
    if core_space is not None and core_space <= 0:
        for w in free_workers:
            tasks.pop(w["id"], None)
        return {}
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
    threat_zones: set[tuple[int, int]] | None = None,
    core_cell_reserved: bool = False,
) -> tuple[str, tuple]:
    """返回 (action, args)，action ∈ {'harvest','deposit','move','wait'}。

    优先级：受威胁撤退 > 满载回 Core > 到位采集 > 按任务移动。
    planner 非空时，撤退/回 Core/去资源/侦察四类移动统一交给混合规划器；
    planner 为 None 时保持旧的单步贪心行为（回归兼容，含侦察 away 兜底）。
    threat_cells 是能打到自己的格子（撤退触发）；threat_zones 是更大的路线
    惩罚圈（如敌方基地周边），传给规划器的 threat_penalty 绕开硬穿。
    core_cell_reserved：本 Tick 已有其他 Worker 申报进入 Core 格——Core 每格
    只能容 1 个 Unit，多个人同时申报进格会被服务端依赖图整批拒绝，必须串行。
    """
    pos = worker["pos"]
    last_pos = worker.get("last_pos")
    forbidden = {last_pos} if last_pos else set()
    route_threat = threat_zones if threat_zones is not None else threat_cells

    def move_or_wait(result) -> tuple[str, tuple]:
        if result.steps:
            return ("move", (result.steps[0],))
        return ("wait", ())

    if planner is not None:
        wid = worker["id"]
        threat = frozenset(route_threat)
        # 遭遇敌人：向 Core 撤退（Worker 完全不能攻击）
        if pos in threat_cells:
            r = planner.next_step(wid, pos, core_pos, obstacles=obstacles, occupied=occupied,
                                  forbidden=forbidden, allow_goal_occupied=True,
                                  threat=threat, goal_kind="core")
            return move_or_wait(r)
        if worker["cargo"] > 0:
            if pos == core_pos:
                return ("deposit", ())
            # Core 格是占位实体，交付时必须走进去：allow_goal_occupied=True；
            # 但本 Tick 已有人申报进 Core 时按占用处理，在旁排队
            r = planner.next_step(wid, pos, core_pos, obstacles=obstacles, occupied=occupied,
                                  forbidden=forbidden,
                                  allow_goal_occupied=not core_cell_reserved,
                                  threat=threat, goal_kind="core")
            return move_or_wait(r)
        # 自动治疗：在自己 Core 格上带伤时优先恢复（HEAL 完整动作，一次可回满；
        # 资源不足时服务端私下失败不扣资源，下一 Tick 重试）
        if pos == core_pos:
            hp, hp_max = worker.get("hp"), worker.get("hp_max")
            if hp is not None and hp_max and hp < hp_max:
                return ("heal", ())
        target = assignment.get(wid)
        if target is None:
            # 侦察：航点作为普通路线目标交给规划器；BLOCKED 由探索前沿兜底
            explore = worker.get("explore_target")
            if explore is None or pos == explore:
                # 到点停下，下一 Tick 由 assign_explore_targets 换目标
                return ("wait", ())
            r = planner.next_step(wid, pos, explore, obstacles=obstacles, occupied=occupied,
                                  forbidden=forbidden, threat=threat, goal_kind="scout")
            return move_or_wait(r)
        if pos == target:
            # 只有当前视野确认该格仍有资源才 harvest，否则原地等重新分配
            visible = worker.get("visible_resources")
            if visible is None or target in visible:
                return ("harvest", ())
            return ("wait", ())
        r = planner.next_step(wid, pos, target, obstacles=obstacles, occupied=occupied,
                              forbidden=forbidden, threat=threat, goal_kind="harvest")
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
        # Core 格是占位实体，交付时必须走进去，所以寻路时不把 Core 格当 occupied；
        # 本 Tick 已有人申报进 Core 时保持占用，在旁排队（见 core_cell_reserved）
        occupied_for_return = occupied - (set() if core_cell_reserved else {core_pos})
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
    """旧侦察移动（仅 planner=None 的回退路径）：走向探索航点；走不通沿远离 Core 的轴再试一次。"""
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
    planner=None,
    raid_target: tuple[int, int] | None = None,
    war_armed: bool = False,
    threat_zones: set[tuple[int, int]] | None = None,
) -> tuple[str, tuple]:
    """Vanguard 自卫与出征：有敌近身先打；战争状态下向目标 Core 行军围攻；
    否则守在 Core 旁相邻空位（绝不蹲交付口）。

    enemies: [{'id','pos','unit_type'}] 当前可见敌方对象（含敌方 Core）。
    SWEEP 不需要目标 UUID，也绝不会伤到自己人，直接朝敌方所在相邻格打。
    raid_target/war_armed 由 agent 在战争开启且库存达保留线时传入；
    planner 用于跨区长途行军（贪心单步会卡死在障碍上）。
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

    dist_core = abs(pos[0] - core_pos[0]) + abs(pos[1] - core_pos[1])
    # 出生/滞留在 Core 格上：带伤先治疗（HEAL 完整动作、一次可回满），否则让出交付口
    if dist_core == 0:
        hp, hp_max = vanguard.get("hp"), vanguard.get("hp_max")
        if hp is not None and hp_max and hp < hp_max:
            return ("heal", ())
        for d, nxt in neighbors(pos):
            if nxt not in obstacles and nxt not in occupied:
                return ("move", (d,))
        return ("wait", ())

    # 出征：战争状态下向目标 Core 行军；到达相邻位后上面的
    # adjacent_enemies 分支自动 SWEEP 围攻（相邻 1 格 = SWEEP 射程）
    if war_armed and raid_target is not None:
        if abs(pos[0] - raid_target[0]) + abs(pos[1] - raid_target[1]) > 1:
            if planner is not None:
                r = planner.next_step(
                    vanguard["id"], pos, raid_target, obstacles=obstacles,
                    occupied=occupied, threat=frozenset(threat_zones or ()),
                    goal_kind="scout",
                )
                if r.steps:
                    return ("move", (r.steps[0],))
                return ("wait", ())
            d = step_direction(pos, raid_target, obstacles, occupied)
            if d:
                return ("move", (d,))
            return ("wait", ())
        return ("wait", ())

    # 敌人接近 Core（视野内距 Core <= 2）：迎击
    for e in sorted(enemies, key=lambda e: abs(e["pos"][0] - core_pos[0]) + abs(e["pos"][1] - core_pos[1])):
        dist_to_core = abs(e["pos"][0] - core_pos[0]) + abs(e["pos"][1] - core_pos[1])
        if dist_to_core <= 2:
            d = step_direction(pos, e["pos"], obstacles, occupied)
            if d:
                return ("move", (d,))

    # 平时蹲守：回到 Core 相邻的空位（不占 Core 格）
    if dist_core > 1:
        d = step_direction(pos, core_pos, obstacles, occupied)
        if d:
            return ("move", (d,))
    return ("wait", ())


def decide_ranger(
    ranger: dict,
    core_pos: tuple[int, int],
    enemies: list[dict],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    planner=None,
    raid_target: tuple[int, int] | None = None,
    war_armed: bool = False,
    threat_zones: set[tuple[int, int]] | None = None,
) -> tuple[str, tuple]:
    """Ranger：射程（横/竖/斜 1~3 格）内见敌就射（优先敌方 Core）；
    战争状态随队出征，行进到目标相邻位开火；否则回 Core 旁待命。"""
    pos = ranger["pos"]
    # 1) 射程内最优目标
    cell = ranger_shoot_cell(pos, enemies, obstacles)
    if cell is not None:
        return ("shoot", (cell[0], cell[1]))

    dist_core = abs(pos[0] - core_pos[0]) + abs(pos[1] - core_pos[1])
    # 2) Core 格上：带伤治疗，否则让位
    if dist_core == 0:
        hp, hp_max = ranger.get("hp"), ranger.get("hp_max")
        if hp is not None and hp_max and hp < hp_max:
            return ("heal", ())
        for d, nxt in neighbors(pos):
            if nxt not in obstacles and nxt not in occupied:
                return ("move", (d,))
        return ("wait", ())

    # 3) 出征：行军到目标相邻位（相邻必在射程内,停下开火）
    if war_armed and raid_target is not None:
        if abs(pos[0] - raid_target[0]) + abs(pos[1] - raid_target[1]) > 1:
            if planner is not None:
                r = planner.next_step(
                    ranger["id"], pos, raid_target, obstacles=obstacles,
                    occupied=occupied, threat=frozenset(threat_zones or ()),
                    goal_kind="scout",
                )
                if r.steps:
                    return ("move", (r.steps[0],))
                return ("wait", ())
            d = step_direction(pos, raid_target, obstacles, occupied)
            if d:
                return ("move", (d,))
            return ("wait", ())
        return ("wait", ())

    # 4) 平时蹲守：回到 Core 相邻的空位
    if dist_core > 1:
        d = step_direction(pos, core_pos, obstacles, occupied)
        if d:
            return ("move", (d,))
    return ("wait", ())
