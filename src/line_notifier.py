"""LINE Messaging API notification module.

Provides LineNotifier for pushing daily investment reports via the
LINE Bot API (v2).
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LINE_PUSH_URL: str = "https://api.line.me/v2/bot/message/push"


class LineNotifier:
    """Send push notifications through the LINE Messaging API.

    Credentials are read from environment variables:
        - ``LINE_CHANNEL_ACCESS_TOKEN``
        - ``LINE_USER_ID``
    """

    def __init__(self) -> None:  # noqa: D401
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None

        self.token = self._get_env("LINE_CHANNEL_ACCESS_TOKEN")
        self.user_id = self._get_env("LINE_USER_ID")

        if not self.token:
            raise ValueError(
                "Environment variable LINE_CHANNEL_ACCESS_TOKEN is not set."
            )
        if not self.user_id:
            raise ValueError("Environment variable LINE_USER_ID is not set.")

    def send_push_message(self, message: str) -> None:
        """Send a text push message to the configured LINE user.

        Args:
            message: The plain-text message body (supports Unicode /
                     traditional Chinese characters).

        Raises:
            requests.exceptions.RequestException: On network / HTTP errors.
        """
        payload: dict = {
            "to": self.user_id,
            "messages": [
                {
                    "type": "text",
                    "text": message,
                }
            ],
        }

        headers: dict = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        logger.info("Sending LINE push notification to user '%s'.", self.user_id)
        resp: requests.Response = requests.post(
            LINE_PUSH_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(
                "LINE API returned status %d: %s",
                resp.status_code,
                resp.text,
            )
            resp.raise_for_status()

        logger.info("LINE push message sent successfully.")

    @staticmethod
    def _get_env(name: str, default: str = "") -> Optional[str]:
        """Read an environment variable, falling back to *default*."""
        import os

        val: Optional[str] = os.environ.get(name)
        if val is None or val.strip() == "":
            return default if default else None
        return val.strip()
