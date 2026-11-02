"""Presentation of incoming room events, independent of speech selection."""


def room_message_entry(record):
    data = record.get("data") or {}
    kind = data.get("type")
    label = {"chat": "弹幕", "gift": "礼物", "member": "进场",
             "social": "互动", "like": "点赞"}.get(kind)
    if label is None:
        return None
    user = str(data.get("user") or "观众")
    text = str(data.get("text") or "")
    if kind == "gift" and data.get("gift"):
        text = f"送出 {data['gift']} × {data.get('count') or 1}"
    elif kind == "member" and not text:
        text = "进入直播间"
    timestamp = str(record.get("time") or "")
    return timestamp, label, user, text
