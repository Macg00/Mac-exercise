"""
LINE 運動挑戰 Bot - Webhook 伺服器
"""
import os, re, json, hmac, hashlib, base64, requests
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "app8LIJdTuh4z1tDE")
AIRTABLE_TABLE_ID = os.environ.get("AIRTABLE_TABLE_ID", "tblY5ou0tLSZrgnV3")
TW_TZ = timezone(timedelta(hours=8))

TRIGGER_KEYWORDS = ["運動","跑步","健身","游泳","騎車","瑜珈","走路","爬山","打球","籃球","羽毛球","桌球","重訓","有氧","exercise","workout","run","swim","gym"]

def parse_exercise(text):
    lower = text.lower()
    has_keyword = any(kw in lower for kw in TRIGGER_KEYWORDS)
    direct = re.fullmatch(r"\d+\s*(?:分鐘|分|min|mins|minutes)?", text.strip())
    if not has_keyword and not direct:
        return None
    m = re.search(r"(?P<type>[^\d\s]+)?\s*(?P<minutes>\d+)\s*(?:分鐘|分|min|mins|minutes)?", text, re.UNICODE)
    if not m: return None
    mins = int(m.group("minutes"))
    if mins <= 0 or mins > 600: return None
    t = (m.group("type") or "").strip()
    return mins, t if t else "運動"

def save_to_airtable(user_id, display_name, minutes, note, date_str):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
    payload = {"records": [{"fields": {
        "成員名稱": display_name, "LINE_User_ID": user_id,
        "運動時間_分鐘": minutes, "運動日期": date_str, "備註": note,
        "紀錄時間戳": datetime.now(TW_TZ).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }}]}
    requests.post(url, headers=headers, json=payload, timeout=10).raise_for_status()

def get_weekly_ranking():
    today = datetime.now(TW_TZ).date()
    monday = today - timedelta(days=today.weekday())
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    params = {"filterByFormula": f"AND({{運動日期}} >= '{monday}', {{運動日期}} <= '{today}')", "fields[]": ["成員名稱","運動時間_分鐘"]}
    records = requests.get(url, headers=headers, params=params, timeout=10).raise_for_status() or []
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    records = resp.json().get("records", [])
    totals = {}
    for r in records:
        name = r["fields"].get("成員名稱","未知")
        totals[name] = totals.get(name,0) + r["fields"].get("運動時間_分鐘",0)
    return sorted(totals.items(), key=lambda x: x[1], reverse=True)

def reply_line(reply_token, text):
    requests.post("https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"},
        json={"replyToken": reply_token, "messages": [{"type":"text","text":text}]}, timeout=10)

def push_line(group_id, text):
    requests.post("https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"},
        json={"to": group_id, "messages": [{"type":"text","text":text}]}, timeout=10)

def get_user_profile(user_id, group_id):
    r = requests.get(f"https://api.line.me/v2/bot/group/{group_id}/member/{user_id}",
        headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=10)
    return r.json().get("displayName","成員") if r.ok else "成員"

def verify_signature(body, signature):
    h = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(h).decode(), signature)

def build_ranking_message(ranking):
    if not ranking: return "📊 本週還沒有運動紀錄，快來記錄你的運動吧！💪"
    medals = ["🥇","🥈","🥉"]
    lines = ["🏆 本週運動排行榜 🏆",""]
    for i,(name,total) in enumerate(ranking):
        medal = medals[i] if i < 3 else f"{i+1}."
        h,m = divmod(total,60)
        d = f"{h}小時{m}分鐘" if h else f"{m}分鐘"
        lines.append(f"{medal} {name}　{d}（{total}分鐘）")
    lines += ["", f"🎉 本週冠軍是 {ranking[0][0]}！恭喜！繼續加油！💪"]
    return "\n".join(lines)

@app.get("/")
def health(): return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Line-Signature","")):
        raise HTTPException(status_code=403)
    for event in json.loads(body).get("events",[]):
        await handle_event(event)
    return JSONResponse({"status":"ok"})

async def handle_event(event):
    if event.get("type") != "message" or event.get("message",{}).get("type") != "text": return
    text = event["message"]["text"]
    reply_token = event.get("replyToken","")
    source = event.get("source",{})
    user_id = source.get("userId","")
    group_id = source.get("groupId","")

    if text.strip() in ["排行","排行榜","本週排行","🏆","冠軍"]:
        reply_line(reply_token, build_ranking_message(get_weekly_ranking())); return
    if text.strip() in ["幫助","help","說明"]:
        reply_line(reply_token, "🏃 使用說明\n\n傳「跑步 30」記錄30分鐘\n傳「運動 60」記錄60分鐘\n傳「排行」查看排行榜\n\n🏆 每週五晚上9點自動公布週冠軍！"); return

    result = parse_exercise(text)
    if not result: return
    minutes, exercise_type = result
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    display_name = get_user_profile(user_id, group_id) if group_id else "成員"
    try:
        save_to_airtable(user_id, display_name, minutes, exercise_type, today)
    except Exception as e:
        reply_line(reply_token, f"❌ 儲存失敗：{str(e)[:50]}"); return
    reply_line(reply_token, f"✅ 已記錄！\n👤 {display_name}\n🏃 {exercise_type} {minutes} 分鐘\n📅 {today}\n\n傳「排行」查看本週累計 🏆")
```

---

## 📄 requirements.txt
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
requests==2.31.0
python-dotenv==1.0.1
