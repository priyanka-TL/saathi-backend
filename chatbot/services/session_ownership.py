"""
Who may use a chat (ChatSession).

A session ID comes from the client, so it is never proof of access on its own. Every
place that loads a chat by session ID for a signed-in profile checks it here first:
the WebSocket authenticate (consumers/async_consumer.py), the chat orchestrator
(services/core/base_service.py) and MIP creation (services/mip/creation.py).

Only server-side identity is trusted: the chat's profile and the profile's Elevate
userid (Profile.userid, written from Elevate's verified response; unique per profile).
The chat's own user_id can be set by REST clients, so it counts only for legacy chats
that have no profile.
"""


def is_session_owner(chat_session, profile):
    """
    True when `profile` may use `chat_session`:
    - the chat belongs to this profile; or
    - it is a legacy chat with no profile whose user_id is this profile's (non-empty) Elevate userid.
    """
    if profile is None or chat_session is None:
        return False
    if chat_session.profile_id is not None:
        return chat_session.profile_id == profile.pk
    session_user = str(chat_session.user_id or '').strip()
    return bool(session_user) and session_user == str(profile.userid or '').strip()
