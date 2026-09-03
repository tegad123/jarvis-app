# Restore a WAL-inclusive tenant backup (slice 3)

The provisioner installs `{slug}-backup.timer`, which runs
`scripts/backup_tenant.py --slug {slug}` daily. Archives land in
`/root/backups/{slug}/<UTC stamp>/` — **outside** the tenant tree the app
serves. Retention is **14 daily** directories (manifest `backup.keep_daily`
overrides). The plan did not fix N; 14 is the slice-3 choice.

Each archive contains:

- Consistent `.db` files produced by Python `sqlite3.Connection.backup()`
  after copying `db`+`-wal`+`-shm` aside (the verify-jarvis ledger procedure).
  The `sqlite3` CLI is not installed and is not required.
- `staging/` with the raw wal-inclusive copies.
- JSONL copies (`activity_feed.jsonl`, approvals) when present.

## Restore into a scratch dir (the drill)

Never restore onto a running `/opt/{slug}` or `/root/.hermes`.

```bash
ARCHIVE=$(ls -d /root/backups/clientzero/20* | tail -1)
DEST=/tmp/clientzero-restore
rm -rf "$DEST"
/root/jarvis-app/.venv/bin/python scripts/restore_tenant_backup.py \
  --archive "$ARCHIVE" --dest "$DEST" --compare jarvis_accounts.db
```

PASS is a sha256 match of one DB between archive and dest.

## Restore onto a stopped tenant (human checklist, not automated)

1. `systemctl stop {slug}-app {slug}-gateway`
2. WAL-checkpoint is already in the archive's consistent `.db` files.
3. Copy `{archive}/*.db` over `/opt/{slug}/hermes/state/` (and JSONL if replacing ledgers).
4. Remove leftover `-wal`/`-shm` next to those files so SQLite does not replay a stale WAL against a replaced DB.
5. `systemctl start {slug}-app {slug}-gateway`
6. `GET /api/health` and a PIN login.

Do not run that against live or testco from this slice.
