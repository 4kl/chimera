# Chimera

Adaptive, selector-free Android UI automation. Natural-language commands
("Send John hey on WhatsApp") are decomposed by a local LLM into abstract
steps; every selector is discovered at runtime, ranked, cached, validated
before reuse, and self-healed when the UI changes. Selectors are keyed by
`(app, version, screen, role)` so the system gets faster every run and
automatically relearns after app updates.

---

## Requirements

| Thing | Version | Why |
|---|---|---|
| Python | ≥ 3.10 | Type hints, `dataclass` kw-only, `lxml` 5 |
| Android SDK platform-tools | any recent | `adb` for device connectivity |
| An Android device / emulator | API 21+ | target for automation |
| Ollama | ≥ 0.1.30 | local LLM reasoning |
| Disk | a few MB | `chimera.db` grows with learned selectors |

---

## Install

### 1. Clone and create a venv

```bash
cd /path/to/Android_automation
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -e .
# Optional: visual-healing path (OpenCV)
pip install -e '.[visual]'
# Optional: dev tooling
pip install -e '.[dev]'
```

### 2. Set up the device

Enable **Developer options** → **USB debugging** on your phone, plug it in,
and accept the host key on the device.

```bash
adb devices          # should list your device as "device" (not "unauthorized")
```

If you have multiple devices connected, pass the serial later with
`--serial <serial>` (or set `ANDROID_SERIAL`).

### 3. Set up Appium

Chimera drives the device through Appium (not the `uiautomator2` Python
client). You need Node.js, the Appium CLI, and the UiAutomator2 driver.

```bash
# Node.js 18+ from https://nodejs.org or `brew install node`
npm install -g appium
appium driver install uiautomator2

# Start the server (leave running in a separate terminal)
appium --allow-insecure=adb_shell
# or for more permissive dev mode:
# appium --relaxed-security
```

The `--allow-insecure=adb_shell` flag lets `mobile: shell` work for commands
like `dumpsys` and `pm list packages`. Chimera also uses host-side `adb`
(if on PATH) as the preferred shell path, so this flag is only a fallback.

Endpoint defaults to `http://127.0.0.1:4723`; override via `APPIUM_URL` or
`--appium-url`.

### 4. Set up Ollama

Install Ollama from <https://ollama.com/download>, then start the server and
pull a model:

```bash
ollama serve &                        # runs on http://localhost:11434
ollama pull qwen2.5:7b-instruct       # default model
# If your machine is small, use a lighter one:
# ollama pull qwen2.5:3b-instruct
```

Any JSON-mode-capable instruction model works. Override the default via
`OLLAMA_MODEL` (see Config below).

### 5. Smoke-test the wiring

```bash
# Confirms device + perception pipeline (no LLM, no actions taken)
python scripts/probe_device.py
```

You should see the current package, activity, screen fingerprint, and a few
clickable nodes from whatever is on-screen.

```bash
# Confirms the offline pieces (parser, selector engine, memory, learning)
pip install pytest
pytest tests/       # 23 tests pass
```

---

## Usage

### CLI

```bash
chimera "Open WhatsApp"
chimera "Send John hey on WhatsApp"
chimera "Search for pizza on Google Maps" -v
```

Flags:

```
chimera COMMAND [COMMAND ...]
    --db PATH              selector cache (default: chimera.db)
    --serial SERIAL        ADB device serial
    --ollama-url URL       default: $OLLAMA_URL or http://localhost:11434
    --ollama-model NAME    default: $OLLAMA_MODEL or qwen2.5:7b-instruct
    -v / -vv               verbose / very verbose (show per-step decisions)
```

### Python

```python
from chimera.orchestrator import Chimera

ch = Chimera(db_path="chimera.db")
try:
    summary = ch.run("Send John hey on WhatsApp")
    print(summary)
    # {'duration_s': 3.8, 'reused': 4, 'learned': 1, 'healed': 0,
    #  'migrated': 0, 'failures': 0, 'detail': {...}}
finally:
    ch.close()
```

### What happens the first time vs. later

**First run on a new `(app, version)`** — every step enters `LEARN` mode:
the live UI tree is sent to Ollama, the target element is picked by role,
selectors are generated + ranked + stored. Slower, exploratory.

```
$ chimera "Open WhatsApp" -v
plan for 'Open WhatsApp': 2 steps (app_hint=whatsapp)
target: whatsapp (com.whatsapp) v=2.24.10
step 1/2 role=app action=launch
step 2/2 role=app_ready action=wait
role=app_ready mode=learn conf=0.00 reason=no cached bundle ...
session: {'duration_s': 4.1, 'reused': 0, 'learned': 1, ...}
```

**Second run, same version** — every step enters `REUSE` mode, no LLM call:

```
$ chimera "Open WhatsApp" -v
role=app_ready mode=reuse conf=0.95 reason=fresh cache hit (conf=0.95)
session: {'duration_s': 0.7, 'reused': 1, 'learned': 0, ...}
```

**After an app update** — the new `versionName` is detected; if screen
fingerprints overlap with a previous version (Jaccard ≥ 0.5), Chimera
seeds the cache forward (`migrated`, scaled confidence). Each migrated
selector re-validates on first use: if it still resolves the same element,
it gets promoted to `learned`; if not, the step enters `HEAL` mode and
re-discovers only the broken role.

---

## Configuration

Environment variables (all optional):

```bash
export OLLAMA_URL=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:7b-instruct
```

Chimera constructor knobs:

```python
Chimera(
    db_path="chimera.db",         # SQLite cache
    serial=None,                  # ADB serial (multi-device)
    ollama_url=None,              # overrides OLLAMA_URL
    ollama_model=None,            # overrides OLLAMA_MODEL
    max_heal_retries=1,           # per-step heal retries before giving up
)
```

---

## Architecture

```
chimera/
├── core/         models (UINode, SelectorBundle, ActionStep, Session), driver wrapper, errors
├── profiler/     (package, versionName) + runtime pm-list-packages resolver
├── perception/   capture (XML + screenshot) → UINode tree → screen fingerprint
├── selector/     multi-strategy candidate generation + ranking + validation
├── reasoning/    Ollama client, intent / element-pick / state-classify prompts
├── learning/     per-step mode decision (REUSE/REVALIDATE/LEARN/HEAL) + session
├── state/        UIState + StateTransition, detector, graph, Dijkstra planner, navigator
├── execution/    try cached → fallbacks → trigger discovery/heal; state-aware pre-check
├── healing/      LLM re-pick on live tree (+ opt. visual match)
├── memory/       SQLite: selectors / app_profiles / version_migrations / events / states / state_transitions
└── orchestrator.py
```

| Layer | One-line role |
|---|---|
| Perception | a11y dump → normalized `UINode` tree + screen fingerprint |
| Selector | generate `resource-id` / `content-desc` / text / rel-XPath / class-index / abs-XPath candidates, rank by stability |
| Reasoning | Ollama decomposes intent into roles, later picks the element realizing each role on the current screen |
| Profiler | `(package, versionName, versionCode)` from `uiautomator2.app_info()` with a `dumpsys` fallback |
| Memory | SQLite, version-aware. 4-tier lookup: exact → screen-wildcard → cross-version seed → cross-version seed (any screen). Confidence decays with age + failure count. |
| Learning Engine | Per step: `REUSE` if cache fresh, `REVALIDATE` if stale/migrated, `LEARN` if unknown, `HEAL` on failure |
| Execution | REUSE validates live element fingerprint before acting; fallbacks tried in ranked order; failures trigger heal |
| Healing | LLM re-pick + optional visual match; emits new bundle with bumped revision |

## Declarative AppGraph (PUMA-style, XPath-first)

For apps where you want **zero LLM calls on the happy path**, use the
declarative `chimera.app_graph` layer. You write states and transitions as
class-level declarations with raw XPaths; the `@action(state)` decorator
auto-navigates; and when an XPath stops resolving after an app update, the
hybrid driver transparently falls back to Chimera's LLM-driven discovery,
caches the repaired selector under the new app version, and the next run is
XPath-fast again.

```python
from chimera.app_graph import (
    AppGraph, DeclaredState, action, compose_clicks, supported_version,
)
from chimera.orchestrator import Chimera

CHAT_INPUT = "//*[@resource-id='com.whatsapp:id/entry']"
CHAT_SEND  = "//*[@resource-id='com.whatsapp:id/send']"
CONTACT_ROW = ("//*[@resource-id='com.whatsapp:id/conversations_row_contact_name'"
               " and @text='{conversation}']")

def go_to_chat(driver, conversation: str):
    driver.click(CONTACT_ROW.format(conversation=conversation))

@supported_version("2.24.0")
class WhatsApp(AppGraph, package="com.whatsapp"):
    conversations_state = DeclaredState(
        name="conversations",
        xpaths=["//*[@resource-id='com.whatsapp:id/home_root_layout']"],
        initial=True,
    )
    chat_state = DeclaredState(
        name="chat",
        xpaths=["//*[@resource-id='com.whatsapp:id/conversation_contact_name']",
                CHAT_INPUT],
        parent=conversations_state,
    )
    conversations_state.to(chat_state, via=go_to_chat)

    @action(chat_state)
    def send_message(self, message_text: str, conversation: str = None):
        self.driver.click(CHAT_INPUT, role="message_input")
        self.driver.send_keys(CHAT_INPUT, message_text, role="message_input")
        self.driver.click(CHAT_SEND, role="send_button")


app = WhatsApp(Chimera())
app.send_message("hey", conversation="John")
# → @action detects current state
# → if not in chat with John, navigates via conversations_state.to(chat_state)
# → clicks CHAT_INPUT (direct XPath — no LLM)
# → if the XPath doesn't resolve (app updated), XPathDriver asks Chimera's
#   executor to find the element by role, caches the new selector, replays.
```

A full example is at `examples/whatsapp.py` + `examples/whatsapp_xpaths.py`.

Key pieces:

| Piece | Purpose |
|---|---|
| `DeclaredState(name, xpaths, parent, initial, validator)` | XPath-signature-based state; XPaths auto-translate to state features so the detector recognizes them without runtime learning |
| `state.to(target, via=fn)` | Declare a transition; `fn(driver, **ctx_kwargs)` performs it |
| `compose_clicks([xp1, xp2])` | Transition helper — tap each XPath in order |
| `@action(state, end_state=None)` | Decorator: auto-navigate to `state` before the call; if `validator` set and context kwargs given, also verify context |
| `@supported_version("…")` | Records the versionName the XPaths were written for; version drift triggers the heal path |
| `XPathDriver.click/send_keys/is_present` | XPath-first; on failure → LLM discovery via Chimera's executor, caches repaired selector |
| `PopupHandler` | Register expected popups; AppGraph dismisses them before each action |

The AppGraph layer reuses the same `StateStore` + Navigator as the
NL-command orchestrator — the graph you hand-wire here is the same graph
runtime navigation walks, and every success/failure updates the same
confidence counters. You can mix-and-match: declare what you know, let
Chimera learn the rest.

## State-based navigation

Every screen in an app is represented as a **state** (`main_page`,
`chat_screen`, `search_screen`, ...). Each element-based step carries an
optional `target_state`; before the step executes, the executor detects the
current state and, if it doesn't match, plans a path through the
**state graph** and navigates.

- **Detection** (`chimera/state/detector.py`) is tiered: exact
  fingerprint hit → structural feature-score match → LLM classification.
  Unknown screens are labelled lazily by the model and stored with 2–5
  key identifying features (resource-ids, text-contains, class counts).
- **Graph** (`chimera/state/graph.py`) is built from
  `state_transitions` rows. An edge `(from, role, action) → to` gains
  confidence each time the action in that state actually lands in the
  expected next state.
- **Planner** (`chimera/state/planner.py`) is Dijkstra over edge cost
  `1 + (1 − confidence) * 3`, with a small penalty for `back` actions.
- **Navigator** (`chimera/state/navigator.py`) walks the planned path,
  validates the state after each hop, and recovers (press back + re-plan)
  if the actual state doesn't match the expected one.

Cross-version priors: if the new app version has no states yet, the detector
borrows states from a prior version so the first run on an update isn't
ice-cold.

Inspect the state system:

```bash
python scripts/inspect_db.py --states                    # all learned states
python scripts/inspect_db.py --states com.whatsapp       # filter by app
python scripts/inspect_db.py --graph com.whatsapp        # all transitions
python scripts/inspect_db.py --graph com.whatsapp 2.24   # filter by version
```

## Session modes

Every step independently resolves into one of four modes, logged in the
`events` table:

| Mode | When | LLM called? |
|---|---|---|
| `REUSE` | Cached bundle, fresh, effective confidence ≥ 0.55 | No |
| `REVALIDATE` | Cached but stale/decayed, or migrated from another version | Only if primary + fallbacks fail |
| `LEARN` | No cache for `(app, version, screen, role)` and no migratable seed | Yes — first run / new screen |
| `HEAL` | Cached selector resolved nothing / resolved the wrong element | Yes — re-pick on live tree |

## Database layout (SQLite, `chimera.db`)

- `selectors(app_package, app_version, screen_fp, role)` — primary + ranked fallbacks, provenance (`learned` / `healed` / `migrated`), failure count.
- `app_profiles(app_package, app_version)` — known screen fingerprints per version, used for similarity.
- `version_migrations` — ledger of `{from_version → to_version, jaccard, roles_copied}`.
- `events` — append-only log (`learned` / `ok` / `fail_primary` / `fail_all` / `healed` / `migrated`).
- `states(app_package, app_version, name)` — semantic state catalogue (features, fingerprints, allowed roles, confidence).
- `state_transitions(app_package, app_version, from_state, role, action, to_state)` — directed edges + success/failure counters.

Schema migrations are automatic (`chimera/memory/migrations.py`): a v1 DB is
upgraded in place, `app_version` backfills to `""`.

Inspect it directly:

```bash
# Summary: apps + versions + role counts + last-ok age
python scripts/inspect_db.py

# All roles stored for one app (across versions)
python scripts/inspect_db.py com.whatsapp

# Just one version
python scripts/inspect_db.py com.whatsapp 2.24

# Full bundle dump (primary + all fallbacks + description)
python scripts/inspect_db.py com.whatsapp 2.24 --full

# Recent event log (learned / ok / fail_primary / healed / migrated)
python scripts/inspect_db.py --events
```

Or with raw SQL:

```bash
sqlite3 chimera.db '.schema selectors'
sqlite3 chimera.db "SELECT role, primary_strategy, failures, provenance FROM selectors WHERE app_package='com.whatsapp';"
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Appium-Python-Client not installed` | `pip install -e .` inside the venv you're using |
| `failed to connect to Appium at …` | `appium` server isn't running; start with `appium --allow-insecure=adb_shell` |
| `Could not find a driver for automationName 'UiAutomator2'` | `appium driver install uiautomator2` |
| `adb devices` shows `unauthorized` | unlock device → accept the host RSA prompt |
| `mobile:shell` returns empty / "Not allowed" | server started without `--allow-insecure=adb_shell`; host-side `adb` is used as a fallback, so just install platform-tools on PATH |
| `Ollama request failed: ... 404` | `ollama serve` isn't running, or model isn't pulled |
| `Ollama returned non-JSON content` | model isn't instruction-tuned; use `qwen2.5:*-instruct` or similar |
| `LLM could not locate role=...` | target element isn't on-screen yet — either the plan is wrong or a `wait` step is missing; rerun with `-v` to see the pick's `reason` |
| Selectors keep getting healed every run | the element truly is unstable; check `events` table — the `fail_primary` strategy can inform which prior choice was bad |
| Multiple devices attached | pass `--serial <serial>` or set `ANDROID_SERIAL` |

Reset learning for a specific app (keep other apps' knowledge):

```bash
sqlite3 chimera.db "DELETE FROM selectors WHERE app_package='com.whatsapp'; DELETE FROM app_profiles WHERE app_package='com.whatsapp';"
```

Wipe everything:

```bash
rm chimera.db
```

---

## Status

Scaffold, end-to-end wired. **23 offline tests pass** (parser, fingerprint,
selector ranking, memory roundtrip, version-aware lookup, similarity-based
migration, confidence decay, learning-mode decisions, app profiler, name
normalization).

Visual healing is a seam that only activates if `opencv-python` is
installed — see `chimera/healing/healer.py::_try_visual`.

Quick-start summary for you:

  python3 -m venv .venv && source .venv/bin/activate
  pip install -e .
  python -m uiautomator2 init
  ollama serve & ; ollama pull qwen2.5:7b-instruct
  python scripts/probe_device.py          # sanity check
  chimera "Open WhatsApp" -v               # first real run