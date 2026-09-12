# -*- coding: utf-8 -*-
"""地图记忆：障碍永久有效，资源点观察会过期（文档《地图与视野·探索记忆》）。

schema_version=2 起新增 obstacle_revision 与 chunk_navigation 字段；
旧版文件（无 schema_version）可正常加载；损坏的导航字段只丢摘要，
不丢障碍与资源记忆。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pathfinding import ChunkNavigationIndex

DEFAULT_PATH = Path(__file__).with_name("memory.json")
SCHEMA_VERSION = 2


class MapMemory:
    """跨 Tick 保存探索所得。障碍与已知资源点分开管理。"""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self.obstacles: set[tuple[int, int]] = set()
        # resource cell -> 最后一次见到的 tick；迷雾里可能已被采掉
        self.resource_seen: dict[tuple[int, int], int] = {}
        # 自己 Core 的已知位置（Core 可能迁移，不是永久的）
        self.core_position: tuple[int, int] | None = None
        # 障碍版本号：新增永久障碍时单调递增，用于路线缓存失效
        self.obstacle_revision = 0
        # 区块导航摘要（不作为可信执行计划；重连后只恢复静态摘要）
        self.chunk_index = ChunkNavigationIndex()
        self._dirty = False
        self._last_save = 0.0
        self._load()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            # 整个文件损坏就丢弃重建，地图可以重新探索
            self.obstacles = set()
            self.resource_seen = {}
            self.core_position = None
            return
        try:
            self.obstacles = {tuple(p) for p in data.get("obstacles", [])}
            self.obstacle_revision = int(data.get("obstacle_revision", 0) or 0)
            # 保存格式是 "x,y"；但历史版本可能存过列表或坏键，统一校验
            self.resource_seen = {}
            for k, v in data.get("resources", {}).items():
                try:
                    if isinstance(k, str):
                        x, y = k.split(",")
                        if x == "" or y == "":
                            continue
                        cell = (int(x), int(y))
                    elif isinstance(k, (list, tuple)):
                        cell = (int(k[0]), int(k[1]))
                    else:
                        continue
                    self.resource_seen[cell] = int(v)
                except (ValueError, TypeError, IndexError):
                    continue
            core = data.get("core_position")
            self.core_position = tuple(core) if core else None
        except (ValueError, TypeError, OSError):
            self.obstacles = set()
            self.resource_seen = {}
            self.core_position = None
        # 导航摘要独立解析：损坏只丢摘要，绝不影响障碍与资源记忆
        nav = data.get("chunk_navigation")
        if nav is not None:
            try:
                self.chunk_index.from_dict(nav)
            except Exception:
                self.chunk_index = ChunkNavigationIndex()

    def maybe_save(self, interval: float = 30.0) -> None:
        now = time.monotonic()
        if not (self._dirty and now - self._last_save >= interval):
            return
        self.save()

    def save(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "obstacles": [list(p) for p in self.obstacles],
            "resources": {f"{k[0]},{k[1]}": v for k, v in self.resource_seen.items()},
            "core_position": list(self.core_position) if self.core_position else None,
            "obstacle_revision": self.obstacle_revision,
            "chunk_navigation": self.chunk_index.to_dict(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False
        self._last_save = time.monotonic()

    # ---------- 更新 ----------
    def observe(self, tick: int, obstacle_cells, resource_cells, core_pos, visible_cells=None) -> None:
        """把本 Tick 视野合并进记忆。

        visible_cells: 当前可见的所有格子（含空地）。传入后，视野内已消失的资源点
        会被立刻从记忆中删除，避免 Worker 反复 harvest 一个已被采空的点。
        """
        before = len(self.obstacles)
        self.obstacles |= {tuple(c) for c in obstacle_cells}
        if len(self.obstacles) != before:
            # 新增永久障碍：版本号递增，触发路线缓存失效
            self.obstacle_revision += 1
            self._dirty = True
        # 区块导航摘要：先更新障碍记忆，再更新索引（计划 §Phase6 观测顺序）
        if self.chunk_index is not None:
            cells = {tuple(c) for c in obstacle_cells}
            if visible_cells is not None:
                cells |= {tuple(c) for c in visible_cells}
            else:
                cells |= set(self.obstacles)
            if self.chunk_index.observe(cells, self.obstacles, now_tick=tick) > 0:
                self._dirty = True
        for cell in resource_cells:
            cell = tuple(cell)
            if cell not in self.resource_seen or self.resource_seen[cell] != tick:
                self._dirty = True
            self.resource_seen[cell] = tick
        if core_pos and tuple(core_pos) != self.core_position:
            self.core_position = tuple(core_pos)
            self._dirty = True
        if visible_cells is not None:
            vis = {tuple(c) for c in visible_cells}
            res = {tuple(c) for c in resource_cells}
            for cell in list(self.resource_seen):
                if cell in vis and cell not in res:
                    del self.resource_seen[cell]
                    self._dirty = True

    # ---------- 查询 ----------
    def known_resources(self, tick: int, max_age: int = 64) -> list[tuple[int, int]]:
        """返回最近见过的资源点，按离 Core 距离由近到远。迷雾里保留有界记忆。"""
        if not self.core_position:
            return list(self.resource_seen)
        cx, cy = self.core_position
        fresh = {c for c, t in self.resource_seen.items() if tick - t <= max_age}
        return sorted(fresh, key=lambda c: abs(c[0] - cx) + abs(c[1] - cy))


