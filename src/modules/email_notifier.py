"""
Email notifications for the review workflow.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from loguru import logger

from src.core import config


class EmailNotifier:
    """Send plain-text review emails via SMTP."""

    def __init__(self):
        self.logger = logger

    def configured(self) -> bool:
        settings = config.settings
        return bool(
            settings.smtp_host
            and settings.autopilot_review_email_to
            and settings.autopilot_review_email_from
        )

    def send_review_email(
        self,
        *,
        subject: str,
        body: str,
        attachment_path: Optional[Path] = None,
    ) -> bool:
        settings = config.settings
        if not self.configured():
            self.logger.warning("[autopilot] review email not configured; skipping email")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.autopilot_review_email_from
        msg["To"] = settings.autopilot_review_email_to
        msg.set_content(body)

        if attachment_path and attachment_path.exists() and attachment_path.stat().st_size <= 20_000_000:
            msg.add_attachment(
                attachment_path.read_bytes(),
                maintype="video",
                subtype="mp4",
                filename=attachment_path.name,
            )
        elif attachment_path:
            self.logger.info(
                f"[autopilot] not attaching review video ({attachment_path.stat().st_size if attachment_path.exists() else 0} bytes)"
            )

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
            self.logger.info(f"[autopilot] review email sent to {settings.autopilot_review_email_to}")
            return True
        except Exception as e:
            self.logger.error(f"[autopilot] review email failed: {e}")
            return False
