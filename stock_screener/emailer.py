"""Gmail SMTP sender (規格書 6, 憑證紀律). Credentials come ONLY from the
environment — which the GitHub Actions workflow populates from repository
Secrets — and there are deliberately NO fallback/default values: this repo is
public, so a missing secret must fail loudly, never silently fall back to a
baked-in account.

Required environment variables (map these from GitHub Secrets in the
workflow; see README "Phase 4 排程與憑證設定"):

    GMAIL_USER      寄件 Gmail 帳號（完整 email）
    GMAIL_PASSWORD  該帳號的 Google 應用程式密碼（非登入密碼）
    MAIL_TO         收件人 email（可用逗號分隔多個）

`send_report` raises MissingCredentialError listing exactly which variables
are absent, so a dispatch run's log points straight at the fix.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

ENV_USER = "GMAIL_USER"
ENV_PASSWORD = "GMAIL_PASSWORD"
ENV_TO = "MAIL_TO"


class MissingCredentialError(RuntimeError):
    """Raised when a required email env var / secret is absent or blank."""


def _require_env() -> tuple[str, str, list[str]]:
    missing = [name for name in (ENV_USER, ENV_PASSWORD, ENV_TO)
               if not (os.environ.get(name) or "").strip()]
    if missing:
        raise MissingCredentialError(
            "缺少 email 憑證環境變數: " + ", ".join(missing)
            + "。這些值只走 GitHub Secrets，程式端無備援值；請在 repo 的"
            " Settings → Secrets and variables → Actions 新增同名 secret"
            "（見 README『Phase 4 排程與憑證設定』）。"
        )
    user = os.environ[ENV_USER].strip()
    password = os.environ[ENV_PASSWORD].strip()
    recipients = [r.strip() for r in os.environ[ENV_TO].split(",") if r.strip()]
    if not recipients:
        raise MissingCredentialError(f"{ENV_TO} 未包含任何有效收件人")
    return user, password, recipients


def send_report(subject: str, html_body: str, *, text_body: str | None = None,
                from_name: str = "台股選股工具") -> list[str]:
    """Send the HTML report. Returns the recipient list on success; raises
    MissingCredentialError if secrets are absent, or smtplib errors on send
    failure (the caller logs these to fetch_log and still finishes the run)."""
    user, password, recipients = _require_env()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, user))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())
    logger.info("Report email sent to %s", recipients)
    return recipients
