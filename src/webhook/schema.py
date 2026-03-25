from pydantic import BaseModel
from typing import Optional


class NormalisedMessage(BaseModel):
    channel:   str            # "whatsapp" | "telegram"
    user_id:   str            # stable user identifier
    user_name: Optional[str] = None
    text:      str
    timestamp: str            # ISO-8601
    reply_to:  str            # phone number (WA) or chat_id (TG)
