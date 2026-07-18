# Chatbot Eval Lab

Eval Lab 是独立于 Chatbot 产品界面的测试页面：

```text
Chatbot（被测系统，执行请求并上报 trace）
                  ↓
Eval Lab（固定 Case、版本聚合、质量判定、结果对比）
```

Chatbot 首页和侧栏不会展示评测入口。评测人员直接访问：

```text
http://localhost:3000/evals
```

Eval Lab 有独立的页面标题、登录页、品牌头部和工作区布局。它复用已有账号验证数据
权限，但登录后只留在评测系统，不跳转到聊天页面。

## 启动

```bash
npm run backend
npm run dev
```

## 每轮优化的推荐流程

1. 固定一组回归 Case，并为每条 Case 使用稳定 ID，例如 `case-01`。
2. 在基线代码上完整执行 Case 集；Chatbot 后端自动记录 Token、LLM/工具调用、
   耗时、上下文策略和事件时间线。
3. 打开 `/evals`，为每条 run 填写 Case ID，并判定通过或未通过。
4. 修改 Agent、Graph、Prompt、模型或工具实现并重启后端。运行时代码 SHA-256
   指纹会自动形成新版本。
5. 用同一组 Case 复跑，在 Eval Lab 对比最近三版的同类问题 Token、总/平均成本、
   耗时、调用膨胀与回答通过率，并点入失败 run 回放具体阶段。

如需给优化版本设置易读名称：

```bash
OBSERVABILITY_RELEASE=skill-v2 npm run backend
```

未设置时版本名为 `agent-<代码指纹前 10 位>`。不同模型会分开聚合，避免污染
优化结论。

## 指标口径

- `Token`：模型供应商返回的 input、output 与 total token usage。
- `LLM 调用`：主回答、Supervisor、研究和上下文压缩等嵌套模型调用总数。
- `工具调用`：该轮可观测到的工具调用，并记录缓存命中与工具错误。
- `缓存命中层`：Tool Trace 中的 `cache_layer`，取值为 `l1/l2/l3`；未命中为空。
- `耗时`：从开始执行到完成回复持久化前的墙钟时间。
- `回答通过率`：`通过数 / 已评测数`；未评测 run 不进入分母。
- `运行成功率`：工作流是否结束，只用于稳定性判断，不代表回答质量。

## 数据与权限

Trace 使用现有 `message_parts` JSON 扩展位保存，不为评测本身新增业务数据库表。三层缓存的
`tool_cache_entries` 是独立的可重建派生数据表，不保存评测结论。所有评测 API
都要求登录，并通过 conversation ownership 做用户隔离。

- `GET /api/observability/overview?limit=200`
- `PATCH /api/observability/runs/{run_id}/evaluation`

## 三层缓存性能 Eval

```bash
npm run eval:cache
```

脚本把改造前 Redis-only 热读作为基线，把改造后 L1 热读作为实验组。为避免本机
Redis 安装、网络和其他租户流量影响结果，Redis 替身固定模拟 1ms RTT；L3 使用
SQLAlchemy Async 连接真实临时 SQLite。输出指标包括：

- before/after 的 p50、p95、mean 和总耗时；
- p50 加速倍数与延迟下降百分比；
- 晋升后省去的 Redis GET 次数；
- 首次 L2 晋升、L3 SQLite 写入和 L3 回填耗时（只作冷路径观察，不设门槛）；
- L2→L1 晋升、L3→L2/L1 回填、Redis 故障回退和 value 完整性。

这是可重复微基准，不等同于线上 Redis/数据库压测；线上效果还取决于真实 RTT、
key 热度分布、进程数和 L1 命中率。脚本要求正确性全部通过且 p50 至少提升 2 倍。
