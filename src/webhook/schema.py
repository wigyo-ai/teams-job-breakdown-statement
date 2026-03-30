from pydantic import BaseModel
from typing import Optional


class NormalisedMessage(BaseModel):
    channel:         str            # "teams"
    user_id:         str            # stable user identifier (AAD object ID)
    user_name:       Optional[str] = None
    text:            str
    timestamp:       str            # ISO-8601
    reply_to:        str            # Teams: incoming activity ID (used in reply URL)
    service_url:     Optional[str] = None   # Teams: Bot Framework service URL
    conversation_id: Optional[str] = None   # Teams: conversation ID
