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

# LINE APIの設定
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

DIFY_API_KEY = os.environ["DIFY_API_KEY"]
DIFY_API_URL = os.environ.get("DIFY_API_URL", "https://api.dify.ai/v1")

# ユーザーごとの会話IDを保存（簡易実装：サーバー再起動でリセット）
conversation_ids = {}


def ask_dify(user_id: str, message: str) -> str:
    """DifyのChat APIにメッセージを送り、返答テキストを返す"""
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": {},
        "query": message,
        "response_mode": "blocking",  # 同期モード
        "user": user_id,
    }

    # 同じユーザーの会話を継続する
    if user_id in conversation_ids:
        payload["conversation_id"] = conversation_ids[user_id]

    try:
        response = requests.post(
            f"{DIFY_API_URL}/chat-messages",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        # 会話IDを保存して次回も同じ会話を継続
        conversation_ids[user_id] = data.get("conversation_id", "")

        return data.get("answer", "返答を取得できませんでした。")

    except requests.exceptions.Timeout:
        return "タイムアウトしました。もう一度お試しください。"
    except requests.exceptions.RequestException as e:
        print(f"Dify APIエラー: {e}")
        return "エラーが発生しました。しばらく後でお試しください。"


@app.route("/callback", methods=["POST"])
def callback():
    """LINEからのWebhookを受け取るエンドポイント"""
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    # デバッグ用ログ（問題解決後に削除）
    print(f"[DEBUG] signature: {signature[:20]}...")
    print(f"[DEBUG] body: {body[:200]}")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        print(f"[ERROR] 署名検証失敗: {e}")
        print(f"[ERROR] Channel Secret 先頭8文字: {os.environ.get('LINE_CHANNEL_SECRET', '')[:8]}")
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """テキストメッセージを受け取り、Difyで処理して返信する"""
    user_id = event.source.user_id
    user_message = event.message.text

    print(f"受信: user={user_id}, message={user_message}")

    # Difyに問い合わせ
    reply_text = ask_dify(user_id, user_message)

    print(f"返信: {reply_text}")

    # LINEへ返信
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
