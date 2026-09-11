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
from strategy import (
    DELTA,
    RESOURCE_COOLDOWN_TICKS,
    StrategyState,
    assign_explore_targets,
    assign_resources,
    chunk_of,
    decide_vanguard,
    decide_worker,
)

HERE = Path(__file__).parent
log = logging.getLogger("agent")


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
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.mem = MapMemory()
        self.strat = StrategyState()
        self.last_resources = None
        self.last_log_tick = 0

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
        # 当前视野覆盖的格子：己方对象视野并集（曼哈顿半径，障碍遮挡简化忽略）
        visible_cells = set()
        vision = {"CORE": 5, "WORKER": 3, "VANGUARD": 4, "RANGER": 5}
        if core:
            cx, cy = core.position[0], core.position[1]
            for dx in range(-5, 6):
                for dy in range(-5, 6):
                    if abs(dx) + abs(dy) <= 5:
                        visible_cells.add((cx + dx, cy + dy))
        for u in list(turn.workers) + list(turn.vanguards) + list(turn.rangers):
            r = 3 if u in turn.workers else (4 if u in turn.vanguards else 5)
            ux, uy = u.position[0], u.position[1]
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) + abs(dy) <= r:
                        visible_cells.add((ux + dx, uy + dy))
        self.mem.observe(
            tick,
            turn.obstacle_cells,
            turn.resource_cells,
            core_pos,
            visible_cells=visible_cells,
        )
        self.mem.maybe_save()
        # 记录本 Tick 视野覆盖到的 32×32 区块，供侦察选「最久未见」
        for cell in visible_cells:
            self.strat.chunk_last_seen[chunk_of(cell)] = tick

        # HARVEST_FAILED：该 (worker, 失败格) 进入冷却，避免反复去采空点
        for ev in turn.events:
            et = getattr(ev, "event_type", None)
            if et in ("HARVEST_FAILED", "RESOURCE_DEPLETED") or (isinstance(et, str) and "HARVEST_FAILED" in et):
                actor = str(getattr(ev, "actor_id", "") or "")
                pos = getattr(ev, "position", None)
                if actor and pos is not None:
                    cell = (int(pos[0]), int(pos[1]))
                    self.strat.resource_cooldowns[(actor, cell)] = tick + RESOURCE_COOLDOWN_TICKS
                    # 清掉卡住的任务
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
            {"id": str(e.id), "pos": (e.position[0], e.position[1]), "unit_type": getattr(e, "unit_type", None)}
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

        # 敌人所在格及邻格视为威胁
        threat_cells = set()
        for e in enemies:
            x, y = e["pos"]
            threat_cells.add((x, y))
            for dx, dy in DELTA.values():
                threat_cells.add((x + dx, y + dy))

        # ---- 资源分配 ----
        resource_cells = [tuple(c) for c in turn.resource_cells]
        # 补上记忆中较新的资源点（视野外），排除障碍格（旧记忆可能过期冲突）
        for cell in self.mem.known_resources(tick):
            if cell not in resource_cells and cell not in obstacles:
                resource_cells.append(cell)
        assignment = assign_resources(
            workers, resource_cells, obstacles, self.strat.worker_tasks,
            tick=tick, cooldowns=self.strat.resource_cooldowns,
        )

        # 没有资源任务的 Worker：环形侦察，目标选最久没扫过的区块。
        # 卡住 SCOUT_STALL_TICKS 或到达目标 → 换下一个。
        assign_explore_targets(
            workers, assignment, core_pos, self.strat, tick, obstacles=obstacles,
        )

        # ---- Worker 行动 ----
        for w, wdict in zip(turn.workers, workers):
            action, args = decide_worker(
                wdict, core_pos, assignment, obstacles, occupied, threat_cells
            )
            log.info(
                "tick %s: worker %s @%s cargo=%s -> %s %s (target=%s explore=%s)",
                tick, wdict["id"][:8], wdict["pos"], wdict["cargo"],
                action, args, assignment.get(wdict["id"]),
                wdict.get("explore_target"),
            )
            self._apply_worker(w, action, args, occupied)
            # 决策前位置记入 last_pos（给下一 Tick 禁止回头）。
            # stall：本 Tick 发出的动作不是 move，视为停在原地。
            if action != "move":
                self.strat.stall_count[wdict["id"]] = self.strat.stall_count.get(wdict["id"], 0) + 1
            else:
                self.strat.stall_count[wdict["id"]] = 0
            self.strat.last_pos[wdict["id"]] = wdict["pos"]

        # ---- Vanguard 行动 ----
        for v, vdict in zip(turn.vanguards, vanguards):
            action, args = decide_vanguard(vdict, core_pos, enemies, obstacles, occupied)
            self._apply_vanguard(v, action, args, occupied)

        # ---- Core 行动：优先补 Worker，够数后补 Vanguard ----
        self._decide_core(turn, core, len(workers), len(vanguards))

        turn.submit()
        self._log_progress(tick, turn)

    # ---------- 指令翻译 ----------
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
            if n_workers < self.cfg["max_workers"]:
                price = unit_cost(UnitType.WORKER, turn.state.population)
                if turn.resources >= price:
                    core.spawn(UnitType.WORKER)
                    log.info("tick %s: 生产 Worker（%s/%s），价格 %s", turn.tick, n_workers + 1, self.cfg["max_workers"], price)
                    return
            if n_vanguards < self.cfg["max_vanguards"] and n_workers >= 3:
                price = unit_cost(UnitType.VANGUARD, turn.state.population)
                if turn.resources >= price:
                    core.spawn(UnitType.VANGUARD)
                    log.info("tick %s: 生产 Vanguard 自卫（%s/%s），价格 %s", turn.tick, n_vanguards + 1, self.cfg["max_vanguards"], price)
        except Exception:
            log.exception("core 动作失败")

    # ---------- 日志 ----------
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
