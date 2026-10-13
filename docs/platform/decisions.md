# Decisions and open questions

This is Section 15 of [docs/SPEC.md](../SPEC.md), kept as a working log. Date every entry. Evidence for the current state is in [GAP-ANALYSIS.md](GAP-ANALYSIS.md).

## Decided 2026-10-13 (human decisions)

These resolve the five architecture forks below. Each is recorded as the human stated it. A decision says which way to go; it does not approve the work. Each dependency, migration, send-path change, and `AGENTS.md` edit it implies still needs its own explicit human yes (`AGENTS.md` rule 6).

1. **Runtime: one runtime per user (Docker), as the spec says.** Resolves fork 1 (option A).
2. **Storage: Postgres for new platform tables only:** users, roles, approvals, grants, policies, audit log. Existing per-tenant SQLite stores are **not** migrated now; they stay as they are until the Phase 11 migration. Resolves fork 2 (a scoped option A).
3. **Credentials: the first step, before anything else, is to remove the service-account key and plaintext `google_token.json` fallbacks** (`_shared/google_creds.py` in the skills origin, and `GOOGLE_TOKEN_PATH` in `app/config.py`). The spec's Phase 3 item adds that the remaining no-connection failure must be loud (`AGENTS.md`: a fallback that does not announce itself is a false green). Resolves fork 3's first step. **End state: assumed per spec 9.6 (surrogate tokens at egress). The human did not state it explicitly; needs human confirmation.**
4. **Roles: adopt `owner/admin/agent/assistant`**, and keep `operator` as break-glass with the never-bodies rule. The accounts migration and the `AGENTS.md` update are required later, and each needs its own human yes. Resolves fork 4 (option A).
5. **Live: onboard Eddie, Blake, and Martin first, in shadow mode, on the new layout.** Spencer's live setup stays where it is and keeps getting bug fixes. Spencer cuts over in Phase 11 to the provisioned layout, keeping `/root/jarvis-app` and `/root/.hermes` intact for at least two weeks as the rollback path. Resolves fork 5 (option B, then a supervised cutover) and open question 1.

Where the spec contradicted decisions 2 and 5, notes were added in place in `docs/SPEC.md` (Section 6 header, Phase 0 storage note, Phase 11, Section 15). The sections were not rewritten.

## Decided (from the spec, recorded 2026-10-10)

- Customer #1: 34 Dev team.
- Hosting: Docker containers on shared DigitalOcean hosts, managed over Tailscale. *(Spec decision; nothing of this is installed yet. Confirmed for runtimes by decision 1 on 2026-10-13.)*
- Channels for v1: WhatsApp, Slack, PWA. No SMS or Twilio.
- iMessage: read through a Mac companion app, read-only, opt-in per chat.
- CRM: adapter interface; Google Sheets first.
- HAR: out of v1. ShowingSmart: in (email layer first, browser layer flagged).
- Autonomy: customizable per user and per skill, with safe defaults and a locked class.
- Agent backbone: Hermes. Builder: Tega in Cursor.

## Open

1. ~~Spencer: an earlier decision kept Spencer on OpenClaw with new clients on Hermes. Does Spencer move to the new platform, and when?~~ **Answered 2026-10-13** (decision 5): yes, in Phase 11, after Eddie, Blake, and Martin, with a two-week rollback window.
2. Model choice and routing: which model for the main agent, which for Sentinel, which for cheap tasks like parsing fallback and digests?
3. ~~Is Hermes's skill-body loading behavior the same gotcha as OpenClaw's? Check in Phase 2.~~ **Answered 2026-10-10, line numbers re-checked and recorded in the spec 2026-10-13.** Yes, the same default.
   - The system prompt carries only each skill's name and description: `hermes:agent/prompt_builder.py` `_parse_skill_file` (lines 1511–1531) and `build_skills_system_prompt` (line 1584), rendering `<available_skills>` at lines 1836–1863 with the instruction to "load it with skill_view(name)".
   - Bodies load on demand through `skill_view` (`hermes:tools/skills_tool.py` line 962).
   - Bodies are injected without the model choosing only for CLI/TUI preloads (`hermes:agent/skill_commands.py` `build_preloaded_skills_prompt`, lines 747–812) and for cron jobs that list `skills:` (`hermes:cron/scheduler.py` lines 2444–2645, `skill_view` at line 2606).
   - Consequence: rules that must hold go in code gates (`gated_send`, `outbound_boundary`, `write_guard`), the frontmatter `description`, or a preload, not only the SKILL.md body.
   - `hermes:` = `/usr/local/lib/hermes-agent`, branch `scale-vault-local`, `276e5c7`.
4. Pricing per seat, to compare against measured cost per user after Phase 11.
5. ShowingSmart terms of use for automated access (gate for Phase 12).
6. Data retention for client emails and transcripts kept in events.
7. Who on the team is the org owner vs admin in 34 Dev?
8. Which apps from the 8.8 catalog does each 34 Dev agent actually use? That sets the Next-tier order.

## Architecture forks (2026-10-10; all five resolved 2026-10-13)

The spec assumes things that differ from how `jarvis-app` is built. These five choices shape Phases 1 to 5 and 11. They were listed with tradeoffs only; all five were decided by a human on 2026-10-13 (see "Decided 2026-10-13" above). The tradeoffs are kept for the record.

### Fork 1: Runtime granularity — one runtime per user, or per tenant — **RESOLVED 2026-10-13: A (per user, Docker), decision 1**

Today: one app, one Hermes gateway, and one `HERMES_HOME` per tenant, stamped by `scripts/provision_tenant.py` under systemd.

- **A. Per user, as the spec says.** Gives per-user memory and isolation for free, and makes "a runtime can't reach another runtime" testable. Costs: a container runtime (Docker, a new dependency), a Runtime Manager, roughly N times the gateway memory per tenant, and team features (delegation, shared tasks) must move to the control plane, since the skills today assume one shared tasks store per tenant.
- **B. Per tenant, as today, with per-user scoping inside.** Reuses the provisioner, loadouts, and verify suites. Costs: per-user memory, policies, and audit must be scoped by `person_key` inside shared stores and Hermes' shared memory directory, and one compromised or injected gateway still sees every user in the tenant.
- **C. Hybrid.** Per-tenant control plane and stores; per-user Hermes processes (systemd or containers) pointing at per-user `HERMES_HOME`s. Middle cost; still needs a decision on where shared tasks live.

### Fork 2: Storage — Postgres control plane, or per-tenant SQLite — **RESOLVED 2026-10-13: Postgres for new platform tables only; SQLite untouched until Phase 11, decision 2**

Today: per-tenant SQLite under `$HERMES_HOME/state/` plus JSONL files; backups by `scripts/backup_tenant.py`.

- **A. Postgres.** Matches Section 6, makes cross-org queries, an admin view, and point-in-time recovery straightforward. Costs: a new dependency and server, a migration of every store, a tenant-scoped query layer that must be proven by a cross-tenant canary, and a new backup path.
- **B. Keep per-tenant SQLite.** Isolation stays physical (one file set per tenant), existing verify suites keep working, no new server. Costs: the Section 6 tables (`policies`, `actions`, `events`, `usage`) become per-tenant schema migrations; anything org-wide (owner admin view across users, billing) needs a small separate store or aggregation job.

### Fork 3: Credential boundary — app-hosted tools, or in-process `google_creds` — **RESOLVED 2026-10-13 (first step): remove the SA-key and plaintext fallbacks first, decision 3; end state assumed per spec 9.6, needs human confirmation**

Today: skills in the gateway and timers import `app.google_connection` through `origin:_shared/google_creds.py` and decrypt tokens in their own process; a plaintext `google_token.json` fallback remains.

- **A. App-hosted connector tools (the spec).** The app (control plane) holds `APP_GOOGLE_TOKEN_KEY` and exposes Gmail, Calendar, and Sheets as MCP or HTTP tools scoped to the caller. A compromised gateway cannot exfiltrate tokens. Costs: a send-path change, rewriting every `google_creds` importer (gmail-push-trigger, email-router, task-manager, sheet_tab, meeting-briefing, and others) to call tools instead, possibly an MCP library (dependency), and an app process that becomes a hard dependency for every skill.
- **B. Keep in-process `google_creds`, harden it.** Remove the plaintext fallback, keep the key off the gateway environment, and constrain scopes. Cheaper and incremental. Costs: the Phase 3 acceptance check ("the runtime never sees the OAuth token") cannot pass, and Section 12 stays violated.

### Fork 4: Role vocabulary — **RESOLVED 2026-10-13: A (owner/admin/agent/assistant), operator kept as break-glass, decision 4**

Today: `owner`, `admin`, `member` in `app/accounts.py` `ROLES`, plus a session-only `operator` (break-glass, structure but never bodies) in `app/auth.py`.

- **A. Adopt the spec's `owner/admin/agent/assistant`.** Rename `member` to `agent`; add `assistant` with grants to specific agents' data. Costs: an accounts migration, every role gate and verify script that names `member`, and `AGENTS.md`'s role invariant must be updated.
- **B. Keep the code's names and map them.** `member` = spec `agent`; add `assistant` as a new role or as a grant on `member`; keep `operator` as a platform role outside the org. Costs: two vocabularies to translate between in docs.
- Either way, `operator` has no spec equivalent and its "never bodies" rule must survive.

### Fork 5: Spencer and the live migration — **RESOLVED 2026-10-13: B first (Eddie, Blake, Martin in shadow mode), then Spencer cuts over in Phase 11 with a two-week rollback window, decision 5**

Today: live (34 Dev) runs from `/root/jarvis-app` with `HERMES_HOME=/root/.hermes` on port 8700, outside the provisioner's `/opt/{slug}` layout. The skills origin keeps a separate `live-34dev` branch (`7b9bb7f`, baseline 2026-09-02). Testco is 17 commits behind `main`.

- **A. Re-provision live as `/opt/34dev`** with `scripts/provision_tenant.py`, then cut over (tokens, stores, Gmail watch, DNS or tunnel). One layout for every tenant; live gets the loadout completeness guard and verify suites. Costs: a supervised cutover with downtime risk on the only paying tenant; requires touching `/root/.hermes`, systemd, and the tunnel, all human-only per `AGENTS.md`.
- **B. Leave Spencer where he is; onboard Eddie, Blake, and Martin on the platform first** (spec Phase 11 order). No risk to live. Costs: two layouts and two skills trees to maintain, and team features cannot span Spencer and the others until he moves.
- **C. Freeze live, build the platform, migrate once at the end.** Least churn on live. Costs: live drifts further from `main` and the cutover gets larger.
- Precondition for any option: sync testco to `main` (human-supervised, per `AGENTS.md`).
