with open("src/line_notifier.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: Add guard in __init__
old1 = '            logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set. Report generated but not sent to LINE.")'
new1 = '            logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set. Skipping LINE push.")\n            self.use_broadcast = False\n            self.user_ids = []'
content = content.replace(old1, new1)

# Fix 2: Handle 401/403 in _send_to_user
old2 = """        if resp.status_code != 200:
            logger.error(
                "LINE API returned status %d for user %s: %s",
                resp.status_code,
                user_id,
                resp.text,
            )
            resp.raise_for_status()

        logger.info("LINE push message sent to '%s' successfully.", user_id)"""
new2 = """        if resp.status_code == 200:
            logger.info("LINE push message sent to '%s' successfully.", user_id)
            return

        if resp.status_code in (401, 403):
            logger.error(
                "LINE authentication failed for user %s. Check LINE_CHANNEL_ACCESS_TOKEN. Response: %s",
                user_id,
                resp.text,
            )
            return

        logger.error(
            "LINE API returned status %d for user %s: %s",
            resp.status_code,
            user_id,
            resp.text,
        )
        resp.raise_for_status()"""
content = content.replace(old2, new2)

# Fix 3: Handle 401/403 in _send_broadcast
old3 = """        if resp.status_code != 200:
            logger.error(
                "LINE broadcast API returned status %d: %s",
                resp.status_code,
                resp.text,
            )
            resp.raise_for_status()

        logger.info("LINE broadcast sent successfully.")"""
new3 = """        if resp.status_code == 200:
            logger.info("LINE broadcast sent successfully.")
            return

        if resp.status_code in (401, 403):
            logger.error(
                "LINE authentication failed. Check LINE_CHANNEL_ACCESS_TOKEN. Response: %s",
                resp.text,
            )
            return

        logger.error(
            "LINE broadcast API returned status %d: %s",
            resp.status_code,
            resp.text,
        )
        resp.raise_for_status()"""
content = content.replace(old3, new3)

with open("src/line_notifier.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Fixed line_notifier.py")
