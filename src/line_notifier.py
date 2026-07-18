"""LINE Messaging API notification module.

Provides LineNotifier for pushing daily investment reports via the
LINE Bot API (v2).  Supports multiple recipient User IDs.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

LINE_PUSH_URL: str = "https://api.line.me/v2/bot/message/push"


class LineNotifier:
    """Send push notifications through the LINE Messaging API.

    Credentials are read from environment variables:
        - ``LINE_CHANNEL_ACCESS_TOKEN``  (required)
        - ``LINE_USER_ID``              (optional, comma-separated for multiple users)

    If ``LINE_USER_ID`` is not set, the message is sent to everyone who
    has added the bot as a friend (broadcast).
    """

    def __init__(self) -> None:  # noqa: D401
        self.token: Optional[str] = None
        self.user_ids: List[str] = []

        self.token = self._get_env("LINE_CHANNEL_ACCESS_TOKEN")
        if not self.token:
            raise ValueError(
                "Environment variable LINE_CHANNEL_ACCESS_TOKEN is not set."
            )

        # Support comma-separated User IDs
        user_id_raw: Optional[str] = self._get_env("LINE_USER_ID", default="")
        if user_id_raw:
            self.user_ids = [
                uid.strip() for uid in user_id_raw.split(",") if uid.strip()
            ]
        # If empty, we'll broadcast to all followers

    def send_push_message(self, message: str) -> None:
        """Send a text push message to configured LINE users.

        Args:
            message: The plain-text message body (supports Unicode /
                     traditional Chinese characters).

        Raises:
            requests.exceptions.RequestException: On network / HTTP errors.
        """
        if self.user_ids:
            # Send to specific users
            for user_id in self.user_ids:
                self._send_to_user(user_id, message)
        else:
            # Broadcast to all followers
            self._send_broadcast(message)

    def _send_to_user(self, user_id: str, message: str) -> None:
        """Send a push message to a single user.

        Args:
            user_id: LINE User ID.
            message: Message text.
        """
        payload: dict = {
            "to": user_id,
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

        logger.info("Sending LINE push to user '%s'.", user_id)
        resp: requests.Response = requests.post(
            LINE_PUSH_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(
                "LINE API returned status %d for user %s: %s",
                resp.status_code,
                user_id,
                resp.text,
            )
            resp.raise_for_status()

        logger.info("LINE push message sent to '%s' successfully.", user_id)

    def _send_broadcast(self, message: str) -> None:
        """Broadcast a message to all followers.

        Args:
            message: Message text.
        """
        payload: dict = {
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

        logger.info("Sending LINE broadcast to all followers.")
        resp: requests.Response = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(
                "LINE broadcast API returned status %d: %s",
                resp.status_code,
                resp.text,
            )
            resp.raise_for_status()

        logger.info("LINE broadcast sent successfully.")

    @staticmethod
    def _get_env(name: str, default: str = "") -> Optional[str]:
        """Read an environment variable, falling back to *default*."""
        val: Optional[str] = os.environ.get(name)
        if val is None or val.strip() == "":
            return default if default else None
        return val.strip()
