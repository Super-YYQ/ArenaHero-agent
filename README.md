# ArenaHero-agent

Arena Hero 经济流无人值守 Agent：Worker 采集资源、Core 滚雪球生产，少量 Vanguard 守家。

游戏：[app.arenahero.io](https://app.arenahero.io/) · 规则：[doc.arenahero.io](https://doc.arenahero.io/zh-Hans/) · 本地摘录：[ArenaHero规则全文.md](ArenaHero规则全文.md)

## 做什么

- 空载 Worker 认领最近已知资源点，采满回 Core 交付
- 没有资源时按 **16 方向 × 4 环** 侦察最久没扫过的 32×32 区块
- 资源够就造 Worker（上限可配，默认 10）；3 个 Worker 后造最多 2 个 Vanguard 自卫
- HARVEST_FAILED 后该格子冷却 8 Tick；卡住 3 Tick 换侦察目标；禁止回头，避免 2 格振荡
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
