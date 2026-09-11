# ArenaHero-agent

Arena Hero 经济流无人值守 Agent：Worker 采集资源、Core 滚雪球生产，少量 Vanguard 守家。

游戏：[app.arenahero.io](https://app.arenahero.io/) · 规则：[doc.arenahero.io](https://doc.arenahero.io/zh-Hans/) · 本地摘录：[ArenaHero规则全文.md](ArenaHero规则全文.md)

## 做什么

- 空载 Worker 认领最近已知资源点，采满回 Core 交付
- 没有资源时按 **16 方向 × 4 环航点** 侦察：没去过的近处优先，障碍格跳过；到点、卡住或绕圈无进展则换目标
- 资源够就造 Worker（上限可配，默认 10）；3 个 Worker 后造最多 2 个 Vanguard 自卫
- HARVEST_FAILED 后该格子冷却 8 Tick；卡住 3 Tick 换侦察目标；到点停下再换目标，禁止回头，避免 2 格振荡
- 断线 5 秒自动重连；障碍/资源记忆落盘 `memory.json`

浏览器不用开。Agent 走官方 HTTP + WebSocket API，跟网页前端完全独立。

## 要求

- Python 3.11+
- `pip install arena-hero`

## 运行

```bash
copy config.example.json config.json   # Linux/macOS: cp config.example.json config.json
```

编辑 `config.json`，把 `api_key` 换成你在 [app.arenahero.io](https://app.arenahero.io/) 用 LINUX DO Connect 登录后生成的 Key。

```bash
python agent.py
```

日志写到 `agent.log`。停：`Ctrl+C`。

| 配置 | 默认 | 含义 |
|---|---|---|
| `max_workers` | 10 | Worker 人口上限 |
| `max_vanguards` | 2 | 自卫 Vanguard 上限 |
| `log_level` | INFO | 日志级别 |

**不要把 `config.json` 提交到 git。** 它已被 `.gitignore` 忽略。Key 相当于账号凭据。

## 文件

| 文件 | 作用 |
|---|---|
| `agent.py` | 入口：连接、每 Tick 决策、重连 |
| `strategy.py` | 纯函数策略（寻路、分配、侦察、自卫） |
| `memory.py` | 地图记忆：障碍永久、资源点带时间戳 |
| `test_strategy.py` | 策略单测，不联网 |

```bash
python test_strategy.py
```

## 策略要点

侦察选点参考社区成熟方案 [Drew-Z/arena-hero-agent](https://github.com/Drew-Z/arena-hero-agent)：按区块覆盖时间去扫最久未见的区块，而不是朝一个远点走直线。资源按区块配额刷新，扫过才知道有没有点。

Vanguard 平时贴 Core 蹲守，邻格有敌则 SWEEP；Worker 遭遇敌人向 Core 撤退。不主动抢 Beacon。

## 参考

### 官方

| | |
|---|---|
| 游戏 | [app.arenahero.io](https://app.arenahero.io/) |
| 规则与 API | [doc.arenahero.io/zh-Hans](https://doc.arenahero.io/zh-Hans/) |
| 世界与 Tick | [rules/world-and-ticks](https://doc.arenahero.io/zh-Hans/rules/world-and-ticks) |
| 规则速查 | [reference/numbers](https://doc.arenahero.io/zh-Hans/reference/numbers) |
| Python SDK | [sdk/quickstart](https://doc.arenahero.io/zh-Hans/sdk/quickstart) · 包名 `arena-hero` |
| 原始 API | [agent/quickstart](https://doc.arenahero.io/zh-Hans/agent/quickstart) |
| 游戏介绍帖 | [linux.do/t/topic/2703804](https://linux.do/t/topic/2703804) |

### 社区 Agent / 经验（侦察与发育参考）

| | |
|---|---|
| Drew-Z 无人值守 Agent（本仓库侦察策略主要来源） | [github.com/Drew-Z/arena-hero-agent](https://github.com/Drew-Z/arena-hero-agent) · [介绍帖](https://linux.do/t/topic/2703873) |
| 新手快速上路 / 踩坑复盘 | [linux.do/t/topic/2706070](https://linux.do/t/topic/2706070) |
| 其他开源 Agent | [linux.do/t/topic/2726683](https://linux.do/t/topic/2726683) |
| 进化框架（含可部署 Agent） | [linux.do/t/topic/2723397](https://linux.do/t/topic/2723397) |
| Agent 评测与模拟器 | [linux.do/t/topic/2757655](https://linux.do/t/topic/2757655) |

Drew-Z 仓库里我们**已经用上**的：16 方向 × 4 环侦察、按区块 `last_seen` 选点、HARVEST 失败冷却、卡住换目标。选点改为全方向候选 + 到点换朝向，避免多人锁在东/东南近处打转。

**还没搬、值得下一步做的**（按收益大致排序）：

1. **A\* 寻路** — 现在是单步贪心，绕复杂障碍会抖；走不通才换目标，远点经常绕路失败。
2. **匈牙利算法分配资源** — Worker 多了之后「就近认领」会撞车；最小费用匹配 + 粘性奖励更稳。
3. **Worker 冲到 18** — 人口 0–19 是基础价，社区发育流默认打满这段再转兵。
4. **Core 容量缓冲** — 留 10 点空仓，避免掉两个 Unit 后容量下降把库存销毁。
5. **威胁分级** — 看见敌人不是只让 Worker 跑：预估伤害、Vanguard 拦截、必要时 Core 迁移或自毁重生。
6. **开机自启** — Windows 任务计划 / 已有社区方案的 Docker + systemd，关终端也不停。

不在近期范围：抢 Beacon、主动进攻、Ranger 风筝。当前目标仍是稳定攒资源。
