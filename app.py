import os
import datetime
from dotenv import load_dotenv
load_dotenv()
import requests
import httpx
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, Response
from openai import OpenAI

app = Flask(__name__, static_folder="static", template_folder="templates")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client(timeout=30)) if OPENAI_API_KEY else None
MODEL = "gpt-4o-mini"


def supabase_headers(user_token=None):
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {user_token or SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def verify_token(token):
    """Supabase Auth トークンを検証してユーザー情報を返す"""
    if not token or not SUPABASE_URL:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


def auth_token():
    """リクエストヘッダーからトークンを取得"""
    return request.headers.get("Authorization", "").replace("Bearer ", "").strip()


# ── メイン画面 ──
@app.route("/")
def index():
    return render_template("index.html",
                           supabase_url=SUPABASE_URL,
                           supabase_key=SUPABASE_KEY)


# ── 担当住民一覧取得 ──
@app.route("/api/residents", methods=["GET"])
def get_residents():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/residents",
        headers=supabase_headers(token),
        params={"select": "id,name,address,notes", "is_active": "eq.true", "order": "name.asc"},
        timeout=10,
    )
    if r.ok:
        return jsonify({"residents": r.json()})
    return jsonify({"error": r.text}), r.status_code


# ── AI日報生成 ──
@app.route("/api/report/generate", methods=["POST"])
def generate_report():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    if not client:
        return jsonify({"error": "OpenAI APIキーが設定されていません"}), 500

    data = request.get_json(silent=True) or {}
    voice_text = data.get("voice_text", "").strip()
    resident_name = data.get("resident_name", "対象者")

    if not voice_text:
        return jsonify({"error": "音声テキストが空です"}), 400

    today = datetime.date.today().strftime("%Y年%m月%d日")

    user_msg = (
        f"以下は民生委員が{resident_name}さんを訪問した際の音声記録です。"
        f"民生委員の訪問日報として使える文章を作成してください。\n\n"
        f"【訪問日】{today}\n"
        f"【音声記録】\n{voice_text}\n\n"
        f"【日報の形式】\n"
        f"以下の6項目を必ず出力してください。"
        f"音声記録から読み取れる情報は具体的に記載し、情報がない項目は「記録なし」と記載してください。\n\n"
        f"①安否確認：在宅確認・呼びかけへの応答・健康状態\n"
        f"②生活状況：食事・睡眠・住環境・身の回りの清潔さ\n"
        f"③相談・心配事：本人や家族からの相談・悩み・不安\n"
        f"④対応・支援内容：今回行ったこと・声かけ・関係機関への連絡\n"
        f"⑤要支援事項：行政や専門機関（包括支援センター等）への連絡が必要な事項\n"
        f"⑥次回予定・特記事項：次回訪問予定日・引き継ぎ事項・気になること\n\n"
        f"丁寧で簡潔な文体（敬体）で出力してください。"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "あなたは民生委員の訪問活動を支援するアシスタントです。"},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1000,
            temperature=0.3,
        )
        return jsonify({"report": response.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 日報をSupabaseに保存 ──
@app.route("/api/report/save", methods=["POST"])
def save_report():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    data = request.get_json(silent=True) or {}
    payload = {
        "resident_id": data.get("resident_id"),
        "commissioner_id": user["id"],
        "visited_at": data.get("visited_at", datetime.date.today().isoformat()),
        "raw_voice_text": data.get("voice_text", ""),
        "full_report": data.get("report", ""),
    }

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/visit_reports",
        headers={**supabase_headers(token), "Prefer": "return=representation"},
        json=payload,
        timeout=10,
    )
    if r.ok:
        return jsonify({"status": "ok"})
    return jsonify({"error": r.text}), r.status_code


# ── 日報テキストダウンロード ──
@app.route("/api/download", methods=["POST"])
def download_report():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    filename = data.get("filename", "日報.txt")
    if not content:
        return jsonify({"error": "empty"}), 400
    resp = Response(content.encode("utf-8"), mimetype="text/plain; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(filename)
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
