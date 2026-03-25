from .schema import NormalisedMessage
from datetime import datetime, timezone


def parse_telegram_event(payload: dict) -> NormalisedMessage | None:
    try:
        msg  = payload["message"]
        user = msg["from"]
        text = msg.get("text", "").strip()

        if not text:
            return None  # ignore non-text messages

        ts = datetime.fromtimestamp(msg["date"], tz=timezone.utc).isoformat()
        name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))

        return NormalisedMessage(
            channel="telegram",
            user_id=str(user["id"]),
            user_name=name or None,
            text=text,
            timestamp=ts,
            reply_to=str(msg["chat"]["id"]),
        )
    except (KeyError, TypeError):
        return None
