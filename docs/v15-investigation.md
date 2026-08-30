# v1.5 feasibility investigation — images, GBrain, audio

Investigation only, 2026-08-17. Nothing on the box was changed. All findings are read from
source or from live read-only inspection; no LLM calls were made. File:line references are to
`/usr/local/lib/hermes-agent/` unless stated otherwise.

**Headline:** images already work end to end and need no gateway change. GBrain does not exist
on this droplet at all — the skill points at a Mac. Audio is the thinnest of the three: the
config block is real but there is no engine, no endpoint, and no credential behind it.

---

## 1. Multimodal / image input — WORKS TODAY, no gateway change needed

`POST /v1/chat/completions` accepts image parts and they reach Anthropic as **native image
blocks**. I traced the whole path.

### What the endpoint accepts

`_normalize_multimodal_content` — `gateway/platforms/api_server.py:550`, called from the chat
handler at `:3925`.

| Input | Behaviour |
|---|---|
| `content` as a plain string | passed through, truncated at 64 KB |
| text parts `{"type": "text"\|"input_text"\|"output_text"}` | accepted (`_TEXT_PART_TYPES`, `:545`) |
| image parts `{"type": "image_url"\|"input_image"}` | accepted (`_IMAGE_PART_TYPES`, `:546`) |
| `image_url` as `{"url": ..., "detail": ...}` | accepted (Chat Completions shape) |
| `image_url` as a bare string | accepted (Responses shape) |
| `data:image/...;base64,...` | accepted |
| `http(s)://...` | accepted |
| any other `data:` payload | **400** `unsupported_content_type` |
| `file` / `input_file` / `file_id` parts | **400** `unsupported_content_type` — "uploaded files and document inputs are not supported on this endpoint" |
| unknown part type | **400** `unsupported_content_type` — rejected explicitly, *not* silently dropped |

Two details worth keeping:

- **Text-only content collapses back to a plain string** (`:661`) specifically so prompt caching
  on text-only turns is unaffected. Adding image support did not regress the text path.
- Errors are translated to OpenAI-style 400s by `_multimodal_validation_error` (`:684`), so a
  malformed attachment produces a readable client error rather than a dropped turn.

### Limits

| Limit | Value | Source |
|---|---|---|
| Text per part | 65,536 bytes | `MAX_NORMALIZED_TEXT_LENGTH`, `api_server.py:155` |
| Parts per message | 1,000 | `MAX_CONTENT_LIST_SIZE`, `:156` |
| Whole request body | 10,000,000 bytes | `MAX_REQUEST_BYTES`, `:153` → **413** |

No media-type allowlist beyond "must be `image/*`", and no per-image count cap. The binding
constraint for the app is the 10 MB body: base64 inflates by ~4/3, so a data-URL image is
limited to roughly **7.5 MB of original bytes**, less whatever the conversation history costs.

### The path to Anthropic (this is the part that matters)

1. Normalised content stays in **native OpenAI vision format** — the agent pipeline takes it
   verbatim.
2. `_prepare_anthropic_messages_for_api` — `run_agent.py:6661`. If `_model_supports_vision()`
   is true it **returns `api_messages` unchanged** and lets the adapter do the work. The comment
   there is explicit that the legacy text-fallback is skipped for vision models.
3. `agent/anthropic_adapter.py:_convert_content_part_to_anthropic` (`:1815`) turns each
   `image_url`/`input_image` part into `{"type": "image", "source": ...}`.
4. `_image_source_from_openai_url` (`:1793`) builds the source:
   - `data:` URL → `{"type": "base64", "media_type": ..., "data": ...}`
   - `http(s)` URL → `{"type": "url", "url": ...}` — **Anthropic fetches it**, the gateway does
     not download it server-side.

**Verified on this box**, not assumed:

```
_lookup_supports_vision('anthropic', 'claude-sonnet-5', cfg) -> True
```

So the active model takes the native-image path. The text-fallback
(`_preprocess_anthropic_content`, `run_agent.py:6600`, which replaces each image with a
`[description]` note) only fires for non-vision models and is not in play here.

### Caveats

- **System messages are flattened to text.** Only user/assistant messages carry images.
- **There is no upload endpoint anywhere on the gateway.** The full route table
  (`_http_route_table`, `:2006`) has no `/v1/files`, no multipart, nothing upload-shaped. The
  app must read the file in the browser and inline it as a base64 data URL.
- `/v1/responses` and `/v1/runs` call the same normaliser (`:5098`, `:5124`), so they are no
  more capable for images — there is nothing to "upgrade to". `/v1/chat/completions` is already
  the right route.

### What this means for the app

`app/chat.py` currently sends `{"role": "user", "content": "<string>"}`. Supporting images is a
**shape change in one place** — content becomes an array when an attachment is present — plus a
browser-side file→data-URL step and a size guard well under 10 MB. No gateway work, no new
route, no new dependency.

---

## 2. GBrain — NOT on this droplet; the skill points at a Mac

**There is no GBrain here, and the skill cannot run.**

| Component | Status |
|---|---|
| `gbrain-memory` skill (SKILL.md + 3 scripts) | present |
| `/root/.gbrain/` and `env.sh` | **absent** |
| `gbrain` binary (PATH, `/usr/local/bin`, `~/.local/bin`) | **absent** |
| systemd unit matching gbrain | **none** |
| Docker | **not installed** |
| Unexplained listeners | **none** — only 18790 (Gmail webhook), 8642 (gateway), 8700 (app), systemd-resolved, sshd, and three cloudflared metrics ports |

The give-away is in SKILL.md itself, which instructs the agent to run:

```
bash /Users/nemoclaw/.openclaw/workspace/skills/gbrain-memory/scripts/brain_search.sh "TERMS"
```

`/Users/nemoclaw/...` is a **macOS path** — this skill was written for the Mini and imported
verbatim. Even the local copies of the scripts cannot work: each one does

```bash
if [ -f "$HOME/.gbrain/env.sh" ]; then source "$HOME/.gbrain/env.sh"; fi
gbrain search "$QUERY" --limit "$LIMIT" 2>&1
```

The `env.sh` guard is a silent no-op here, then `gbrain` is not found. Note the failure mode:
`2>&1` means the agent gets "command not found" as *content* rather than an error, so a question
routed to this skill returns shell noise that looks like a search miss.

### What GBrain is, from what this box reveals

A searchable archive built from Spencer's real email history. Command surface, all **read-only**:

| Command | Purpose |
|---|---|
| `gbrain search "<terms>" --limit N` | keyword search |
| `gbrain query "<question>"` | hybrid/fuzzy search, better recall |
| `gbrain get "<slug>"` | fetch a full page, slug shaped like `emails/some-slug-here` |

Results carry relevance score, subject, sender, date. SKILL.md says it holds "email history only
(no contact graph yet)". It is **not** wired into Hermes as a tool or MCP server — the only route
in is these shell scripts.

### For "chat + uploads ingest into GBrain, gateway pulls from it"

**Present on this droplet:** the skill contract and the three read verbs. That is all.

**Missing:** essentially the entire system.

- No `gbrain` client binary, and no `env.sh` to say what host/port/API it would even talk to —
  so the droplet has no configured endpoint for a remote GBrain either.
- **No ingest path of any kind.** All three verbs are reads. Nothing in the skill or on the box
  writes to GBrain, so "chat + uploads ingest into GBrain" has no existing surface to build on.
- No embedding store, no vector DB, no index, no service, nothing listening.
- No documentation of GBrain's server side anywhere on this box — no README, no package
  metadata, no API spec. I cannot tell from here whether GBrain is a local binary with an
  on-disk index or a client for a hosted service.

Practically, this is a **decision, not a wiring job**: either (a) GBrain runs on the Mini and
this droplet needs a network route plus credentials to reach it — which needs the Mini's
`env.sh` to know what it points at — or (b) GBrain gets stood up here, which needs the software
itself, an ingest API that does not currently exist in the command surface, and a store. Before
either, someone needs to answer what GBrain actually is on the Mini; the droplet cannot tell us.

---

## 3. Audio — config block is real, everything behind it is missing

### What exists

`~/.hermes/config.yaml:115`:

```yaml
stt:
  enabled: true
  language: en
  local:
    model: base
  openai:
    model: whisper-1
    language: ''
```

That block is read by `cli.py:12287`, `tools/voice_mode.py`, and `tools/transcription_tools.py`
— **CLI and agent-tool paths only**. There is an agent tool `transcribe_audio(file_path, model)`
at `tools/transcription_tools.py:2524`, plus a plugin-provider registry.

### What does not exist

| Piece | Status |
|---|---|
| Audio HTTP endpoints on the gateway | **none.** No `/v1/audio/transcriptions`, no `/v1/audio/speech`. `/v1/capabilities` reports `audio_api: False`, `realtime_voice: False` — confirmed against the full route table |
| `faster_whisper` | **not installed** in any of the three environments (system 3.12, hermes venv, jarvis-app venv) |
| `openai-whisper` / `whisper.cpp` binary | **absent** |
| Whisper model weights | **absent** — no `~/.cache/whisper`, no `~/.cache/huggingface` |
| `OPENAI_API_KEY` | **not present** — no OpenAI/ElevenLabs/Deepgram/AssemblyAI key name anywhere in `~/.hermes/.env` |
| `~/.openclaw/workspace/secrets/openai.env` (referenced by jarvis-render) | **does not exist** |
| Any TTS engine | **none** — no piper, coqui, espeak |
| **ffmpeg** | **PRESENT** (`/usr/bin/ffmpeg`, `/usr/bin/ffprobe`) — the one useful piece already here |

ElevenLabs appears only in unrelated creative skills (`ascii-video`, `popular-web-designs`
templates, `manim-video`) and in Hermes' own docs. Nothing is wired.

The `openai` python package is installed in the hermes venv, but a library without a key is not
a capability.

### What the recording skills actually do today — they do not transcribe

This is the part most likely to be misremembered as "we already have transcription":

- `email-router/scripts/handle_plaud_recording.py` pulls the transcript out of
  `classification.extracted_metadata` — i.e. **the Plaud device/service already transcribed it**
  and the text arrived in the email body. The script then sends that text to the gateway.
- `discord-recording-handler/scripts/handle_recording_upload.py` takes
  `--transcript "Optional pre-supplied transcript text"` as an argument.

**No audio is transcribed anywhere on this droplet today.** Both paths consume text somebody
else produced.

### For push-to-talk + reply playback

**Push-to-talk (record → text).** Browser capture is free: `MediaRecorder` is standard and needs
no dependency; nothing in the repo uses it yet. What is missing is everything after the upload:

1. An endpoint to receive the audio. The gateway has none, so this is a **new `/api/*` route on
   jarvis-app** (multipart or base64) — consistent with the BFF pattern anyway.
2. An STT engine, one of:
   - **Local `faster-whisper`** — free per-use, needs the package plus a `base` model download
     (~150 MB) and burns droplet CPU per utterance. ffmpeg is already here, which is the usual
     stumbling block. Fits the existing `stt.local.model: base` config.
   - **OpenAI `whisper-1`** — matches `stt.openai.model` already in config, trivial to call,
     but **requires an `OPENAI_API_KEY` that does not exist on this box**. Someone must add a
     credential.

**Reply playback (text → audio).** Nothing exists. Two shapes:

- **Browser `SpeechSynthesis`** — free, zero infrastructure, no credential, works offline in the
  PWA. Voice quality is the OS default and varies by device; on iOS it is passable.
- **A hosted TTS (ElevenLabs / OpenAI)** — good voice, but **no credential of either kind exists
  today**, plus a new endpoint and per-reply cost and latency.

**Summary:** ffmpeg and browser MediaRecorder are the only pieces present. STT needs an engine
(local download or a new paid key) *and* a new endpoint. TTS needs everything, and the
zero-credential option is browser-side synthesis.

---

## Rough effort ordering

| Item | Verdict |
|---|---|
| **Images in chat** | Smallest by a distance. Gateway already does it; the work is one payload-shape change in `app/chat.py`, a browser file→data-URL step, and a size guard. No new dependency, no new credential. |
| **Audio** | Medium and dependent on a decision: local Whisper (free, CPU, ~150 MB download) vs OpenAI (needs a key that doesn't exist). Plus one new app endpoint. TTS is separable and cheapest as browser `SpeechSynthesis`. |
| **GBrain** | Largest and least defined. Not a wiring job — the droplet has no GBrain, no endpoint config, and crucially **no ingest surface at all** (all three verbs are reads). Needs a decision about where GBrain runs and what its write side even is, before any app work. |
