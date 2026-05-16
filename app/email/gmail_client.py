"""Gmail API client for polling apartment alert emails.

Handles OAuth2 authentication, message fetching, and label management.
Designed for polling Zillow and Apartments.com email alerts from a
dedicated Gmail account.
"""

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app.core.config import PROJECT_ROOT
from app.core.logging import get_logger

logger = get_logger(__name__)

GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", str(PROJECT_ROOT / "credentials.json"))
GMAIL_TOKEN_JSON = os.getenv("GMAIL_TOKEN_JSON", str(PROJECT_ROOT / "token.json"))
GMAIL_PROCESSED_LABEL = os.getenv("GMAIL_PROCESSED_LABEL", "apartment-scraper/processed")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class EmailMessage:
    """A fetched email with parsed metadata."""
    message_id: str
    sender: str
    subject: str
    html_body: str
    received_at: datetime


def _build_service():
    """Build an authenticated Gmail API service."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(GMAIL_TOKEN_JSON):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_JSON, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(GMAIL_CREDENTIALS_JSON):
                raise FileNotFoundError(
                    f"Gmail credentials not found at {GMAIL_CREDENTIALS_JSON}. "
                    "Run scripts/gmail_auth.py to set up OAuth."
                )
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDENTIALS_JSON, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_JSON, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _ensure_label(service, label_name: str) -> str:
    """Get or create a Gmail label, return its ID."""
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == label_name:
            return label["id"]

    label_body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=label_body).execute()
    logger.info("gmail_label_created", label=label_name, label_id=created["id"])
    return created["id"]


def _extract_html_body(payload: dict) -> str:
    """Recursively extract the HTML body from a Gmail message payload."""
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        html = _extract_html_body(part)
        if html:
            return html

    return ""


def _get_header(headers: list[dict], name: str) -> str:
    """Get a header value by name (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


class GmailClient:
    """Polls a Gmail account for apartment alert emails."""

    def __init__(self):
        self._service = None
        self._processed_label_id: str | None = None

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service()
        return self._service

    @property
    def processed_label_id(self) -> str:
        if self._processed_label_id is None:
            self._processed_label_id = _ensure_label(self.service, GMAIL_PROCESSED_LABEL)
        return self._processed_label_id

    def fetch_unprocessed(self, sender_filter: str | list[str], max_results: int = 20) -> list[EmailMessage]:
        """Fetch unprocessed emails from a specific sender.

        Args:
            sender_filter: Email address or list of addresses to filter by
            max_results: Maximum number of messages to fetch

        Returns:
            List of EmailMessage objects with parsed HTML bodies.
        """
        if isinstance(sender_filter, list):
            from_clause = " OR ".join(f"from:{s}" for s in sender_filter)
            from_clause = f"{{{from_clause}}}"
        else:
            from_clause = f"from:{sender_filter}"
        query = f"{from_clause} is:unread -label:{GMAIL_PROCESSED_LABEL}"
        logger.info("gmail_fetching", query=query)

        try:
            results = self.service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()
        except Exception as e:
            logger.error("gmail_list_error", error=str(e))
            return []

        messages_meta = results.get("messages", [])
        if not messages_meta:
            logger.info("gmail_no_new_messages", sender=sender_filter)
            return []

        emails: list[EmailMessage] = []
        for meta in messages_meta:
            try:
                msg = self.service.users().messages().get(
                    userId="me", id=meta["id"], format="full"
                ).execute()

                payload = msg.get("payload", {})
                headers = payload.get("headers", [])

                sender = _get_header(headers, "From")
                subject = _get_header(headers, "Subject")
                date_str = _get_header(headers, "Date")

                received_at = datetime.now(timezone.utc)
                if date_str:
                    try:
                        received_at = parsedate_to_datetime(date_str)
                    except (ValueError, TypeError):
                        pass

                html_body = _extract_html_body(payload)
                if not html_body:
                    logger.warning("gmail_no_html_body", message_id=meta["id"])
                    continue

                emails.append(EmailMessage(
                    message_id=meta["id"],
                    sender=sender,
                    subject=subject,
                    html_body=html_body,
                    received_at=received_at,
                ))

            except Exception as e:
                logger.error("gmail_message_fetch_error", message_id=meta["id"], error=str(e))

        logger.info("gmail_fetched", sender=sender_filter, count=len(emails))
        return emails

    def mark_processed(self, message_id: str) -> None:
        """Mark an email as processed by adding a label and removing from inbox."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={
                    "addLabelIds": [self.processed_label_id],
                    "removeLabelIds": ["INBOX", "UNREAD"],
                },
            ).execute()
            logger.debug("gmail_marked_processed", message_id=message_id)
        except Exception as e:
            logger.error("gmail_mark_error", message_id=message_id, error=str(e))


def is_gmail_configured() -> bool:
    """Check if Gmail credentials are available."""
    return os.path.exists(GMAIL_TOKEN_JSON) or os.path.exists(GMAIL_CREDENTIALS_JSON)
