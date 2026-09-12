# Arena Hero Agent 路线规划优化开发计划

- 日期：2026-09-12
- 适用仓库：`ArenaHero-agent`
- 文档性质：可交给其他 AI 逐阶段执行的开发计划
- 目标：在不改变现有经济策略和服务端协议的前提下，把当前单步贪心移动升级为渐进式、可缓存、可降级的混合路线规划系统

## 1. 背景与结论

当前路线规划集中在 `strategy.py`：`step_direction()` 每个 Tick 只根据当前位置、目标、障碍和占用格选择下一步。侦察、采集和回 Core 都依赖这个函数。现有的 `last_pos`、停滞计数、无进展计数和目标冷却机制能够抑制两格振荡，但无法可靠处理以下情况：

- 目标被长墙、瓶颈或凹形障碍隔开时，局部贪心可能反复选错绕行方向；
- 已经发现的远距离资源点没有可复用的完整路线，每个 Tick 都重复做局部判断；
- 多个 Worker 同时移动时，动态占用会让静态路线临时失效；
- “搜索失败”与“当前搜索预算不足”没有严格区分，容易过早放弃本来可达的目标；
- 新探索区域的地图信息是渐进增加的，不能假定整张世界地图一开始已知。

本计划采用混合策略，但对复杂度做约束：

1. **快速层**：简单、近距离、无明显障碍时继续使用贪心单步。
2. **局部规划层**：遇到障碍、目标较远或无进展时，用有上限的 A* 规划一段路径。
3. **增量区块层**：随着障碍记忆扩展，维护区块摘要、边界门户和连通性，用于选择跨区块路由；不在未知地图上伪造精确距离场。
4. **渐进降级层**：A* 受限或失败时，用 BFS 找到“更接近目标的可达前沿”，沿前沿继续探索；只有确认当前已知区域不可达且没有合理前沿时才放弃。
5. **运行时动态避障**：静态路径缓存不包含其他 Unit 的永久承诺；每个 Tick 检查下一格，受阻就局部重规划。

不纳入本阶段：多资源点路线、主动进攻路径、Ranger 战术、完整 D* Lite/JPS、全地图精确 Flow Field、异步多线程规划、修改服务端协议。

## 2. 设计原则

### 2.1 路线规划与任务分配分离

`assign_resources()` 继续只负责“哪个 Worker 去哪个资源格”。它不负责判断路线是否可达，也不直接操作地图。路线规划器只接收起点、目标和地图快照，返回路线或下一步动作。

这样可以保持匈牙利分配的纯函数测试，并允许未来替换代价模型，而不影响资源分配逻辑。

### 2.2 静态地图与动态占用分离

- 永久障碍来自 `MapMemory.obstacles`，参与路径缓存键和连通性计算。
- Unit、Core、敌人属于动态占用，只参与当前 Tick 的下一步合法性检查。
- 缓存路线不能因为某个 Worker 临时站在格子上而整体失效。
- 当前 Tick 形成的计划占用仍需作为保守约束，避免多个 Worker 被同时安排进入同一格。

### 2.3 未知区域采用“乐观可通行、到达即校正”

当前地图只知道已观察区域。未知格不能被当作障碍，否则远距离目标永远无法规划；也不能声称未知格一定可通行。路线规划采用：

- 对未知格按普通可通行格计算，但增加可配置的未知惩罚；
- 到达视野范围后，用新观察到的障碍修正路线；
- 如果路线撞到新障碍，增加地图版本号并立即重规划；
- 只有已知障碍才用于“不可达”判断。

### 2.4 规划必须有预算

每个 Worker 每 Tick 的规划必须有节点展开上限和时间/工作量上限。推荐第一版只使用确定性的节点展开上限，避免不同机器性能造成行为差异：

- 快速层：常数时间；
- 局部 A*：默认最多展开 800 个节点；
- 前沿 BFS：默认最多展开 1,200 个节点；
- 每 Tick 所有 Worker 的总规划预算默认 5,000 个节点；
- 预算耗尽时返回部分结果或保守下一步，不阻塞 `turn.submit()`。

所有预算都应放在配置或常量中，测试中使用较小值验证边界行为。

## 3. 目标行为与验收指标

### 3.1 功能目标

1. 简单直线路径的动作结果与现有行为一致。
2. 单个已知障碍墙、L 形障碍、凹形障碍和一格瓶颈场景中，Worker 能找到可达目标，不因局部振荡提前放弃。
3. 回 Core 的路线与去资源路线使用同一套规划器，但允许 Core 目标特殊处理。
4. 侦察目标在未知区域仍能持续推进，遇到新障碍后能重新规划，而不是重复等待。
5. 一个 Tick 内多个 Worker 不会因为共用缓存而错误地沿用另一 Worker 的动态占用判断。
6. 目标确实位于已知不可达区域时，规划器返回明确的不可达状态，调用方再执行现有冷却/换目标逻辑。
7. 地图障碍增加后，受影响路径缓存自动失效；未受影响的缓存可以继续使用。
8. 断线重连后，静态地图和必要的规划元数据能够恢复；无法安全恢复的路径本身必须丢弃并重新规划。

### 3.2 建议验收指标

在离线固定地图测试集上记录以下指标，基线取当前 `step_direction()`：

- 可达目标到达率：复杂障碍场景提升到 90% 以上；
- 平均无进展 Tick：相对基线降低 50% 以上；
- 规划失败误判率：将“预算不足”误判为“不可达”的比例为 0；
- 简单直线路径额外计算开销：不超过当前策略的明显量级，目标为小于 1 ms/Worker；
- 10 个 Worker、5,000 节点总预算下，单 Tick 决策仍能在服务端命令窗口内稳定完成；
- 路线缓存命中时，不重复展开完整 A*。

性能指标需要在实际运行环境记录，不要在没有基准数据时宣称绝对毫秒数。

## 4. 目标模块结构

建议新增 `pathfinding.py`，让 `strategy.py` 保持任务状态机和策略编排职责：

```text
pathfinding.py
  - 网格基础类型和方向工具
  - PathStatus / PathResult
  - A* 局部规划
  - BFS 可达前沿
  - 路线缓存
  - ChunkNavigationIndex（增量区块摘要）
  - HybridPathPlanner

strategy.py
  - WorkerTask / StrategyState
  - 资源分配
  - 侦察目标分配
  - Worker/Vanguard 高层行为决策
  - 调用 HybridPathPlanner 获取下一步

memory.py
  - 障碍与资源记忆
  - 地图版本号或障碍版本号
  - 区块导航摘要的持久化字段

agent.py
  - 每 Tick 汇总地图变化
  - 更新规划器索引
  - 为每个 Worker 建立动态规划上下文
  - 执行动作并记录路线状态

test_pathfinding.py
  - 路径算法、缓存、预算、边界、动态占用测试

test_strategy.py
  - 保留高层策略回归测试
```

第一阶段也可以暂时把实现放入 `strategy.py`，但在加入缓存和区块摘要前必须拆出 `pathfinding.py`，避免继续扩大策略文件的职责。

## 5. 核心数据结构设计

### 5.1 路线结果

```python
@dataclass(frozen=True)
class PathResult:
    status: Literal["FOUND", "FRONTIER", "BLOCKED", "BUDGET_EXHAUSTED", "AT_TARGET"]
    steps: tuple[str, ...]
    endpoint: tuple[int, int] | None
    expanded: int
    cost: int
    map_version: int
    reason: str = ""
```

语义必须固定：

- `FOUND`：已找到目标路线；
- `FRONTIER`：没有在预算内找到目标，但找到更靠近目标的可达前沿路线；
- `BLOCKED`：在当前已知地图中确认没有可行路径；
- `BUDGET_EXHAUSTED`：搜索被预算截断，不能当成不可达；
- `AT_TARGET`：起点已是目标。

禁止使用 `None` 同时表达“到达、不可达、暂时没算完、下一步被动态占用”。

### 5.2 路线请求

```python
@dataclass(frozen=True)
class PathRequest:
    start: tuple[int, int]
    goal: tuple[int, int]
    obstacles: frozenset[tuple[int, int]]
    occupied: frozenset[tuple[int, int]]
    forbidden: frozenset[tuple[int, int]]
    unknown: frozenset[tuple[int, int]]
    allow_goal_occupied: bool = False
    max_expansions: int = 800
    unknown_penalty: int = 1
    threat_penalty: int = 0
```

如果当前调用方还没有可靠的 `unknown` 集合，第一版可将未知判断封装为 `is_known(cell)`，不要为了凑接口把所有无限网格显式展开成集合。

### 5.3 Worker 路线状态

向 `StrategyState` 增加：

```python
@dataclass
class WorkerRoute:
    target: tuple[int, int]
    steps: tuple[str, ...]
    next_index: int
    map_version: int
    planned_endpoint: tuple[int, int]
    status: str
    replans: int = 0
    blocked_ticks: int = 0
```

至少保留以下映射：

```python
routes: dict[str, WorkerRoute]
route_cache: dict[RouteCacheKey, PathResult]
map_version: int
```

路线状态不能持久化到 `memory.json` 作为可信执行计划。重连后允许丢弃所有路线，仅恢复静态地图和区块摘要。

### 5.4 缓存键

```python
@dataclass(frozen=True)
class RouteCacheKey:
    start: tuple[int, int]
    goal: tuple[int, int]
    map_version: int
    planner_mode: str
```

动态 `occupied` 不放入静态缓存键。缓存命中后必须在返回动作前重新验证首步：

1. 首步不是障碍；
2. 首步不是动态占用；
3. 首步不违反 `forbidden`；
4. 首步仍然朝缓存路径的下一个格子移动。

若验证失败，只丢弃当前路线或做一次局部重规划，不污染静态缓存。

## 6. 混合规划器的决策规则

### 6.1 快速层

保留现有 `step_direction()`，但把它限定为快速候选，不再让它承担完整寻路职责。

使用条件：

- 起点到目标的曼哈顿距离小于等于 12；
- 当前目标附近没有已知障碍导致直行受阻；
- 当前 Worker 没有连续无进展；
- 之前没有该目标的路线失败记录。

快速层返回的方向仍需通过动态占用检查。如果快速方向被占用，直接进入局部 A*，不要在当前 Tick 固定等待。

### 6.2 局部 A* 层

A* 使用四方向网格，启发函数使用曼哈顿距离：

```text
f(n) = g(n) + h(n)
h(n) = Manhattan(n, goal)
```

基础移动代价为 1。建议代价模型：

```text
step_cost = 1
          + unknown_penalty if next_cell is unknown
          + threat_penalty if next_cell is threatened
```

当前版本威胁优先级已经由 Worker 撤退逻辑处理，因此默认 `threat_penalty=0`，不要把战斗策略和普通路线代价混在一起。后续增加威胁路线时使用单独配置。

A* 要求：

- 使用 `heapq` 优先队列；
- 对相同 `f` 值使用确定性次序：`(f, h, y, x)`；
- `came_from` 记录前驱；
- 达到 `max_expansions` 立即返回 `BUDGET_EXHAUSTED` 或当前最佳前沿；
- 不把“没有搜到”直接返回 `BLOCKED`；
- 允许目标格为 Core 时由 `allow_goal_occupied=True`；
- 动态占用只影响当前请求，不写入静态地图。

### 6.3 增量区块层

区块层不是一开始就为每个区块建立完整距离场。原因是服务端不会提供世界 seed，且探索区域之外的障碍未知；在不完整地图上生成完整 Flow Field 会造成错误的确定性假象。

第一版只维护以下轻量摘要：

```python
@dataclass
class ChunkNavigationSummary:
    chunk: tuple[int, int]
    known_obstacles: frozenset[tuple[int, int]]
    known_cells: frozenset[tuple[int, int]]
    boundary_openings: dict[str, tuple[tuple[int, int], ...]]
    connected_components: tuple[frozenset[tuple[int, int]], ...]
    revision: int
```

每当某个区块新增已知障碍或新增视野覆盖时：

1. 更新 `known_cells` 和 `known_obstacles`；
2. 扫描四条边，找已知可通行的边界格；
3. 对当前已知格做有限 flood fill，更新连通分量；
4. 更新相邻区块的边界连接关系；
5. 增加该区块 `revision` 和全局 `map_version`；
6. 使经过该区块的路线缓存失效。

跨区块规划流程：

1. 先根据起点区块、目标区块和已知边界开放情况生成候选区块走廊；
2. 候选走廊优先选择曼哈顿距离短、已知边界连接多、威胁较低的区块序列；
3. 在当前区块内用 A* 规划到下一个边界门户；
4. 穿越门户后重新以新的起点规划下一段；
5. 任意一段发现新障碍、无门户或预算不足时，退回局部 A* 或 BFS 前沿；
6. 未知区块不能被标记为不可达，只能标记为“需要探索”。

不在第一版实现跨全图的精确区块距离场。只有当测试证实多个 Worker 频繁共享同一目标、且局部 A* 的总开销成为瓶颈时，再单独评估反向 BFS/Flow Field。

### 6.4 BFS 前沿降级

BFS 只负责两类任务：

- A* 达到预算上限时，找当前可达且更靠近目标的前沿；
- 当前目标路线被已知障碍阻断时，找能推进探索的可达位置。

评分建议：

```text
frontier_score = 4 * Manhattan(cell, goal)
               + 1 * distance_from_start
               + unknown_boundary_penalty
               + threat_penalty
```

BFS 必须保存完整前驱链，返回到最佳前沿的路线，而不是只返回一个坐标。若最佳前沿就是起点，返回 `BUDGET_EXHAUSTED` 或 `BLOCKED`，由调用方决定是等待、切换侦察方向还是冷却资源目标。

## 7. Worker 行为集成

### 7.1 统一移动入口

在 `strategy.py` 中新增内部统一入口，或直接调用 `HybridPathPlanner.next_step()`：

```python
def next_worker_move(
    worker: dict,
    target: tuple[int, int],
    planner: HybridPathPlanner,
    context: PathContext,
) -> PathResult:
    ...
```

`decide_worker()` 只保留高层优先级：

1. 受到威胁，回 Core；
2. 有 cargo，回 Core；
3. 已到资源点，执行 harvest；
4. 有资源分配目标，去资源；
5. 没有资源目标，去侦察航点；
6. 没有路线，wait。

其中第 1、2、4、5 项的移动统一交给规划器。这样不会出现采集路径、回 Core 路径、侦察路径各自拥有一套不同的绕障逻辑。

### 7.2 路线生命周期

每个 Tick 对每个 Worker：

1. 根据任务状态计算逻辑目标；
2. 检查现有路线的目标是否仍一致；
3. 检查路线 `map_version` 是否仍有效；
4. 检查路线首步是否可执行；
5. 可执行则发出首步，并推进 `next_index`；
6. 首步受动态占用阻挡则不修改静态地图，先做一次局部重规划；
7. 发现新障碍则增加地图版本、丢弃受影响路线并重规划；
8. 规划状态为 `FRONTIER` 时继续移动到前沿，同时保持任务目标；
9. 规划状态为 `BLOCKED` 时记录目标失败，不立即删除全部资源记忆；
10. 只有资源事件或现有资源冷却逻辑确认采空/失败时，才清除资源任务。

### 7.3 动态占用和多 Worker

当前 `_apply_worker()` 会把计划目的地加入 `occupied`，这是保守策略，但路线规划集成时要明确“当前占用”和“本 Tick 已预约占用”两个集合：

```text
occupied_now       = Tick 开始时所有对象所在格
reserved_destinations = 本 Tick 已经选定的 Worker 目的地
```

规划当前 Worker 时，禁止进入两者的并集，但允许进入自己的当前格。Worker 规划失败时不能把失败的目标格永久写入障碍记忆。下一 Tick 动态占用自然变化后再重试。

本阶段不实现完整 MAPF（多智能体路径规划）。只保证：不重复占用、临时阻塞可重规划、不会因共享缓存误用动态数据。

## 8. 地图记忆与持久化

### 8.1 地图版本

在 `MapMemory` 中维护单调递增的 `obstacle_revision`，至少在以下情况增加：

- 新增永久障碍；
- 发现原记忆中不是障碍的格现在是障碍；
- 载入地图记忆后完成版本校验时不需要增加。

资源变化不应使静态障碍路线缓存全部失效。资源点只影响任务目标和资源记忆，不影响地形路线。

### 8.2 `memory.json` 扩展

建议增加版本化字段：

```json
{
  "schema_version": 2,
  "obstacles": [[x, y]],
  "resource_seen": {"x,y": 123},
  "core_position": [x, y],
  "obstacle_revision": 17,
  "chunk_navigation": {
    "0,0": {
      "revision": 4,
      "known_cells": [[x, y]],
      "boundary_openings": {
        "UP": [[x, y]],
        "DOWN": [[x, y]],
        "LEFT": [[x, y]],
        "RIGHT": [[x, y]]
      }
    }
  }
}
```

实现要求：

- 兼容旧版无 `schema_version` 或无导航字段的文件；
- 损坏的导航字段只能丢弃导航摘要，不能丢弃障碍和资源记忆；
- 路线缓存、动态占用、Worker 当前路线不写入文件；
- 保存时使用临时文件加替换，避免进程中断产生半个 JSON；
- 不把 API Key 写入 `memory.json`。

如果现有 `memory.py` 使用不同的 JSON 结构，以其现有结构为准，以上为逻辑字段而非强制文本格式。

## 9. 分阶段实施清单

### Phase 0：基线和保护网

目标：在修改移动逻辑前建立可比较的行为基线。

步骤：

1. 阅读并记录 `strategy.py`、`agent.py`、`memory.py` 中所有移动调用点。
2. 为固定地图建立测试夹具：空旷地图、直墙、L 形、凹形、一格瓶颈、封闭区域、负坐标区块。
3. 为当前 `step_direction()` 增加回归测试，确认简单场景动作不变。
4. 在 `Agent` 中增加可选的路线统计字段：规划次数、命中次数、失败原因、展开节点数、到达/冷却次数。
5. 运行 `python test_strategy.py`，确认基线通过。

完成标准：

- 测试夹具不依赖网络；
- 每个后续阶段都能比较规划失败、停滞和动作次数；
- 不修改服务端动作协议。

### Phase 1：独立实现 A* 和明确结果状态

目标：先得到可单测的局部规划器，不接入主循环。

步骤：

1. 新建 `pathfinding.py`。
2. 实现 `PathResult`、`PathRequest`、四方向邻居和路径回溯。
3. 实现有确定性 tie-break 的 A*。
4. 支持障碍、动态占用、禁止回头、Core 目标格例外。
5. 实现节点预算；预算耗尽必须返回 `BUDGET_EXHAUSTED` 或 `FRONTIER`。
6. 实现路径方向转换，验证每个方向和相邻格一致。
7. 新建 `test_pathfinding.py`，覆盖空旷、绕墙、不可达、预算耗尽、起点即目标、负坐标和目标占用。

完成标准：

- A* 在所有固定地图上返回最短或等长最短路径；
- 任何 `BLOCKED` 结果都能由测试证明当前静态地图不可达；
- 预算截断不返回 `BLOCKED`；
- 不修改 `strategy.py` 的现有行为。

### Phase 2：统一 Worker 移动并逐步接入

目标：只替换去资源和回 Core 的移动，侦察暂时保留旧逻辑以降低风险。

步骤：

1. 实现 `HybridPathPlanner` 的快速层和 A* 层。
2. 近距离无遮挡目标走快速层；其他情况走 A*。
3. 在 `decide_worker()` 中为“去资源”和“回 Core”接入规划器。
4. 保留 `last_pos` 作为禁止回头的软约束，但不要把它当永久障碍。
5. 确保到 Core 时 `allow_goal_occupied=True`，到资源点时目标格按资源规则处理。
6. 规划 `BUDGET_EXHAUSTED` 时优先采用返回的前沿路径；无前沿才 wait。
7. 规划 `BLOCKED` 时沿用现有资源冷却/任务清理规则，不新增隐式冷却。
8. 增加开关，例如 `enable_path_planner`，默认在测试验证后开启；关闭时回到旧 `step_direction()`。
9. 为 `test_strategy.py` 增加 Worker 集成回归测试。

完成标准：

- 简单直线行为和原策略兼容；
- 复杂障碍不再因为一次局部受阻就 wait 或切换目标；
- 资源任务和 deposit 状态机不改变；
- 旧开关关闭时所有原测试通过。

### Phase 3：路线缓存与动态占用校验

目标：避免每个 Tick 对同一静态目标重复完整规划。

步骤：

1. 增加 `RouteCacheKey` 和按目标/起点/地图版本的缓存。
2. 把路径分成“静态路线”和“当前执行游标”；游标不能写进共享缓存。
3. 缓存命中后仅验证首步，不把动态占用加入缓存内容。
4. 首步被临时占用时执行一次局部重规划。
5. 新增障碍时使全局 `obstacle_revision` 增加，并清除受影响路线；第一版可以保守地清除全部路线，之后再优化为按区块清除。
6. 为缓存设置有限容量和简单 LRU，防止远距离资源点持续增长内存。
7. 记录 cache hit、cache miss、invalidated 和 replan 原因。

完成标准：

- 同一 Worker 的相同目标在地图未变化时可以命中缓存；
- 另一 Worker 的动态占用不会污染缓存；
- 动态阻塞解除后能恢复前进；
- 缓存有上限，不会随永久运行世界无限增长。

### Phase 4：BFS 前沿和渐进降级

目标：严格区分“当前没算完”和“已知不可达”，让探索和采集不会过早放弃。

步骤：

1. 实现带前驱链的 BFS 前沿搜索。
2. 定义前沿评分和确定性 tie-break。
3. A* 预算耗尽时调用 BFS，优先返回更接近目标的前沿路线。
4. 已知障碍把路线完全分割时，返回前沿而不是直接清除目标。
5. 对连续若干 Tick 没有改善的前沿路线，才交给现有 `RESOURCE_NO_PROGRESS_TICKS` 或 `SCOUT_NO_PROGRESS_TICKS` 机制。
6. 增加测试证明：预算不足不会触发资源冷却；确认不可达才会触发目标失败处理。

完成标准：

- 长路线在有限预算下可以逐段推进；
- 凹形障碍不再产生两格振荡；
- 目标不可达时最终能被高层策略回收，不会永久占用 Worker。

### Phase 5：增量区块导航摘要

目标：使用已探索地图提升跨区块路线质量，但不假设未知地图完整可知。

步骤：

1. 在 `pathfinding.py` 实现 `ChunkNavigationSummary` 和 `ChunkNavigationIndex`。
2. 复用 `chunk_of()` 的负坐标语义，增加负坐标测试。
3. 每 Tick 将新观察到的可见格和障碍更新到对应区块。
4. 只在区块内容实际变化时重算边界开放格和连通分量。
5. 为相邻区块建立已知边界门户关系。
6. 先以曼哈顿区块走廊生成候选区块序列，再按门户逐段调用 A*。
7. 跨区块规划失败时，退回 A* 前沿；未知区块只能标记为需要探索。
8. 把摘要写入 `memory.json`，按 schema 版本兼容旧文件。
9. 对索引更新增加单元测试和持久化往返测试。

完成标准：

- 已知长墙和区块边界瓶颈能利用门户绕行；
- 新障碍出现后摘要和路线版本同步失效；
- 未知区块不会被错误标记为永久不可达；
- 重启后加载失败只影响导航摘要，不影响基础障碍记忆。

### Phase 6：侦察接入和统一观测反馈

目标：让侦察也使用统一路线规划，并把新地图信息反馈到索引。

步骤：

1. 将 `assign_explore_targets()` 产生的航点作为普通路线目标。
2. `decide_worker()` 的侦察移动改用规划器。
3. 到达航点、前沿推进、确认被障碍阻挡时，分别更新 `waypoint_last_seen` 和进度状态。
4. 新视野发现障碍后先更新 `MapMemory`，再更新 `ChunkNavigationIndex`，最后规划 Worker 行动。
5. 删除或保留旧的“沿远离 Core 的轴再试一次”逻辑必须由回归测试决定；如果新规划器已覆盖该行为，应删除重复 fallback。
6. 验证多个 Worker 的侦察目标不会因为共享路线缓存而互相锁定。

完成标准：

- 侦察遇到墙时会绕行或切换合理前沿；
- 航点覆盖逻辑和资源分配逻辑不回归；
- 视野反馈顺序稳定：观察、更新记忆、更新导航、规划、提交动作。

### Phase 7：性能、观测和稳定性收尾

目标：适应永久运行和长期地图增长。

步骤：

1. 建立固定规模基准：1、3、10 个 Worker，近距离和跨区块目标。
2. 统计每 Tick 总展开节点、各层调用次数、缓存命中率、前沿返回率、路线重规划次数。
3. 为导航摘要设置热区/冷区策略：只保留最近访问区块的详细连通分量，冷区保留边界摘要。
4. 限制路线缓存、失败记录和进度记录的容量，清理失效 Worker ID。
5. 检查所有异常路径都仍会调用 `wait()` 或其他合法动作，不能让单个 Worker 异常阻止整 Tick 提交。
6. 模拟断线重连和损坏 `memory.json`，验证降级行为。
7. 在真实 API 环境采用观察模式或低风险配置运行，确认日志没有泄露 API Key。
8. 删除临时调试输出，保留可控级别的结构化规划统计。

完成标准：

- 长时间运行内存稳定；
- 规划预算始终有上限；
- 单个目标或单个 Worker 的异常不会中断全局 Tick；
- 所有测试和基准结果记录在提交说明或项目文档中。

## 10. 测试计划

### 10.1 单元测试

必须覆盖：

- `step_direction()` 兼容行为；
- A* 空旷最短路；
- 单墙绕行；
- L 形和凹形障碍；
- 一格瓶颈；
- 完全封闭目标；
- 起点等于目标；
- 目标是 Core 且被占用；
- 动态占用阻挡首步；
- `forbidden` 只影响当前请求；
- unknown 惩罚改变路线但不把未知当障碍；
- 预算耗尽与不可达的状态区分；
- BFS 前沿路径可回溯且终点更接近目标；
- 路线方向和坐标逐步一致；
- 缓存命中、失效和 LRU 上限；
- 地图版本增加后路线失效；
- 区块边界、负坐标、跨区块门户；
- 导航摘要 JSON 往返和旧 schema 兼容。

### 10.2 属性测试/随机测试

如果项目环境允许，加入有限随机网格测试：

- 随机生成障碍后，用完整 BFS 作为正确性 oracle；
- 当 A* 返回 `FOUND` 时，验证路径每步合法且终点正确；
- 当 A* 在足够预算下返回 `BLOCKED` 时，用 BFS 验证不可达；
- 当预算不足时，禁止断言为 `BLOCKED`；
- 对相同输入重复运行，输出必须相同；
- 对动态占用变化，静态缓存内容不能发生变化。

### 10.3 高层集成测试

构造假的 Tick/Unit 对象，不联网验证：

- Worker 去资源、采集、带 cargo 回 Core、deposit 后重新分配；
- 新障碍出现在旧路线前方时，下一 Tick 重新规划；
- 多 Worker 目的地不重复；
- Worker 受威胁时仍优先回 Core，不被普通采集路线覆盖；
- 侦察目标到达、卡住、无进展和切换朝向行为不回归；
- `turn.submit()` 在规划异常后仍被调用。

### 10.4 运行验证

按顺序执行：

```bash
python -m unittest discover -v
python test_strategy.py
```

如果项目后续引入 pytest，再补充：

```bash
python -m pytest -q
```

不要在没有配置真实 API Key 的情况下把联网运行作为单元测试依赖。

## 11. 错误处理和降级矩阵

| 情况 | 规划层处理 | 高层行为 |
|---|---|---|
| 目标已到达 | `AT_TARGET` | 执行 harvest/deposit/wait |
| 快速层首步可行 | 返回一步 | 正常移动 |
| 快速层受阻 | 转 A* | 不立即 wait |
| A* 找到完整路径 | `FOUND` | 执行首步并缓存 |
| A* 预算不足且有前沿 | `FRONTIER` | 沿前沿推进 |
| A* 预算不足且无前沿 | `BUDGET_EXHAUSTED` | wait 或下一 Tick 重试 |
| 已知区域确认不可达 | `BLOCKED` | 记录失败，交给现有冷却/换目标 |
| 首步被动态 Unit 占用 | 缓存不失效，局部重规划 | 本 Tick 换步或 wait |
| 新增永久障碍 | 增加地图版本，失效路线 | 下一步重规划 |
| 导航摘要损坏 | 丢弃摘要 | 使用基础障碍记忆和 A* |
| 单个 Worker 规划异常 | 记录异常，返回 wait | 继续其他 Worker 并提交 Tick |
| 规划总预算用尽 | 剩余 Worker 使用快速层/前沿/等待 | 保证命令窗口和提交流程 |

## 12. 配置建议

初始配置建议集中在 `config.example.json`，并为缺失字段提供默认值：

```json
{
  "enable_path_planner": true,
  "path_planner_mode": "hybrid",
  "astar_max_expansions": 800,
  "frontier_bfs_max_expansions": 1200,
  "total_path_budget": 5000,
  "route_cache_size": 256,
  "fast_path_distance": 12,
  "unknown_cell_penalty": 1,
  "enable_chunk_navigation": true
}
```

配置解析必须满足：

- 旧版 `config.json` 缺少新字段时仍能启动；
- 数值小于 0 或明显超出合理范围时记录错误并使用默认值；
- `enable_path_planner=false` 能回退到旧实现；
- 不把配置中的 API Key 打入日志。

## 13. 不建议现在实现的算法

### 13.1 D* Lite

D* Lite 适合大量动态变化或机器人持续移动的地图，但本项目的永久障碍是单调增加、动态 Unit 数量有限，第一版使用版本化 A* 加局部重规划更容易验证。只有在路线频繁因新增障碍失效、A* 重算成为主要开销时再评估。

### 13.2 Jump Point Search

JPS 对规则静态网格有效，但未知区域、动态占用和局部预算会增加实现复杂度。先保证 A* 的正确性和缓存命中率，再用基准证明需要 JPS。

### 13.3 全局 Flow Field

Flow Field 适合大量 Unit 同时去同一个目标，但当前 Worker 目标通常由匈牙利匹配分散分配，且资源点会变化。没有共享目标的实测瓶颈时不应提前维护大量距离场。

### 13.4 完整多智能体 MAPF

CBS、优先级规划和时间扩展 A* 能处理协同避碰，但会显著增加状态空间，而且服务端结算与当前客户端计划占用模型仍需进一步验证。本阶段只做动态首步检查和保守目的地预约。

## 14. 提交拆分建议

每个提交只包含一个可验证主题，建议顺序：

1. `Add pathfinding regression fixtures and metrics`
2. `Add bounded deterministic A* planner`
3. `Integrate hybrid planner for worker routes`
4. `Add route cache and dynamic occupancy validation`
5. `Add BFS frontier fallback`
6. `Add incremental chunk navigation summaries`
7. `Route scouting through hybrid planner`
8. `Add planner benchmarks and persistence recovery tests`

每个提交都应满足：

- 单元测试通过；
- 不混入无关格式化；
- 提交说明写明规划层、预算和回归结果；
- 不提交 `config.json`、`memory.json`、`agent.log` 或任何凭据。

## 15. 最终验收清单

- [ ] `strategy.py` 不再让单步贪心承担复杂绕障责任。
- [ ] 近距离简单路线仍走快速路径。
- [ ] 中距离和受阻路线使用有预算的确定性 A*。
- [ ] A* 的预算不足、前沿、不可达状态严格区分。
- [ ] 回 Core、去资源、侦察共用移动规划入口。
- [ ] 动态占用不会污染静态路线缓存。
- [ ] 新障碍会使相关路线失效并触发重规划。
- [ ] 增量区块摘要只基于已知地图，不伪造未知区域信息。
- [ ] 资源分配、采集冷却、墓碑和补充复查逻辑保持兼容。
- [ ] Worker 威胁撤退优先级保持不变。
- [ ] 旧配置可以启动，规划器可以关闭并回退。
- [ ] `memory.json` 旧格式可加载，损坏导航字段可局部丢弃。
- [ ] 离线测试覆盖所有边界状态。
- [ ] 固定地图基准证明规划质量提升且预算受控。
- [ ] 长时间运行没有无界缓存和 Worker 状态泄漏。
- [ ] 未执行未经请求的 `git push`，也未将任何凭据纳入提交。

## 16. 给执行 AI 的工作方式

请按 Phase 0 到 Phase 7 顺序执行，不要一次性实现全部算法。每完成一个 Phase：

1. 先读取当前工作树和相关文件，保留用户已有修改；
2. 只实现该阶段范围；
3. 添加或更新对应测试；
4. 运行测试并记录真实输出；
5. 检查 diff，确认没有凭据和无关文件；
6. 再进入下一阶段。

遇到以下情况必须停在当前阶段并修正，而不是继续叠加功能：

- 规划器把预算不足报告为不可达；
- 路线在重复目标上产生两格振荡；
- Worker cargo、deposit 或威胁撤退行为回归；
- 新障碍没有让旧路线失效；
- 动态占用被持久化为永久障碍；
- `turn.submit()` 因单个规划异常没有执行；
- 测试只能通过联网或真实 API Key 执行。

最终交付应包括代码、离线测试、基准结果和本计划中每个已完成 Phase 的简短变更记录。