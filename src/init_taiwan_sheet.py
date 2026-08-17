"""Add 追蹤層級 column to Taiwan_Stock sheet if missing."""
import logging
import sys
from config import GoogleSheetsManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

manager = GoogleSheetsManager()
worksheet = manager.client.open(manager.sheet_name).worksheet("Taiwan_Stock")

headers = worksheet.row_values(1)
logger.info("Current headers: %s", headers)

if "追蹤層級" in headers:
    logger.info("追蹤層級 column already exists at index %d", headers.index("追蹤層級"))
    sys.exit(0)

new_headers = headers[:1] + ["追蹤層級"] + headers[1:]
worksheet.update("A1", [new_headers])
logger.info("Added 追蹤層級 column")

rows = worksheet.row_count
if rows > 1:
    default_val = "一般追蹤"
    range_str = f"B2:B{rows}"
    worksheet.update(range_str, [[default_val] for _ in range(rows - 1)])
    logger.info("Filled 追蹤層級 with '%s' for %d rows", default_val, rows - 1)

logger.info("Done. Headers now: %s", worksheet.row_values(1))
