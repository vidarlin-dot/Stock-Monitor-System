# -*- coding: utf-8 -*-
"""Helper to convert a GCP service account JSON file into an env-ready string.

Usage:
    python setup_gcp.py

Steps:
    1. Paste path to downloaded .json key file
    2. Outputs the single-line value to paste into .env
"""

import json
import os
import sys


def json_to_env_value(json_path: str) -> str:
    """Read a GCP service account JSON file and return it as a single-line env string."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalize private_key: replace real newlines with literal \n
    if "private_key" in data:
        data["private_key"] = data["private_key"].replace("\n", "\\n")

    # Compact JSON with no extra whitespace
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def main():
    print("=== GCP Service Account JSON → .env helper ===")
    print()

    json_path = input("請貼上 GCP Service Account JSON 檔案路徑 (例如 C:\\Downloads\\key.json): ").strip().strip('"')

    if not os.path.exists(json_path):
        print(f"❌ 檔案不存在: {json_path}")
        sys.exit(1)

    try:
        env_value = json_to_env_value(json_path)
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        sys.exit(1)

    print()
    print("✅ 轉換完成！請複製下方內容，貼入 .env 的 GCP_SERVICE_ACCOUNT_JSON= 後面：")
    print()
    print(f"GCP_SERVICE_ACCOUNT_JSON={env_value}")
    print()
    print("長度：{:,} 字元".format(len(env_value)))
    print()
    print("完整 .env 內容預覽：")
    print("-" * 60)


if __name__ == "__main__":
    main()
