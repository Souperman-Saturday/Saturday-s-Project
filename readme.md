# Saturday LINE AI 管家（V7）

## 功能
- 私聊：聊天、長期記憶（共享/私密）、提醒、晨報
- 群組：只支援查詢（天氣/新聞/運勢/日期），不做設定/記憶/提醒
- 天氣：WeatherAPI（結構化）
- 新聞：Serper News（近24h，中文/台灣視角）
- 運勢：Serper 搜來源 + Gemini 摘要

## Render
Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`

## UptimeRobot
Ping `/health` 避免 Free instance 休眠
