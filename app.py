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

# ── LLMプロバイダ設定 ──
# LLM_PROVIDER で切り替える（openai / gemini / vertex）。
# 自治体案件では ISMAP 登録済みの vertex（東京リージョン）を使うこと。
# Gemini API の無料枠は入力が学習に利用される可能性があるため本番では使用禁止。
LLM_PROVIDERS = {
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "base_url": None,
        "model": "gpt-4o-mini",
    },
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
    },
}

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai").lower()
_provider = LLM_PROVIDERS.get(LLM_PROVIDER, LLM_PROVIDERS["openai"])
LLM_API_KEY = os.environ.get(_provider["key_env"], "")
MODEL = os.environ.get("LLM_MODEL", _provider["model"])

client = (
    OpenAI(
        api_key=LLM_API_KEY,
        base_url=_provider["base_url"],
        http_client=httpx.Client(timeout=30),
    )
    if LLM_API_KEY
    else None
)


def generate_text(system_prompt, user_prompt, max_tokens=1000, temperature=0.3):
    """LLMにテキスト生成させる。プロバイダの違いはここで吸収する。"""
    if not client:
        raise RuntimeError(
            f"{_provider['key_env']} が設定されていません（LLM_PROVIDER={LLM_PROVIDER}）"
        )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


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


class SupabaseUnavailable(Exception):
    """Supabaseへの問い合わせ自体が失敗した（通信断・タイムアウト・サーバエラー）。

    「担当外の住民」と区別するために用意している。混同すると、通信の一時的な
    失敗を権限エラーとして表示してしまい原因の切り分けができなくなる。
    """


def fetch_own_resident(token, resident_id):
    """自分が担当する住民かを確認して住民情報を返す。担当外・不存在なら None。

    ユーザートークンで問い合わせるため、RLS が担当外の行を除外する。
    通信に失敗した場合は SupabaseUnavailable を送出する（Noneと区別する）。
    """
    if not resident_id:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/residents",
            headers=supabase_headers(token),
            params={"select": "id,name", "id": f"eq.{resident_id}"},
            timeout=10,
        )
    except Exception as e:
        raise SupabaseUnavailable(str(e))

    if r.ok:
        rows = r.json()
        return rows[0] if rows else None
    # 4xx は不正なIDなど「見つからない」側、5xx はサーバ側の障害として扱う
    if r.status_code >= 500:
        raise SupabaseUnavailable(f"status {r.status_code}")
    return None


def mask_name(text, name):
    """音声記録から対象者の氏名を伏せる（AI事業者へ氏名を送らないため）。

    「田中 花子」なら フルネーム・「田中花子」・「田中」・「花子」を置換する。
    誤爆を避けるため1文字の姓名は対象外。
    """
    if not text or not name:
        return text
    parts = [p for p in name.replace("　", " ").split(" ") if p]
    # 姓名の区切りは半角/全角スペース・詰めの表記ゆれがあるため全て候補にする
    # 長い表記から順に置換する（「田中花子」を「田中」で先に壊さないため）
    candidates = [name, "".join(parts), " ".join(parts), "　".join(parts)] + parts
    for c in sorted(set(candidates), key=len, reverse=True):
        if len(c) >= 2:
            text = text.replace(c, "対象者")
    return text


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
        return jsonify({"error": "AIのAPIキーが設定されていません"}), 500

    data = request.get_json(silent=True) or {}
    voice_text = data.get("voice_text", "").strip()
    resident_id = data.get("resident_id")

    if not voice_text:
        return jsonify({"error": "音声テキストが空です"}), 400

    try:
        resident = fetch_own_resident(token, resident_id)
    except SupabaseUnavailable:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503
    if not resident:
        return jsonify({"error": "担当する住民が見つかりません"}), 403

    # 氏名は外部のAIへ送らない。音声記録に含まれる氏名もここで伏せる。
    voice_text = mask_name(voice_text, resident.get("name"))

    today = datetime.date.today().strftime("%Y年%m月%d日")

    user_msg = (
        f"以下は民生委員が対象者を訪問した際の音声記録です。"
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
        report = generate_text(
            "あなたは民生委員の訪問活動を支援するアシスタントです。",
            user_msg,
            max_tokens=1000,
            temperature=0.3,
        )
        return jsonify({"report": report})
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

    # 他の民生委員が担当する住民に日報を紐づけられないよう検証する
    resident_id = data.get("resident_id")
    try:
        if not fetch_own_resident(token, resident_id):
            return jsonify({"error": "担当する住民が見つかりません"}), 403
    except SupabaseUnavailable:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503

    payload = {
        "resident_id": resident_id,
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
