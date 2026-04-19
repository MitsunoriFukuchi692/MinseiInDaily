import os
import io
import random
import string
from flask import Flask, render_template, request, jsonify, make_response, send_file, Response
from openai import OpenAI
import requests
import httpx
from urllib.parse import quote
import datetime

app = Flask(__name__, static_folder="static", template_folder="templates")

# ── ルーム管理（メモリ上） ──
# rooms = { "1234": { "log": [...], "created_at": datetime } }
rooms = {}

def generate_room_id():
    """4桁のランダムなルームIDを生成"""
    return ''.join(random.choices(string.digits, k=4))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE = "histories"
SUPABASE_REST = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
SUPABASE_HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client(timeout=30)) if OPENAI_API_KEY else None
MODEL = "gpt-4o-mini"
LANG_MAP = {"en": "English", "fil": "Filipino (Tagalog)", "id": "Indonesian", "vi": "Vietnamese", "ja": "Japanese"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/jibunshi")
def jibunshi():
    return render_template("index.html")

@app.route("/new")
def index_new():
    resp = make_response(render_template("index_new.html"))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/ja/new")
@app.route("/ja/new/")
def care_new():
    resp = make_response(render_template("ja/index_new.html"))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/ja/caree")
@app.route("/ja/caree/")
def care_caree():
    """被介護者用のシンプルな入力画面"""
    resp = make_response(render_template("ja/caree.html"))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

# ── ルームAPI ──
@app.route("/room/create", methods=["POST"])
def room_create():
    """介護士がルームを作成する"""
    room_id = generate_room_id()
    # 万一重複したら再生成
    while room_id in rooms:
        room_id = generate_room_id()
    rooms[room_id] = {"log": [], "created_at": datetime.datetime.now().isoformat()}
    return jsonify({"room_id": room_id})

@app.route("/room/post", methods=["POST"])
def room_post():
    """介護士または被介護者がメッセージを送信する"""
    data = request.get_json(silent=True) or {}
    room_id = data.get("room_id", "").strip()
    text = data.get("text", "").strip()
    role = data.get("role", "caree")  # デフォルトは被介護者
    if not room_id or room_id not in rooms:
        return jsonify({"error": "ルームが見つかりません。ルームIDを確認してください。"}), 404
    if not text:
        return jsonify({"error": "empty"}), 400
    entry = {"role": role, "text": text, "time": datetime.datetime.now().strftime("%H:%M:%S")}
    rooms[room_id]["log"].append(entry)
    return jsonify({"status": "ok"})

@app.route("/room/poll", methods=["GET"])
def room_poll():
    """介護士側が新しいメッセージを取得する"""
    room_id = request.args.get("room_id", "").strip()
    since = int(request.args.get("since", 0))
    if not room_id or room_id not in rooms:
        return jsonify({"error": "ルームが見つかりません"}), 404
    log = rooms[room_id]["log"]
    new_entries = log[since:]
    return jsonify({"entries": new_entries, "total": len(log)})

@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json()
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "empty"}), 400
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "あなたは自分史のライティングアシスタントです。"},
                {"role": "user", "content": "以下の文章を整えてください：\n" + prompt}
            ],
            max_tokens=800, temperature=0.2)
        return jsonify({"text": response.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/translate", methods=["POST"])
def translate():
    try:
        data = request.get_json(silent=True) or {}
        text = data.get("text", "").strip()
        direction = data.get("direction", "ja-en")
        parts = direction.split("-")
        to_lang = parts[1] if len(parts) == 2 else "en"
        lang_name = LANG_MAP.get(to_lang, "English")
        if not text:
            return jsonify({"error": "empty"}), 400
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Translate into " + lang_name + ". Output only the translated text."},
                {"role": "user", "content": text}
            ],
            max_tokens=500, temperature=0.3)
        return jsonify({"translated": response.choices[0].message.content.strip(), "lang": to_lang})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tts", methods=["POST"])
def tts():
    try:
        data = request.get_json(silent=True) or {}
        text = data.get("text", "").strip()
        lang = data.get("lang", "en")
        if not text:
            return jsonify({"error": "empty"}), 400
        voice = {"ja": "shimmer", "en": "alloy", "fil": "nova", "id": "nova", "vi": "nova"}.get(lang, "alloy")
        response = client.audio.speech.create(model="tts-1", voice=voice, input=text)
        audio_bytes = io.BytesIO(response.content)
        audio_bytes.seek(0)
        return send_file(audio_bytes, mimetype="audio/mpeg", as_attachment=False)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/daily-report-inline", methods=["POST"])
def daily_report_inline():
    try:
        data = request.get_json(silent=True) or {}
        log = data.get("log", [])
        if not log:
            return jsonify({"error": "empty"}), 400
        lines = []
        for entry in log:
            role = "介護士" if entry.get("role") == "caregiver" else "被介護者"
            lines.append("[" + entry.get("time","") + "] " + role + "：" + entry.get("text",""))
        log_text = "\n".join(lines)
        today = datetime.date.today().strftime("%Y年%m月%d日")
        sys_msg = "あなたは介護施設の日報作成を支援するアシスタントです。"
        user_msg = (
            "以下は介護現場での会話記録です。施設の日報として使える文章を作成してください。\n\n"
            "【日付】" + today + "\n"
            "【会話記録】\n" + log_text + "\n\n"
            "【日報の形式】\n"
            "- 冒頭に日付・担当者欄（担当者名は「（記入）」としておく）\n"
            "- 「体調・バイタル」「食事・水分」「排泄」「活動・リハビリ」「会話・コミュニケーション」「特記事項」の6項目で構成\n"
            "- 会話から読み取れる情報は具体的に記載し、不明な項目は「記録なし」と記載\n"
            "- 丁寧で簡潔な文体で200〜300字程度にまとめる\n"
            "- 日本語で出力する"
        )
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=800, temperature=0.3)
        return jsonify({"report": response.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download-log", methods=["POST"])
def download_log():
    try:
        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        filename = data.get("filename", "log.txt")
        if not content:
            return jsonify({"error": "empty"}), 400
        resp = Response(content.encode("utf-8"), mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(filename)
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/save", methods=["POST"])
def save_history():
    try:
        data = request.get_json(silent=True) or {}
        payload = {"user_name": data.get("user_name", "guest"), "prompt": data.get("prompt", ""), "response": data.get("response", "")}
        r = requests.post(SUPABASE_REST, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        if r.status_code in (200, 201):
            return jsonify({"status": "ok", "data": r.json()})
        return jsonify({"status": "error", "detail": r.text}), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

@app.route("/get", methods=["GET"])
def get_histories():
    try:
        params = {"select": "*", "order": "created_at.desc", "limit": 20}
        r = requests.get(SUPABASE_REST, headers=SUPABASE_HEADERS, params=params, timeout=10)
        if r.ok:
            return jsonify({"status": "ok", "data": r.json()})
        return jsonify({"status": "error", "detail": r.text}), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
