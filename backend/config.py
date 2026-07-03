"""
Configuration module for the conversational AI system.
Centralizes all configuration and environment variable handling.
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _default_data_path(local_path: str, vercel_path: str) -> str:
    """Use writable /tmp storage on Vercel, local data files elsewhere."""

    return vercel_path if os.getenv("VERCEL") else local_path


def _env_data_path(name: str, local_path: str, vercel_path: str) -> str:
    return os.getenv(name, _default_data_path(local_path, vercel_path)).strip()


class ModelConfig:
    """Configuration for the model backend."""
    
    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        default_temperature: float = 0.7,
        default_max_tokens: int = 1024,
        system_prompt_scene: str = "default"
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.system_prompt_scene = system_prompt_scene


# Default model configuration from environment variables
DEFAULT_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("MODEL_NAME", "mimo-v2.5-pro").strip(),
    base_url=os.getenv("BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1").strip(),
    api_key=os.getenv("API_KEY", "").strip(),
    default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "0.7")),
    default_max_tokens=int(os.getenv("DEFAULT_MAX_TOKENS", "1024")),
    system_prompt_scene=os.getenv("SYSTEM_PROMPT_SCENE", "default")
)


class AppConfig:
    """Application-level configuration."""
    
    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Logging settings
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")  # json or text
    
    # Session settings
    SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
    SESSION_CLEANUP_INTERVAL: int = int(os.getenv("SESSION_CLEANUP_INTERVAL", "300"))
    
    # Rate limiting
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    
    # CORS settings
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")

    # PAHF memory settings (single memory architecture)
    PAHF_BACKEND: str = os.getenv("PAHF_BACKEND", "sqlite")
    PAHF_SQLITE_DB_PATH: str = _env_data_path(
        "PAHF_SQLITE_DB_PATH",
        "./data/pahf/pahf_memory.db",
        "/tmp/servicebot/pahf/pahf_memory.db",
    )
    PAHF_FAISS_PATH: str = _env_data_path(
        "PAHF_FAISS_PATH",
        "./data/pahf/pahf_memory",
        "/tmp/servicebot/pahf/pahf_memory",
    )
    PAHF_TOP_K: int = int(os.getenv("PAHF_TOP_K", "5"))
    PAHF_SIMILARITY_THRESHOLD: str = os.getenv("PAHF_SIMILARITY_THRESHOLD", "0.45")
    PAHF_QUERY_ENCODER: str = os.getenv("PAHF_QUERY_ENCODER", "facebook/dragon-plus-query-encoder")
    PAHF_CONTEXT_ENCODER: str = os.getenv("PAHF_CONTEXT_ENCODER", "facebook/dragon-plus-context-encoder")
    PAHF_EMBED_DEVICE: str = os.getenv("PAHF_EMBED_DEVICE", "")
    PAHF_EMBEDDING_MODE: str = os.getenv("PAHF_EMBEDDING_MODE", "hash")
    PAHF_ENABLE_PRE_CLARIFICATION: bool = os.getenv("PAHF_ENABLE_PRE_CLARIFICATION", "true").lower() == "true"
    PAHF_ENABLE_POST_CORRECTION: bool = os.getenv("PAHF_ENABLE_POST_CORRECTION", "true").lower() == "true"
    PAHF_LLM_MODEL: str = os.getenv("PAHF_LLM_MODEL", "")

    # Phase 3 tool calling
    TOOLS_ENABLED: bool = os.getenv("TOOLS_ENABLED", "true").lower() == "true"
    TOOLS_ALLOWLIST: list = os.getenv(
        "TOOLS_ALLOWLIST",
        "kb_search,create_ticket,get_ticket,list_tickets,"
        "product_search,get_product_detail,check_inventory,get_order,list_orders,"
        "track_shipment,recommend_products,list_coupons,apply_coupon,initiate_return",
    ).split(",")
    TOOL_MAX_CALLS_PER_TURN: int = int(os.getenv("TOOL_MAX_CALLS_PER_TURN", "3"))
    TOOL_TIMEOUT_SECONDS: float = float(os.getenv("TOOL_TIMEOUT_SECONDS", "3.0"))
    TOOL_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("TOOL_RATE_LIMIT_PER_MINUTE", "30"))
    KB_FILE_PATH: str = _env_data_path(
        "KB_FILE_PATH",
        "./data/kb/faq.json",
        "/tmp/servicebot/kb/faq.json",
    )
    TICKET_DB_PATH: str = _env_data_path(
        "TICKET_DB_PATH",
        "./data/tickets/tickets.db",
        "/tmp/servicebot/tickets/tickets.db",
    )

    # Virtual store (e-commerce catalog) settings
    CATALOG_DB_PATH: str = _env_data_path(
        "CATALOG_DB_PATH",
        "./data/catalog/catalog.db",
        "/tmp/servicebot/catalog/catalog.db",
    )
    CATALOG_AUTO_SEED: bool = os.getenv("CATALOG_AUTO_SEED", "true").lower() == "true"

    # Realtime + human-in-the-loop settings
    CONVERSATION_DB_PATH: str = _env_data_path(
        "CONVERSATION_DB_PATH",
        "./data/conversations/conversations.db",
        "/tmp/servicebot/conversations/conversations.db",
    )
    NOTIFY_WEBHOOK_URL: str = os.getenv("NOTIFY_WEBHOOK_URL", "")
    FEEDBACK_DB_PATH: str = _env_data_path(
        "FEEDBACK_DB_PATH",
        "./data/feedback/feedback.db",
        "/tmp/servicebot/feedback/feedback.db",
    )

    # Product review-analytics settings
    REVIEW_DB_PATH: str = _env_data_path(
        "REVIEW_DB_PATH",
        "./data/reviews/reviews.db",
        "/tmp/servicebot/reviews/reviews.db",
    )
    REVIEW_AUTO_SEED: bool = os.getenv("REVIEW_AUTO_SEED", "true").lower() == "true"

    # Backoffice admin settings
    ADMIN_DB_PATH: str = _env_data_path(
        "ADMIN_DB_PATH",
        "./data/admin/admin.db",
        "/tmp/servicebot/admin/admin.db",
    )
    ADMIN_DEFAULT_USERNAME: str = os.getenv("ADMIN_DEFAULT_USERNAME", "admin").strip()
    ADMIN_DEFAULT_PASSWORD: str = os.getenv("ADMIN_DEFAULT_PASSWORD", "Admin@2026!").strip()
    ADMIN_SESSION_TTL_SECONDS: int = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "86400"))


def get_model_config() -> ModelConfig:
    """Get the default model configuration."""
    return DEFAULT_MODEL_CONFIG


def get_app_config() -> AppConfig:
    """Get the application configuration."""
    return AppConfig()
