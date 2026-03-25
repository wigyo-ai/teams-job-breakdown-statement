from .schema import NormalisedMessage
from datetime import datetime, timezone


def parse_whatsapp_event(payload: dict) -> NormalisedMessage | None:
    try:
        entry   = payload["entry"][0]
        change  = entry["changes"][0]["value"]
        message = change["messages"][0]
        contact = change["contacts"][0]

        if message.get("type") != "text":
            return None  # ignore non-text (images, audio, etc.)

        return NormalisedMessage(
            channel="whatsapp",
            user_id=message["from"],
            user_name=contact.get("profile", {}).get("name"),
            text=message["text"]["body"],
            timestamp=datetime.fromtimestamp(
                int(message["timestamp"]), tz=timezone.utc
            ).isoformat(),
            reply_to=message["from"],
        )
    except (KeyError, IndexError):
        return None
