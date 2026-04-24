"""XPath constants for the WhatsApp AppGraph example.

Mirrors the PUMA style where a single module holds the raw XPath strings so
the state/action code stays readable. Values are illustrative — adjust to
your WhatsApp build. The framework will self-heal most of them if the live
app differs."""

WHATSAPP_PACKAGE = "com.whatsapp"

# Conversations (main) screen
CONVERSATIONS_WHATSAPP_LOGO = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/toolbar']"
    f"//*[@text='WhatsApp']"
)
CONVERSATIONS_HOME_ROOT = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/home_root_layout']"
)
CONVERSATIONS_NEW_CHAT_FAB = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/fab']"
)
CONVERSATIONS_CHATS_TAB = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/pager_tab_chats']"
)
CONVERSATIONS_CONTACT_ROW = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/conversations_row_contact_name'"
    f" and @text='{{conversation}}']"
)

# Chat screen
CHAT_CONTACT_HEADER = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/conversation_contact_name']"
)
CHAT_ROOT_LAYOUT = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/main_layout']"
)
CHAT_INPUT = f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/entry']"
CHAT_SEND_BUTTON = f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/send']"

# Search screen
SEARCH_OPEN_ICON = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/menuitem_search']"
)
SEARCH_INPUT = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/search_src_text']"
)
SEARCH_RESULT_ROW = (
    f"//*[@resource-id='{WHATSAPP_PACKAGE}:id/contact_row_container']"
    f"[.//*[@text='{{conversation}}']]"
)
