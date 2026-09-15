# -*- coding: utf-8 -*-
"""LINE Bot webhook server for interactive stock commands.

Receives LINE messaging events, validates the request signature,
and routes commands to the appropriate handlers.

Supported commands (triggered by user typing in LINE chat):
  /taiwan  - Immediately regenerate and send the Taiwan daily report
  /status  - Show when the last report was sent
  /help    - List available commands

Deployment note:
  LINE requires a public HTTPS URL for the webhook endpoint.
  For local testing use ngrok or Cloudflare Tunnel, e.g.:
    ngrok http 5000
  Then paste the https://xxx.ngrok.io URL into LINE Console ->
  Messaging settings -> Webhook settings.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional

import pytz
import requests
from flask import Flask, Request, Response, jsonify, request
# import fix
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from line_notifier import LineNotifier

logger = logging.getLogger(__name__)

TW_TZ = pytz.timezone("Asia/Taipei")

# Path for persisting the last report timestamp
_LAST_REPORT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "last_report_time.json"
)

LINE_VERIFY_URL = "https://api.line.me/v2/bot/verify/rchannelsecret"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """Verify the LINE request signature using HMAC-SHA256."""
    import hashlib
    import hmac

    hash_obj = hmac.new(
        channel_secret.encode("utf-8"), body, hashlib.sha256
    )
    expected = hash_obj.hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Last-report timestamp persistence
# ---------------------------------------------------------------------------

def _load_last_report_time() -> Optional[str]:
    try:
        with open(_LAST_REPORT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("last_report_time")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_last_report_time(ts: str) -> None:
    os.makedirs(os.path.dirname(_LAST_REPORT_PATH), exist_ok=True)
    with open(_LAST_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_report_time": ts}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_help(user_id: str, notifier: Any) -> None:
    """Send help message to user."""
    msg = (
        "📋 台股 AI 摘要 Bot 指令\n\n"
        "• /taiwan — 發送台股每日焦點報告\n• /us     — 發送美股每日焦點報告\n• /all    — 發送台股+美股報告\n"
        "• /status — 查看上次報告時間\n"
        "• /help   — 顯示此說明\n\n"
        "每日早上 10:00（台北時間）自動發送，也可手動觸發。"
    )
    notifier.send_push_message(msg)


def _cmd_status(user_id: str, notifier: Any) -> None:
    """Send last report timestamp to user."""
    last = _load_last_report_time()
    if last:
        msg = f"⏰ 上次報告發送時間：{last}"
    else:
        msg = "⏰ 尚未發送過報告。使用 /taiwan 手動觸發。"
    notifier.send_push_message(msg)


def _run_taiwan_report_async(notifier: Any) -> None:
    """Generate the Taiwan focus report in a background thread."""
    logger.info("Background: generating Taiwan report from webhook")
    try:
        # Import here to avoid circular import at module load time
        from daily_taiwan_report import main as run_report

        # main() prints the report and also calls LineNotifier internally.
        # We intercept by monkey-patching the print output and reusing notifier.
        import io
        import sys

        capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = capture

        # Set required env vars from current process so main() can find them
        if "GCP_SERVICE_ACCOUNT_JSON" not in os.environ:
            key_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..",
                "stock-monitor-502815-d2e7cdb6f0a2.json"
            )
            if os.path.exists(key_path):
                os.environ["GCP_SERVICE_ACCOUNT_JSON"] = open(
                    key_path, encoding="utf-8"
                ).read().lstrip("\ufeff")
            else:
                os.environ["GCP_SERVICE_ACCOUNT_JSON"] = os.environ.get(
                    "GCP_SERVICE_ACCOUNT_JSON", ""
                )

        run_report()
        sys.stdout = old_stdout

        report_text = capture.getvalue()
        now_str = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")
        _save_last_report_time(now_str)
        logger.info("Background: Taiwan report sent successfully")
    except Exception as exc:
        logger.exception("Background: Taiwan report failed: %s", exc)
        notifier.send_push_message(
            f"⚠️ 台股報告生成時發生錯誤：{exc}"
        )


def _cmd_taiwan(user_id: str, notifier: Any) -> None:
    """Start report generation in background and reply immediately."""
    ack = "⏳ 正在生成台股每日報告，請稍候..."
    notifier.send_push_message(ack)
    threading.Thread(target=_run_taiwan_report_impl, args=(notifier,), daemon=True).start()
    logger.info("Taiwan report thread started for user %s", user_id)

def _run_us_report_impl(notifier: Any) -> None:
    """Generate the US focus report in background."""
    logger.info("Background: generating US report from webhook")
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from main import build_daily_report
        from config import GoogleSheetsManager
        manager = GoogleSheetsManager()
        data = manager.load_config()
        holdings = data["holdings"]
        report_text, qualified, stocks_data, stock_info = build_daily_report(holdings)
        _send_report_chunks(notifier, report_text)
        logger.info("US report sent successfully")
    except Exception as exc:
        logger.exception("US report failed: %s", exc)
        notifier.send_push_message(f"⚠️ 美股報告生成時發生錯誤：{exc}")

def _cmd_us(user_id: str, notifier: Any) -> None:
    """Start US report generation in background."""
    notifier.send_push_message("⏳ 正在生成美股焦點報告，請稍候...")
    threading.Thread(target=_run_us_report_impl, args=(notifier,), daemon=True).start()
    logger.info("US report thread started for user %s", user_id)

def _cmd_all(user_id: str, notifier: Any) -> None:
    """Send both Taiwan and US reports sequentially."""
    notifier.send_push_message("⏳ 正在生成台股+美股報告，請稍候...")
    threading.Thread(target=_run_taiwan_report_impl, args=(notifier,), daemon=True).start()
    threading.Thread(target=_run_us_report_impl, args=(notifier,), daemon=True).start()
    logger.info("Both reports thread started for user %s", user_id)


def _run_taiwan_report_impl(notifier: Any) -> None:
    """Actual report execution (separated to avoid capturing stdout)."""
    import io
    import sys
    from daily_taiwan_report import build_taiwan_focus_report
    from taiwan_market_data import (
        fetch_all_stock_data,
        is_taiwan_trading_day,
        load_cnyes_ratings,
        merge_cnyes_into_data,
    )
    from config import GoogleSheetsManager

    try:
        if not is_taiwan_trading_day():
            now_tw = datetime.now(TW_TZ)
            date_str = now_tw.strftime("%Y-%m-%d (%a)")
            msg = (
                f"# 台股AI摘要｜{date_str}\n\n"
                f"⚠️ 註：今日處於休市日，今日不派發。\n"
            )
            notifier.send_push_message(msg)
            return

        manager = GoogleSheetsManager()
        watchlist = manager.load_taiwan_stocks()
        if not watchlist:
            notifier.send_push_message("❌ 無法載入股監人watchlist，請檢查Google Sheets。")
            return

        cnyes_ratings = load_cnyes_ratings()
        tickers = [
            _extract_ticker_from_row(h) for h in watchlist
        ]
        tickers = [t for t in tickers if t]
        stocks_data = fetch_all_stock_data(tickers)
        qfii_merged = merge_cnyes_into_data(tickers, cnyes_ratings)
        for ticker, qfii_info in qfii_merged.items():
            if ticker not in stocks_data:
                continue
            sd = stocks_data[ticker]
            sd.qfii_target = qfii_info["qfii_target"]
            sd.qfii_rating = qfii_info["qfii_rating"]
            sd.qfii_upside = qfii_info["qfii_upside"]
            if sd.current_price > 0 and qfii_info["qfii_target"] > 0:
                sd.qfii_upside = round(
                    (qfii_info["qfii_target"] - sd.current_price)
                    / sd.current_price * 100, 1
                )
            sd.qfii_broker = qfii_info["qfii_broker"]

        if not stocks_data:
            notifier.send_push_message(
                "❌ 無法取得股票資料，請稍後再試。"
            )
            return

        report = build_taiwan_focus_report(
            stocks_data, watchlist,
            qfii_data=qfii_merged,
            cnyes_ratings=cnyes_ratings,
        )
        _send_report_chunks(notifier, report)

        now_str = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")
        _save_last_report_time(now_str)
        logger.info("Taiwan report generated and sent at %s", now_str)

    except Exception as exc:
        logger.exception("Report generation failed: %s", exc)
        notifier.send_push_message(f"⚠️ 報告生成時發生錯誤：{exc}")


def _extract_ticker_from_row(h: Dict[str, Any]) -> str:
    raw = str(h.get("ticker", h.get("代碼", ""))).strip()
    import re
    m = re.match(r"(\d+)(.*)", raw)
    return m.group(1) if m else raw


def _send_report_chunks(notifier: Any, message: str, max_length: int = 4800) -> None:
    if len(message) <= max_length:
        notifier.send_push_message(message)
        return
    lines = message.split("\n")
    current_chunk: list = []
    current_length = 0
    for line in lines:
        line_len = len(line) + 1
        if current_length + line_len > max_length and current_chunk:
            notifier.send_push_message("\n".join(current_chunk))
            current_chunk = []
            current_length = 0
        current_chunk.append(line)
        current_length += line_len
    if current_chunk:
        notifier.send_push_message("\n".join(current_chunk))


# ---------------------------------------------------------------------------
# Flask route
# ---------------------------------------------------------------------------

def get_channel_secret() -> Optional[str]:
    return os.environ.get("LINE_CHANNEL_SECRET", "")

def get_channel_token() -> Optional[str]:
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

def _load_gcp_json_from_env() -> Optional[str]:
    """Load GCP_SERVICE_ACCOUNT_JSON from .env and normalize newlines."""
    raw = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None
    # Handle literal \n from env file (two chars: backslash + n)
    normalized = raw.replace("\\\\n", "\\n")
    return normalized


def get_channel_token() -> Optional[str]:
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


@app.route("/webhook/line", methods=["POST"])
def webhook():
    """Handle LINE webhook events."""
    channel_secret = get_channel_secret()
    channel_token = get_channel_token()

    if not channel_secret or not channel_token:
        logger.warning("LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN not set")
        return jsonify({"error": "Configuration missing"}), 500

    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()

    if signature and not _verify_signature(channel_secret, body, signature):
        logger.warning("Invalid LINE signature, rejecting request")
        return jsonify({"error": "Invalid signature"}), 400

    event_payload = request.get_json(force=True)
    if not isinstance(event_payload, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    events = event_payload.get("events", [])
    if not events:
        return jsonify({"status": "ok"})

    notifier = LineNotifier()
    responses_to_send: list[Dict[str, Any]] = []

    for event in events:
        event_type = event.get("type", "")

        if event_type == "message":
            msg = event.get("message", {})
            msg_type = msg.get("type", "")
            user_id = event.get("userId", "")

            if msg_type == "text":
                text = msg.get("text", "").strip().lower()
                logger.info("Received command from %s: %s", user_id, text)

                if text == "/taiwan":
                    _cmd_taiwan(user_id, notifier)
                elif text == "/us":
                    _cmd_us(user_id, notifier)
                elif text == "/all":
                    _cmd_all(user_id, notifier)
                elif text == "/help":
                    _cmd_help(user_id, notifier)
                elif text == "/status":
                    _cmd_status(user_id, notifier)
                else:
                    resp = "未認識的指令，請輸入 /help 查看可用指令。"
                    notifier.send_push_message(resp)

        elif event_type == "follow":
            notifier.send_push_message(
                "👋 感謝關注！\n\n輸入 /taiwan 取得台股報告、/us 取得美股報告、/all 兩份都發，輸入 /help 查看更多指令。"
            )

    return jsonify({"status": "ok"})


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "service": "taiwan-stock-line-bot"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_webhook(host: str = "0.0.0.0", port: int = 5000) -> None:
    """Start the LINE webhook Flask server."""
    logger.info("Starting LINE webhook server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_webhook()

# ---------------------------------------------------------------------------
# Auto-load .env file
# ---------------------------------------------------------------------------
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            if _k not in os.environ:
                os.environ[_k] = _v
