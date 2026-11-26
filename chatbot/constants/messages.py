"""
Messages for the chatbot app: text sent to users or returned in API responses.

New code defines such text here and imports it from this module. Other constant
values (keys, event names, codes) live in chatbot/constants/constants.py; log
messages stay inline where they are logged.

Like constants.py, this module imports nothing from chatbot or Django.
"""

# ── chat session ownership ───────────────────────────────────────────────────
# WebSocket system message (event WS_EVENT_SESSION_FORBIDDEN) when a session ID belongs to another user's chat.
WS_MSG_SESSION_FORBIDDEN = 'This chat is not available. Please start a new chat.'
# Sent instead of a bot reply if a chat request reaches the orchestrator for a chat the profile doesn't own.
CHAT_MSG_SESSION_FORBIDDEN = 'This chat is not available.'
