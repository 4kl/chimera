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
python -m uiautomator2 init   # installs the uiautomator2 ATX agent on the device
```

If you have multiple devices connected, pass the serial later with
`--serial <serial>`.

### 3. Set up Ollama

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

### 4. Smoke-test the wiring

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
├── core/         models (UINode, SelectorBundle, Session), driver wrapper, errors
├── profiler/     resolves (package, versionName) via uiautomator2 + dumpsys
├── perception/   capture (XML + screenshot) → UINode tree → screen fingerprint
├── selector/     multi-strategy candidate generation + ranking + validation
├── reasoning/    Ollama client, intent + element-pick prompts
├── learning/     per-step mode decision (REUSE/REVALIDATE/LEARN/HEAL) + session
├── execution/    try cached → fallbacks → trigger discovery/heal
├── healing/      LLM re-pick on live tree (+ opt. visual match)
├── memory/       SQLite: selectors / app_profiles / version_migrations / events
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

Schema migrations are automatic (`chimera/memory/migrations.py`): a v1 DB is
upgraded in place, `app_version` backfills to `""`.

Inspect it directly:

```bash
sqlite3 chimera.db '.schema selectors'
sqlite3 chimera.db "SELECT role, primary_strategy, failures, provenance FROM selectors WHERE app_package='com.whatsapp';"
sqlite3 chimera.db "SELECT ts, outcome, role FROM events ORDER BY ts DESC LIMIT 20;"
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uiautomator2 not installed` | `pip install -e .` inside the venv you're using |
| `adb devices` shows `unauthorized` | unlock device → accept the host RSA prompt |
| `python -m uiautomator2 init` hangs | usually a flaky USB cable; try another port/cable |
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
