"""Runtime settings, read once from the environment (and backend/.env if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = BACKEND_DIR / "agents"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Storage
    data_dir: Path
    db_path: Path

    # ZooWork
    zoowork_model: str | None          # explicit model id; None = platform default from list_models()
    agent_label_app: str
    agent_label_env: str
    run_timeout_s: float               # max wall time for one agent turn (incl. tool calls)

    # Email: outbound (SMTP)
    email_mode: str                    # "smtp" (real) or "dry_run" (log only, nothing leaves)
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_security: str                 # "ssl" | "starttls" | "none"
    from_address: str
    from_name: str

    # Email: inbound (IMAP polling)
    imap_enabled: bool
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str
    poll_seconds: int

    # Communication loop limits
    reminder_after_hours: float
    max_reminders: int
    max_outbound_per_vendor: int
    default_run_deadline_hours: float
    tick_seconds: int


def load_settings() -> Settings:
    _load_dotenv(BACKEND_DIR / ".env")
    data_dir = Path(os.environ.get("PD_DATA_DIR") or (BACKEND_DIR / "var"))
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("PD_DB_PATH") or (data_dir / "pd_partner.sqlite3")),
        zoowork_model=os.environ.get("ZOOWORK_MODEL") or None,
        agent_label_app=os.environ.get("PD_AGENT_LABEL_APP", "brilliant-pd-partner"),
        agent_label_env=os.environ.get("PD_AGENT_LABEL_ENV", "dev"),
        run_timeout_s=_float("PD_RUN_TIMEOUT_S", 900.0),
        email_mode=os.environ.get("EMAIL_MODE", "smtp").strip().lower(),
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=_int("SMTP_PORT", 465),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_security=os.environ.get("SMTP_SECURITY", "ssl").strip().lower(),
        from_address=os.environ.get("EMAIL_FROM_ADDRESS", os.environ.get("SMTP_USER", "")),
        from_name=os.environ.get("EMAIL_FROM_NAME", "Sourcing Team"),
        imap_enabled=_bool("IMAP_ENABLED", True),
        imap_host=os.environ.get("IMAP_HOST", ""),
        imap_port=_int("IMAP_PORT", 993),
        imap_user=os.environ.get("IMAP_USER", os.environ.get("SMTP_USER", "")),
        imap_password=os.environ.get("IMAP_PASSWORD", os.environ.get("SMTP_PASSWORD", "")),
        imap_folder=os.environ.get("IMAP_FOLDER", "INBOX"),
        poll_seconds=_int("IMAP_POLL_SECONDS", 60),
        reminder_after_hours=_float("PD_REMINDER_AFTER_HOURS", 48.0),
        max_reminders=_int("PD_MAX_REMINDERS", 2),
        max_outbound_per_vendor=_int("PD_MAX_OUTBOUND_PER_VENDOR", 8),
        default_run_deadline_hours=_float("PD_RUN_DEADLINE_HOURS", 168.0),
        tick_seconds=_int("PD_TICK_SECONDS", 300),
    )
