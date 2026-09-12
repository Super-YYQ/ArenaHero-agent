# 路线规划优化 — 变更记录

对应计划:`docs/superpowers/specs/2026-09-12-route-planning-development-plan.md`
本文档记录每个已完成 Phase 的简短变更、移动调用点清单与基准结果。

## 追加:近场逐区块扫掠(2026-09-12 下午)

**背景**:该世界 Core 位于 ring≈94,配额公式 `max(2, floor(128/(8+ring)))`
下每区块只有 2 个资源点;旧侦察航点(4 环 64 点)是稀疏线采样,3 天日志
仅 24 次成功采集,库存常年 0-4/35,经济饿死。

**改动**(`enable_chunk_sweep`,默认开启):

- `chunk_sweep_points()`:区块内 5 条扫描线(行距 7,Worker 视野 ±3 无缝覆盖
  全部 32 行)× 5 个停留点;行走沿线即覆盖整个区块。
- `assign_explore_targets(sweep=True)`:空闲 Worker 按"到期复查区块优先 →
  上次扫掠时间最旧优先"认领 Core 周边 5×5 区块,逐点推进扫描线;
  全局游标 `sweep_cursor` 支持多 Worker 接力,`chunk_last_swept` 驱动轮转,
  `sweep_claims` 防止同 Tick 重复认领;扫掠与采集循环闭环:
  扫掠发现点 → `assign_resources` 分配采集 → 补充复查到期 → 再扫再采。
- `sweep=False` 保留旧的稀疏环形航点行为(回归兼容)。

**效果**:25 个邻近区块理论上限约 44 点/分钟;扫掠一轮(7 Worker)约 2-3 小时,
相比此前 3 天 24 次采集是数量级提升。朝原点方向(ring 更低、配额更高)的
远期扩张待经济转正后另行规划。

## 移动调用点清单(Phase 0 记录)

### strategy.py
| 调用点 | 场景 | 说明 |
|---|---|---|
| `decide_worker()` 受威胁撤退 | `step_direction(pos, core_pos, …)` | Worker 在威胁格内向 Core 撤退 |
| `decide_worker()` 满载回 Core | `step_direction(pos, core_pos, …)` | occupied 需剔除 Core 格(交付要走进去) |
| `decide_worker()` 侦察移动 | `step_direction(pos, explore, …)` | 无资源任务时走向探索航点 |
| `decide_worker()` 侦察 fallback | `step_direction(pos, away, …)` | 沿远离 Core 的轴再试一次 |
| `decide_worker()` 去资源 | `step_direction(pos, target, …)` | 走向匈牙利分配的资源格 |
| `decide_vanguard()` 迎击/回防 | `step_direction(…)` ×2 | Vanguard 自卫移动 |

### agent.py
| 调用点 | 说明 |
|---|---|
| `_apply_worker()` | 把 `move` 翻译为 `w.move(Direction(d))`,并把目的地加入 `occupied`(本 Tick 预约占用) |
| `_apply_vanguard()` | 同上,针对 Vanguard |
| `handle_turn()` | 每 Tick 组装 `obstacles` / `occupied` / `threat_cells`,顺序调度 Worker → Vanguard → Core → `turn.submit()` |

### memory.py
无直接移动调用;`observe()` 提供障碍记忆,`known_resources()` 提供资源目标候选。

## Phase 0:基线和保护网

- 新增 `map_fixtures.py`:空旷、直墙、L 形、凹形(U 口袋)、一格瓶颈、封闭区域、负坐标象限、跨区块长途共 8 个固定地图夹具,附 `bfs_oracle()` 正确性基准。
- `test_strategy.py` 新增夹具自检、`step_direction` 简单场景回归(42/42 通过)。
- 增加 `load_tests` 钩子,`python -m unittest discover` 现在可执行全部函数式测试。
- `agent.py` 增加 `route_stats` 统计字段与每 100 Tick 的统计日志,供后续阶段比较规划失败、停滞与动作量。
- 基线:`python test_strategy.py` → 42/42 passed;`python -m unittest discover` → OK。

## Phase 1:独立实现 A* 和明确结果状态

- 新增 `pathfinding.py`:`PathResult` / `PathRequest`、四方向邻居、确定性 tie-break `(f, h, y, x)` 的 A*、节点预算(耗尽返回 `BUDGET_EXHAUSTED` 或 `FRONTIER`,绝不误报 `BLOCKED`)、`forbidden` 软约束(被挡时允许一次带 forbidden 的重搜)、`allow_goal_occupied`(Core 例外)、unknown 惩罚经 `is_known` 回调注入。
- `step_direction` / `DELTA` / `DIRECTIONS` / `neighbors` / `CHUNK_SIZE` 移至 `pathfinding.py`,`strategy.py` 原地 re-export,现有导入不受影响。
- 新增 `test_pathfinding.py`:空旷最短路、单墙绕行、L 形、凹形、瓶颈、封闭 BLOCKED、预算截断不误报、起点即目标、负坐标、目标占用、forbidden 软约束、方向-坐标一致性。
- 不修改 `strategy.py` 现有行为。

## Phase 2:统一 Worker 移动并逐步接入

- `pathfinding.py` 新增 `HybridPathPlanner`:快速层(曼哈顿 ≤ `fast_path_distance` 且无近期失败记录时沿用贪心)+ A* 层;每 Tick 总预算 `total_path_budget`,预算耗尽时剩余 Worker 只能走快速层或 wait。
- `WorkerRoute` 路线游标(`next_index` / `map_version` / `blocked_ticks`)进入 `StrategyState.routes`;`StrategyState` 增加 `route_cache` 与 `map_version`。
- `decide_worker()` 增加 `planner` 参数:受威胁撤退、满载回 Core、去资源三类移动统一走规划器(回 Core `allow_goal_occupied=True`);侦察移动本阶段保持旧逻辑;`planner=None` 时行为与旧版完全一致。
- BLOCKED 沿用现有冷却/任务清理规则(由 agent 层执行),不新增隐式冷却。
- `agent.py`:构造规划器、每 Tick `begin_tick(map_version)`、按顺序合并本 Tick 预约占用、处理 BLOCKED 冷却;`enable_path_planner=false` 时回退旧实现。
- `config.example.json` 增加规划器配置项;旧 config 缺字段时使用默认值。

## Phase 3:路线缓存与动态占用校验

- `RouteCacheKey(start, goal, map_version, planner_mode)`;动态 `occupied` 不进缓存键。
- `RouteCache`:有序字典 LRU,容量 `route_cache_size`(默认 256),命中/未命中/失效计数。
- 缓存命中后仅验证首步(非障碍/非占用/非 forbidden);首步被动态占用时以 `planner_mode="replan"` 局部重规划,不污染静态缓存。
- 障碍版本(`MapMemory.obstacle_revision`)递增时全局失效缓存并清除 Worker 路线游标。
- 路线失败记录与快速层失败计数均有容量上限。

## Phase 4:BFS 前沿和渐进降级

- `bfs_frontier()`:带完整前驱链的有限 BFS(仅扩展已知格或半径上限内),`approach` 模式找更接近目标的可达前沿,`explore` 模式找未知边界前沿。
- 前沿评分 `4*Manhattan(cell,goal) + distance_from_start + unknown_boundary_penalty + threat_penalty`,tie-break `(score, y, x)`。
- A* 预算耗尽时自动尝试 approach 前沿;确认 BLOCKED 时仅对侦察类目标追加 explore 前沿,资源/回 Core 目标交给现有冷却。
- 预算不足绝不触发资源冷却;确认不可达才进入目标失败处理(有测试覆盖)。

## Phase 5:增量区块导航摘要

- `ChunkNavigationSummary` / `ChunkNavigationIndex`:按 32×32 区块维护已知格、已知障碍、四边边界门户、已知格连通分量与 `revision`;内容实际变化才重算。
- 复用 `chunk_of()` 负坐标语义;跨区块规划:曼哈顿区块走廊 → 逐段 A* 到下一门户,失败退回普通 A*/前沿;未知区块只标记"需要探索"。
- 冷热区:长期未更新的区块丢弃详细连通分量,保留边界摘要,再次活跃时惰性重算。
- `memory.json` 升级 `schema_version=2`:新增 `obstacle_revision`、`chunk_navigation`;旧文件可加载,损坏导航字段只丢摘要不丢障碍/资源;保存保持临时文件替换。

## Phase 6:侦察接入和统一观测反馈

- 侦察航点作为普通路线目标进入规划器(`enable_scout` 默认开启);到达航点/前沿推进/确认被挡分别更新 `waypoint_last_seen` 与进度状态。
- BLOCKED 的侦察目标先尝试 explore 前沿,确实无前沿才记录航点时间并换目标。
- 旧"沿远离 Core 的轴再试一次" fallback 仅在规划器关闭时保留。
- 观测顺序固定:观察 → 更新 MapMemory → 更新 ChunkNavigationIndex → 规划 → 提交动作。

## Phase 7:性能、观测和稳定性收尾

- `benchmark_pathfinding.py`:9 张固定地图 × 基线贪心 vs 混合规划器,输出到达率、无进展 Tick、规划失败误判、单步决策耗时、多 Worker 单 Tick 吞吐、缓存命中行为。
- Worker/Vanguard 决策循环逐单元异常隔离:单 Worker 规划异常时该 Worker wait,其余 Worker 与 `turn.submit()` 不受影响。
- `HybridPathPlanner.prune_workers()`:每 Tick 清理失效 Worker ID 的路线/失败/受阻记录;失败目标与缓存均有容量上限。
- `ChunkNavigationIndex.prune_components()`:冷区(512 Tick 未更新)丢弃详细连通分量、保留边界摘要,内容再次变化时惰性重建。
- 重连降级:静态地图与导航摘要从 `memory.json` 恢复,路线游标全部丢弃重新规划(测试覆盖);损坏 `memory.json` 导航字段只丢摘要。
- 日志不含 API Key;真实 API 环境的联网验证按计划要求不作为测试依赖执行。

## 基准结果(离线固定地图,2026-09-12,Windows/Python 3.12)

复现命令:`python benchmark_pathfinding.py`(确定性,无网络)。

| 指标 | 基线贪心 | 混合规划器 | 要求 |
|---|---|---|---|
| 可达地图到达率 | 7/8(88%) | **8/8(100%)** | ≥90% ✓ |
| 可达地图平均无进展 Tick | 53.0 | **4.8** | 相对降低 ≥50%(实际 -91%)✓ |
| 预算不足误判为不可达 | — | **0** | =0 ✓ |
| 简单直线单步决策 | 1.8 µs | 7.8 µs(快速层) | <1 ms/Worker ✓ |
| 10 Worker 单 Tick 最坏耗时 | — | 近距 3.0 ms / 跨区块 6.8 ms | 稳定完成 ✓ |
| 同一路线二走 A* 展开 | — | 0 次(缓存命中) | 不重复展开 ✓ |

关键用例 `greedy_trap`(散乱墙构成振荡环):基线贪心 400 Tick 内无法到达(386 个无进展 Tick),规划器 21 Tick 到达(BFS 最短 20)。
耗时数字为开发机参考值,用于量级比较;服务端命令窗口内余量充足。
