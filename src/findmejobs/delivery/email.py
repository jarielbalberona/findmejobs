from __future__ import annotations

import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage

from findmejobs.config.models import EmailDeliveryConfig


class EmailDeliveryError(RuntimeError):
    pass


@dataclass(slots=True)
class EmailSendResult:
    provider_message_id: str
    attempts: int


class SMTPEmailSender:
    def __init__(self, config: EmailDeliveryConfig) -> None:
        self.config = config
        self.last_attempt_count = 0

    def send(self, *, subject: str, body_text: str) -> EmailSendResult:
        if not self.config.enabled:
            raise EmailDeliveryError("email_delivery_disabled")
        if not self.config.host or not self.config.sender or not self.config.recipient:
            raise EmailDeliveryError("email_delivery_not_configured")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.sender
        message["To"] = self.config.recipient
        message.set_content(body_text)
        self.last_attempt_count = 0
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self.last_attempt_count = attempt
            try:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=20) as smtp:
                    if self.config.use_tls:
                        smtp.starttls()
                    if self.config.username and self.config.password:
                        smtp.login(self.config.username, self.config.password)
                    response = smtp.send_message(message)
                provider_message_id = "sent" if not response else "queued"
                return EmailSendResult(provider_message_id=provider_message_id, attempts=attempt)
            except (smtplib.SMTPException, OSError, EmailDeliveryError) as exc:
                last_error = exc
                if attempt >= 3:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
        assert last_error is not None
        raise last_error
