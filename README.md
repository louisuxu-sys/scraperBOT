# SPORTIQ LINE Bot

體育即時比分 · AI 智能分析 LINE Bot

## 功能

- 📊 查看今日各聯賽賽事（NBA、MLB、SBL、歐洲職籃、足球、冰球、網球）
- 🤖 AI 賽前分析（基於戰績、近況、主客場、盤口等數據）
- 📅 支援昨天/今天/明天賽事查詢
- 🔍 依隊名搜尋特定比賽分析

## 使用指令

| 指令 | 說明 |
|---|---|
| `籃球` | 查看今日籃球賽事 |
| `棒球` / `足球` / `冰球` / `網球` | 查看對應運動賽事 |
| `NBA` / `MLB` / `SBL` | 快速查看特定聯賽 |
| `分析 湖人` | 查看與湖人相關比賽的 AI 分析 |
| `昨天 籃球` | 查看昨天籃球賽事 |
| `明天 棒球` | 查看明天棒球賽事 |
| `比分` | 今日籃球比分 |
| `幫助` | 顯示功能說明 |

## 部署步驟

### 1. 建立 LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider → 建立 Messaging API Channel
3. 取得：
   - **Channel Secret**（Basic settings 頁面）
   - **Channel Access Token**（Messaging API 頁面，點 Issue 產生）

### 2. 部署到 Render

1. 在 Render 建立新的 **Web Service**
2. 連結 GitHub repo，設定：
   - **Root Directory**: `linebot`
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
3. 加入環境變數：
   - `LINE_CHANNEL_ACCESS_TOKEN` = 你的 Channel Access Token
   - `LINE_CHANNEL_SECRET` = 你的 Channel Secret
4. 部署完成後取得 URL，例如 `https://sportiq-linebot.onrender.com`

### 3. 設定 Webhook

1. 回到 LINE Developers Console
2. Messaging API → Webhook URL 填入：
   ```
   https://你的網域/callback
   ```
3. 開啟 **Use webhook**
4. 關閉 **Auto-reply messages**（在 LINE Official Account Manager）

### 4. 本地開發

```bash
cd linebot
pip install -r requirements.txt

# 設定環境變數
set LINE_CHANNEL_ACCESS_TOKEN=你的Token
set LINE_CHANNEL_SECRET=你的Secret

# 啟動
python app.py
```

使用 ngrok 建立臨時公開 URL 來測試：
```bash
ngrok http 5000
```

## 檔案結構

```
linebot/
├── app.py              # LINE Bot 主程式（Flask webhook）
├── scraper.py          # playsport.cc 資料爬取
├── analyzer.py         # AI 分析引擎（規則式）
├── requirements.txt    # Python 依賴
└── README.md           # 本文件
```

## 資料來源

- [playsport.cc](https://www.playsport.cc/) - 即時比分、戰績、盤口
