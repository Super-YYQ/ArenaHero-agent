# -*- coding: utf-8 -*-
"""寻路与策略决策。

策略函数是纯函数：输入当前状态与记忆，输出要执行的指令序列。
不直接调用 SDK 的动作方法，由 agent.py 把指令翻译成 SDK 调用，
这样策略可以脱离网络单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 四个正方向（服务端只接受这四种）
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
DELTA = {
    "UP": (0, -1),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
    "RIGHT": (1, 0),
}


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
    forbidden 用于禁止回头（刚走过的格子），打断 2 格振荡。
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
CHUNK_SIZE = 32


def chunk_of(cell: tuple[int, int]) -> tuple[int, int]:
    """格子所属 32×32 区块（向下取整，与规则文档一致）。"""
    x, y = cell
    return (x // CHUNK_SIZE if x >= 0 else -((-x - 1) // CHUNK_SIZE) - 1,
            y // CHUNK_SIZE if y >= 0 else -((-y - 1) // CHUNK_SIZE) - 1)


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


def assign_explore_targets(
    workers: list[dict],
    assignment: dict[str, tuple[int, int]],
    core_pos: tuple[int, int],
    state: StrategyState,
    tick: int,
    obstacles: set[tuple[int, int]] | None = None,
) -> None:
    """给没有资源任务的空载 Worker 分配环形侦察目标。原地改 workers 与 state。"""
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
        stuck = dist >= best and state.no_progress_count.get(wid, 0) + 1 >= SCOUT_NO_PROGRESS_TICKS
        return stuck

    def track_progress(wid: str, wdict: dict, target: tuple[int, int]) -> None:
        dist = _manhattan(wdict["pos"], target)
        best = state.scout_best_dist.get(wid)
        if best is None or dist < best:
            state.scout_best_dist[wid] = dist
            state.no_progress_count[wid] = 0
        else:
            state.no_progress_count[wid] = state.no_progress_count.get(wid, 0) + 1

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
        drop = should_drop(wid, wdict, target)
        if drop:
            if target is not None:
                state.scout_slots[wid] = (state.scout_slots[wid] + 1) % len(SCOUT_VECTORS)
            avoid: set[tuple[int, int]] = set()
            if target is not None:
                avoid.add(target)
                state.waypoint_last_seen[target] = tick
            if target is not None and wdict["pos"] == target:
                avoid.add(wdict["pos"])
            target = pick_scout_target(
                core_pos,
                state.chunk_last_seen,
                state.scout_claims,
                state.scout_slots[wid],
                tick,
                avoid=avoid,
                obstacles=obstacles,
                waypoint_last_seen=state.waypoint_last_seen,
            )
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
) -> dict[str, tuple[int, int]]:
    """把资源点分配给空载 Worker：每格最多一个 Worker，最近的优先。

    cooldowns: (worker_id, cell) -> 冷却解除 tick。未到解除 tick 的点不分配给该 Worker。
    """
    cooldowns = cooldowns or {}
    free_workers = [w for w in workers if w["cargo"] == 0]
    claimed: set[tuple[int, int]] = set()
    assignment: dict[str, tuple[int, int]] = {}

    def cooled(wid: str, cell: tuple[int, int]) -> bool:
        return cooldowns.get((wid, cell), 0) > tick

    # 第一遍：已有有效 harvest 任务的 Worker 保住自己的目标（就近持续开采）
    for w in free_workers:
        t = tasks.get(w["id"])
        if t and t.state == "harvest" and t.target in resource_cells and not cooled(w["id"], t.target):
            claimed.add(t.target)
            assignment[w["id"]] = t.target
        elif t and t.state == "harvest":
            tasks.pop(w["id"], None)

    # 第二遍：没有有效任务的 Worker 认领最近的未占用、未冷却资源点
    for w in free_workers:
        if w["id"] in assignment:
            continue
        best, best_cost = None, None
        for cell in resource_cells:
            if cell in claimed or cooled(w["id"], cell):
                continue
            cost = abs(w["pos"][0] - cell[0]) + abs(w["pos"][1] - cell[1])
            if best_cost is None or cost < best_cost:
                best, best_cost = cell, cost
        if best is None:
            continue
        claimed.add(best)
        assignment[w["id"]] = best
        tasks[w["id"]] = WorkerTask(state="harvest", target=best)
    return assignment


def decide_worker(
    worker: dict,
    core_pos: tuple[int, int],
    assignment: dict[str, tuple[int, int]],
    obstacles: set[tuple[int, int]],
    occupied: set[tuple[int, int]],
    threat_cells: set[tuple[int, int]],
) -> tuple[str, tuple]:
    """返回 (action, args)，action ∈ {'harvest','deposit','move','wait'}。

    优先级：受威胁撤退 > 满载回 Core > 到位采集 > 按任务移动。
    """
    pos = worker["pos"]
    last_pos = worker.get("last_pos")
    forbidden = {last_pos} if last_pos else set()
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
        # 附近没有已知资源：朝持久探索目标走，扩大视野。
        # last_pos 禁止回头，避免 2 格振荡。
        explore = worker.get("explore_target")
        if explore is not None and pos == explore:
            # 到点停下，下一 Tick 由 assign_explore_targets 换朝向；不要沿远离 Core 绕圈
            return ("wait", ())
        if explore and pos != explore:
            d = step_direction(pos, explore, obstacles, occupied, forbidden=forbidden)
            if d:
                return ("move", (d,))
        # 走不通：沿远离 Core 的轴再试一次
        away = (pos[0] * 2 - core_pos[0], pos[1] * 2 - core_pos[1])
        d = step_direction(pos, away, obstacles, occupied, forbidden=forbidden)
        if d:
            return ("move", (d,))
        return ("wait", ())
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
