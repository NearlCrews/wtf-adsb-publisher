# Migrating the publisher

Follow the [production cutover runbook](cutover.md) for the reviewed shadow,
activation-fence, final handoff, and observation gates. The commands below cover
individual data transfers; they do not switch services by themselves.

Keep the previous publisher as the only production writer while comparing it with the engine and this publisher. The source checkout is a rollback artifact. This repository's systemd templates point to `~/src/wtf-adsb-publisher`; installing a template alone must not enable a second set of timers.

1. Export the engine feed and import it into a private publisher inbox. The exporter must include every supported retained finding before site selection, with novelty context, the type norms of norm-kind findings (contract 1.1.0), and enough media evidence for maps, replays, and spike charts. A 1.0.0 stream stays readable, but its speed stories are staged without a norm and keep none (the pass reports them as `type_norm_unexported`), so import a 1.1.0 feed before the first production pass. Novelty context covers the days of the exported observations only; a prior story without a saved `automation.routine_type` on a date with no current observation does not hold a later story, as a journal-wide count would. Bootstrapped and staged records carry that field, so only an older record without it is affected.
2. Compare the old and new policy on a consistent SQLite backup, at the same timestamp and with the same private publication history. Preserve article IDs, paths, Ghost owner tags, owner removals, withdrawals, AI receipts, research bindings, and media caches. First ingestion binds existing source identities to engine IDs; subsequent corrections use those opaque IDs even when timestamps change, preserving article identities. Evidence reconstruction uses only frozen enrichment from the feed.
3. Stop the old publication, writing, and weekly timers. Stop any old private-note writer that shares their AI cap before moving the spending store. Save a Ghost inventory and preserve the last checked static release.
4. Copy the stopped publisher's entire private state into a new directory. The migration command refuses an existing destination and symlinks:

   ```sh
   .venv/bin/wtf-adsb-publisher migrate-state --source /path/to/old/state --destination /path/to/new/state
   .venv/bin/wtf-adsb-publisher migrate-budget --source /path/to/receiver.sqlite3 --destination /path/to/new/state/budget.sqlite3
   ```

   Budget migration uses SQLite's backup API. It copies completed spending and uncertain reservations, preserves cumulative one-time spending, and pauses the new store. Source aliases, including hardlinks, and existing destination spending history are refused. A failed copy leaves any initialized destination paused. Keep the original private notes and drafts as an archive; their prose does not become public evidence.
5. Point configuration at the new inbox and budget database, retain the original `live_since` and other site settings, and run a dry pass against the saved Ghost inventory. Confirm selected identities, dates, evidence, media, and proposed target actions. A restored engine stream or an ambiguous legacy binding needs explicit reconciliation before production consumption.
6. Deploy one replacement runtime at a time. Catch up the inbox and confirm fresh, complete analysis before resuming the paid writers and enabling one set of publisher timers. The receiver Compose project still owns its networks and secrets.

`migrate-state` and `migrate-budget` are explicit maintenance operations. Normal publication reads the publisher inbox and never opens the receiver database. The JSON reader verifies sequence order, hashes, supported versions, duplicate records, and file boundaries before advancing its cursor. Only a fresh, complete live feed authorizes new stories. Synthetic feeds hold new releases in both preview and production runs; an offline dry run may simulate them. Replay publication requires its own explicit configuration. If a later batch fails, trusted retractions already committed to the inbox still withdraw articles while new releases wait. Group merges and splits follow stable finding IDs. A supersession whose replacement has not arrived suspends the old article reversibly; the next complete import restores its existing identity automatically when the finding survives.

For rollback, stop current writers first and preserve their latest ledger, inbox, cursors, AI receipts, and reservations. Reconcile the current Ghost inventory before resuming an older publisher. Restoring a pre-cutover ledger after new posts were created can duplicate articles or erase owner edits. If old code cannot read the current state, keep the last checked static release serving while repairing forward.

The previous collector's private notes and drafts stay out of the public feed.
The engine's separate read-only archive viewer keeps them readable as of the
final capture. New paid writing belongs to the publisher alone.
