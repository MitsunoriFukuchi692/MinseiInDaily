import os
from flask import Flask, render_template, request, jsonify, make_response
from openai import OpenAI
import requests
import httpx

app = Flask(__name__, static_folder="static", template_folder="templates")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE = "histories"
SUPABASE_REST = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
SUPABASE_HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client(timeout=30)) if OPENAI_API_KEY else None
MODEL = "gpt-4o-mini"

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

@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json()
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "プロンプトが空です"}), 400
        response = client.chat.completions.create(model=MODEL, messages=[{"role": "system", "content": "あなたは自分史のライティングアシスタントです。"}, {"role": "user", "content": f"以下の文章を整えてください：\n{prompt}"}], max_tokens=800, temperature=0.2)
        return jsonify({"text": response.choices[0].message.content.strip()})
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