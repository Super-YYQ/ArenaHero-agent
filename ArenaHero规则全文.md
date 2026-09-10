# Arena Hero 游戏规则（规则 v0.14 / API v0.1）

> 来源：https://doc.arenahero.io/zh-Hans/rules/* 与 /reference/numbers，抓取于 2026-09-10

## 世界与 Tick

世界与 Tick
一个永久世界​

所有玩家共享同一个永久运行的二维方格世界。
没有赛季，没有对局重置，也没有 NPC、野怪或服务器舰队。
每个账号同一时间最多拥有一个存活的 Core。
Unit 和每一代 Core 使用不可枚举的 UUID。对象活着的时候 ID 一直不变，死亡之后
这个 ID 永不复用。
每个 Core 都带公开的 owner_username，客户端显示为 @username。内部账号 ID、邮箱和
Unit 所属玩家不会进入状态。
v0.1 没有公开排行榜。

如果一个账号正好在世界结算期间激活，它不会被塞进一份没做完的快照里。服务端会给它
记一个持久的 activation_tick，玩家在那个 Tick 走确定性重生流程进入世界，和其他人
一样。
确定性区块生成​
地形以 32×32 区块为单位，由一个永久保密的 world seed 加上版本化的 HMAC-SHA256 协议
生成，客户端永远拿不到 seed。
这给了你几条可以放心依赖的保证：

世界、生成器版本、平衡参数和坐标都相同，地形就一定相同。
相邻区块之间的边界通道是确定性的。
任何可通行区域都连到区块主干上；但允许出现一格宽的瓶颈，而且这种地形往往很关键。
[0, 0] 以及它通往主干的那条路永久是 EMPTY，所以 Champion Beacon 不会被围死。
生成器协议对不上，服务直接拒绝启动。也就是说改动生成语义就得换一个世界数据库。

障碍和主干通道属于永久地形。资源点则是另一层可消耗地图数据，每个区块都有固定配额，
补充位置同样按确定性规则生成。配额和放置规则见
地图与视野。
Tick 生命周期​
每个逻辑 Tick 都是固定长度的命令阶段，加上一段长短不固定的结算阶段。

窗口先开，之后服务端才逐个发布玩家状态。所以你拿到手的，是那 15 秒里还剩下的
那部分；收到 state 并不会给你单独起一个 15 秒计时。协议也故意不公开
opened_at 和 deadline_at。
两条消息别搞混：tick 只是宣布有这么一个逻辑 Tick，此时命令还没开放；state 才是
唯一的行动信号。
结算顺序​
下面这个顺序属于协议的一部分，不是实现细节：

锁定最终有效的 Agent 与 Manual 计划。
结算所有 SELF_DESTRUCT，立即移除这些 Unit，并把 Worker Cargo 掉在最后所在格。
结算 Unit 移动，以及走到第 4 个 Tick 的 Core 迁移。
检查新提交的 Core START_MOVE。
结算 Champion Beacon 的拾取与放下。
结算 Worker 的采集与交付。
冻结不可变战斗快照，累计所有合法攻击。
同时应用伤害、移除死亡对象，并转移符合条件的被摧毁 Core 库存。
结算所有战斗后仍存活 Core 的 SELF_DESTRUCT；销毁库存和全军，并让 Worker Cargo
与 Beacon 掉在各自实际位置。
按 Unit UUID 顺序结算仍存活 Unit 的 HEAL。
记录每名玩家此时的存活人口，再结算其余存活 Core 的 HEAL、REPAIR_SHIELD 或
动态定价的 SPAWN 动作。
立即尝试让新摧毁的 Core 重生，只有找不到合法出生点时才顺延到下一 Tick 重试。
每结算满 4 个 Tick，按区块只补回已经消耗的资源槽位，使可用资源总数回到该区块的
固定配额。
原子提交世界、资源层、事件、统计、journal 和新时钟。
宣布下一个 Tick，并准备新的私有状态。

服务端不会为了追上墙上时间而跳 Tick，停服期间世界就是暂停。两个 Tick 也绝不会同时
结算。
资源补充属于本 Tick 的结算末尾。在补充 Tick 里被采掉的点会先消失，随后补充步骤再填满
区块缺少的槽位。没被采的点不会移动，没用掉的配额也不会累计到上限以上。
原子性与重放​
一个 Tick 的结果在同一个 PostgreSQL 事务里提交，所以客户端不存在看到「结算了一半的
世界」的时机。规则引擎同样不允许用 map 遍历顺序、墙上时间、进程随机数或者无序的查询
结果去决定胜负。
世界状态和锁定计划都相同，规则算出来的结果就逐字节相同。
崩溃恢复​
故障点恢复方式状态准备失败不开启命令窗口。状态发布失败中止 gate；恢复后重新宣布同一 Tick 并重开完整 15 秒。OPEN 阶段崩溃保留已持久化计划；重启后发送同一 tick、完整 state 和最新回执，并重开完整窗口。锁定后崩溃不重新开放窗口，确定性重放已锁定计划。服务离线世界和所有逻辑计时暂停。
现有世界切换​
可消耗资源版本会替换现有世界的旧资源布局，但不会重置世界时钟、玩家、Core、Unit、
库存、重生状态或 Champion Beacon 状态。资源位置作为地图层迁移到新的区块配额与补充
协议。
规则 v0.5 保留 Worker 死亡后的 Cargo 资源堆，并增加随人口变化的 Core 容量。规则
v0.6 将容量改为 max(10, population × 5)。规则 v0.7 让 Ranger 射击穿过 Unit 和
Core，只有障碍物阻挡。规则 v0.8 加入射程 1-3 的 45° 斜线射击，并且只检查射线实际
经过的中间格障碍物。规则 v0.9 把战斗中被摧毁 Core 的容量内库存交给对它伤害最高、
且 Core 在同 Tick 战斗后仍存活的玩家。规则 v0.10 把 Core 的资源动作移到战斗后，
并加入 Unit 与 Core 的 HP 恢复。规则 v0.11 曾把维护费欠款从 Core 伤害改为超额 Unit
伤害。规则 v0.12 加入战斗后无条件、无冷却的 Core 自毁。规则 v0.13 加入无需目标的
Ranger 按格射击，以及按最低 HP、UUID 确定目标的规则。规则 v0.14 删除维护费，改用
精确的随人口变化的 Unit 价格。现有 v0.1 到 v0.13 世界可以在
OPEN 或 COMMITTED 边界升级，
不会重置游戏状态。当前规则同时移除了复活冷却：Core 被摧毁后会在同一个 Tick 的后续
阶段立即尝试重生。如果旧服务停在 LOCKED 或 RESOLVING，必须先用旧规则完成该 Tick，
才能升级。

## 地图与视野

地图与视野
地形与资源点​
每个格子都有永久的基础地形；可通行格上还可能存在自然资源点或死亡 Worker 掉落的 Cargo：
可见 kindUnit 可进入Core 可迁入阻挡视野阻挡 RangerEMPTY是是否否RESOURCE是否否否OBSTACLE否否是是
Core 和 Unit 占着格子，但它们不是地形。障碍和区块主干通道是永久的。自然资源点一次
HARVEST 后被消耗；Cargo 资源堆可能需要采集多次，只要资源堆或同格自然点还有剩余，
该格就继续显示为 RESOURCE。
资源配额​
区块大小是 32×32。格子 [x, y] 所属的区块坐标使用向下取整：
cx = floor(x / 32)cy = floor(y / 32)
围绕原点的 2×2 个区块属于第 0 环。定义：
axis(c) = c       当 c >= 0          -c - 1  当 c < 0ring = axis(cx) + axis(cy)x = max(2, floor(16 * 8 / (8 + ring)))
x 是一次补充完成后，该区块固定拥有的可用资源点数。因此第 0 环每个区块有 16 个，
配额随距离下降，但最低不会少于 2 个。
消耗与补充​
一次成功采集只消耗一个点。普通 Worker 从中得到 1 点资源；所属玩家持有 Champion
Beacon 的 Worker，则从同一个点得到 2 点。
每结算满 4 个 Tick——大约每分钟一次——服务端统计每个区块仍可用的点，只生成足够填回
固定配额 x 的替代点。

未采集的点不移动。
缺口不累计，补充后不会超过 x。
替代位置使用确定性随机选择。
替代点只能出现在可通行、非障碍、非区块主干通道，而且结算后没有 Core 的格子。
替代点可以出现在 Unit 脚下，也可以出现在落地的 Champion Beacon 脚下。

Worker Cargo 资源堆不计入这份配额。Worker 因自毁、战斗或所属 Core 被摧毁而死亡时，
全部 Cargo 会累加到最后所在格。资源堆一直保留到被取完，既不会减少也不会增加区块的
自然资源补充配额。
版本化的世界协议保证这个选择可重放：同一个世界、已结算 Tick、区块状态和缺口，会得到
同一组替代位置。
视野值​
对象曼哈顿半径Core5Worker3Vanguard4Ranger5
你当前的私有视野，是所有存活己方对象各自视野的并集。障碍遮挡走的是整数 supercover
直线：障碍格本身你看得见，它后面就看不见了；射线正好从两格共用的角穿过时，两侧都算
经过，任意一侧是障碍就会挡住。
Unit、Core 和资源点完全不挡视野，也不挡 Ranger 的射击。只有障碍地形会挡住
Ranger 的射线。
服务端发送什么​
每份 state 里有：

你自己全部的 Core 和 Unit，哪怕它们不在任何己方视野里；
敌方 Core 和 Unit，但只有当前可见的；
可见障碍与当前可用资源点，分别合并成一个 OBSTACLE 对象和一个 RESOURCE 对象；
Champion Beacon 的坐标，这对所有人公开；
Beacon 的状态和 carrier ID，但只在它所在的格子可见时才有。

敌方对象带 controlled: false。可见 Core 仍带公开的 owner_username，界面显示时在
前面加 @；内部 owner UUID 和 Unit 所属玩家不会公开。Worker 的 cargo 是私有的，
只会出现在你自己的 Worker 上。
探索记忆​
服务端只发当前视野，不会替你重放去过哪儿。网页会在本地缓存观察结果。Agent 想要一张
地图，就得自己保存看过的内容——换台设备重新连，你手上就只有当前视野。
旧知识可能过期记住的障碍一直作数，因为基础地形是永久的。记住的资源点只是「上次看见」：它可能在
迷雾里被采掉，新补出来的点也要等格子重新进入视野才会暴露。你最后看到的 Unit、Core
或 Beacon 载体同样可能早就挪地方了。
可见自然资源点一旦被采掉，下一份完整 state 就不再包含它，除非同格还有 Cargo
资源堆，或者补充步骤又在这里放了一个点。state.objects 只公开 RESOURCE 位置，
不公开资源堆剩余数量，因此只取走一部分后，同一个位置可能继续可见。
Beacon 信息边界​
坐标永远都在：
{"position": [0, 0]}
看得见它躺在地上时：
{"position": [0, 0], "status": "GROUND"}
看得见携带它的载体时：
{  "position": [0, 0],  "status": "CARRIED",  "carrier_id": "175f47f4-f7de-4785-b45c-9a2d2289a8ea"}
carrier_id 依然不会告诉你载体属于谁。

## Core 与经济

Core 与经济
Core 属性​
属性默认值HP 上限5护盾上限5持有 Beacon 时护盾上限10视野5重生启动资源5
战斗伤害先扣护盾，扣完了才动 HP。Core 是你放资源的地方，也负责接收交付、生产 Unit、
恢复 HP 和护盾，以及——很慢地——迁移。
资源容量​
人口只计算存活 Unit，不计算 Core。Core 最少能存 10 点资源；人口超过 2 后，每个 Unit
提供 5 点容量：
resource_capacity = max(10, population × 5)
新玩家和重生玩家以 1 个 Worker、5 点资源开始。人口下降后，如果现有库存高于新容量，
高出的资源会立刻销毁。私有事件 CORE_RESOURCE_OVERFLOW_DESTROYED 会给出损失数量和
新容量。
Worker 只存入剩余容量，装不下的部分继续留在 Worker 身上。Core 已满时，
返回 DEPOSIT_FAILED / CORE_RESOURCE_FULL，Core 库存和 Worker Cargo 都不变。
在战斗中摧毁敌方 Core 也可能增加己方库存。对该 Core 伤害最高的玩家只能拿到当前容量
装得下的部分，多出的资源直接销毁；如果该玩家的 Core 也在同 Tick 被摧毁，受害者库存
则全部销毁。详见摧毁与重生。
Core 动作​
一份来源计划里最多写一个 Core 动作：
动作参数用途SPAWNunit_type在 Core 格生产一个 Unit。HEAL无战斗后消耗资源恢复 Core HP，1 资源恢复 1 HP，直至回满。REPAIR_SHIELD无消耗 1 资源恢复 1 护盾。START_MOVEdirection开始四 Tick 迁移。CANCEL_MOVE无取消迁移并清零进度。PICKUP_BEACON无拾取同格地面 Beacon。DROP_BEACON无放下 Core 携带的 Beacon。SELF_DESTRUCT无战斗后销毁这个 Core、库存和所有己方 Unit。WAIT无显式不行动。
Core 自毁​
任意存活 Core 都可以提交不带其他字段的 {"type":"SELF_DESTRUCT"}。它不检查资源、
Unit 数量、迁移状态或历史自毁次数，也没有冷却。迁移中的 Core 会先推进或完成本 Tick
位移，并照常承受攻击。
战斗优先。如果敌方攻击已经摧毁 Core，照常计算摧毁参与和资源归属，自毁不再执行。
如果 Core 在战斗后仍存活，它会在 Unit 恢复、Core 恢复、修盾和生产之前自毁：

Core 库存全部销毁，不退款也不转移；
所有己方 Unit 被移除并计入 units_lost；
Worker Cargo 和 Champion Beacon 掉在各载体实际的战后位置；
不造成伤害，不给任何玩家摧毁参与或战利品；
立即进入普通重生流程，并增加 respawn_count。

私有 CORE_DESTROYED 使用 reason_code: SELF_DESTRUCT，不含 destroyed_by；出生点
部署成功时随后出现 CORE_RESPAWNED。新 Core 可在下一 Tick 再次自毁。
生产与动态价格​
前 20 个存活 Unit 使用基础价格。之后每完整增加 5 个 Unit，三种 Unit 的价格都提高 30%：
N = 结算 Core 动作时的存活 Unit 数k = max(0, floor((N - 20) / 5) + 1)price = round_half_up(base_price × (13 / 10)^k)
服务端使用精确分数，只在最后舍入一次；刚好一半时向上取整。N 包括 Worker、
Vanguard 和 Ranger，不包括 Core。
Unit基础价格N = 0-19N = 20-24N = 25-29N = 30-34Worker557811Vanguard1010131722Ranger1212162026
第 20 个 Unit 仍按基础价购买，第 21 个才第一次涨价。初始 Worker 和每次重生附带的
Worker 都免费。
每 Tick 最多生产一个。一格能放两个可占位实体，Core 自己已经占掉一个，所以同时最多
只能有一个 Unit 和它待在一起——往满格里生产会拿到 CELL_UNIT_LIMIT，资源不扣。
刚生产出来的 Unit：

在被创建的这个 Tick 里不能行动；
在战斗结束后才创建，所以出生 Tick 不会被攻击；
会立刻增加人口，从而影响之后的生产价格。

Worker 交付排在 Core 动作之前，所以这个 Tick 实际存入的资源，可以在动作本身合法时
用于该 Core 动作。战斗中从敌方 Core 夺取的资源，可以在同 Tick 先供 Unit 恢复 HP，
再供 Core 动作使用。
生产价格使用 Core 动作阶段开始时的存活人口。Unit 自毁和战斗死亡已经结算，因此都能
在同 Tick 降低价格。公开状态和官方前端显示的是最新完整状态推算出的价格；如果本 Tick
有人死亡，服务端实际扣款可能更少。最终以 CORE_SPAWN_SUCCEEDED.values.cost 和
CORE_SPAWN_FAILED.values.required 为准。
HP 恢复​
HEAL 是 Unit 或 Core 的完整动作。它在同时战斗伤害结算后执行，每实际恢复 1 HP 就从
Core 扣 1 资源，并自动持续到对象 HP 回满或 Core 资源耗尽。
Unit 必须仍然存活，并与自己静止的 Core 同格。Unit 按 UUID 原始字节序依次恢复，然后才
结算 Core 动作。致死伤害无法恢复。即使当前 HP 已满或资源为零，也可以提前提交 HEAL：
因为对象可能先受到非致死战斗伤害，Core 也可能先夺取到资源。到结算时条件仍不满足，
动作只会私下失败，不扣资源。
修盾​
REPAIR_SHIELD 固定花 1 资源换 1 点护盾，而且不会让你超过当前上限。失败时会收到
私有的 CORE_REPAIR_FAILED，原因是 SHIELD_FULL 或 INSUFFICIENT_RESOURCES。
持有 Champion Beacon 只是把上限抬到 10，并不附送护盾。Beacon 一丢，超过 5 的部分
立刻被压回 5。
四 Tick 迁移​
Core 往正方向挪一格，要花四个逻辑 Tick。
START_MOVE 结算  -> 进度 1/4下一 Tick        -> 进度 2/4下一 Tick        -> 进度 3/4下一 Tick        -> 尝试真实位移
中途不用反复提交，WAIT 也不会让它暂停。想换方向就得先 CANCEL_MOVE，一取消进度
就清零。
迁移期间的 Core：

不能生产、恢复 HP、修盾，也不能拾取或放下 Beacon，但可以 SELF_DESTRUCT；
不能接收 Worker 交付；
照常挨打；
库存保留；
同格的 Unit 不会被一起带走。

携带的 Beacon 要等真实位移成功了才跟着走。另外，开始迁移不预留任何东西：在你的第 4
个 Tick 到来之前，别人照样可以穿过目的格，甚至直接占住它。
第 4 Tick 的那次位移，进的是和 Unit 移动同一张全局依赖图。失败的话 Core 原地不动，
进度清零。
游戏没有每 Tick 自动扣除的维护费。人口会影响 Core 容量和下一个 Unit 的价格，但不会
自动消耗资源，也不会自动伤害 Unit。

## 单位

单位
每个 Unit 活着的时候 UUID 保持不变，占一格容量，每 Tick 最多走一格、最多做一个动作。
对比​
UnitHP视野基础价格攻击职责Worker235无采集与交付Vanguard4410范围 1 格、伤害 1相邻格范围压制Ranger2512八方向直线 1-3 格、伤害 1精确远程攻击
MOVE、PICKUP_BEACON、DROP_BEACON、HEAL、SELF_DESTRUCT 和 WAIT 所有 Unit
都能用，其余动作则要看类型。
表中是人口 0-19 时的基础价格。从人口 20 开始，下一个 Unit 会更贵；详见
生产与动态价格。
Worker​
允许的动作：MOVE、HARVEST、DEPOSIT、PICKUP_BEACON、DROP_BEACON、
HEAL、SELF_DESTRUCT、WAIT。
HARVEST 要求一个空载 Worker 站在 RESOURCE 格上。自然资源点通常产出 1 点，
所属玩家持有 Champion Beacon 时产出 2 点，随后该点被消耗。若同格有死亡 Worker
留下的 Cargo 资源堆，则优先回收：普通 Worker 一次拿 1 点，Beacon Worker 最多拿
2 点，但不会超过资源堆实际剩余量。
如果同一个 Tick 有多个合格的空载 Worker 采同一个点，只有 UUID 原始字节序最小的
Worker 成功。资源点只消耗一次，其他竞争者都收到 HARVEST_FAILED，reason 是
RESOURCE_DEPLETED。Beacon 不会改变这个胜负顺序。
所谓载重上限，其实就是上一次成功采集或回收拿到的量：平时是 1，有 Beacon 时最多是 2。
Beacon 丢了也不会把 Worker 身上已经背着的那点加成资源抹掉。
DEPOSIT 要求 Worker 和自己的 Core 同格，而且这个 Core 必须处于正常、可接收的
状态——正在迁移或者刚迁移完还在恢复的 Core 收不了货。Core 容量是
max(10, population × 5)；交付只存入能装下的部分，剩余 Cargo 留在 Worker 身上。Core 已满
时返回 CORE_RESOURCE_FULL。任何交付失败都不会动 Cargo。Worker
不管因为什么死亡，全部 Cargo 都会变成最后所在格的资源堆。
Worker 完全不能攻击。
Vanguard​
允许的动作：MOVE、带 cardinal direction 的 SWEEP、PICKUP_BEACON、
DROP_BEACON、HEAL、SELF_DESTRUCT、WAIT。
SWEEP 打你指定方向上的那一格相邻格：站在那儿的每个敌方 Unit 各受 1 伤害，敌方
Core 同样受 1。多个 Vanguard 打同一格，伤害会在同一份战斗快照里累加。
横扫不需要 target UUID，也绝不会伤到自己人。
Ranger​
允许的动作：MOVE、带 expected_cell 和可选 target_id 的 SHOOT、PICKUP_BEACON、
DROP_BEACON、HEAL、SELF_DESTRUCT、WAIT。
一次按格射击只有在下面几条全部成立时才合法：

expected_cell 和 Ranger 在同一条横线、竖线或 45° 斜线上；
沿该直线的距离是 1、2 或 3——相对位置 (3, 3) 算 3 格，(2, 1) 不在合法直线上；
中间的格子里没有障碍物。

移动先结算。格内有敌方对象时，服务端命中 HP 最低者；HP 相同时按 UUID 原始字节序。
没有敌方对象就正常落空。Unit 和 Core 无论敌我都不阻挡 Ranger 射击，同格对象之间没有
可利用的前后顺序。斜射只检查射线实际经过的斜线中间格，斜线两侧的障碍物不阻挡。
要保持向后兼容的精准射击，可以带上 target_id；这种模式只会命中仍为敌方、并且仍在
expected_cell 的指定对象。POST 接口仍故意接受你没见过、甚至根本不存在的 UUID。
到结算时，空格、精准目标不存在或移开、距离不对、射线被挡，全都归成同一个私有的
SHOT_MISSED 事件。
自毁​
所有 Unit 都能提交：
{"type": "SELF_DESTRUCT"}
它会在移动前移除这个 Unit，因此本 Tick 后面的 Core 动作会按自毁后的实际人口计算
生产价格。Unit 不再执行
其他动作，不返还生产费用，不造成范围伤害，也不给任何玩家摧毁参与。Worker 携带的资源
会掉在当前格。携带 Beacon 的 Unit 也会把 Beacon 掉在这里，而且本 Tick 不能再次拾取。玩家
会收到 UNIT_SELF_DESTRUCTED，units_lost 同时增加 1。
恢复 HP​
所有 Unit 都能提交：
{"type": "HEAL"}
Unit 会用掉本 Tick 的完整动作。战斗结束后，它必须仍然存活，并与自己静止的 Core 同格。
动作从 Core 库存中按 1 资源恢复 1 HP，可以一次恢复多点，直到 HP 回满或资源耗尽。Unit
按 UUID 原始字节序依次恢复，然后才结算 Core 动作。致死伤害会先移除 Unit，无法恢复。
满血或当前没有资源时提交仍是一份合法计划；若到结算时情况没有改变，动作失败且不扣资源。
因战斗死亡的 Unit 已经被移除，不会扣资源。
动作示例​
Worker 采集{"type": "HARVEST"}
任意 Unit 自毁{"type": "SELF_DESTRUCT"}
任意 Unit 恢复 HP{"type": "HEAL"}
Vanguard 向右横扫{"type": "SWEEP", "direction": "RIGHT"}
Ranger 射击{  "type": "SHOOT",  "target_id": "175f47f4-f7de-4785-b45c-9a2d2289a8ea",  "expected_cell": [120, 85]}
一个动作只能带它自己 type 需要的字段。多出一个无关字段，整份计划就会以
UNEXPECTED_ACTION_FIELDS 被拒，哪怕它的值是 null、空字符串或者全零 UUID。

## 指令与优先级

指令与优先级
两个独立来源槽​
每位玩家每个 Tick 有一个 AGENT 计划槽和一个 MANUAL 计划槽，逐对象合并：
Manual 显式动作 > Agent 显式动作 > WAIT

Agent 计划放的是自动动作。没写到的对象按 WAIT 处理，除非 Manual 那边给了动作。
Manual 计划放的是人工覆盖。没写到的对象回退成 Agent 的动作。
想让某个 Agent 动作停下来，Manual 必须显式发一个 WAIT，光是不写它是不够的。
同一玩家所有的 Agent 客户端共用一个 Agent 槽。
同一玩家所有的网页标签共用一个 Manual 槽。

后一次计划会替换前一次​
同一来源每次提交成功，都会把之前那份整个换掉。服务端从不 patch，也不 merge。

所以 Agent 如果只想改一个 Unit、其他 Unit 保持原样，那些动作也得跟着一起再发一遍。
静态与动态校验​
静态检查在持久化之前跑，看这些：

body 是一个 JSON object，没有未知字段；
Tick 是正数；
Unit 的 key 是小写、带连字符的 UUID；
引用到的行动 Unit 都属于这名玩家；
动作类型适用于该 Unit；
必需字段都在，无关字段都不在。

只要有一处不对，整份请求就被原子拒绝，你之前那份有效计划原封不动。
动态条件是另一回事，它们只能等到结算时才见分晓：

目标移动了；
目的格被占满了；
移动被争夺；
资源不够了；
Beacon 被更小的 UUID 抢走；
Ranger 的射线被障碍物挡住。

这些都不会让你的 POST 作废，它们会出现在下一条 state.events 里。
顺序与限制​
同一个 (player, tick, source) 的有效请求，按进入 gate 的先后顺序串行处理，后存的
计划盖掉先存的。协议里没有客户端提供的版本号。
每个来源槽每 Tick 最多接受 64 个新提交，计数发生在幂等预查之后——有效的和静态非法的
都算。再多就是 429 COMMAND_RATE_LIMITED，你最后那份有效计划不受影响。
幂等与回执​
每个命令请求都带 Idempotency-Key。

同 key 同 body：把原来那个 HTTP 响应再给你一次。
同 key 不同 body：IDEMPOTENCY_CONFLICT。
幂等重放不会再广播一条 received。

服务端存下新计划之后：

HTTP 返回一个精简的 202 Accepted，带回执元数据。
这名玩家所有在线连接都会在 received.plan 里收到服务端存下的那份计划。
在同一个 OPEN Tick 内重连，会恢复每个来源最新的回执。

回执在下一个 Tick 开始时清空。它不是一个查计划历史的服务。

## 移动与叠加

移动与叠加
基础限制​

Unit 每 Tick 最多移动一格。
只能走四个正方向：UP、DOWN、LEFT、RIGHT。
移动会用掉 Unit 的动作，所以这个 Tick 它不能再攻击或者干活。
障碍阻挡一切移动。
资源格接受 Unit，但不接受迁移中的 Core。
每格最多容纳两个可占位实体；Core、Worker、Vanguard、Ranger 各算一个。
不同玩家的对象绝不能在 Tick 结束时停在同一格。

移动不是一条请求一条请求地处理的。引擎会建一张全局依赖图，把所有 Unit 移动和所有
到期的 Core 迁移一次算完。
争夺目的格​
两个玩家想进同一格，两边都会失败。什么都打不破这个平局：舰队大小、谁先提交、
命令来自哪个来源，都不算。

但如果争的是同一个玩家自己的对象，规则不一样：位置不够时，按对象 UUID 原始字节
升序，谁小谁进，剩下的以 CELL_UNIT_LIMIT 失败。这条规则确定，但别拿它当战术用——
按你预期的容量来排计划。
已占据目的格​
只要原来的占据者全部成功离开、最终容量也算得过来，你就能进这一格。移动链就是这么
成立的：
A → B 的原格B → C 的原格C → 空格
C 能走通，整条链就都能走通；中间任何一环走不掉，失败就往前一路传回去。
有一点要记住：如果一格上是同一个玩家的两个对象，那两个都得离开，敌人才进得来。
交换与环​
不同玩家的两个对象想沿同一条边换位置，永远失败：
A [0,0] → [1,0]B [1,0] → [0,0]
三个位置以上的闭环则可以成功，前提是每个最终格的 owner 和容量都说得通。在四方向
方格上，你实际会遇到的最短环是四格。
Core 参与​
走到第 4 个 Tick 的 Core 迁移，会带着真实移动意图进入同一张依赖图。而静止的 Core
就是一个敌人进不来的占据者。
第 4 Tick 的这次位移仍然可能失败，原因包括：

地形不可通行；
有符号坐标溢出；
原占据者没有离开；
目的格被争夺；
敌人同时要进这一格；
最终容量不够。

失败后 Core 留在原来的位置，迁移进度清零。
网页路线不是服务端规则​
在网页里你可以点一个远处已探索的格子并得到一条路线，但这条路线纯粹是本地的 Manual
自动化：每收到一份新 state，浏览器就重算一次，并且只提交下一步。服务端只接受
MOVE 和 START_MOVE，从不接受多格路径。
网页一关，路线也就跟着停了。

## Champion Beacon

Champion Beacon
世界上只有一个 Champion Beacon，它不可摧毁，初始位置是 [0, 0]。重启既不会移动它，
也不会把它重置。
可见性​
每份 state 里都带着 Beacon 的坐标，对所有人、在任何时候都公开。只有当它所在的格子
恰好可见时，你才会知道它是 GROUND 还是 CARRIED，以及一个不带 owner 的
carrier_id。
Beacon 本身很不占地方：

不占用格子容量；
什么都不阻挡——移动、视野、Ranger 射线都不挡；
可以和其他实体待在同一格；
网页路线正好经过它，也不会顺手把它捡起来。

拾取与放下​
任意 Unit，或者一个没在迁移的正常 Core，只要和地面上的 Beacon 同格，就可以花掉整个
动作去 PICKUP_BEACON。而 DROP_BEACON 只有当前载体能用。
如果同一个 Tick 有好几个对象同时去拿，就比载体 UUID 的原始字节，最小的那个拿到。
你没法从一个还活着的载体手里直接抢走它。
Beacon 动作在 Worker 采集之前结算，这带来一个很方便的结果：

这个 Tick 拾取成功，采集增益当场就生效；
这个 Tick 放下成功，增益当场就没了。

护盾增益​
持有 Beacon 会把这名玩家的 Core 护盾上限从 5 提到 10。

拾取本身不赠送护盾，也不做任何修复。
你照样得花 REPAIR_SHIELD，1 资源换 1 点护盾。
Beacon 一丢，当前超过 5 的护盾立刻被压回 5。

Worker 增益​
符合条件的空载 Worker 平时采回 1 点资源；所属玩家持有 Beacon 时，采集并携带 2 点。
两种情况都只消耗一个资源点。已经背在身上的那份加成 cargo，即使 Beacon 丢了也还是
2 点，可以一次交付掉。同一个点有多个合格 Worker 竞争时，最低 UUID 赢；Beacon 只把
赢家的 cargo 翻倍，不会让第二个人成功，也不会多消耗一个点。
移动与死亡掉落​
载体每成功移动一次，Beacon 就跟着走。Core 迁移期间，它一直停在 Core 当前的逻辑
位置，直到第 4 Tick 的真实位移成功为止。
假设 Tick 开始时 Beacon 已经在被携带，之后它被主动放下，或者载体死了，或者所属
Core 被摧毁——这三种情况下 Beacon 都会落在载体最终的实际位置，而且本 Tick 谁也捡
不起来，最早也得等下一个 Tick。
这个冷却就是重点：它阻止一个 Tick 之内把 Beacon 在一串载体之间接力传下去。
终身排行榜​
只有结算结束时玩家仍然拥有 Beacon 载体，公开的信标持有榜才增加 1 Tick。在 Tick 结束
前主动放下或因死亡掉落，都不计入。详见排行榜 API。

## 战斗

战斗
单一不可变快照​
战斗排在移动、Beacon 和 Worker 动作之后，但在 HP 恢复、修盾和生产之前。引擎会冻结
一份不可变快照，然后完全照着它算：

所有锁定攻击都基于同一份快照校验。
累计全部合法攻击的伤害。
把累计的伤害同时应用。
到这一步才移除死亡的 Unit 和被摧毁的 Core。

移除结束后，仍存活的 Unit 可以恢复 HP，然后仍存活的 Core 才能恢复 HP、修盾或生产。
所以致死伤害无法恢复，刚修好的护盾不能替你吸收刚结束的这轮伤害，新生产的 Unit 也不会
在出生 Tick 被攻击。
因为校验和结算用的都是这张定格的画面，战斗中被杀死的对象仍然能打出它此前锁定的
合法攻击，同归于尽也就成了很常见的结果。谁都拿不到先手：请求到达的顺序、完成的
顺序、数据库行的顺序，以及计划来自 Manual 还是 Agent，全都不算。
v0.1 没有随机伤害、闪避、暴击、护甲、自动反击、体力、等级和装备。
Vanguard 横扫​
{"type": "SWEEP", "direction": "UP"}
引擎看的是战斗快照里那一格相邻格的样子：

格子里每个敌方 Unit 各受 1 伤害；
格子里的敌方 Core 受 1 伤害；
友方对象不受伤害。

几个横扫指向同一个目标，伤害就叠起来。
Ranger 射击​
{  "type": "SHOOT",  "expected_cell": [120, 85]}
Ranger 可以射击横线、竖线或 45° 斜线上距离 1-3 格的任意格子，即使命令提交时该格
还是空的。移动先结算；服务端命中届时格内 HP 最低的敌方对象，HP 相同时按 UUID 原始
字节序。相对位置 (3, 3) 算 3 格，(2, 1) 不在合法直线上。只有射线实际经过的中间格
障碍物会挡住射线。Unit 和 Core 无论敌我都不阻挡，斜线两侧的障碍物也不阻挡。
旧客户端仍可带 target_id。这种精准模式只会命中仍为敌方且仍在 expected_cell 的
指定对象，不会改打同格的其他对象。
接口只检查结构，别的一概不管。所有动态失败都推迟到结算，统一变成 SHOT_MISSED：

格子为空，或精准目标不存在、是友军、已经移开；
目标格不在八条射击直线上，或者超出射程；
射线被障碍物挡住。

这种含糊是故意的。按格射击落空时不带 target_id；命中时会报告服务端实际选中的对象。
Core 伤害​
伤害永远先扣护盾，再扣 HP。全部战斗伤害合并之后 Core 的 HP 归零，它的舰队才会被
移除——但在那之前，它那些还活着的 Unit 在快照里打出的攻击一样算数。
这里没有独占的「最后一击」统计。同一个 Tick 里多名玩家一起打掉同一个目标，所有人
都获得摧毁参与。被摧毁 Core 的资源单独判断：本 Tick 对该 Core 总伤害最高者获得容量
内的资源，伤害相同时按玩家 UUID 原始字节序。详见摧毁与重生。
公开的伤害榜会计算每次合法命中，包括护盾伤害和同 Tick 超过目标剩余护盾与 HP 的
过量伤害。公开的 Core 摧毁参与榜会给摧毁 Tick 内每名伤害过该 Core 的玩家增加 1。
详见排行榜 API。
返回结果​
战斗结果出现在下一条 state.events 里，形如：
{  "event_id": "e1841781-2a89-44e4-a5ce-d4bbc46d33a1",  "tick": 10583,  "event_type": "SHOT_HIT",  "actor_id": "9d3e4941-2816-4a39-a220-df8cd95e877d",  "target_id": "175f47f4-f7de-4785-b45c-9a2d2289a8ea",  "position": [120, 85]}
普通可见状态里的敌方 Core 会带公开的 owner_username，但没有内部 owner ID；
敌方 Unit 不带所属玩家的 username。

## 摧毁与重生

摧毁与重生
Core 摧毁​
Core 在战斗中 HP 归零，或者存活 Core 结算 SELF_DESTRUCT 时，下面这些同时发生：

Core 被移除；
如果 Core 因战斗被摧毁，它的库存会尝试交给本 Tick 对这个 Core 造成伤害最多的玩家；
主动自毁则直接销毁库存；
这名玩家的所有 Unit 被移除；
这些对象剩下的计划也就没意义了；
携带中的 Champion Beacon 按掉落规则落地；
玩家暂时进入 RESPAWNING，等待本 Tick 后面的出生点解析。

账号和 Agent 的访问权限不受影响。
战斗摧毁优先于已提交的自毁。主动自毁不产生伤害、摧毁参与或库存归属。它的私有
CORE_DESTROYED 使用 reason_code: SELF_DESTRUCT，不含 destroyed_by；攻击摧毁则
使用 reason_code: ATTACK。
库存归谁​
所有攻击者照常获得 Core 摧毁参与。资源归属是另一项确定性判断：

分别累计每名玩家在摧毁 Tick 对这个 Core 造成的伤害。
总伤害最高者获胜；伤害相同时，玩家 UUID 原始字节序较小者获胜。
全部战斗伤害结算后，获胜者必须仍有存活 Core。
最多存入获胜者战后容量 max(10, population × 5)，多出的部分直接销毁。

如果获胜者的 Core 也在这个战斗 Tick 被摧毁，受害者库存全部销毁。资源不会进入刚重生
的 Core，也不会顺延给第二名。游戏没有自动按人口造成的维护伤害。
同 Tick 有多个 Core 被摧毁时，按受害者玩家 UUID 原始字节序处理。先夺取的资源会占用
后续战利品可用的容量。Core 存活的获胜者会收到私有 CORE_RESOURCES_CAPTURED：
{"amount":3,"available":8,"destroyed":5,"capacity":10}
amount 是实际存入量，available 是受害者摧毁前的库存，destroyed 是装不下而销毁
的数量。获胜者已经满仓时，事件仍会发送，且 amount 为 0。
立即重生​
复活没有冷却。仍在同一个结算 Tick 内，确定性出生点解析会立即尝试部署新的 Core 和
Worker。正常情况下，你收到的下一份状态已经是 ACTIVE，事件里会同时出现
CORE_DESTROYED 和 CORE_RESPAWNED。
只有找不到合法出生点时，玩家才会保持 RESPAWNING。这种异常情况下，下一份状态长这样：
{  "status": "RESPAWNING",  "respawn_at_tick": 10604,  "resources": 0,  "population": 0,  "champion_beacon": {"position": [0, 0]},  "objects": [],  "events": []}
respawn_at_tick 表示下一次重试的 Tick，不是冷却结束时间。每次失败只顺延一个 Tick，
并换下一组确定性候选继续尝试。
恢复资产​
重生成功之后你会拿到：
资产值新 Core5 HP、5 护盾资源5Worker1无敌保护无
新的 Core 和 Worker 用的是新 UUID，已摧毁的 UUID 永不复用。
出生位置​
通常要求距离最近的存活 Core 有 20-30 的曼哈顿距离，并且在合法候选里优先挑周围实体
密度更低的地方。Core 落点一定是合法空地，而且至少有两个可通行的相邻格。
Tick、世界、账号和重生次数都相同，算出来的候选序列就每次都一样——崩溃重放的确定性
靠的就是这一点。

## 规则速查

规则速查
时序​
规则数值全局命令窗口15 秒资源补充每 4 个已结算 Tick（约 1 分钟）Core 迁移每格 4 个逻辑 TickCore 重生尝试被摧毁的同一个 TickWebSocket Ping 间隔20 秒WebSocket Pong 超时60 秒凭证重新校验约 5 秒推荐重连退避250 ms → 5 秒，带随机抖动
Core​
属性数值HP5护盾5携带 Beacon 时护盾上限10视野5初始资源5初始 Worker1资源容量max(10, population × 5)HP 恢复1 资源 → 1 HP，战斗后结算护盾修复1 资源 → 1 护盾
Units​
UnitHP视野基础价格伤害 / 射程Worker235无Vanguard4410对相邻目标格造成 1 伤害Ranger2512八方向直线 1-3 格造成 1 伤害
世界​
规则数值单格容量2 个占位实体地形类型EMPTY、RESOURCE、OBSTACLE区块大小32×32中央区块环cx, cy ∈ {-1, 0} 的 2×2 个区块资源配额每区块 max(2, floor(16 × 8 / (8 + ring)))与最近存活 Core 的出生距离20-30坐标类型有符号 int64 [x, y]Beacon 初始位置[0, 0]
经济​
axis(c) = c if c >= 0 else -c - 1ring = axis(cx) + axis(cy)resource_quota = max(2, floor(16 * 8 / (8 + ring)))
一个点让普通 Worker 得到 1 资源，让 Beacon 玩家的 Worker 得到 2 资源；两种情况都只
消耗一个点。同点竞争由最低的合格 Worker UUID 获胜。
Worker 死亡时会把全部 Cargo 掉在最后所在格。普通回收一次取 1 点，有 Beacon 时最多
取 2 点，但不会超过资源堆剩余量。Cargo 资源堆不计入区块自然资源配额。
population = Worker + Vanguard + Rangerresource_capacity = max(10, population × 5)k = max(0, floor((population - 20) / 5) + 1)unit_price = round_half_up(base_price × (13 / 10)^k)
交付只存入能装下的部分。人口下降时，高于新容量的库存会立刻销毁。
游戏没有每 Tick 自动扣除的维护费。动态价格使用同 Tick 自毁和战斗死亡后的存活人口；
精确分数只在最后舍入一次，刚好一半时向上取整。初始和重生附带的 Worker 免费。
战斗中被摧毁 Core 的库存交给本 Tick 对它总伤害最高的玩家，但不能超过这个容量。伤害
相同时按玩家 UUID 原始字节序；超额资源销毁，获胜方 Core 同 Tick 也死亡时则全部销毁。
生产前人口WorkerVanguardRanger0-195101220-247131625-298172030-34112226100-1044338651,038
命令​
限制数值幂等键8-128 个可见 ASCII 字节每个 (player, tick, source) 的新提交64每种凭证来源的并发命令体4WebSocket 入站帧限制1024 字节WebSocket 消息tick、state、received命令来源AGENT、MANUAL
