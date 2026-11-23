"""Markaziy AI javob blokirovkasi: foydalanuvchi boshqa funksiyada bo‘lsa AI jim turadi."""
from telegram.ext import ConversationHandler


def user_has_active_conversation(application, user_id: int) -> bool:
    """PTB ConversationHandler ichida shu foydalanuvchiga tegishli faol holat bormi?"""
    if not application or user_id is None:
        return False
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            if not isinstance(handler, ConversationHandler):
                continue
            conversations = getattr(handler, "_conversations", {}) or {}
            for key, state in conversations.items():
                if state is not None and isinstance(key, tuple) and int(user_id) in key:
                    return True
    return False


def user_has_pending_feature(application, user_id: int) -> bool:
    """Oddiy handlerlarda saqlanadigan kutish holatlarini ham hisobga oladi."""
    data = getattr(application, "user_data", {}).get(user_id, {}) if application else {}
    if not isinstance(data, dict):
        return False
    # Funksiya wizardlari va maxsus sozlash jarayonlari.
    keys = (
        "al_waiting", "business_auto_reply_waiting_text", "_student_feature_running",
        "cw_pages", "cw_waiting", "essay_state", "pptx_state", "quiz_state",
    )
    return any(data.get(key) for key in keys)


def is_user_busy(application, user_id: int) -> bool:
    if user_id is None:
        return False
    if user_has_active_conversation(application, user_id):
        return True
    if user_has_pending_feature(application, user_id):
        return True
    # Shaxsiy chatda uzoq davom etayotgan /command yoki AI generatsiyasi.
    try:
        import cancel_registry
        if cancel_registry.has_active_user_job(user_id, exclude_current=True):
            return True
    except Exception:
        pass
    return False
