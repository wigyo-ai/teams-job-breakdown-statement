from .schema import NormalisedMessage
from datetime import datetime, timezone


def parse_teams_event(payload: dict) -> NormalisedMessage | None:
    """
    Parse a Bot Framework Activity sent by Microsoft Teams.

    Teams delivers Activity objects whose `type` field indicates the event kind.
    Only `message` activities carrying user text are forwarded to the Orchestrator.
    Typing indicators, reactions, and other event types are silently ignored.
    """
    try:
        if payload.get("type") != "message":
            return None

        text = (payload.get("text") or "").strip()
        if not text:
            return None

        from_user    = payload.get("from", {})
        conversation = payload.get("conversation", {})

        # Prefer the stable AAD object ID; fall back to the Teams channel user ID
        user_id = from_user.get("aadObjectId") or from_user.get("id", "")

        # Timestamp is provided by Teams in ISO-8601; fall back to now if absent
        timestamp = payload.get("timestamp") or datetime.now(tz=timezone.utc).isoformat()

        return NormalisedMessage(
            channel="teams",
            user_id=user_id,
            user_name=from_user.get("name"),
            text=text,
            timestamp=timestamp,
            reply_to=payload.get("id", ""),           # incoming activity ID
            service_url=payload.get("serviceUrl", ""),
            conversation_id=conversation.get("id", ""),
        )
    except (KeyError, TypeError):
        return None
