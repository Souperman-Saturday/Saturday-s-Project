# Saturday v7.1 (Render + LINE OA)

這是一個可部署在 Render 的 LINE 官方帳號 AI 管家：
- 晨報（預設 07:00，可指令調整）
- 天氣（WeatherAPI 結構化）
- 星座（Serper 搜尋近24h + 摘要）
- 全球重大新聞（Serper News 近24h、繁中摘要、8則內）
- 提醒（N分鐘後 / 指定日期時間）
- 記憶（Supabase 持久化；embedding 可為 NULL）
- 傳訊（別名綁定 LINE userId，可指令換人）
- webhookEventId 去重（避免 LINE 重送造成重複處理）

---

## 一、需要的網站 & API Key（按網站分）

### 1) LINE Developers（建立官方帳號 Bot）
1. 到 LINE Developers 建立 Provider
2. 建立 Messaging API channel
3. 取得：
   - Channel access token
   - Channel secret
4. Webhook URL（等 Render 上線後再回來填）：
   - `https://<你的Render網址>/callback`
5. Webhook 必須 enable
6. 「Auto-reply」「Greeting」建議關掉（避免干擾）

### 2) Supabase（資料庫）
1. 建立 Project
2. 進入 SQL Editor，貼上 `supabase_schema.sql` 執行
3. 到 Settings → API：
   - Project URL → `SUPABASE_URL`
   - Secret key（sb_secret_...）→ `SUPABASE_KEY`（建議）

### 3) WeatherAPI
1. 申請 WeatherAPI key
2. 存到 `WEATHER_API_KEY`

### 4) Serper (Google Search API)
1. 申請 Serper API Key
2. 存到 `SERPER_API_KEY`

### 5) Google AI Studio（Gemini）
1. 取得 Gemini API key
2. 存到 `GEMINI_API_KEY`
> 若不填 Gemini，系統仍可工作，但摘要與中文整理能力會下降。

---

## 二、Render 部署（Web Service，不用 Background Worker）
1. Render → New → Web Service
2. 連 GitHub repo
3. Build Command：
   - `pip install -r requirements.txt`
4. Start Command：
   - (Render 會用 Procfile) 或填 `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
5. Environment Variables（照 `.env_example` 加入）
6. Deploy

部署成功後，你會拿到一個 Render URL，例如：
- `https://saturday-xxxx.onrender.com`

---

## 三、LINE Webhook 回填
回到 LINE Developers → Messaging API：
- Webhook URL：`https://<Render URL>/callback`
- Enable webhook
- Verify（應該會成功）

---

## 四、UptimeRobot（保持 Render free 不睡死）
1. 建立 HTTP(s) Monitor
2. URL 填：
   - `https://<Render URL>/health`
3. Interval 建議 5 分鐘

---

## 五、上線後測試順序
請看最後的「指令表」：先做基本連線，再做記憶，再測提醒，再測晨報，再測傳訊。

