# 电商客服扩展：虚拟店铺 · 实时通信 · 人机协同（A/B/C/D）

本文档描述把 PAHF 智能体升级为**完整电商智能客服系统**所新增的四个模块。整体保持
**SQLite 轻量、进程内、零额外中间件**（无需 Redis/MQ），可单机一键跑通。

```
顾客商城(聊天挂件) ──┐                          ┌── 坐席工作台(队列/接管/AI建议)
                     │  WebSocket / REST        │
              ┌──────▼──────────────────────────▼──────┐
              │ FastAPI 网关 (backend/main.py)          │
              │  实时路由 backend/realtime/api.py        │
              └──────┬───────────────┬──────────────────┘
        ┌────────────▼───┐   ┌───────▼────────┐   ┌──────────────┐
        │ AI 管线         │   │ 人机协同引擎    │   │ 业务服务      │
        │ LangGraph+PAHF  │◄──┤ ChatService     │   │ CatalogStore  │
        └─────────────────┘   │ + 升级路由       │   │ (商品/订单)   │
                              └───────┬─────────┘   └──────────────┘
                          SQLite: conversations / messages / catalog / pahf
```

---

## A. 虚拟店铺数据层 + 电商工具

- `backend/tools/catalog_store.py`：SQLite 商品库，首次启动**自动灌入种子数据**
  （4 大类 9 商品含规格/库存、2 客户、4 订单含物流轨迹、3 优惠券）。
  表：`products / skus / customers / orders / order_items / shipments / coupons / return_requests`。
- `backend/tools/commerce.py`：10 个电商工具，注册进现有 `ToolRegistry`：
  `product_search · get_product_detail · check_inventory · get_order · list_orders ·
   track_shipment · recommend_products · list_coupons · apply_coupon · initiate_return`。
- `backend/tools/planner.py` `_plan_commerce`：规则识别电商意图（搜索/订单/物流/退货/优惠券/推荐）。
- 关键设计：`customer_id == user_id == PAHF person_id`，同一个人的订单与长期记忆天然打通。

> 中文检索用 `_search_units`（ascii 分词 + 中文字符 bigram 子串匹配），解决「卫衣」匹配「圆领卫衣」。

---

## B. 实时通信 + 会话持久化

- `backend/realtime/conversation_store.py`：SQLite 持久化会话与消息，状态机
  `bot → queued → human → resolved`。重启不丢历史，人工可见全程。
- `backend/realtime/events.py`：进程内 `asyncio` 发布/订阅事件总线。话题：
  `conv:{conversation_id}`（单会话流）、`agents`（坐席台全局流）。
- WebSocket（`backend/realtime/api.py`）：
  - `/ws/customer/{customer_id}`：顾客收发消息（主通道）。
  - `/ws/agent/{agent_id}`：坐席在线状态 + 队列/升级通知推送。
  - `/ws/conversation/{conversation_id}`：只读订阅某会话（坐席查看实时消息）。

---

## C. 人机协同（HITL）⭐

### 升级路由（何时转人工）
`backend/realtime/escalation.py` `evaluate_escalation(...)`，确定性、可解释，每次决策都带触发信号：

| 优先级 | 信号 | 触发示例 |
|---|---|---|
| urgent(4) | `complaint_or_legal` 投诉/法律/媒体 | “我要投诉到 315” |
| high(3) | `user_requested_human` 显式要人工 | “转人工” |
| high(3) | `sensitive_account` 账户/资金安全 | “银行卡被盗刷” |
| high(3) | `user_frustrated` 不满/重复 | “说了好几遍还是没解决” |
| high(3) | `high_value_return` 大额退款(≥¥1000) | 退款金额超阈值 |
| medium(2) | `tool_failure` 工具/系统失败 | 工具异常 |
| medium(2) | `no_answer_found` 订单/物流查无结果 | 查不到订单 |

- **pre-check**：调用 LLM 前先查显式/风险/重复信号 → 命中直接转人工，省一次模型调用。
- **post-check**：生成后用本轮 trace（工具错误/空结果/大额退款）兜底。

### 协同与通知
`backend/realtime/service.py` `ChatService`：
- 顾客消息按会话状态分流：`bot` 自动答；`queued` 排队提示；`human` 不自动答，但后台为坐席**自动生成 AI 建议回复（Copilot）**。
- 升级时：置 `queued` + 写系统消息 + 给顾客安抚语 + 推送 `agents` 队列事件（角标/声音由前端实现）。
- **离线兜底**：无坐席在线时发 `alert` 事件，并可选 POST 到 `NOTIFY_WEBHOOK_URL`（钉钉/企业微信/飞书/邮件网关）。
- 坐席操作：认领 `claim` → 接管；`agent_send` 回复；`release` 释放回 AI；`resolve` 结束（可带 CSAT）。
- `get_context`：客户 360（会话 + 历史订单 + PAHF 记忆画像）。

### 坐席 REST
```
GET  /api/v1/agent/conversations?status=queued|human|all
GET  /api/v1/agent/conversations/{cid}                 # 360 上下文
POST /api/v1/agent/conversations/{cid}/claim           {agent_id, agent_name}
POST /api/v1/agent/conversations/{cid}/message         {agent_id, content}
POST /api/v1/agent/conversations/{cid}/release         {agent_id}
POST /api/v1/agent/conversations/{cid}/resolve         {agent_id, csat?}
GET  /api/v1/agent/conversations/{cid}/suggest         # AI 建议回复
GET  /api/v1/agent/stats                               # 队列计数 + 在线坐席
```

---

## D. 前端三界面

`frontend/src/Root.tsx` 顶部切换：**🛒 商城 / 🎧 坐席工作台 / 🛠️ 调试台（原 App）**。

- `Storefront.tsx`：电商商城（分类/搜索/商品卡/详情规格库存）+ 右下角**浮动聊天挂件**（顾客 WS，
  支持「转人工/我的订单/优惠券」快捷键，实时显示排队/人工接入状态）。
- `AgentConsole.tsx`：坐席工作台 — 左队列（优先级/升级原因/状态）、中聊天（认领/AI建议/释放/结束）、
  右客户 360（订单 + PAHF 记忆）。坐席 WS 通知 + 会话 WS 实时消息。
- `shopApi.ts` / `shopTypes.ts`：REST + WS 客户端与类型。

---

## 用户评价系统（CSAT + 逐条反馈）

混合式收集，数据落库 `backend/realtime/feedback_store.py`（`data/feedback/feedback.db`），用于后续模型改进。

- **逐条反馈**：聊天挂件每条 AI 回复下方 👍/👎 → `message_feedback` 表（最细粒度训练信号，可做偏好对/RLHF 数据）。
- **整体评价（CSAT）**：会话结束时弹窗 — 1-5 星 + 低分原因标签（≤3 星出现）+ 可选文字 → `conversation_ratings` 表；
  分数同时回写 `conversations.csat`。
- **会话结束触发**：顾客点「结束咨询」(`POST /shop/end`) 或坐席 `resolve` → 推送 `status: resolved` →
  前端弹出评价弹窗。
- **聚合分析**：`GET /api/v1/feedback/summary` 返回平均星级、星级分布、👍率、Top 原因标签；坐席工作台顶部展示满意度概览。

```
GET  /api/v1/feedback/tags                # 低分原因标签建议
POST /api/v1/feedback/message             {conversation_id, message_id, customer_id, value: up|down}
POST /api/v1/feedback/rating              {conversation_id, customer_id, stars, tags[], comment}
GET  /api/v1/feedback/summary             # 聚合统计（后台/分析用）
GET  /api/v1/feedback/ratings?limit=      # 导出原始评价（构建训练集）
POST /api/v1/shop/end                     {customer_id}   # 顾客结束咨询 -> 触发评价
```

> 实现说明：修复了事件总线一个竞态——WS 订阅改为**同步 register/unregister**（`events.py`），
> 保证连接后紧接着发布的事件不丢，CSAT 的 `resolved` 推送可靠到达。

## 商店浏览 REST（供商城）
```
GET /api/v1/shop/categories
GET /api/v1/shop/products?query=&category=&max_price=&limit=
GET /api/v1/shop/products/{product_id}
POST /api/v1/shop/chat              {customer_id, message}   # WS 之外的兜底
GET  /api/v1/shop/conversation/{customer_id}
```

## 配置项（环境变量）
```
CATALOG_DB_PATH=./data/catalog/catalog.db
CATALOG_AUTO_SEED=true
CONVERSATION_DB_PATH=./data/conversations/conversations.db
NOTIFY_WEBHOOK_URL=          # 可选：无坐席在线时的外部告警 webhook
```

## 试用路径
1. `python run_all.py`（首启自动建库灌种子）。
2. 浏览器开前端 → **商城**：搜索/看详情 → 点开右下角客服，问「有降噪耳机吗」「订单 SO20260012 到哪了」。
3. 发「我要投诉」或「转人工」→ 会话进入排队。
4. 切到 **坐席工作台** → 队列里认领该会话 → 用「AI 建议」起草并发送 → 释放/结束。

## 后续可扩展（未做）
- 升级路由可从服务层下沉为 LangGraph 条件节点；规则 planner 升级为 LLM function-calling。
- token 级流式；Redis 化（多实例）；认证授权（顾客/坐席/管理员）；管理后台与数据看板（E 阶段）。
