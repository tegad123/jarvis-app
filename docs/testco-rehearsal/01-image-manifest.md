# What "the golden image" contains — decided 2026-08-20 (testco dress rehearsal)

## INCLUDED
| Path | Why |
|---|---|
| `skills/openclaw-imports/**` (code, SKILL.md, tests) | The platform. Purged of tenant data in step 2; the tree itself IS the product. |
| per-skill `data/` DIRECTORIES (emptied) | Skills expect them to exist; provisioning creates them otherwise. |
| `hermes/state/`, `hermes/credentials/` (emptied) | Same: the shape must exist, the contents must not. |
| `hermes/config.yaml` | Gateway/model config — platform, not tenant. |
| `jarvis-app/**` app code + `static/` (BUILT) | The image must serve the UI without a build step on first boot. |
| `jarvis-app/.venv` | The app must run without pip install on a fresh droplet. |

## EXCLUDED, and why
| Path | Reason |
|---|---|
| `jarvis-app/data/tts/*.onnx` (429 MB) | **THE 8.1 IMAGE-SIZE DECISION, MADE: EXCLUDE.** Kokoro model weights for a capability that is OFF — TTS v1 ships browser SpeechSynthesis, and local Kokoro was deferred pending a 4-core resize. Shipping them costs ~40% of the image (521 MB with them excluded vs ~950 MB) to carry a feature no tenant can use yet. Re-downloading is a documented step in the day the resize happens; carrying them on every tenant droplet forever is not. REVISIT if Kokoro is ever enabled by default. |
| `jarvis-app/frontend/node_modules` | Rebuildable, ~600 MB, and never needed at runtime — the app serves the pre-built `static/`. Only a developer rebuilding the UI needs it. |
| `~/.hermes/.env` | **SECRETS. Per-tenant, never imaged.** APP_PASSPHRASE, APP_SESSION_SECRET, API_SERVER_KEY. Provisioning must mint these per tenant — see the rehearsal report, this turned out to be a real manual step. |
| `__pycache__`, `*.pyc` | Byte-code for another machine's paths. |
| All tenant DATA | Removed by purge_tenant_data.sh in step 2, verified by its own verifier. |

## LAYOUT NOTE (rehearsal artifact, not the real thing)
The clone is laid out as `<root>/{skills,hermes,jarvis-app}` because that is what
`purge_tenant_data.sh --root` expects. A REAL image is a filesystem snapshot with the
paths in place (`/root/.hermes`, `/root/jarvis-app`) and the script runs with no `--root`
at all, gated on hostname + `/etc/jarvis-snapshot-prep`. See the report's final section.
