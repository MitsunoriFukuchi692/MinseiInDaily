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

# ── 閲覧数カウンター設定 ──
# 他サイトから叩かれるため、集計対象は決め打ちの許可リストのみに限定する
# （任意の site 名で行が量産されるのを防ぐ）。
COUNTER_ALLOWED_SITES = {
    "120gakkai.com", "120gakkai.com/braintrain",
    "robostudy.jp", "robostudy.jp/chatbot",
}

# ── 脳トレゲーム 挑戦記録設定 ──
# 120学会HP（braintrain）の各ゲームから叩かれる。任意の game 名で行が
# 量産されないよう、決め打ちの許可リストのみ受け付ける。
BRAIN_GAME_ALLOWED = {
    "kiokusagashi", "numberguess", "natsukashi-shiritori",
    "kotowaza-anaume", "showa-crossword", "nou-nenrei",
}

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


# ── 過去の日報一覧取得 ──
@app.route("/api/reports", methods=["GET"])
def get_reports():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    resident_id = request.args.get("resident_id")
    try:
        if not fetch_own_resident(token, resident_id):
            return jsonify({"error": "担当する住民が見つかりません"}), 403
    except SupabaseUnavailable:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/visit_reports",
            headers=supabase_headers(token),
            params={
                "select": "id,visited_at,full_report,created_at",
                "resident_id": f"eq.{resident_id}",
                "order": "visited_at.desc,created_at.desc",
                "limit": "50",
            },
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503

    if r.ok:
        return jsonify({"reports": r.json()})
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
        next_check = generate_text(
            "あなたは民生委員の訪問活動を支援するアシスタントです。",
            f"以下は今回作成した訪問日報です。この内容を踏まえて、次回訪問時に"
            f"民生委員が確認すべき事項を1文（30〜50文字程度）で簡潔に提案してください。"
            f"「次回確認：」などの接頭辞は付けず、確認事項の本文だけを出力してください。\n\n{report}",
            max_tokens=100,
            temperature=0.3,
        )
        return jsonify({"report": report, "next_check": next_check.strip()})
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
        "next_check": data.get("next_check", ""),
        "status": "未対応",
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


# ── 対応状況を更新（未対応/対応中/完了） ──
@app.route("/api/report/update_status", methods=["POST"])
def update_report_status():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    data = request.get_json(silent=True) or {}
    report_id = data.get("report_id")
    status = data.get("status")
    if status not in ("未対応", "対応中", "完了"):
        return jsonify({"error": "不正なステータスです"}), 400
    if not report_id:
        return jsonify({"error": "report_idが必要です"}), 400

    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/visit_reports",
        headers=supabase_headers(token),
        params={"id": f"eq.{report_id}", "commissioner_id": f"eq.{user['id']}"},
        json={"status": status},
        timeout=10,
    )
    if r.ok:
        return jsonify({"status": "ok"})
    return jsonify({"error": r.text}), r.status_code


# ── 担当住民ごとの最終訪問日・未訪問日数 ──
@app.route("/api/insights/overview", methods=["GET"])
def insights_overview():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/visit_reports",
            headers=supabase_headers(token),
            params={
                "select": "id,resident_id,visited_at,status,next_check",
                "order": "resident_id.asc,visited_at.desc,created_at.desc",
            },
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503

    if not r.ok:
        return jsonify({"error": r.text}), r.status_code

    # resident_idごとに先頭（最新の日報）だけ残す
    latest = {}
    for row in r.json():
        rid = row["resident_id"]
        if rid not in latest:
            latest[rid] = row

    today = datetime.date.today()
    insights = []
    for rid, row in latest.items():
        days_since = (today - datetime.date.fromisoformat(row["visited_at"])).days
        insights.append({
            "resident_id": rid,
            "last_visited_at": row["visited_at"],
            "days_since": days_since,
            "report_id": row["id"],
            "status": row.get("status") or "未対応",
            "next_check": row.get("next_check") or "",
        })

    return jsonify({"insights": insights})


# ── AIによる振り返りメモ ──
@app.route("/api/insights/reflect", methods=["POST"])
def insights_reflect():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    if not client:
        return jsonify({"error": "AIのAPIキーが設定されていません"}), 500

    data = request.get_json(silent=True) or {}
    resident_id = data.get("resident_id")

    try:
        resident = fetch_own_resident(token, resident_id)
    except SupabaseUnavailable:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503
    if not resident:
        return jsonify({"error": "担当する住民が見つかりません"}), 403

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/visit_reports",
            headers=supabase_headers(token),
            params={
                "select": "visited_at,full_report",
                "resident_id": f"eq.{resident_id}",
                "order": "visited_at.desc,created_at.desc",
                "limit": "10",
            },
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503
    if not r.ok:
        return jsonify({"error": r.text}), r.status_code

    reports = r.json()
    if not reports:
        return jsonify({"reflection": "まだ日報がありません。訪問記録が溜まると振り返りを表示します。"})

    name = resident.get("name")
    body = "\n\n".join(
        f"【{rep['visited_at']}】\n{mask_name(rep.get('full_report', ''), name)}" for rep in reversed(reports)
    )

    user_msg = (
        f"以下は同じ対象者（民生委員の担当住民）についての、時系列順の訪問日報です。"
        f"これらを踏まえて、変化点や気になる傾向を3行程度で振り返ってください。"
        f"個人が特定できる氏名は使わず「対象者」と表記してください。\n\n{body}"
    )

    try:
        reflection = generate_text(
            "あなたは民生委員の見守り活動を支援するアシスタントです。",
            user_msg,
            max_tokens=400,
            temperature=0.3,
        )
        return jsonify({"reflection": reflection})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 引き継ぎシート用の経緯まとめ ──
@app.route("/api/handover/summary", methods=["POST"])
def handover_summary():
    token = auth_token()
    user = verify_token(token)
    if not user:
        return jsonify({"error": "認証が必要です"}), 401

    if not client:
        return jsonify({"error": "AIのAPIキーが設定されていません"}), 500

    data = request.get_json(silent=True) or {}
    resident_id = data.get("resident_id")

    try:
        resident = fetch_own_resident(token, resident_id)
    except SupabaseUnavailable:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503
    if not resident:
        return jsonify({"error": "担当する住民が見つかりません"}), 403

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/visit_reports",
            headers=supabase_headers(token),
            params={
                "select": "visited_at,full_report",
                "resident_id": f"eq.{resident_id}",
                "order": "visited_at.desc,created_at.desc",
                "limit": "50",
            },
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました。電波状況を確認して、もう一度お試しください。"}), 503
    if not r.ok:
        return jsonify({"error": r.text}), r.status_code

    reports = r.json()
    if not reports:
        return jsonify({"summary": "まだ日報がありません。"})

    name = resident.get("name")
    body = "\n\n".join(
        f"【{rep['visited_at']}】\n{mask_name(rep.get('full_report', ''), name)}" for rep in reversed(reports)
    )

    user_msg = (
        f"以下は同じ対象者（民生委員の担当住民）についての、時系列順の訪問日報の全記録です。"
        f"後任の民生委員が引き継ぐ際に読む「経緯まとめ」を3〜5行で作成してください。"
        f"個人が特定できる氏名は使わず「対象者」と表記してください。\n\n{body}"
    )

    try:
        summary = generate_text(
            "あなたは民生委員の引き継ぎ資料作成を支援するアシスタントです。",
            user_msg,
            max_tokens=500,
            temperature=0.3,
        )
        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


# ── 閲覧数カウンター ──
# 120学会HPなど、静的サイトのTOPに「訪問者数」を表示するための共有API。
# 別オリジン（120gakkai.com）から呼ばれるため CORS を許可する。
@app.after_request
def _counter_cors(resp):
    if request.path.startswith("/api/counter") or request.path.startswith("/api/braingame"):
        origin = request.headers.get("Origin")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/counter", methods=["GET"])
def counter_get():
    """カウントを増やさずに現在値だけ返す（サイト所有者が自分の閲覧を数え
    ないようにする用途。表示はするがインクリメントはしない）。"""
    site = (request.args.get("site") or "").strip()
    if site not in COUNTER_ALLOWED_SITES:
        return jsonify({"error": "unknown site"}), 400
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/site_counters",
            headers=supabase_headers(),
            params={"select": "count", "site": f"eq.{site}"},
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました"}), 503
    if r.ok:
        rows = r.json()
        return jsonify({"count": rows[0]["count"] if rows else 0})
    return jsonify({"error": r.text}), r.status_code


@app.route("/api/counter/hit", methods=["POST", "OPTIONS"])
def counter_hit():
    # ブラウザのプリフライト（OPTIONS）にはボディなしで応答する
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    site = (data.get("site") or "").strip()
    if site not in COUNTER_ALLOWED_SITES:
        return jsonify({"error": "unknown site"}), 400

    # Postgres 側の関数で原子的に +1 して新しい値を受け取る
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/increment_site_counter",
            headers=supabase_headers(),
            json={"p_site": site},
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました"}), 503

    if r.ok:
        return jsonify({"count": r.json()})
    return jsonify({"error": r.text}), r.status_code


# ── 脳トレゲーム 挑戦記録 ──
# 120学会HP（braintrain）の各ゲームから叩かれる、みんなで見られる挑戦記録。
@app.route("/api/braingame/records", methods=["GET"])
def braingame_records():
    game = (request.args.get("game") or "").strip()
    if game not in BRAIN_GAME_ALLOWED:
        return jsonify({"error": "unknown game"}), 400

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 100))

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/get_brain_game_records",
            headers=supabase_headers(),
            json={"p_game": game, "p_limit": limit},
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました"}), 503

    if r.ok:
        rows = r.json()
        records = [
            {
                "nickname": row["nickname"],
                "score": row["score"],
                "cleared": row["cleared"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return jsonify({"records": records})
    return jsonify({"error": r.text}), r.status_code


@app.route("/api/braingame/record", methods=["POST", "OPTIONS"])
def braingame_record():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    game = (data.get("game") or "").strip()
    if game not in BRAIN_GAME_ALLOWED:
        return jsonify({"error": "unknown game"}), 400

    nickname = (data.get("nickname") or "").strip()[:20]
    if not nickname:
        return jsonify({"error": "nickname is required"}), 400

    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid score"}), 400
    if not (0 <= score <= 1000):
        return jsonify({"error": "invalid score"}), 400

    cleared = bool(data.get("cleared"))

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/insert_brain_game_record",
            headers=supabase_headers(),
            json={"p_game": game, "p_nickname": nickname, "p_score": score, "p_cleared": cleared},
            timeout=10,
        )
    except Exception:
        return jsonify({"error": "通信に失敗しました"}), 503

    if r.ok:
        return jsonify({"ok": True})
    return jsonify({"error": r.text}), r.status_code


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
