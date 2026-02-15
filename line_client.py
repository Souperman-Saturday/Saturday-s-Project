from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)

MAX_LEN = 1800  # 保守避免 LINE 文字過長截斷

def _split_text(s: str, limit: int = MAX_LEN):
    s = (s or "").strip()
    if not s:
        return ["（空白）"]
    chunks = []
    buf = ""
    for line in s.splitlines(True):
        if len(buf) + len(line) <= limit:
            buf += line
        else:
            if buf:
                chunks.append(buf.rstrip())
            # 若單行爆長，硬切
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            buf = line
    if buf:
        chunks.append(buf.rstrip())
    return chunks

class LineClient:
    def __init__(self, access_token: str):
        self.cfg = Configuration(access_token=access_token)

    def reply_text(self, reply_token: str, text: str):
        msgs = [TextMessage(text=t) for t in _split_text(text)]
        with ApiClient(self.cfg) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=msgs[:5])
            )

    def push_text(self, to_user_id: str, text: str):
        msgs = [TextMessage(text=t) for t in _split_text(text)]
        with ApiClient(self.cfg) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=to_user_id, messages=msgs[:5])
            )
