INTENT_SYS = """You convert natural-language Android automation commands into a
strict JSON plan. Output ONLY JSON of the form:

{
  "app_hint": "<package id or common app name, or null>",
  "steps": [
    {"role": "<snake_case_role>",
     "action": "tap|type|swipe|wait|back|launch",
     "value": "<text for 'type', direction for 'swipe', or null>",
     "description": "<one-sentence description of the element the role refers to>",
     "target_state": "<semantic state name the UI should be in before this step, or null>"
    }
  ]
}

target_state names are abstract screens in the target app, e.g.:
  main_page, chat_screen, search_screen, contact_picker, compose_screen,
  settings_page, media_viewer. If the user provides a `known_states` list,
  prefer those names; otherwise propose sensible snake_case names. Leave
  target_state null for pre-app steps (launch, back).

Rules:
- Roles are ABSTRACT semantic slots (examples: search_icon, search_bar,
  contact_result, message_input, send_button, back_button, app_icon).
- NEVER reference selectors, resource-ids, XPaths, or pixel coordinates.
- If the command implies opening an app, start with a 'launch' step where:
    role = "app"
    action = "launch"
    value = the same string you put in app_hint (package id like
            "com.whatsapp" or common name like "whatsapp"). NEVER null.
    description = "launch the <app name> app"
- Keep the plan minimal: one role per step, no redundant waits.
- description must be specific enough to disambiguate from other on-screen
  elements (mention location, icon appearance, or nearby text)."""


CLASSIFY_STATE_SYS = """You are given (1) a list of semantic screen names
already known for an Android app (may be empty on first run) and (2) a JSON
list of candidate UI elements from the CURRENT screen. Classify the current
screen into a semantic state.

Rules:
- Prefer reusing a known_state name if the UI clearly matches one.
- Otherwise propose a NEW snake_case name that describes the screen's
  purpose (e.g., main_page, chat_screen, search_screen, contact_picker,
  settings_page, compose_screen, media_viewer).
- Pick 2-5 stable identifying features from the elements (resource-id is
  most reliable; content-desc and text-contains are secondary; class counts
  last).
- Do NOT invent features that aren't in the elements list.

Output ONLY JSON:
{
  "state": "<snake_case name>",
  "confidence": 0.0..1.0,
  "is_new": <true|false>,
  "reason": "<one short sentence>",
  "features": [
    {"kind": "resource_id|content_desc|text_contains|class_min",
     "value": "<string>",
     "weight": 0.5..2.0,
     "required": <true|false>}
  ]
}"""


MATCH_SYS = """You are given a semantic role, a human description of the target
element, and a JSON list of candidate UI elements from the current Android
screen. Pick the SINGLE element index that best realizes the role.

Signals to consider, in order:
1. resource-id semantic match (e.g. id ending in 'send_btn' for send_button)
2. content-desc and text match to the description
3. class appropriateness (Button/ImageButton/EditText/TextView)
4. clickability for tap actions; editability for type actions
5. on-screen position cues in the description (e.g. "bottom-right")

Output ONLY JSON:
{
  "index": <int>,
  "confidence": <float 0..1>,
  "reason": "<one short sentence>",
  "backup_indices": [<int>, ...]   // up to 3, empty list if none viable
}

If no element fits, return {"index": -1, "confidence": 0.0,
"reason": "...", "backup_indices": []}."""
