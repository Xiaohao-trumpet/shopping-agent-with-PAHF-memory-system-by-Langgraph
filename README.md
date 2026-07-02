# 智能客服（PAHF 记忆增强版）

> 项目组：25组-电商售后客服与用户评价分析系统
> 组员：周晓昊、唐执策、张承涛

一个面向真实业务场景的智能客服项目：**FastAPI + LangGraph + OpenAI 兼容接口 + 可观测工具调用 + PAHF 长期记忆系统**。  
项目重点是“可持续个性化对话”：助手不只会聊天，还能在多轮对话中稳定记住用户事实与偏好，并在后续回复中正确使用。

## 项目简介（What / Why）

这个项目解决两个核心问题：

1. 如何用 OpenAI 兼容接口快速接入任意大模型并落地客服场景。
2. 如何让助手具备“长期可更新记忆”，而不是每轮都从零开始。

本仓库已将旧版自定义记忆系统完全替换为 **PAHF（Meta 论文与开源实现）**，并通过 LangGraph 固化为标准流程：

`检索 -> 注入 -> 生成 -> 抽取 -> 更新`

这样做的直接收益：

- 记忆按 `person_id`（本项目中等价于 `user_id`）隔离，避免串用户。
- 支持相似记忆检测与更新，减少重复写入。
- 支持 SQLite / FAISS 持久化，重启后记忆仍可用。
- 记忆变更过程可通过接口和 trace 直接观测。

## 核心特性（Features）

- **PAHF 作为唯一记忆系统**（无备用实现、无回退分支）
- **LangGraph 记忆编排节点**：
  - `memory_retrieval_node`
  - `assistant_generation_node`
  - `memory_extraction_node`
  - `memory_update_node`
- **OpenAI 兼容 API**：`/v1/models`、`/v1/chat/completions`
- **FastAPI 业务 API**：健康检查、场景选择、会话对话、PAHF 记忆管理
- **前端工作台（React + Vite）**：
  - 对话
  - 模型/场景切换
  - PAHF 记忆增改查 / 相似查找
  - trace 可视化
- **工具调用子系统**：知识库检索、工单创建/查询（可开关）

## 电商客服扩展：虚拟店铺 · 实时 · 人机协同（新增）

在原有「AI 内核」之上扩展为**完整电商智能客服系统**，保持 SQLite 轻量、单机一键跑通：

- **虚拟店铺数据层**：`backend/tools/catalog_store.py` 内置 55 件、8 类真实感商品，以及订单/物流/优惠券/退货库，首启自动灌种子；
  新增 10 个电商工具（商品搜索、订单查询、物流追踪、推荐、优惠券、退货等）。
- **实时通信 + 会话持久化**：`backend/realtime/` 提供会话落库（状态机 `bot→queued→human→resolved`）、
  进程内事件总线与 WebSocket（顾客 / 坐席 / 会话三类通道）。
- **人机协同（HITL）**：可解释的**升级路由**（显式转人工 / 投诉 / 账户安全 / 重复不满 / 工具失败 / 查无结果），
  坐席队列、认领接管、AI 建议回复（Copilot）、客户 360 上下文、离线 webhook 告警。
- **前端三界面**：顶部切换 **🛒 商城（含浮动聊天挂件） / 🎧 坐席工作台 / 🛠️ 调试台**。
- **用户评价系统**：每条 AI 回复 👍/👎 + 会话结束 CSAT（星级 + 低分标签 + 文字），数据落库并提供聚合统计接口，用于后续模型改进。
- **商品与店铺评价分析（发展潜力）**：`backend/analytics/` 采集商品维度评价，用**确定性可解释**的潜力评分引擎
  （满意度/增长势能/口碑情绪/评价体量/销售拉动 → 0–100 分 + 明星/潜力/平稳/预警分档 + 驱动因子）评估
  单品与整店的发展潜力；AI 层可**生成评价 / 生成经营洞察**（离线自动降级为规则引擎）；后台「潜力分析」页含
  店铺潜力仪表盘、分类潜力榜、商品潜力排行与深度分析抽屉。

> 详见 [`docs/ECOMMERCE_REALTIME_HITL.md`](./docs/ECOMMERCE_REALTIME_HITL.md) 与
> [`docs/REVIEW_ANALYTICS.md`](./docs/REVIEW_ANALYTICS.md)。

## 系统整体流程图

![系统整体流程图](./assets/流程图.png)

上图表达的主链路是：**前端请求 -> FastAPI API Gateway -> LangGraph Orchestrator -> PAHF MemoryBank / Retriever / LLM -> 返回前端**。  
其中记忆相关关键点：请求前先检索，回复后再抽取并更新，形成闭环。

## 架构概览（模块分层）

- `frontend/`：React + Vite UI，调用后端 OpenAI 兼容接口与记忆接口
- `backend/main.py`：FastAPI 入口、路由、生命周期初始化
- `backend/agents/`：LangGraph 工作流与节点实现
- `backend/pahf_memory/`：PAHF MemoryBank 集成层（SQLite/FAISS、检索、更新）
- `backend/models/universal_chat.py`：统一模型调用抽象（OpenAI compatible）
- `backend/tools/`：工具注册、规划、执行与存储
- `PAHF/`：官方 PAHF 源码（本项目直接导入使用）

## 快速开始（Quickstart）

### 1) 最快启动路径（10 分钟内可跑通）

> 必须使用 conda 环境：`servicebot`

```powershell
# 进入项目根目录
git clone https://github.com/Xiaohao-trumpet/shopping-agent-with-PAHF-memory-system.git

# 激活环境（必须）
conda create -n shopping-agent python=3.10

# 安装本项目依赖（首次）
pip install -r requirements.txt

# 安装 PAHF 依赖（首次，使用本地 PAHF 目录）
pip install -r PAHF/requirements.txt

# 安装前端依赖（首次）
cd frontend
npm install
cd ..

# 准备配置文件
编辑 `.env`，至少配置这三项：

```env
MODEL_NAME= " "
BASE_URL= " "
API_KEY=你的模型服务密钥
```

一键启动前后端：

```powershell
python run_all.py
```

启动后默认访问：

- 前端：`http://localhost:3000`
- 后端健康检查：`http://localhost:8000/health`
- 模型列表：`http://localhost:8000/api/v1/models`

### 2) 启动成功验证

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/models
```

期望：

- `health` 返回 `{"status":"ok", ...}`
- `models` 返回 `{"object":"list","data":[...]}`

然后打开浏览器 `http://localhost:3000`，输入消息即可对话。

### 3) 开发模式（热更新 / 日志 / 调试）

后端热更新（`run_backend.py` 内置 `reload=True`）：

```powershell
python run_backend.py
```

前端开发（另开一个终端）：

```powershell
cd frontend
npm run dev -- --host 0.0.0.0 --port 3000
```

调试建议：

- 将 `.env` 中 `LOG_FORMAT` 设为 `text`，本地日志更易读。
- 观察前端右侧 `Tools / Trace` 面板，重点看：
  - `retrieved_memories`
  - `memory_candidate`
  - `memory_update`
- 用记忆接口直接核对持久化结果（见下文 API 示例）。

## 配置说明（.env）

### 基础运行配置

```env
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
LOG_FORMAT=json
CORS_ORIGINS=*
```

### 模型配置

```env
MODEL_NAME= ''
BASE_URL= ''
API_KEY=your_api_key_here
DEFAULT_TEMPERATURE=0.7
DEFAULT_MAX_TOKENS=1024
SYSTEM_PROMPT_SCENE=default
```

### PAHF 记忆配置（核心）

```env
PAHF_BACKEND=sqlite
PAHF_SQLITE_DB_PATH=./data/pahf/pahf_memory.db
PAHF_FAISS_PATH=./data/pahf/pahf_memory
PAHF_TOP_K=5
PAHF_SIMILARITY_THRESHOLD=0.45
PAHF_QUERY_ENCODER=facebook/dragon-plus-query-encoder
PAHF_CONTEXT_ENCODER=facebook/dragon-plus-context-encoder
PAHF_EMBED_DEVICE=
PAHF_ENABLE_PRE_CLARIFICATION=true
PAHF_ENABLE_POST_CORRECTION=true
PAHF_LLM_MODEL=
```

说明：

- `PAHF_BACKEND` 支持 `sqlite` / `faiss`。
- 默认使用 `sqlite`（推荐先跑通）。
- 若使用 `faiss`，需本地环境已具备 FAISS 运行条件。

### 工具子系统配置（可选）

```env
TOOLS_ENABLED=true
TOOLS_ALLOWLIST=kb_search,create_ticket,get_ticket,list_tickets
TOOL_MAX_CALLS_PER_TURN=3
TOOL_TIMEOUT_SECONDS=3.0
TOOL_RATE_LIMIT_PER_MINUTE=30
KB_FILE_PATH=./data/kb/faq.json
TICKET_DB_PATH=./data/tickets/tickets.db
```

## API 说明（核心接口 + 示例）

### 健康与模型

- `GET /health`
- `GET /api/v1/models`
- `GET /v1/models`（OpenAI 兼容）

### 聊天接口

- `POST /api/v1/chat`
- `POST /api/v1/chat/completions`
- `POST /v1/chat/completions`（OpenAI 兼容）

示例：

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus",
    "user": "demo_user",
    "stream": false,
    "messages": [
      {"role": "user", "content": "我叫小昊，我的鞋码是30。"}
    ]
  }'
```

### PAHF 记忆接口

- `POST /api/v1/memory`：新增记忆
- `GET /api/v1/memory?user_id=...`：列出用户记忆
- `GET /api/v1/memory/{memory_id}?user_id=...`：按 ID 查询
- `PUT /api/v1/memory/{memory_id}`：更新记忆
- `POST /api/v1/memory/search`：语义检索
- `POST /api/v1/memory/find-similar`：查找最相似记忆

示例：新增 + 查询 + 搜索

```bash
# 新增
curl -X POST "http://localhost:8000/api/v1/memory" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","text":"用户鞋码是30"}'

# 列表
curl "http://localhost:8000/api/v1/memory?user_id=demo_user"

# 搜索
curl -X POST "http://localhost:8000/api/v1/memory/search" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","query":"鞋码","top_k":5}'
```

## 记忆系统详解（PAHF，重点）

### 1) 参考来源

本项目的记忆机制参考并集成了 Meta 提出的 PAHF 思路与官方开源实现：

- 论文：<https://arxiv.org/abs/2602.16173>
- 代码：<https://github.com/facebookresearch/PAHF>

![teaser](./PAHF/assets/teaser.jpeg)

### 2) 本项目中的记忆闭环

在一次用户对话中，记忆按以下顺序工作：

1. **检索（Retrieval）**
   - `memory_retrieval_node` 调用 PAHF MemoryBank。
   - 按 `person_id=user_id` 检索 top-k 相关记忆。
2. **注入（Injection）**
   - 检索结果被组织到提示词中（`PAHF Memory Context` / `Retrieved PAHF Memories`）。
3. **生成（Generation）**
   - `assistant_generation_node` 在“当前消息 + 检索记忆 + 工具结果”条件下生成回复。
4. **抽取（Extraction）**
   - `memory_extraction_node` 判断本轮是否包含“稳定可长期保存”的用户事实/偏好。
5. **更新（Update）**
   - `memory_update_node` 执行相似检测：
     - 无相似 -> 新增
     - 有相似且同主题 -> 合并并更新
     - 有相似但不同主题 -> 新增

### 3) 为什么这样设计

- 先检索再生成：保证回复“带着用户画像”进行。
- 生成后再抽取更新：避免把短暂噪声直接写入长期库。
- 相似检测 + 同主题判断 + 合并：避免记忆无限重复、降低冲突。

### 4) PAHF 关键能力在本项目中的体现

- **person_id 隔离**：每个 `user_id` 绑定独立记忆命名空间。
- **相似记忆检测**：通过 `find_similar_memory` 先找候选再决策更新。
- **去重与覆盖更新**：支持“纠正语句”覆盖旧偏好，而不是仅追加。
- **持久化**：SQLite/FAISS 后端持久存储。
- **检索鲁棒性**：基于 DRAGON+ embedding 检索。

### 5) 短期记忆 vs 长期记忆

- **短期记忆**：当前轮与近轮消息上下文（前端会话消息 + 请求消息历史）。
- **长期记忆**：PAHF MemoryBank 中可持久化的用户稳定事实/偏好。

边界原则：

- 临时上下文用于当下回答；
- 稳定事实/偏好经过抽取与判断后写入长期记忆。

### 6) 端到端示例场景

示例对话：

1. 用户：`我叫小昊，我的鞋码是30。`
2. 助手：正常回复后，系统抽取“鞋码是30”并写入 PAHF。
3. 用户：`我鞋码是多少？`
4. 助手：检索到长期记忆并回答 `30`。
5. 用户：`更正一下，我现在鞋码是31。`
6. 系统：触发相似检测与更新，覆盖/合并旧记忆。
7. 用户：`我现在鞋码是多少？`
8. 助手：基于更新后的 PAHF 记忆回答 `31`。

![example](./assets/example.png)



## 目录结构（真实结构）

```text
.
├─ backend/
│  ├─ agents/                 # LangGraph 图与节点
│  ├─ models/                 # 模型抽象（UniversalChat）
│  ├─ pahf_memory/            # PAHF 记忆集成实现
│  ├─ prompts/                # Prompt 模板与构建器
│  ├─ tools/                  # 工具注册/规划/执行
│  ├─ utils/                  # 日志/异常/兼容层
│  ├─ config.py
│  └─ main.py
├─ frontend/
│  ├─ src/
│  │  ├─ App.tsx              # 对话+记忆+trace UI
│  │  ├─ api.ts               # 前端 API 调用
│  │  └─ styles.css
│  └─ package.json
├─ PAHF/                      # 官方 PAHF 仓库（本地）
├─ docs/
│  ├─ PAHF_MEMORY.md
│  └─ FRONTEND.md
├─ tests/
├─ .env.example
├─ run_all.py
├─ run_backend.py
├─ run_tests.py
└─ README.md
```

## 开发指南

### 本地开发推荐流程

```powershell
# 终端 1：后端（热更新）
python run_backend.py

# 终端 2：前端
cd frontend
npm run dev -- --host 0.0.0.0 --port 3000
```

### 测试

```powershell
# 全量
pytest

# 或使用脚本
python run_tests.py

# 记忆/图相关重点用例
pytest tests/test_graph.py tests/test_pahf_memory_api.py -q
```

### 代码风格建议

- 后端提交前至少运行 `pytest`。
- 前端提交前至少运行：

```powershell
cd frontend
npm run build
```

## Troubleshooting（常见问题）

1. 模型调用失败（401/403/5xx）
- 检查 `.env` 的 `API_KEY`、`BASE_URL`、`MODEL_NAME` 是否与供应商一致。

2. 前端打不开或接口跨域失败
- 检查 `8000/3000` 端口占用。
- 检查 `CORS_ORIGINS` 配置（本地可先用 `*`）。

3. 首次请求很慢
- DRAGON+ 编码器首次加载需要时间，属于正常冷启动现象。

4. FAISS 后端不可用
- 确保本地已正确安装与 Python/系统匹配的 FAISS。
- 不满足条件时先使用 `PAHF_BACKEND=sqlite`。

5. 记忆未更新
- 检查 `PAHF_ENABLE_POST_CORRECTION=true`。
- 在返回 trace 中查看 `memory_candidate` 与 `memory_update`。

6. PAHF 存储路径权限问题
- 检查 `PAHF_SQLITE_DB_PATH` / `PAHF_FAISS_PATH` 所在目录是否可写。

## 后台登录与数据管理

启动前后端后访问 `http://localhost:3000`，顶部切换到「后台管理」即可进入中后台。默认演示账号：

```text
账号：admin
密码：admin123456
```

后台管理能力：

- 登录认证：`POST /api/v1/auth/login`，后续管理接口使用 `Authorization: Bearer <token>`。
- 总览看板：商品、SKU 库存、订单金额、会话状态、在线坐席、评价均分与消息满意度。
- 会话管理：查看 AI 接待、待人工、人工中、已完结会话。
- 商品库存：查看商品、分类、价格、SKU 数量和库存。
- 服务评价：查看会话 CSAT 星级分布、低分标签、评价评论明细。
- **潜力分析**：店铺发展潜力仪表盘与 AI 经营洞察、分类潜力榜、商品发展潜力排行；进入商品深度分析抽屉可看
  潜力驱动因子、评分分布、好评/差评、销量与评价明细，并支持「生成 AI 洞察」「AI 生成评价」。
- 管理员账号：查看本地后台账号与最近登录时间。

本项目后端 SQL 使用本地 SQLite，启动时自动建表和种子演示数据，适合课程演示与单机部署。主要数据库文件：

```text
data/catalog/catalog.db          # 商品、SKU、客户、订单、物流、优惠券、退货
data/conversations/conversations.db  # 客服会话与消息
data/feedback/feedback.db        # 消息点赞/点踩与会话评分
data/admin/admin.db              # 管理员账号与登录会话
data/pahf/pahf_memory.db         # PAHF 用户记忆
```

这些运行时数据库目录已加入 `.gitignore`，不会提交到仓库。正式使用前请在 `.env` 修改 `ADMIN_DEFAULT_PASSWORD`，首次创建管理员后也建议删除旧的 `data/admin/admin.db` 重新生成。

## Roadmap

- 支持流式输出（`/api/v1/chat/stream` 实装）
- 丰富工具链与业务插件
- 增加更细粒度的记忆审计与可视化
- 增加部署模板（Docker / 云服务）

## License

当前仓库尚未单独声明 License 文件。  
如用于开源分发，建议补充 `LICENSE`（例如 MIT / Apache-2.0）。

## 致谢 / 引用

- PAHF 论文：**“PAHF”**, arXiv:2602.16173  
  <https://arxiv.org/abs/2602.16173>
- 官方实现：Meta Research PAHF  
  <https://github.com/facebookresearch/PAHF>
- 相关框架：FastAPI、LangGraph、React、Vite
