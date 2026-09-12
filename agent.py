# -*- coding: utf-8 -*-
"""Arena Hero 经济流 Agent 入口。

用法：先把 API Key 填进 config.json，然后 `python agent.py`。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from arena_hero import ArenaHeroClient, Direction, UnitType, unit_cost

from memory import MapMemory
from pathfinding import HybridPathPlanner
from strategy import (
    DELTA,
    ENEMY_CORE_ZONE_RADIUS,
    RESOURCE_COOLDOWN_TICKS,
    RESOURCE_MEMORY_TTL,
    VISION,
    StrategyState,
    assign_explore_targets,
    assign_resources,
    chunk_of,
    decide_vanguard,
    decide_worker,
    enemy_threat_cells,
    refill_tick_at_or_after,
    visible_from,
)

HERE = Path(__file__).parent
log = logging.getLogger("agent")

# 规划器配置默认值：旧 config.json 缺字段也能启动（见开发计划 §12）
PLANNER_DEFAULTS = {
    "enable_path_planner": True,
    "path_planner_mode": "hybrid",
    "astar_max_expansions": 800,
    "frontier_bfs_max_expansions": 1200,
    "total_path_budget": 5000,
    "route_cache_size": 256,
    "fast_path_distance": 12,
    "unknown_cell_penalty": 1,
    "enable_chunk_navigation": True,
    "enable_chunk_sweep": True,
    "enemy_threat_penalty": 30,
    "hoard_mode": False,
    "hoard_until_resources": 0,
    "min_spawn_reserve": 0,
}
# 数值型配置的合理上限，超出视为非法
PLANNER_INT_LIMITS = {
    "astar_max_expansions": 100_000,
    "frontier_bfs_max_expansions": 100_000,
    "total_path_budget": 1_000_000,
    "route_cache_size": 65_536,
    "fast_path_distance": 64,
    "unknown_cell_penalty": 100,
    "enemy_threat_penalty": 1_000,
    "hoard_until_resources": 100_000,
}


def planner_config(cfg: dict) -> dict:
    """合并规划器配置：缺字段用默认值；类型/范围非法时记录错误并回退默认。"""
    merged = dict(PLANNER_DEFAULTS)
    for key, default in PLANNER_DEFAULTS.items():
        if key not in cfg:
            continue
        value = cfg[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                log.error("配置 %s=%r 应为布尔值，使用默认 %s", key, value, default)
                continue
        elif isinstance(default, int):
            limit = PLANNER_INT_LIMITS.get(key)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0 or (limit is not None and value > limit)):
                log.error("配置 %s=%r 超出合理范围，使用默认 %s", key, value, default)
                continue
        else:
            merged[key] = value
            continue
        merged[key] = value
    return merged


def setup_logging(level: str) -> Path:
    log_path = HERE / "agent.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def load_config() -> dict:
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    key = cfg.get("api_key", "")
    if not key or "填入" in key:
        print("请先把 API Key 填进 config.json 再运行。", file=sys.stderr)
        sys.exit(1)
    return cfg


class Agent:
    def __init__(self, cfg: dict, mem: MapMemory | None = None) -> None:
        self.cfg = cfg
        self.mem = mem if mem is not None else MapMemory()
        self.strat = StrategyState()
        self.last_resources = None
        self.last_log_tick = 0
        # 路线规划统计（Phase 0 基线字段；规划器接入后由 planner 填充）
        self.planner = None
        self.route_stats = {
            "plans": 0, "fast_steps": 0, "astar_calls": 0, "bfs_calls": 0,
            "cache_hits": 0, "cache_misses": 0, "invalidated": 0, "replans": 0,
            "expanded_nodes": 0, "frontier_returns": 0, "budget_exhausted": 0,
            "blocked": 0, "arrivals": 0, "cooldowns": 0,
        }
        self.last_stats_log_tick = 0
        # 攒钱模式日志去重
        self._hoard_announced = False
        self._hoard_released = False
        pcfg = planner_config(cfg)
        self.pcfg = pcfg
        if pcfg["enable_path_planner"]:
            use_chunks = pcfg["enable_chunk_navigation"]
            if not use_chunks:
                self.mem.chunk_index = None
            self.planner = HybridPathPlanner(
                astar_max_expansions=pcfg["astar_max_expansions"],
                frontier_max_expansions=pcfg["frontier_bfs_max_expansions"],
                total_path_budget=pcfg["total_path_budget"],
                fast_path_distance=pcfg["fast_path_distance"],
                unknown_penalty=pcfg["unknown_cell_penalty"],
                threat_penalty=pcfg["enemy_threat_penalty"],
                routes=self.strat.routes,
                cache=self.strat.route_cache,
                chunk_index=self.mem.chunk_index if use_chunks else None,
            )
            self.strat.route_cache.capacity = pcfg["route_cache_size"]

    # ---------- 主循环 ----------
    def run(self) -> None:
        with ArenaHeroClient(api_key=self.cfg["api_key"]) as game:
            log.info("已连接 Arena Hero，等待游戏状态…")
            for turn in game.turns():
                try:
                    self.handle_turn(turn)
                except Exception:
                    log.exception("处理 tick %s 时出错，本回合跳过", turn.tick)

    # ---------- 单回合决策 ----------
    def handle_turn(self, turn) -> None:
        tick = turn.tick
        state = turn.state

        # 状态不是 ACTIVE（重生中等）：无事可做，等下一 Tick
        status = state.status.value if hasattr(state.status, "value") else state.status
        if status != "ACTIVE" or turn.core is None:
            log.info("tick %s: 状态 %s，等待重生", tick, status)
            return

        # ---- 更新地图记忆 ----
        core = turn.core
        core_pos = (core.position[0], core.position[1]) if core else self.mem.core_position
        # 当前视野：己方对象曼哈顿半径并集，障碍挡住后面（障碍格本身可见）
        obstacles_now = {tuple(c) for c in turn.obstacle_cells} | set(self.mem.obstacles)
        visible_cells = set()
        if core:
            visible_cells |= visible_from(core_pos, VISION["CORE"], obstacles_now)
        for u in turn.workers:
            visible_cells |= visible_from((u.position[0], u.position[1]), VISION["WORKER"], obstacles_now)
        for u in turn.vanguards:
            visible_cells |= visible_from((u.position[0], u.position[1]), VISION["VANGUARD"], obstacles_now)
        for u in turn.rangers:
            visible_cells |= visible_from((u.position[0], u.position[1]), VISION["RANGER"], obstacles_now)
        self.mem.observe(
            tick,
            turn.obstacle_cells,
            turn.resource_cells,
            core_pos,
            visible_cells=visible_cells,
        )
        self.mem.maybe_save()
        # 地图版本同步：新增障碍会使旧路线/缓存失效（begin_tick 内处理）
        # 观测顺序：observe 已更新 MapMemory 与 ChunkNavigationIndex，这里才规划
        self.strat.map_version = self.mem.obstacle_revision
        if self.planner is not None:
            is_known = self.mem.chunk_index.is_known if self.mem.chunk_index else None
            self.planner.begin_tick(self.strat.map_version, is_known=is_known)
        # 记录本 Tick 视野覆盖到的 32×32 区块，供侦察选「最久未见」
        for cell in visible_cells:
            self.strat.chunk_last_seen[chunk_of(cell)] = tick

        # HARVEST_FAILED / 采空：该点冷却，并给所在区块排补充复查
        for ev in turn.events:
            et = getattr(ev, "event_type", None)
            et_s = et if isinstance(et, str) else getattr(et, "value", str(et or ""))
            if et in ("HARVEST_FAILED", "RESOURCE_DEPLETED", "HARVEST_SUCCEEDED") or (
                isinstance(et_s, str) and ("HARVEST" in et_s or "RESOURCE_DEPLETED" in et_s)
            ):
                actor = str(getattr(ev, "actor_id", "") or "")
                pos = getattr(ev, "position", None)
                if pos is not None:
                    cell = (int(pos[0]), int(pos[1]))
                    ready = refill_tick_at_or_after(tick + 1)
                    self.strat.harvested_until[cell] = ready
                    ch = chunk_of(cell)
                    self.strat.chunk_next_refill[ch] = ready
                    self.strat.chunk_anchor[ch] = cell
                    if actor:
                        self.strat.resource_cooldowns[(actor, cell)] = tick + RESOURCE_COOLDOWN_TICKS
                        self.strat.worker_tasks.pop(actor, None)
                elif actor:
                    self.strat.worker_tasks.pop(actor, None)

        # ---- 汇总视野 ----
        visible_resources = set(tuple(c) for c in turn.resource_cells)
        workers = [
            {
                "id": str(w.id),
                "pos": (w.position[0], w.position[1]),
                "cargo": w.cargo,
                "visible_resources": visible_resources,
            }
            for w in turn.workers
        ]
        vanguards = [
            {"id": str(v.id), "pos": (v.position[0], v.position[1])}
            for v in turn.vanguards
        ]
        enemies = [
            {"id": str(e.id), "pos": (e.position[0], e.position[1]),
             "unit_type": getattr(e, "unit_type", None),
             "owner": getattr(e, "owner_username", None)}
            for e in turn.visible_enemies
        ]
        obstacles = set(self.mem.obstacles)
        # 本 Tick 已被占用/计划占用的格子。Core 是占位实体，敌人也占格
        occupied = {core_pos} if core_pos else set()
        for e in enemies:
            occupied.add(e["pos"])
        for wdict in workers:
            occupied.add(wdict["pos"])
        for vdict in vanguards:
            occupied.add(vdict["pos"])
        if core is None:
            return

        # ---- 敌方感知：按类型分级威胁 + 基地记忆 ----
        threat_cells, enemy_zones = enemy_threat_cells(enemies, obstacles)
        # 记忆中的敌方 Core（含历史视野）也纳入路线规避圈
        for cell in self.mem.enemy_cores:
            x, y = cell
            r = ENEMY_CORE_ZONE_RADIUS
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) + abs(dy) <= r:
                        enemy_zones.add((x + dx, y + dy))
        # 视野内敌方 Core 记入长期记忆；搬迁旧位由视野校正清除
        enemy_core_obs = [(e["pos"], e.get("owner")) for e in enemies if e["unit_type"] is None]
        new_bases = self.mem.observe_enemies(tick, enemy_core_obs, visible_cells=visible_cells)
        if new_bases:
            log.info("tick %s: 发现 %s 个敌方基地：%s", tick, new_bases,
                     [(pos, owner) for pos, owner in enemy_core_obs])
        beacon = getattr(turn, "beacon", None)
        if beacon is not None:
            status = getattr(beacon, "status", None)
            if getattr(status, "name", status) == "GROUND":
                log.info("tick %s: Champion Beacon 落地于 %s（坐标全服公开）",
                         tick, tuple(beacon.position))

        # ---- 资源分配 ----
        resource_cells = [tuple(c) for c in turn.resource_cells]
        for cell in resource_cells:
            ch = chunk_of(cell)
            self.strat.chunk_anchor[ch] = cell
            self.strat.harvested_until.pop(cell, None)
        # 迷雾里仍有效的记忆点（过期/墓碑除外），排除障碍
        for cell in self.mem.known_resources(tick, max_age=RESOURCE_MEMORY_TTL):
            if cell not in resource_cells and cell not in obstacles:
                if self.strat.harvested_until.get(cell, 0) <= tick:
                    resource_cells.append(cell)
        assignment = assign_resources(
            workers, resource_cells, obstacles, self.strat.worker_tasks,
            tick=tick, cooldowns=self.strat.resource_cooldowns,
            last_seen=self.mem.resource_seen,
            harvested_until=self.strat.harvested_until,
            progress=self.strat.harvest_progress,
        )

        # 没有资源任务的 Worker：近场逐区块扫掠（发现资源点的主要手段），
        # 卡住 SCOUT_STALL_TICKS 或到达停留点 → 前进到下一个扫描点。
        # 敌方基地圈内的格子不当目标。
        assign_explore_targets(
            workers, assignment, core_pos, self.strat, tick, obstacles=obstacles,
            sweep=self.pcfg["enable_chunk_sweep"], avoid_zones=enemy_zones,
        )

        # ---- Worker 行动（单 Worker 异常隔离，绝不阻塞整 Tick 提交）----
        self._run_workers(turn, workers, core_pos, assignment, obstacles, occupied,
                          threat_cells, enemy_zones, tick)

        # ---- Vanguard 行动 ----
        for v, vdict in zip(turn.vanguards, vanguards):
            try:
                action, args = decide_vanguard(vdict, core_pos, enemies, obstacles, occupied)
                self._apply_vanguard(v, action, args, occupied)
            except Exception:
                log.exception("vanguard %s 行动异常，本 Tick 等待", vdict["id"][:8])
                try:
                    v.wait()
                except Exception:
                    log.exception("vanguard %s 等待失败", vdict["id"][:8])

        # ---- Core 行动：优先补 Worker，够数后补 Vanguard ----
        self._decide_core(turn, core, len(workers), len(vanguards))

        turn.submit()
        self._log_progress(tick, turn)
        self._maybe_log_route_stats(tick)

    # ---------- 指令翻译 ----------
    def _run_workers(self, turn, workers, core_pos, assignment, obstacles, occupied,
                     threat_cells, enemy_zones, tick) -> None:
        """逐 Worker 决策与执行；单个 Worker 的异常只影响自己（wait），不阻塞提交。"""
        for w, wdict in zip(turn.workers, workers):
            try:
                action, args = decide_worker(
                    wdict, core_pos, assignment, obstacles, occupied, threat_cells,
                    planner=self.planner, threat_zones=enemy_zones,
                )
                log.info(
                    "tick %s: worker %s @%s cargo=%s -> %s %s (target=%s explore=%s)",
                    tick, wdict["id"][:8], wdict["pos"], wdict["cargo"],
                    action, args, assignment.get(wdict["id"]),
                    wdict.get("explore_target"),
                )
            except Exception:
                log.exception("worker %s 决策异常，本 Tick 等待", wdict["id"][:8])
                action, args = "wait", ()
            self._apply_worker(w, action, args, occupied)
            try:
                self._handle_route_result(wdict, assignment, tick)
            except Exception:
                log.exception("worker %s 路线结果处理异常", wdict["id"][:8])
            # 决策前位置记入 last_pos（给下一 Tick 禁止回头）。
            # stall：本 Tick 发出的动作不是 move，视为停在原地。
            if action != "move":
                self.strat.stall_count[wdict["id"]] = self.strat.stall_count.get(wdict["id"], 0) + 1
            else:
                self.strat.stall_count[wdict["id"]] = 0
            self.strat.last_pos[wdict["id"]] = wdict["pos"]
        # 清理失效 Worker 的规划内存态（死亡/重连后 ID 变化）
        if self.planner is not None:
            self.planner.prune_workers({wd["id"] for wd in workers})
        # 冷热区：长期未更新区块丢弃详细连通分量
        if self.mem.chunk_index is not None:
            self.mem.chunk_index.prune_components(tick)

    def _handle_route_result(self, wdict: dict, assignment: dict, tick: int) -> None:
        """规划器确认目标不可达时，沿用现有冷却/任务清理与航点放弃规则。"""
        if self.planner is None:
            return
        result = self.planner.last_results.get(wdict["id"])
        if result is None or result.status != "BLOCKED":
            return
        target = assignment.get(wdict["id"])
        if target is not None and wdict["cargo"] == 0:
            # 与 assign_resources 的无进展冷却同一机制，只是确认不可达时提前触发
            self.strat.resource_cooldowns[(wdict["id"], target)] = tick + RESOURCE_COOLDOWN_TICKS
            self.strat.worker_tasks.pop(wdict["id"], None)
            self.strat.harvest_progress.pop(wdict["id"], None)
            self.route_stats["cooldowns"] = self.route_stats.get("cooldowns", 0) + 1
            log.info("tick %s: worker %s 目标 %s 确认不可达，冷却至 tick %s",
                     tick, wdict["id"][:8], target, tick + RESOURCE_COOLDOWN_TICKS)
            return
        # 侦察航点确认被已知障碍封死且无前沿：记录航点并放弃，下一 Tick 换目标
        explore = wdict.get("explore_target")
        if explore is not None:
            self.strat.waypoint_last_seen[explore] = tick
            self.strat.explore_targets.pop(wdict["id"], None)
            log.info("tick %s: worker %s 侦察航点 %s 确认不可达，放弃",
                     tick, wdict["id"][:8], explore)

    def _apply_worker(self, w, action: str, args: tuple, occupied: set) -> None:
        pos = (w.position[0], w.position[1])
        try:
            if action == "move":
                d = args[0]
                nx, ny = pos[0] + DELTA[d][0], pos[1] + DELTA[d][1]
                occupied.add((nx, ny))
                w.move(Direction(d))
            elif action == "harvest":
                w.harvest()
            elif action == "deposit":
                w.deposit()
                # 交付后任务重置，下回合重新分配
                self.strat.worker_tasks.pop(str(w.id), None)
            elif action == "wait":
                w.wait()
        except Exception:
            log.exception("worker %s 执行 %s 失败", w.id, action)

    def _apply_vanguard(self, v, action: str, args: tuple, occupied: set) -> None:
        pos = (v.position[0], v.position[1])
        try:
            if action == "sweep":
                v.sweep(Direction(args[0]))
            elif action == "move":
                d = args[0]
                nx, ny = pos[0] + DELTA[d][0], pos[1] + DELTA[d][1]
                occupied.add((nx, ny))
                v.move(Direction(d))
            else:
                v.wait()
        except Exception:
            log.exception("vanguard %s 执行 %s 失败", v.id, action)

    def _decide_core(self, turn, core, n_workers: int, n_vanguards: int) -> None:
        try:
            from arena_hero import CoreState
            if core.view.state != CoreState.NORMAL:
                return  # 迁移中不能生产
            # 攒钱模式：暂停一切生产；设置了目标库存时，达标后永久恢复生产
            if self.pcfg["hoard_mode"] and not self._hoard_released:
                target = self.pcfg["hoard_until_resources"]
                if target > 0 and turn.resources >= target:
                    self._hoard_released = True
                    log.info("tick %s: 攒钱目标达成（%s/%s），恢复生产",
                             turn.tick, turn.resources, target)
                else:
                    if not self._hoard_announced:
                        self._hoard_announced = True
                        log.info("tick %s: 攒钱模式生效，暂停生产（目标库存 %s）",
                                 turn.tick, target if target > 0 else "不限")
                    return
            reserve = self.pcfg.get("min_spawn_reserve", 0)
            # min_spawn_reserve：只在 resources - price ≥ reserve 时才生产，保住底仓
            if n_workers < self.cfg["max_workers"]:
                price = unit_cost(UnitType.WORKER, turn.state.population)
                if turn.resources >= price + reserve:
                    core.spawn(UnitType.WORKER)
                    log.info("tick %s: 生产 Worker（%s/%s），价格 %s", turn.tick, n_workers + 1, self.cfg["max_workers"], price)
                    return
            if n_vanguards < self.cfg["max_vanguards"] and n_workers >= 3:
                price = unit_cost(UnitType.VANGUARD, turn.state.population)
                if turn.resources >= price + reserve:
                    core.spawn(UnitType.VANGUARD)
                    log.info("tick %s: 生产 Vanguard 自卫（%s/%s），价格 %s", turn.tick, n_vanguards + 1, self.cfg["max_vanguards"], price)
        except Exception:
            log.exception("core 动作失败")

    # ---------- 日志 ----------
    def _maybe_log_route_stats(self, tick: int) -> None:
        """每 100 Tick 汇报一次路线统计，便于比较规划失败/停滞/动作量。"""
        if tick - self.last_stats_log_tick < 100:
            return
        self.last_stats_log_tick = tick
        if self.planner is not None:
            self.route_stats.update(self.planner.stats_snapshot())
        log.info("tick %s: 路线统计 %s", tick, self.route_stats)

    def _log_progress(self, tick: int, turn) -> None:
        cargos = sum(w.cargo for w in turn.workers)
        events_types = [e.event_type for e in turn.events[:6]]
        log.info(
            "tick %s: 资源 %s/%s 人口 %s Worker=%s Vanguard=%s 视野资源=%s 记忆资源=%s 敌人=%s cargo总和=%s 事件=%s",
            tick, turn.resources, turn.resource_capacity,
            turn.state.population, len(turn.workers), len(turn.vanguards),
            len(turn.resource_cells), len(self.mem.resource_seen),
            len(turn.visible_enemies), cargos, events_types,
        )


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.get("log_level", "INFO"))
    log.info("Agent 启动（经济流，Worker 上限 %s，Vanguard 上限 %s）", cfg["max_workers"], cfg["max_vanguards"])
    agent = Agent(cfg)
    while True:
        try:
            agent.run()
            log.warning("连接正常关闭，5 秒后重连…")
        except KeyboardInterrupt:
            log.info("手动停止")
            agent.mem.save()
            break
        except Exception:
            log.exception("连接异常断开，5 秒后重连…")
        time.sleep(5)


if __name__ == "__main__":
    main()
