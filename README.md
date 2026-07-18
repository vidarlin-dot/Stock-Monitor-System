# Stock-Monitor-System

台灣投資者的美股持倉監控系統。以 Google Sheets 為持倉數據的唯一來源，透過 GitHub Actions 每日排程執行，並將日報推播至 LINE。

---

## 專案結構

```
Stock-Monitor-System/
├── .github/workflows/
│   └── daily_monitor.yml       # GitHub Actions 排程
├── src/
│   ├── __init__.py
│   ├── config.py               # Google Sheets 管理
│   ├── line_notifier.py        # LINE Messaging API 推播
│   ├── main.py                 # 每日監控主程式
│   └── update_holdings.py      # 半自動持倉更新工具
├── requirements.txt
└── README.md
```

## 快速開始

### 1. Google Sheets 設定

1. 建立新的 Google Sheet，命名為 `Portfolio`（可自訂，需與 `SHEET_NAME` 一致）。
2. 新增一個工作表（Worksheet），命名為 `Holdings`。
3. 第一列填入以下欄位名稱：

| Ticker | Shares | AvgCost | BuyZone | SellZone | CatalystDate | Notes | Updated |
|--------|--------|---------|---------|----------|--------------|-------|---------|
| AAPL   | 100    | 150.00  | 140.00  | 180.00   | 2026-09-15   | Q3 earnings | |

4. 填入你的持倉資料。

### 2. GCP Service Account 設定

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)。
2. 建立新的專案或選擇現有專案。
3. 啟用 **Google Sheets API**。
4. 建立 **Service Account**，並產生 JSON 金鑰檔。
5. 將 JSON 內容複製起來（後續會設為 GitHub Secret）。
6. 將 Google Sheet 與該 Service Account 的 email 共享（編輯者權限）。

### 3. LINE Developers 設定

1. 前往 [LINE Developers](https://developers.line.biz/) 並登入。
2. 建立新的 **Channel**（Messaging API）。
3. 取得 **Channel Access Token**（長Token）。
4. 取得你的 **LINE User ID**：
   - 在 Channel 設定中新增好友（QR Code）。
   - 使用 [Token-ended Validation](https://developers.line.biz/en/docs/line-messaging-api/test-message-api/#verification-endpoint) 功能或透過 LINE 聊天室取得。

### 4. GitHub Secrets 設定

前往你的 GitHub 倉庫 → **Settings** → **Secrets and variables** → **Actions**，新增以下 Secrets：

| Secret Name                  | Description                                      |
|------------------------------|--------------------------------------------------|
| `GCP_SERVICE_ACCOUNT_JSON`   | GCP Service Account 的完整 JSON 字串             |
| `SHEET_NAME`                 | Google Sheet 的名稱（預設 `Portfolio`）           |
| `LINE_CHANNEL_ACCESS_TOKEN`  | LINE Messaging API 的 Channel Access Token        |
| `LINE_USER_ID`               | LINE 使用者的 User ID                            |

### 5. 本地開發（選配）

```bash
pip install -r requirements.txt

# 設定環境變數（PowerShell 範例）
$env:GCP_SERVICE_ACCOUNT_JSON = '<JSON>'
$env:SHEET_NAME = 'Portfolio'
$env:LINE_CHANNEL_ACCESS_TOKEN = '<TOKEN>'
$env:LINE_USER_ID = '<USER_ID>'

# 執行每日監控
python src/main.py

# 更新持倉
python src/update_holdings.py
```

## 運作流程

```
GitHub Actions (每週一至五 美東 16:05)
    │
    ├─→ 讀取 Google Sheets 持倉配置
    ├─→ yfinance 抓取最新股價
    ├─→ 策略引擎：檢查買賣訊號 / 停損
    ├─→ 催化劑引擎：檢查事件倒數
    ├─→ 生成繁體中文日報
    └─→ LINE Messaging API 推播
```

## 半自動持倉更新

當你在凱基證券完成美股交易後：

1. 複製交易確認明細的文字。
2. 執行 `python src/update_holdings.py`。
3. 貼上明細文字，系統會自動解析並計算新均價。
4. 確認後寫回 Google Sheets。

## 注意事項

- 所有敏感資訊（API Keys、Service Account JSON）皆透過環境變數注入，絕不硬編碼。
- GitHub Actions 執行環境為 Ubuntu，無需本地 Windows 設定。
- yfinance 資料為延遲報價，僅供參考，不作為即時交易依據。
