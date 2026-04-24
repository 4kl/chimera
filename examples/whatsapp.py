"""PUMA-style WhatsApp AppGraph example on top of Chimera.

Run:
    python -c "
    from chimera.orchestrator import Chimera
    from examples.whatsapp import WhatsApp
    app = WhatsApp(Chimera())
    app.send_message('hey', conversation='John')
    "

XPaths are the happy path. When the app updates and a declared XPath stops
resolving, the XPathDriver transparently falls back to Chimera's LLM-driven
discovery, learns the repaired selector for the new app_version, and the
next run is XPath-fast again."""
from __future__ import annotations

from chimera.app_graph import (
    AppGraph, DeclaredState, action, compose_clicks,
    simple_popup_handler, supported_version,
)

from examples.whatsapp_xpaths import (
    CHAT_CONTACT_HEADER, CHAT_INPUT, CHAT_ROOT_LAYOUT, CHAT_SEND_BUTTON,
    CONVERSATIONS_CHATS_TAB, CONVERSATIONS_CONTACT_ROW,
    CONVERSATIONS_HOME_ROOT, CONVERSATIONS_WHATSAPP_LOGO,
    SEARCH_INPUT, SEARCH_OPEN_ICON, SEARCH_RESULT_ROW,
)


# ----- transition helpers (receive the XPathDriver + kwargs) -------------
def go_to_chat(driver, conversation: str):
    driver.click(CONVERSATIONS_CONTACT_ROW.format(conversation=conversation))


def search_and_open(driver, conversation: str):
    driver.click(SEARCH_OPEN_ICON)
    driver.send_keys(SEARCH_INPUT, conversation)
    driver.click(SEARCH_RESULT_ROW.format(conversation=conversation))


# ----- the AppGraph ------------------------------------------------------
@supported_version("2.24.0")
class WhatsApp(AppGraph, package="com.whatsapp"):
    # States
    conversations_state = DeclaredState(
        name="conversations",
        xpaths=[CONVERSATIONS_WHATSAPP_LOGO,
                CONVERSATIONS_HOME_ROOT,
                CONVERSATIONS_CHATS_TAB],
        initial=True,
    )
    search_state = DeclaredState(
        name="search",
        xpaths=[SEARCH_INPUT],
        parent=conversations_state,
    )
    chat_state = DeclaredState(
        name="chat",
        xpaths=[CHAT_CONTACT_HEADER, CHAT_ROOT_LAYOUT],
        parent=conversations_state,
        # Context validation: when the caller passes `conversation="John"`,
        # verify the chat header actually says "John". If not, the AppGraph
        # will step back and re-navigate with the right context.
        validator=lambda driver, conversation=None: (
            conversation is None
            or driver.is_present(
                f"{CHAT_CONTACT_HEADER}[@text='{conversation}']", timeout=1.0)
        ),
    )

    # Transitions
    conversations_state.to(chat_state, via=go_to_chat)
    conversations_state.to(search_state,
                           via=compose_clicks([SEARCH_OPEN_ICON],
                                              name="open_search"))
    search_state.to(chat_state, via=search_and_open)

    def __init__(self, chimera):
        super().__init__(chimera)
        # Example popup handlers (add whatever your install actually shows).
        self.add_popup_handler(simple_popup_handler(
            "//*[@text='Not now']"))
        self.add_popup_handler(simple_popup_handler(
            "//*[@resource-id='android:id/button2']"))

    # -------- actions ---------------------------------------------------
    @action(chat_state)
    def send_message(self, message_text: str, conversation: str = None):
        """Type and send a message. If `conversation` is given and we're
        in a different chat (or not in a chat at all), the AppGraph
        auto-navigates there via the declared transitions."""
        self.driver.click(CHAT_INPUT, role="message_input")
        self.driver.send_keys(CHAT_INPUT, message_text, role="message_input")
        self.driver.click(CHAT_SEND_BUTTON, role="send_button")

    @action(conversations_state)
    def archive_chat(self, conversation: str):
        # Intentionally left as a stub — implement with your real XPaths.
        raise NotImplementedError
