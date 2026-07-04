"""
FastAPI application entrypoint.
Provides HTTP API endpoints for the conversational AI system.
"""

import asyncio
import time
from typing import Optional, Any
from contextlib import asynccontextmanager
import json
import uuid

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
import logging

from .config import get_model_config, get_app_config
from .admin_store import AdminStore
from .auth_deps import require_admin, require_admin_token
from .models.universal_chat import UniversalChat
from .agents.graph import create_generation_graph, create_memory_writeback_graph
from .session_store import get_session_store
from .pahf_memory import build_pahf_memory_service
from .prompts.prompt_factory import get_prompt_factory
from .prompts.builder import PromptBuilder
from .tools import (
    ToolRegistry,
    ToolPlanner,
    ToolExecutor,
    FAQStore,
    TicketStore,
    CatalogStore,
    register_builtin_tools,
    register_commerce_tools,
)
from .realtime import ConversationStore, EventBus, ChatService, FeedbackStore
from .realtime.api import router as realtime_router
from .realtime.runtime import RT
from .analytics import ReviewStore, AIReviewer, AnalyticsService
from .utils.logging import setup_logging, get_logger
from .utils.exceptions import (
    ModelAPIException,
    RateLimitExceededException,
    ValidationException
)

# Initialize configuration
app_config = get_app_config()
model_config = get_model_config()

# Setup logging
setup_logging(log_level=app_config.LOG_LEVEL, log_format=app_config.LOG_FORMAT)
logger = get_logger(__name__)

# Global instances (initialized in lifespan)
model_client: Optional[UniversalChat] = None
chat_graph = None
memory_writeback_graph = None
session_store = None
pahf_memory_service = None
tool_registry = None
tool_planner = None
tool_executor = None
prompt_builder = None
admin_store: Optional[AdminStore] = None

MIN_MODEL_OUTPUT_TOKENS = 512
MAX_MODEL_OUTPUT_TOKENS = 4096


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global model_client, chat_graph, memory_writeback_graph, session_store, pahf_memory_service
    global tool_registry, tool_planner, tool_executor, prompt_builder, admin_store
    
    logger.info("Starting application...")
    
    # Initialize prompt factory and load system prompt
    prompt_factory = get_prompt_factory()
    system_prompt = prompt_factory.get_system_prompt(model_config.system_prompt_scene)
    
    # Initialize model client
    model_client = UniversalChat(
        model_name=model_config.model_name,
        base_url=model_config.base_url,
        api_key=model_config.api_key,
        system_prompt=system_prompt,
        default_temperature=model_config.default_temperature,
        default_max_tokens=model_config.default_max_tokens,
        request_timeout_seconds=app_config.MODEL_REQUEST_TIMEOUT_SECONDS,
    )
    logger.info(f"Initialized model client: {model_config.model_name}")
    
    # Initialize session store
    session_store = get_session_store(ttl_seconds=app_config.SESSION_TTL_SECONDS)
    logger.info("Initialized session store")

    # Initialize PAHF memory service
    pahf_memory_service = build_pahf_memory_service(
        app_config=app_config,
        model_config=model_config,
    )
    logger.info("Initialized PAHF memory service")

    # Initialize tool subsystem
    faq_store = FAQStore(kb_path=app_config.KB_FILE_PATH)
    ticket_store = TicketStore(db_path=app_config.TICKET_DB_PATH)
    catalog_store = CatalogStore(
        db_path=app_config.CATALOG_DB_PATH,
        auto_seed=app_config.CATALOG_AUTO_SEED,
    )
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry, faq_store=faq_store, ticket_store=ticket_store)
    register_commerce_tools(tool_registry, catalog=catalog_store)
    tool_planner = ToolPlanner(
        tools_enabled=app_config.TOOLS_ENABLED,
        max_calls_per_turn=app_config.TOOL_MAX_CALLS_PER_TURN,
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        allowlist=[name.strip() for name in app_config.TOOLS_ALLOWLIST if name.strip()],
        timeout_seconds=app_config.TOOL_TIMEOUT_SECONDS,
        rate_limit_per_minute=app_config.TOOL_RATE_LIMIT_PER_MINUTE,
        max_calls_per_turn=app_config.TOOL_MAX_CALLS_PER_TURN,
    )
    prompt_builder = PromptBuilder(prompt_factory=prompt_factory)
    logger.info("Initialized tool subsystem")

    # Initialize chat graph with PAHF memory hooks. Generation is split from
    # memory writeback so PAHF's post-action correction (extraction + add/update,
    # 2-3 extra LLM calls) never blocks the reply -- it runs afterwards as a
    # background task (see _run_memory_writeback).
    chat_graph = create_generation_graph(
        model_client=model_client,
        pahf_memory_service=pahf_memory_service,
        tool_planner=tool_planner,
        tool_executor=tool_executor,
        tool_registry=tool_registry,
        prompt_builder=prompt_builder,
        prompt_scene=model_config.system_prompt_scene,
        tools_enabled=app_config.TOOLS_ENABLED,
    )
    memory_writeback_graph = create_memory_writeback_graph(pahf_memory_service)
    logger.info("Initialized chat graph")

    # Initialize realtime + human-in-the-loop subsystem (Phase B & C)
    conversation_store = ConversationStore(db_path=app_config.CONVERSATION_DB_PATH)
    event_bus = EventBus()
    chat_service = ChatService(
        conversations=conversation_store,
        event_bus=event_bus,
        chat_graph=chat_graph,
        memory_writeback_graph=memory_writeback_graph,
        memory_writeback_mode=app_config.MEMORY_WRITEBACK_MODE,
        model_client=model_client,
        catalog_store=catalog_store,
        pahf_memory_service=pahf_memory_service,
        logger=logger,
        notify_webhook=app_config.NOTIFY_WEBHOOK_URL,
    )
    feedback_store = FeedbackStore(db_path=app_config.FEEDBACK_DB_PATH)
    if admin_store is None:
        admin_store = AdminStore(
            db_path=app_config.ADMIN_DB_PATH,
            default_username=app_config.ADMIN_DEFAULT_USERNAME,
            default_password=app_config.ADMIN_DEFAULT_PASSWORD,
            session_ttl_seconds=app_config.ADMIN_SESSION_TTL_SECONDS,
            session_secret=app_config.ADMIN_SESSION_SECRET,
        )
    RT.chat_service = chat_service
    RT.event_bus = event_bus
    RT.catalog_store = catalog_store
    RT.conversation_store = conversation_store
    RT.feedback_store = feedback_store
    RT.admin_store = admin_store
    logger.info("Initialized realtime + HITL subsystem")
    logger.info("Initialized admin auth subsystem")

    # Initialize review-analytics subsystem (product & store development potential)
    review_store = ReviewStore(db_path=app_config.REVIEW_DB_PATH)
    ai_reviewer = AIReviewer(model_client=model_client, logger=logger)
    analytics_service = AnalyticsService(
        review_store=review_store,
        catalog_store=catalog_store,
        ai_reviewer=ai_reviewer,
        feedback_store=feedback_store,
        logger=logger,
    )
    analytics_service.ensure_seeded(auto_seed=app_config.REVIEW_AUTO_SEED)
    RT.review_store = review_store
    RT.analytics_service = analytics_service
    logger.info("Initialized review-analytics subsystem")

    yield
    if pahf_memory_service is not None:
        pahf_memory_service.close()
    
    logger.info("Shutting down application...")


# Create FastAPI app
app = FastAPI(
    title="Conversational AI System",
    description="Phase 1: Foundational chatbot with extensible architecture",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount realtime storefront + agent-console routes (Phase B & C)
app.include_router(realtime_router)


# Rate limiting storage (simple in-memory)
rate_limit_store = {}


def check_rate_limit(user_id: str) -> None:
    """Simple rate limiting check."""
    current_time = time.time()
    window_start = current_time - app_config.RATE_LIMIT_WINDOW_SECONDS
    
    # Clean old entries
    if user_id in rate_limit_store:
        rate_limit_store[user_id] = [
            ts for ts in rate_limit_store[user_id] if ts > window_start
        ]
    else:
        rate_limit_store[user_id] = []
    
    # Check limit
    if len(rate_limit_store[user_id]) >= app_config.RATE_LIMIT_REQUESTS:
        raise RateLimitExceededException(
            f"Rate limit exceeded: {app_config.RATE_LIMIT_REQUESTS} requests per "
            f"{app_config.RATE_LIMIT_WINDOW_SECONDS} seconds"
        )
    
    # Add current request
    rate_limit_store[user_id].append(current_time)


# Request/Response models
class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    user_id: str = Field(..., description="User identifier")
    message: str = Field(..., min_length=1, description="User message")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, ge=1, le=4096, description="Maximum tokens to generate")


class AdminLoginRequest(BaseModel):
    """Admin login payload."""
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class CustomerLoginRequest(BaseModel):
    """Customer login payload."""
    customer_id: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class CustomerRegisterRequest(BaseModel):
    """Customer self-registration payload."""
    customer_id: str = Field(..., min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(..., min_length=6, max_length=200)
    name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)


class CustomerUserResponse(BaseModel):
    customer_id: str
    name: str
    email: str
    phone: str
    tier: str
    created_at: float


class CustomerLoginResponse(BaseModel):
    customer: CustomerUserResponse


class AdminUserResponse(BaseModel):
    username: str
    role: str
    display_name: str
    created_at: float
    last_login_at: Optional[float] = None


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: float
    user: AdminUserResponse


class GenerateReviewsRequest(BaseModel):
    """Admin request to AI-generate demo reviews for a product."""
    n: int = Field(default=5, ge=1, le=12)
    skew: str = Field(default="mixed", pattern="^(positive|mixed|critical)$")
    persist: bool = True


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    response: str = Field(..., description="Assistant's response")
    latency_ms: float = Field(..., description="End-to-end processing time in milliseconds")
    trace: Optional[dict] = Field(default=None, description="Optional execution trace metadata")


class HealthResponse(BaseModel):
    """Response model for health check."""
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_name: str
    active_sessions: int


class ModelCard(BaseModel):
    """OpenAI-compatible model card."""
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "conversational-ai-system"


class ModelListResponse(BaseModel):
    """OpenAI-compatible model list response."""
    object: str = "list"
    data: list[ModelCard]


class OpenAIChatMessage(BaseModel):
    """OpenAI-compatible chat message."""
    role: str
    content: Any


class OpenAIChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""
    model: Optional[str] = None
    messages: list[OpenAIChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    user: Optional[str] = None


class MemoryCreateRequest(BaseModel):
    user_id: str = Field(..., description="Mapped to PAHF person_id")
    text: str = Field(..., min_length=1, description="Memory content")


class MemoryUpdateRequest(BaseModel):
    user_id: str = Field(..., description="Mapped to PAHF person_id")
    text: str = Field(..., min_length=1, description="Updated memory content")


class MemoryResponse(BaseModel):
    id: int
    person_id: str
    text: str


class MemorySearchRequest(BaseModel):
    user_id: str = Field(..., description="Mapped to PAHF person_id")
    query: str = Field(..., min_length=1, description="Semantic query")
    top_k: Optional[int] = Field(default=None, ge=1, le=20, description="Max results")


class MemorySearchHit(BaseModel):
    memory: MemoryResponse
    score: float


class MemorySearchResponse(BaseModel):
    hits: list[MemorySearchHit]


class MemoryFindSimilarRequest(BaseModel):
    user_id: str = Field(..., description="Mapped to PAHF person_id")
    text: str = Field(..., min_length=1, description="Candidate memory text")
    threshold: Optional[float] = Field(
        default=None,
        description="Optional similarity threshold override",
    )


def _extract_text_content(content: Any) -> str:
    """Extract text from OpenAI content that may be a string or typed parts."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()

    return ""


def _latest_user_message(messages: list[OpenAIChatMessage]) -> str:
    """Return the latest user message text from OpenAI messages."""
    for msg in reversed(messages):
        if msg.role == "user":
            text = _extract_text_content(msg.content)
            if text:
                return text
    return ""


def _collect_system_messages(messages: list[OpenAIChatMessage]) -> str:
    parts = []
    for msg in messages:
        if msg.role == "system":
            text = _extract_text_content(msg.content)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _render_recent_history(messages: list[OpenAIChatMessage], keep: int = 8) -> str:
    rendered = []
    for msg in messages[-keep:]:
        if msg.role == "system":
            continue
        text = _extract_text_content(msg.content)
        if not text:
            continue
        rendered.append(f"{msg.role}: {text}")
    return "\n".join(rendered).strip()


def _effective_max_tokens(max_tokens: Optional[int]) -> Optional[int]:
    if max_tokens is None:
        return None
    return max(MIN_MODEL_OUTPUT_TOKENS, min(int(max_tokens), MAX_MODEL_OUTPUT_TOKENS))


def _get_admin_store() -> AdminStore:
    global admin_store
    if admin_store is None:
        admin_store = AdminStore(
            db_path=app_config.ADMIN_DB_PATH,
            default_username=app_config.ADMIN_DEFAULT_USERNAME,
            default_password=app_config.ADMIN_DEFAULT_PASSWORD,
            session_ttl_seconds=app_config.ADMIN_SESSION_TTL_SECONDS,
            session_secret=app_config.ADMIN_SESSION_SECRET,
        )
        RT.admin_store = admin_store
    return admin_store


# Admin bearer-token auth is defined in .auth_deps (not here) so the realtime
# router -- mounted below, imported before this module finishes loading --
# can gate its agent-console routes with the exact same dependency.
_require_admin_token = require_admin_token
_require_admin = require_admin


def _require_backoffice_runtime() -> None:
    if (
        RT.catalog_store is None
        or RT.conversation_store is None
        or RT.feedback_store is None
        or RT.chat_service is None
    ):
        raise HTTPException(status_code=503, detail="Backoffice runtime is not ready")


def _require_catalog_store():
    if RT.catalog_store is None:
        raise HTTPException(status_code=503, detail="Catalog runtime is not ready")
    return RT.catalog_store


def _require_analytics():
    if RT.analytics_service is None:
        raise HTTPException(status_code=503, detail="Analytics runtime is not ready")
    return RT.analytics_service


def _require_pahf_memory():
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory runtime is not ready")
    return pahf_memory_service


# Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        model_name=model_config.model_name,
        active_sessions=session_store.get_session_count()
    )


@app.get("/api/v1/models", response_model=ModelListResponse)
@app.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    """List available models in OpenAI-compatible format."""
    return ModelListResponse(
        data=[ModelCard(id=model_config.model_name)]
    )


@app.get("/api/v1/prompt-scenes")
async def list_prompt_scenes():
    """List available prompt scenes for frontend selector."""
    return {
        "scenes": ["default", "it_helpdesk"],
        "default_scene": model_config.system_prompt_scene,
    }


@app.post("/api/v1/auth/customer-login", response_model=CustomerLoginResponse)
async def customer_login(request: CustomerLoginRequest):
    """Authenticate a storefront customer against the seeded customer table."""
    customer = _require_catalog_store().authenticate_customer(
        request.customer_id, request.password
    )
    if customer is None:
        raise HTTPException(status_code=401, detail="Invalid customer id or password")
    return CustomerLoginResponse(customer=CustomerUserResponse(**customer))


@app.post("/api/v1/auth/customer-register", response_model=CustomerLoginResponse)
async def customer_register(request: CustomerRegisterRequest):
    """Create a storefront customer account and return the logged-in profile."""
    try:
        customer = _require_catalog_store().create_customer_account(
            customer_id=request.customer_id,
            password=request.password,
            name=request.name,
            email=request.email,
            phone=request.phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CustomerLoginResponse(customer=CustomerUserResponse(**customer))


@app.post("/api/v1/auth/login", response_model=AdminLoginResponse)
async def admin_login(request: AdminLoginRequest):
    """Authenticate a backoffice administrator."""
    session = _get_admin_store().authenticate(request.username, request.password)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return AdminLoginResponse(
        access_token=session["access_token"],
        token_type=session["token_type"],
        expires_at=session["expires_at"],
        user=AdminUserResponse(**session["user"]),
    )


@app.get("/api/v1/auth/me")
async def admin_me(current_admin: dict = Depends(_require_admin)):
    return {"user": current_admin}


@app.post("/api/v1/auth/logout")
async def admin_logout(token: str = Depends(_require_admin_token)):
    _get_admin_store().logout(token)
    return {"status": "ok"}


@app.get("/api/v1/admin/overview")
async def admin_overview(current_admin: dict = Depends(_require_admin)):
    _require_backoffice_runtime()
    feedback = RT.feedback_store.summary()
    conversation_counts = RT.conversation_store.counts_by_status()
    latest = RT.conversation_store.list_conversations(status="all", limit=8)
    return {
        "generated_at": time.time(),
        "admin": current_admin,
        "catalog": RT.catalog_store.admin_summary(),
        "conversations": {
            "total": sum(conversation_counts.values()),
            "by_status": conversation_counts,
            "latest": latest,
        },
        "feedback": feedback,
        "agents": {
            "online_agents": RT.chat_service.online_agent_count(),
            "agents": RT.chat_service.list_agents(),
        },
    }


@app.get("/api/v1/admin/conversations")
async def admin_conversations(
    status: str = "all",
    limit: int = 50,
    current_admin: dict = Depends(_require_admin),
):
    _require_backoffice_runtime()
    return {
        "conversations": RT.conversation_store.list_conversations(
            status=status,
            limit=max(1, min(int(limit), 200)),
        )
    }


@app.get("/api/v1/admin/products")
async def admin_products(limit: int = 100, current_admin: dict = Depends(_require_admin)):
    _require_backoffice_runtime()
    return {"products": RT.catalog_store.list_products_for_admin(limit=limit)}


@app.get("/api/v1/admin/feedback/ratings")
async def admin_feedback_ratings(limit: int = 100, current_admin: dict = Depends(_require_admin)):
    _require_backoffice_runtime()
    return {"ratings": RT.feedback_store.list_ratings(limit=max(1, min(int(limit), 500)))}


@app.get("/api/v1/admin/users")
async def admin_users(current_admin: dict = Depends(_require_admin)):
    _require_backoffice_runtime()
    admin_accounts = [
        {
            **item,
            "account_type": "admin",
            "email": "",
            "phone": "",
        }
        for item in _get_admin_store().list_users()
    ]
    customer_accounts = RT.catalog_store.list_customer_accounts_for_admin(limit=500)
    return {"users": admin_accounts + customer_accounts}


# ------------------------------------------------ review & potential analytics
@app.get("/api/v1/admin/analytics/store")
async def admin_analytics_store(ai: int = 0, current_admin: dict = Depends(_require_admin)):
    """Store-level development potential + (optional AI) executive summary."""
    analytics = _require_analytics()
    return analytics.store_analytics(with_ai=bool(ai))


@app.get("/api/v1/admin/analytics/products")
async def admin_analytics_products(
    sort: str = "score",
    order: str = "desc",
    category: str = "",
    limit: int = 200,
    current_admin: dict = Depends(_require_admin),
):
    """Per-product review stats + development-potential ranking."""
    analytics = _require_analytics()
    return {
        "products": analytics.list_product_analytics(
            sort=sort, order=order, category=category or None,
            limit=max(1, min(int(limit), 500)),
        )
    }


@app.get("/api/v1/admin/analytics/products/{product_id}")
async def admin_analytics_product_detail(
    product_id: str, ai: int = 0, current_admin: dict = Depends(_require_admin)
):
    """Deep-dive: stats, reviews, potential drivers and an insight narrative."""
    analytics = _require_analytics()
    detail = analytics.product_detail_analytics(product_id, with_ai=bool(ai))
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return detail


@app.post("/api/v1/admin/analytics/products/{product_id}/ai-insight")
async def admin_analytics_ai_insight(
    product_id: str, current_admin: dict = Depends(_require_admin)
):
    """Force a fresh AI (or heuristic-fallback) insight for a product."""
    analytics = _require_analytics()
    detail = analytics.product_detail_analytics(product_id, with_ai=True)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return {"insight": detail["insight"], "potential": detail["potential"]}


@app.post("/api/v1/admin/analytics/products/{product_id}/generate-reviews")
async def admin_analytics_generate_reviews(
    product_id: str, req: GenerateReviewsRequest, current_admin: dict = Depends(_require_admin)
):
    """AI-generate (and optionally persist) demo reviews for a product."""
    analytics = _require_analytics()
    try:
        return analytics.generate_reviews_for(
            product_id, n=req.n, skew=req.skew, persist=req.persist
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="product not found")


# --------------------------------------------------------- memory management
@app.get("/api/v1/admin/memory/customers")
async def admin_memory_customers(current_admin: dict = Depends(_require_admin)):
    """Every person_id with PAHF memories, with a count and (when the id
    matches a known storefront customer) their profile -- lets the backoffice
    verify memory is kept strictly per-user rather than shared/mixed."""
    service = _require_pahf_memory()
    persons = service.list_person_ids_with_counts()
    catalog = RT.catalog_store
    for entry in persons:
        entry["profile"] = catalog.get_customer(entry["person_id"]) if catalog is not None else None
    return {"customers": persons}


@app.get("/api/v1/admin/memory/customers/{person_id}/memories")
async def admin_memory_list(person_id: str, current_admin: dict = Depends(_require_admin)):
    service = _require_pahf_memory()
    items = service.get_all_memories(person_id)
    return {"person_id": person_id, "memories": [{"id": item.id, "text": item.text} for item in items]}


@app.delete("/api/v1/admin/memory/customers/{person_id}/memories/{memory_id}")
async def admin_memory_delete(
    person_id: str, memory_id: int, current_admin: dict = Depends(_require_admin)
):
    service = _require_pahf_memory()
    deleted = service.delete_memory(person_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": True, "memory_id": memory_id}


@app.post("/api/v1/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint for external clients."""
    user_message = _latest_user_message(request.messages)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found in messages")

    system_text = _collect_system_messages(request.messages)
    history_text = _render_recent_history(request.messages[:-1], keep=10)

    composed_message = user_message
    context_parts = []
    if system_text:
        context_parts.append("System instructions:\n" + system_text)
    if history_text:
        context_parts.append("Conversation so far:\n" + history_text)
    if context_parts:
        composed_message = "\n\n".join(context_parts) + "\n\nCurrent user message:\n" + user_message

    user_id = request.user or "frontend_user"
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model_name = request.model or model_config.model_name
    effective_max_tokens = _effective_max_tokens(request.max_tokens)

    if request.stream:
        if model_client is None:
            raise HTTPException(status_code=503, detail="Model client is not initialized")

        async def event_stream():
            first = True
            try:
                async for content in model_client.astream(
                    user_id=user_id,
                    message=composed_message,
                    temperature=request.temperature,
                    max_tokens=effective_max_tokens,
                    use_history=False,
                ):
                    delta = {"content": content}
                    if first:
                        delta["role"] = "assistant"
                        first = False
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": delta,
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception as exc:
                error_chunk = {
                    "error": {
                        "message": str(exc),
                        "type": "stream_error",
                    }
                }
                yield f"event: error\ndata: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"

            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    result = await chat(
        ChatRequest(
            user_id=user_id,
            message=composed_message,
            temperature=request.temperature,
            max_tokens=effective_max_tokens
        )
    )

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": result.response
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        },
        "trace": result.trace,
    }


async def _run_memory_writeback(state: dict) -> None:
    """Fire-and-forget PAHF post-action correction (extraction + add/update).

    Runs after the reply has already been returned to the caller, so a slow
    or failing memory writeback never delays or breaks the chat response.
    """
    if memory_writeback_graph is None:
        return
    try:
        await asyncio.to_thread(memory_writeback_graph.invoke, state)
    except Exception as exc:
        logger.error(f"Memory writeback failed: {exc}", exc_info=True)


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Synchronous chat endpoint.

    Processes a user message and returns the assistant's response.
    """
    start_time = time.time()

    try:
        # Rate limiting
        check_rate_limit(request.user_id)

        # Create or get session
        session = session_store.create_session(request.user_id)

        # Prepare state for graph
        state = {
            "user_id": request.user_id,
            "user_message": request.message,
            "response": None,
            "temperature": request.temperature,
            "max_tokens": _effective_max_tokens(request.max_tokens),
            "session": session,
        }

        # Invoke the (generation-only) chat graph off the event loop
        result = await asyncio.to_thread(chat_graph.invoke, state)
        if app_config.MEMORY_WRITEBACK_MODE == "sync":
            # Serverless (Vercel) kills background tasks once the response is
            # sent, so there the writeback must complete before returning.
            await _run_memory_writeback(result)
        else:
            asyncio.create_task(_run_memory_writeback(result))

        # Extract response
        response_text = result.get("response", "")
        trace_payload = {
            "retrieved_memories": result.get("retrieved_memories", []),
            "pahf_context_text": result.get("pahf_context_text", ""),
            "clarification_question": result.get("clarification_question"),
            # Memory extraction/update now run in the background after this
            # response is returned (see _run_memory_writeback), so they are
            # not available synchronously here.
            "memory_writeback": "scheduled" if memory_writeback_graph is not None else "disabled",
            "intent": result.get("intent"),
            "tool_plan": result.get("tool_plan", []),
            "tool_results": result.get("tool_results", []),
            "tool_errors": result.get("tool_errors", []),
        }
        
        # Calculate latency
        latency_ms = (time.time() - start_time) * 1000
        
        # Log request
        logger.info(
            "Chat request processed",
            extra={
                "user_id": request.user_id,
                "endpoint": "/api/v1/chat",
                "latency_ms": latency_ms,
                "status": "success",
                "model_name": model_config.model_name
            }
        )
        
        return ChatResponse(
            response=response_text,
            latency_ms=latency_ms,
            trace=trace_payload,
        )
    
    except RateLimitExceededException as e:
        logger.warning(f"Rate limit exceeded for user {request.user_id}")
        raise HTTPException(status_code=429, detail=str(e))
    
    except ModelAPIException as e:
        logger.error(f"Model API error: {str(e)}")
        raise HTTPException(status_code=502, detail="Model service unavailable")
    
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """Server-Sent Events chat endpoint backed by OpenAI-compatible streaming."""
    if model_client is None:
        raise HTTPException(status_code=503, detail="Model client is not initialized")

    try:
        check_rate_limit(request.user_id)
    except RateLimitExceededException as e:
        raise HTTPException(status_code=429, detail=str(e))

    async def event_stream():
        started = time.time()
        try:
            async for content in model_client.astream(
                user_id=request.user_id,
                message=request.message,
                temperature=request.temperature,
                max_tokens=_effective_max_tokens(request.max_tokens),
            ):
                payload = {"type": "delta", "content": content}
                yield f"event: delta\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

            done = {"type": "done", "latency_ms": round((time.time() - started) * 1000, 2)}
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"type": "error", "message": str(exc)}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _memory_to_response(item) -> MemoryResponse:
    return MemoryResponse(
        id=int(item.id),
        person_id=item.person_id,
        text=item.text,
    )


@app.post("/api/v1/memory", response_model=MemoryResponse)
async def add_memory(request: MemoryCreateRequest):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    item = pahf_memory_service.add_memory(
        person_id=request.user_id,
        text=request.text,
    )
    return _memory_to_response(item)


@app.get("/api/v1/memory", response_model=list[MemoryResponse])
async def list_memory(user_id: str):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    items = pahf_memory_service.get_all_memories(person_id=user_id)
    return [_memory_to_response(item) for item in items]


@app.get("/api/v1/memory/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: int, user_id: str):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    item = pahf_memory_service.get_memory(person_id=user_id, memory_id=memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(item)


@app.put("/api/v1/memory/{memory_id}", response_model=MemoryResponse)
async def update_memory(memory_id: int, request: MemoryUpdateRequest):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    item = pahf_memory_service.update_memory(
        person_id=request.user_id,
        memory_id=memory_id,
        new_text=request.text,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(item)


@app.post("/api/v1/memory/search", response_model=MemorySearchResponse)
async def search_memory(request: MemorySearchRequest):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    hits = pahf_memory_service.search(
        person_id=request.user_id,
        query=request.query,
        top_k=request.top_k,
    )
    return MemorySearchResponse(
        hits=[
            MemorySearchHit(memory=_memory_to_response(hit.memory), score=hit.score)
            for hit in hits
        ]
    )


@app.post("/api/v1/memory/find-similar", response_model=Optional[MemoryResponse])
async def find_similar_memory(request: MemoryFindSimilarRequest):
    if pahf_memory_service is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    item = pahf_memory_service.find_similar_memory(
        person_id=request.user_id,
        text=request.text,
        threshold=request.threshold,
    )
    if item is None:
        return None
    return _memory_to_response(item)


@app.delete("/api/v1/session/{user_id}")
async def delete_session(user_id: str):
    """Delete a user session and clear conversation history."""
    deleted = session_store.delete_session(user_id)
    
    if deleted:
        # Also clear model client history
        if model_client:
            model_client.clear_history(user_id)
        
        logger.info(f"Session deleted for user {user_id}")
        return {"status": "success", "message": f"Session deleted for user {user_id}"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=app_config.HOST,
        port=app_config.PORT,
        reload=True
    )
