# -*- coding: utf-8 -*-
"""固定地图测试夹具：离线路线规划回归与基准共用。

每个夹具返回 (obstacles, start, goal)：
    obstacles: set[tuple[int, int]] 永久障碍格
    start / goal: 起点与目标格
所有地图不依赖网络，几何手工构造并保证可达性（除 enclosed）。
"""
from __future__ import annotations


def empty_map() -> tuple[set, tuple, tuple]:
    """空旷地图：直线可达，用于验证快速层行为不变。"""
    return set(), (0, 0), (6, 3)


def straight_wall_map() -> tuple[set, tuple, tuple]:
    """竖直长墙 x=4（y ∈ [-4, 4]），必须绕过墙的上下端。"""
    obstacles = {(4, y) for y in range(-4, 5)}
    return obstacles, (0, 0), (8, 0)


def l_shape_map() -> tuple[set, tuple, tuple]:
    """L 形障碍：横臂 + 竖臂，目标在拐角后方。"""
    obstacles = {(x, 3) for x in range(0, 6)} | {(3, y) for y in range(3, 8)}
    return obstacles, (0, 0), (2, 6)


def concave_map() -> tuple[set, tuple, tuple]:
    """凹形（U 形口袋）：口袋口背对起点，贪心会在左墙前局部卡死。

    目标在口袋内部，唯一入口在口袋右侧 x=8。
    """
    obstacles = set()
    obstacles |= {(5, y) for y in (-1, 0, 1)}        # 左墙
    obstacles |= {(x, -2) for x in (5, 6, 7)}        # 上壁
    obstacles |= {(x, 2) for x in (5, 6, 7)}         # 下壁
    return obstacles, (0, 0), (6, 0)


def bottleneck_map() -> tuple[set, tuple, tuple]:
    """一格瓶颈：x=4 的墙只留 (4, 0) 一个通道。"""
    obstacles = {(4, y) for y in range(-4, 5) if y != 0}
    return obstacles, (0, 0), (8, 0)


def enclosed_map() -> tuple[set, tuple, tuple]:
    """完全封闭：目标被已知障碍围死，规划器必须返回 BLOCKED。"""
    obstacles = {(7, -1), (8, -1), (9, -1), (7, 0), (9, 0), (7, 1), (8, 1), (9, 1)}
    return obstacles, (0, 0), (8, 0)


def negative_quadrant_map() -> tuple[set, tuple, tuple]:
    """负坐标象限：路线全程在 x<0、y<0，跨越区块 (-1,-1) → (-2,-2)。"""
    obstacles = {(-8, y) for y in range(-10, 4) if y != -4}
    return obstacles, (-4, -4), (-14, -6)


def cross_chunk_map() -> tuple[set, tuple, tuple]:
    """跨区块长途：起点 (0,0)，目标 (40, 5)，中途两段短墙。"""
    obstacles = {(10, y) for y in range(-2, 3)} | {(25, y) for y in range(2, 8)}
    return obstacles, (0, 0), (40, 5)


FIXTURES = {
    "empty": empty_map,
    "straight_wall": straight_wall_map,
    "l_shape": l_shape_map,
    "concave": concave_map,
    "bottleneck": bottleneck_map,
    "enclosed": enclosed_map,
    "negative_quadrant": negative_quadrant_map,
    "cross_chunk": cross_chunk_map,
}


def get_fixture(name: str) -> tuple[set, tuple, tuple]:
    return FIXTURES[name]()


def bfs_oracle(obstacles: set, start: tuple, goal: tuple, margin: int = 2):
    """完整 BFS 正确性 oracle：夹具地图最短路径长度；不可达返回 None。

    搜索范围限制在 start/goal/障碍的包围盒外加 margin，保证有限终止；
    夹具几何都是局部的，盒外不可能存在必经通道。
    """
    xs = [start[0], goal[0]] + [c[0] for c in obstacles]
    ys = [start[1], goal[1]] + [c[1] for c in obstacles]
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin
    if start == goal:
        return 0
    from collections import deque

    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        (x, y), dist = queue.popleft()
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nxt = (x + dx, y + dy)
            if nxt in seen or nxt in obstacles:
                continue
            if not (x0 <= nxt[0] <= x1 and y0 <= nxt[1] <= y1):
                continue
            if nxt == goal:
                return dist + 1
            seen.add(nxt)
            queue.append((nxt, dist + 1))
    return None
