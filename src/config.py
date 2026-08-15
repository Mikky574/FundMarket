from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "China Stock API"
    cache_ttl_seconds: int = 15
    history_cache_ttl_seconds: int = 300
    # 实际行情按自然时间窗口刷新：600 表示 10:00、10:10、10:20……
    market_refresh_seconds: int = 600
    # Read-only market intelligence pipeline.  Prefer an environment variable;
    # the file option exists for the current local deployment only.
    deepseek_api_key: str = ""
    deepseek_api_key_file: str = r"..\deepseek-Api.txt"
    market_intelligence_root: str = "market_intelligence"
    market_news_rss_urls: str = ""
    market_intelligence_http_timeout_seconds: int = 45
    market_intelligence_max_industries: int = 30
    # A dated evidence packet older than this cannot be used for a public-AI
    # decision draft.  It may still be displayed as historical research.
    market_intelligence_max_age_minutes: int = 90
    # 数据库使用与项目位置无关的绝对目录；不同应用使用各自子目录。
    database_root: str = r"D:\应用数据库\市场分析"
    user_ai_root: str = r"D:\应用数据库\市场分析\user_ai"
    # Historical evaluation data is deliberately outside the production ledger.
    evaluation_data_root: str = r"D:\应用数据库\市场分析\evaluation_data"
    codex_command: str = "codex"
    # 独立 AI 使用单独的高能力推理配置，不继承桌面端的低推理默认值。
    user_ai_codex_model: str = "gpt-5.6-sol"
    user_ai_codex_reasoning_effort: str = "high"
    user_ai_codex_timeout_seconds: int = 300
    # Public AI has no web endpoint.  This setting is used only by the QQ/Codex
    # bridge command that creates an unpersisted decision draft.
    public_ai_codex_model: str = "gpt-5.6-sol"
    public_ai_codex_reasoning_effort: str = "high"
    public_ai_codex_timeout_seconds: int = 300
    # Pepper 必须由生产环境注入，不能写入数据库或提交到代码仓库。
    password_pepper: str = ""
    auth_max_attempts: int = 5
    auth_lock_seconds: int = 900
    session_cookie_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
