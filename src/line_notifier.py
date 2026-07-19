"""LINE Messaging API notification module.

Provides LineNotifier for pushing daily investment reports via the
LINE Bot API (v2).  Supports both push to specific users and
broadcast to all followers.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

LINE_PUSH_URL: str = "https://api.line.me/v2/bot/message/push"
LINE_BROADCAST_URL: str = "https://api.line.me/v2/bot/message/broadcast"


class LineNotifier:
    """Send notifications through the LINE Messaging API.

    Configuration via environment variables:
        - ``LINE_CHANNEL_ACCESS_TOKEN``  (required)
        - ``LINE_USER_ID``              (optional)
          - If set: push to specific user(s)
          - If empty: broadcast to all followers

    Usage::

        notifier = LineNotifier()
        notifier.send_push_message("Hello!")  # Push or broadcast
    """

    def __init__(self) -> None:
        self.token: Optional[str] = None
        self.user_ids: List[str] = []
        self.use_broadcast: bool = False

        self.token = self._get_env("LINE_CHANNEL_ACCESS_TOKEN")
        if not self.token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not set.")

        user_id_raw: Optional[str] = self._get_env("LINE_USER_ID", default="")
        if user_id_raw:
            self.user_ids = [
                uid.strip() for uid in user_id_raw.split(",") if uid.strip()
            ]
        else:
            # No user IDs set → broadcast to all followers
            self.use_broadcast = True
            logger.info("LINE_USER_ID not set. Will broadcast to all followers.")

    def send_push_message(self, message: str) -> None:
        """Send a message to configured recipients.

        If LINE_USER_ID is set, sends to those users (push).
        Otherwise, broadcasts to all followers.

        Args:
            message: The plain-text message body.

        Raises:
            requests.exceptions.RequestException: On network / HTTP errors.
        """
        if self.use_broadcast:
            self._send_broadcast(message)
        else:
            for user_id in self.user_ids:
                self._send_to_user(user_id, message)

    def _send_to_user(self, user_id: str, message: str) -> None:
        """Send a push message to a single user."""
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
        """Broadcast a message to all followers."""
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
            LINE_BROADCAST_URL,
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