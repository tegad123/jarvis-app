# Local TTS for Jarvis reply playback — investigation

Investigation only, 2026-08-17. No product changes were made; nothing was wired into the app.
All numbers are measured on this droplet unless labelled as an estimate.

**Verdict: local TTS FAILS the decision gate on this hardware — but it fails on CPU count, not
on the engine.** Kokoro is the right engine and would comfortably pass on a 4-core box. On these
2 cores it generates speech *slower than the speech plays*, so audio would start and then stall.
The recommendation is therefore a fork: resize the droplet and go local, or take the cheapest
fallback rung now.

---

## 1. Voicebox — not viable headless here, and not worth forcing

Voicebox **does** have the headless surface the brief hoped for: a REST API on `127.0.0.1:17493`
(`POST /generate`, `/speak`, `/transcribe`, `GET /profiles`), an MCP server at `/mcp`, docs at
`/docs`, and a documented `docker compose up`. So the concept is sound.

The problem is getting it running *here*. Its prerequisites are Bun, Rust, Python 3.11+, and the
Tauri desktop toolchain; Linux has no prebuilt headless binary and requires a source build.
Checked on this box:

| Prerequisite | Present |
|---|---|
| `cargo` / `rustc` | **absent** |
| `bun` | **absent** |
| `docker` | **absent** |
| `libwebkit2gtk-*-dev`, `libgtk-3-dev` (Tauri) | **absent** (0 packages) |
| Python 3.12.3 | present |

That is a Rust toolchain, a JS runtime, and a GUI-library dev stack installed on a headless
2-core server, to obtain an API whose engines are **all Python underneath** (Qwen3-TTS, Qwen
CustomVoice, LuxTTS, Chatterbox Multilingual, Chatterbox Turbo, TADA/HumeAI, Kokoro — running on
PyTorch or MLX). Voicebox is a packaging and UX layer, not an inference advantage.

**Conclusion: skip the app, evaluate the engine directly.** Nothing is lost — the engine we would
be reaching for through Voicebox is the same one benchmarked below. Revisit Voicebox only if its
voice-profile management or MCP integration becomes valuable in its own right, and then prefer
the Docker path over a source build.

---

## 2. Kokoro benchmarked directly (the engine that matters)

Installed `kokoro-onnx` rather than the PyTorch `kokoro` package, because **onnxruntime 1.28.0 is
already on this box** (it arrived with faster-whisper) and torch is not. That avoided a ~2.5 GB
dependency for zero benefit.

Test text — a realistic Jarvis reply, 44 words:

> "You have three things that need you today. The builder's risk renewal for eleven Tiel Way is
> due Thursday, William Mchenry has not replied since the open house, and the title company still
> owes you the survey for three thirty two west twenty seventh."

### Results (2 cores, `af_heart` voice)

| Model | Size | Load | TTFA (first sentence, 8w) | Full reply (44w) | RTF | Sustains playback? |
|---|---|---|---|---|---|---|
| **kokoro-v1.0.onnx (fp32)** | 311 MB | 1.9s | **2.76s** | 16.7s → 14.7s audio | **1.14–1.41x** | **NO** |
| kokoro-v1.0.int8.onnx | 89 MB | 2.0s | 8.72s | 50.6s → 14.8s audio | 3.41–4.40x | NO |

Shorter utterances, fp32:

| Utterance | Gen | Audio |
|---|---|---|
| "Got it." (2 words) | 1.24s | 0.77s |
| "You have three things today." (5w) | 2.20s | 1.45s |
| First clause (8w) | 2.68s | 1.96s |

### The int8 result is not a mistake — and it is instructive

int8 came out **3× slower than fp32**, which inverts the usual expectation. The cause is visible
in `/proc/cpuinfo`: this CPU (DigitalOcean "DO-Regular", 2 vCPU) has **AVX2 but no AVX-512 and no
VNNI**. Without VNNI there is no hardware path for int8 GEMM, so the runtime pays
quantise/dequantise overhead on every op and gets nothing back. **Do not assume a quantized model
is faster; on this class of CPU it is reliably slower.** Worth remembering for whisper too — we
chose int8 there and it is fine because whisper had 5-7x of headroom to spare, but the same
caveat applies if that ever gets tight.

### Thread headroom: none

Measured parallelism during generation: **1.85× of 2 cores** (wall 2.48s, CPU 4.59s). ONNX
Runtime is already saturating the box. There is no tuning win available — the ceiling is the
hardware.

---

## 3. Decision gate

The gate was **time-to-first-audio under ~3s**. Kokoro fp32 hits **2.76s** with sentence
chunking, so it clears the letter of the gate. It fails the thing the gate was standing in for.

**Generation is slower than playback (RTF 1.14–1.41x), so streaming stalls.** Concretely, with
sentence chunking on the test reply:

```
chunk 1:  8 words   gen  2.78s ->  1.96s audio
chunk 2: 36 words   gen 13.63s -> 12.29s audio
```

Playback of chunk 1 starts at t=2.78s and finishes at t=4.74s. Chunk 2 is not ready until
t=16.4s. That is an **~11.7 second silence in the middle of a sentence**, and it compounds: every
chunk falls further behind than the last. Finer chunking lowers TTFA but makes the stall *worse*,
because per-chunk overhead rises while the deficit stays.

A voice that starts promptly and then stutters is worse than no voice. **Local TTS does not pass
on this hardware.**

### It is a core-count problem, not an engine problem

Kokoro scaled at ~92.5% efficiency across 2 cores, so extrapolation is reasonable:

| Cores | Estimated RTF | Verdict |
|---|---|---|
| 2 (today) | 1.23x | stalls |
| **4** | **0.62x** | **sustains playback comfortably** |
| 8 | 0.31x | ample |

**A 4-core droplet flips this to a pass**, with roughly 1.6× headroom. That is the single change
that makes local TTS work, and it is worth weighing against per-use API costs: a 4-core DO
droplet is roughly $24/mo versus $12, and it would also remove the whisper CPU contention noted
below.

### The heavier engines are not close

Kokoro is 82M parameters. Estimating from its measured RTF:

| Engine | Params | Est. RTF here | vs Kokoro |
|---|---|---|---|
| Chatterbox Turbo | 350M | ~4.9x | 4× |
| Chatterbox Multilingual | 500M | ~7.0x | 6× |
| Qwen3-TTS 0.6B | 600M | ~8.3x | 7× |
| Qwen3-TTS 1.7B | 1700M | ~23.6x | 21× |

I did **not** install these. Doing so would mean ~2.5 GB of torch to confirm a conclusion the
arithmetic already forces: if the 82M model runs at 1.14x, a 350M model cannot approach realtime
on 2 cores. Stated as an estimate, not a measurement — but the margin is 4× to 21×, far outside
any plausible error in the extrapolation. If the box is resized, Kokoro is still the right
choice; the heavier engines buy quality and cloning, not speed.

---

## 4. Memory posture

| State | RSS |
|---|---|
| Kokoro fp32 loaded | 492 MB |
| Kokoro after generation | 718–973 MB (peak varies with utterance length) |
| jarvis-app today (whisper base.en resident) | 339 MB idle, ~540 MB with whisper loaded |
| **Both resident, worst case** | **~1.5 GB** |

Box has 3.8 GB total, ~2.4 GB available, plus the 3 GB swapfile added after the 2026-08-17 OOM.
Both models resident would fit — but it would put ~1.5 GB of models on a box that has already
been OOM-killed once, and TTS and STT would contend for the same 2 saturated cores. If local TTS
is ever adopted here, **the model should load lazily and unload on idle**, unlike whisper's
stay-resident posture — TTS is a lower-frequency operation and does not justify permanent
residency on this hardware.

---

## 5. Streaming story

Kokoro generates a complete waveform per `create()` call; there is no token-level audio stream.
Streaming is therefore **caller-side chunking**: split the reply on sentence boundaries, generate
sequentially, play each chunk as it lands. That design is sound and would work well at RTF < 0.8x
— it is exactly how this should be built on a 4-core box, and it maps cleanly onto the chat
stream we already have (chunk on the sentence boundaries the NDJSON deltas already reveal).

On 2 cores the same design produces the stall described above. The architecture is fine; the
throughput is not.

---

## 6. Voice cloning — for a future per-tenant Jarvis voice

| Engine | Cloning | Notes |
|---|---|---|
| **Kokoro** | **No** | Fixed voice pack (~54 voices in `voices-v1.0.bin`, e.g. `af_heart`, `am_michael`). Voices can be blended by mixing style vectors, but you cannot clone a target speaker. |
| Chatterbox | Yes | Zero-shot cloning from a short reference clip. |
| Qwen CustomVoice | Yes | Purpose-built for custom voice. |
| ElevenLabs | Yes | Best-in-class, and the original plan. |
| OpenAI TTS | No | Fixed voice set. |

**This is a real strategic fork.** If a distinct per-tenant Jarvis voice matters commercially,
Kokoro cannot deliver it at any core count, and the choice is between a cloning-capable local
engine (Chatterbox — needs materially more CPU than even 4 cores, realistically a GPU) and a
hosted API. If a *consistent, good, non-custom* voice is enough, Kokoro on 4 cores is the cheapest
durable answer and needs no vendor.

Note also: I cannot assess **quality** from here. The benchmark WAVs are at
`/root/jarvis-app/data/tts/bench_af_heart.wav` and `out_fp32.wav`. Kokoro's reputation is good for
its size, but somebody should listen before committing.

---

## 7. Honest fallback ladder

Given local fails on current hardware, in ascending order of cost and commitment:

1. **Resize to 4 cores and go local with Kokoro.** ~$12/mo more. Zero per-use cost, no vendor, no
   key, no data leaving the box. Est. RTF 0.62x sustains streaming playback. Also relieves the
   whisper/TTS core contention. **My recommendation if voice matters and a custom voice does not.**
2. **Browser `SpeechSynthesis`.** Free, zero install, no credential, no server load at all — the
   phone does the work, so the 2-core limit is irrelevant. Voice quality is the OS default:
   acceptable on iOS, poor on some Android builds. **The right move if you want playback this week
   without spending anything**, and it is genuinely zero-risk to try.
3. **OpenAI TTS API.** Good quality, simple integration, streams. Needs an `OPENAI_API_KEY` —
   which still does not exist on this box (confirmed again in this investigation). Per-use cost,
   and reply text leaves the droplet.
4. **ElevenLabs.** Best quality and the best cloning, i.e. the only rung that delivers a bespoke
   per-tenant voice. Highest cost, needs a key, same data-egress consideration.

Rungs 2 and 1 are not mutually exclusive: shipping browser synthesis now costs almost nothing and
would tell you whether users actually want playback before you pay for either a bigger box or a
vendor.

---

## Artefacts left on disk

`/root/jarvis-app/data/tts/` — Kokoro models (311 MB fp32, 89 MB int8, 28 MB voices) and benchmark
WAVs. **~440 MB, and nothing in the app references it.** Delete with
`rm -rf /root/jarvis-app/data/tts` if this is not being pursued; `kokoro-onnx` and `soundfile`
were installed into the app venv and can be removed with
`pip uninstall kokoro-onnx soundfile phonemizer-fork` (note: `soundfile` is only used by this
investigation, not by the transcribe path, which uses ffmpeg).
