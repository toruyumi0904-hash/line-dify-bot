import os
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# LINE APIの設定（strip()で前後の空白・改行を除去）
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip())
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"].strip())

DIFY_API_KEY = os.environ["DIFY_API_KEY"].strip()
DIFY_API_URL = os.environ.get("DIFY_API_URL", "https://api.dify.ai/v1").strip()

# ステップ番号 → 質問テキスト
QUESTIONS = {
    1: "起床時間を教えてください。\n例：7:00",
    2: "就寝時間を教えてください。\n例：23:00",
    3: "固定予定を教えてください。\nなければ「なし」と入力してください",
    4: "今日やるタスクを教えてください",
    5: "最優先のタスクを教えてください",
}

# ステップ番号 → Dify inputsのキー名
STEP_KEYS = {
    1: "wake_time",
    2: "sleep_time",
    3: "fixed_schedule",
    4: "tasks",
    5: "top_priority",
}

# ユーザーごとの会話状態を保存（サーバー再起動でリセット）
# { user_id: { "step": int, "answers": { key: value } } }
user_states = {}


def ask_dify(user_id: str, answers: dict) -> str:
    """5項目が揃ったらDify APIを呼び出してスケジュールを生成する"""
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {
            "wake_time": answers["wake_time"],
            "sleep_time": answers["sleep_time"],
            "fixed_schedule": answers["fixed_schedule"],
            "tasks": answers["tasks"],
            "top_priority": answers["top_priority"],
        },
        "query": "今日のスケジュールを作成してください",
        "response_mode": "blocking",
        "user": user_id,
    }

    try:
        response = requests.post(
            f"{DIFY_API_URL}/chat-messages",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("answer", "返答を取得できませんでした。")

    except requests.exceptions.Timeout:
        return "タイムアウトしました。もう一度お試しください。"
    except requests.exceptions.RequestException as e:
        print(f"Dify APIエラー: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Dify レスポンス詳細: {e.response.text}")
        return "エラーが発生しました。しばらく後でお試しください。"


def handle_conversation(user_id: str, message: str) -> str:
    """ユーザーの会話状態を管理し、次に返すテキストを返す"""

    # リセットコマンドは常に最優先で処理
    if message.strip() == "リセット":
        user_states[user_id] = {"step": 1, "answers": {}}
        return "リセットしました。最初からやり直します。\n\n" + QUESTIONS[1]

    state = user_states.get(user_id)

    # 状態がない場合は最初の質問から開始
    if state is None:
        user_states[user_id] = {"step": 1, "answers": {}}
        return QUESTIONS[1]

    step = state["step"]

    # 現在のステップの回答を保存
    state["answers"][STEP_KEYS[step]] = message.strip()

    next_step = step + 1

    # まだ質問が残っている場合は次の質問を返す
    if next_step <= 5:
        state["step"] = next_step
        return QUESTIONS[next_step]

    # 5項目すべて揃った → Dify APIを呼び出す
    answers = state["answers"].copy()
    del user_states[user_id]  # Dify呼び出し前に状態をリセット
    print(f"Dify呼び出し: user={user_id}, inputs={answers}")
    return ask_dify(user_id, answers)


@app.route("/callback", methods=["POST"])
def callback():
    """LINEからのWebhookを受け取るエンドポイント"""
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        print(f"[ERROR] 署名検証失敗: {e}")
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """テキストメッセージを受け取り、会話状態に応じて返信する"""
    user_id = event.source.user_id
    user_message = event.message.text

    print(f"受信: user={user_id}, message={user_message}")

    reply_text = handle_conversation(user_id, user_message)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


@app.route("/health", methods=["GET"])
def health():
    """サーバーの動作確認用エンドポイント"""
    return {"status": "ok"}, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
