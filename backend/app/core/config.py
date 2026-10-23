from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://meetings:meetings@localhost:55436/meetings"
    redis_url: str = "redis://localhost:56386/0"
    storage_dir: Path = Path(__file__).resolve().parents[3] / "storage"
    public_url: str = "http://localhost:5176"
    jwt_secret: str = "local-development-only-change-before-deploy-32chars"
    token_minutes: int = 480
    cors_origins: list[str] = ["http://localhost:5176", "http://127.0.0.1:5176"]
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""
    wecom_directory_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = Field(default=90, ge=10, le=300)
    native_todo_enabled: bool = False
    native_todo_direct_enabled: bool = True
    native_todo_meeting_enabled: bool = False
    native_todo_all_active_users: bool = False
    native_todo_app_enabled: bool = False
    wecom_direct_notifications_enabled: bool = False
    native_todo_cli_path: Path = Path("/usr/local/bin/wecom-cli")
    native_todo_allowed_wecom_ids: str = ""
    max_upload_mb: int = 30
    backup_dir: Path = Path(__file__).resolve().parents[3] / "backups"
    monitor_state_dir: Path = Path(__file__).resolve().parents[3] / "monitor-state"
    monitor_interval_seconds: int = Field(default=60, ge=15, le=3600)
    monitor_heartbeat_seconds: int = Field(default=180, ge=60, le=3600)
    monitor_queue_minutes: int = Field(default=15, ge=1, le=1440)
    monitor_free_mb: int = Field(default=512, ge=1)
    monitor_backup_hours: int = Field(default=48, ge=1, le=8760)
    alert_webhook_url: str = ""
    alert_webhook_token: str = ""
    alert_email_to: str = ""
    alert_reminder_minutes: int = Field(default=60, ge=1, le=1440)
    backup_alert_reminder_minutes: int = Field(default=1440, ge=60, le=10080)

    def native_todo_allowlist(self) -> set[str]:
        return {value.strip() for value in self.native_todo_allowed_wecom_ids.split(",") if value.strip()}

    def native_todo_source_enabled(self, source: str) -> bool:
        return self.native_todo_enabled and {
            "direct": self.native_todo_direct_enabled,
            "meeting": self.native_todo_meeting_enabled,
        }.get(source, False)

    def native_todo_member_allowed(self, wecom_id: str | None, active: bool) -> bool:
        return bool(active and wecom_id and (
            self.native_todo_all_active_users or wecom_id in self.native_todo_allowlist()
        ))

    def check_production(self) -> None:
        if self.app_env == "production" and (
            self.jwt_secret.startswith("local-") or len(self.jwt_secret) < 32
        ):
            raise RuntimeError("生产环境必须设置至少 32 位的独立 JWT_SECRET")


@lru_cache
def get_settings() -> Settings:
    value = Settings()
    value.check_production()
    return value
