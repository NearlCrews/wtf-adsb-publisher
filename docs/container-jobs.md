# Scheduled container jobs

Three host timers invoke one publisher image through
[`run_container_job.py`](../tools/run_container_job.py). The runner requires
system Python 3.11 or newer, Docker with Compose, and user systemd. It uses only
Python's standard library. No application virtual environment or Docker socket
is needed inside the image.

## Configuration

Copy [the Compose environment](../deploy/container/.env.example) into
`/srv/wtf-adsb-publisher/config/compose.env`, using absolute paths and the tested local
image's `sha256:` ID. Application launches never pull an image. Create directories
before use, owned by UID/GID 1000, with private state mode 0700 and credentials
mode 0600. Mount whole state directories so SQLite WAL and lock files remain
shared across jobs. Configuration, feed, credentials, and SSH files are read-only.

Container `publisher.json` uses `feed_directory: /feed`, `state_dir: /state`,
`inbox_database: /state/inbox.sqlite3`, and `budget_database: /state/budget.sqlite3`;
the [example configuration](../examples/publisher.json) holds host paths for
these four, so change them in the copy the jobs mount. Its key paths are `/run/secrets/ghost-admin-key`
and `/run/secrets/openai-api-key`. `PUBLISHER_REFERENCE_DIR` is optional: it
names the host directory of the operator-supplied
[reference files](../src/wtf_adsb_publisher/data/README.md#reference-data),
which every job mounts read-only at `/references`, and `publisher.json` then
sets `reference_dir: /references`. The engine can share that directory. Create
it, with the files in place, before you set the variable: Compose turns a
missing path into an empty root-owned directory, and the jobs then run without
references. The example ships the variable empty, which keeps the built-in
rules. The image keeps writable build output under
`/app/artifacts/ghost-theme`; persist that entire directory, including verified
media, social images, and basemap caches.

The publish job alone receives Ghost and SSH credentials. The two AI jobs alone
receive the shared AI environment and API key. Use a project-specific SSH config
with `IdentityFile /home/publisher/.ssh/id_ed25519`, `IdentitiesOnly yes`,
`StrictHostKeyChecking yes`, and the verified project `known_hosts` file. Preserve
the configured SSH alias, and verify private network DNS and routes (Tailscale,
for example) from the actual image.
Preserve an existing `HostKeyAlias` when the verified host key is stored under
that name; changing the client filesystem must not change the trusted identity.
Do not mount the host's entire SSH directory or agent socket.

Create `/srv/wtf-adsb-publisher/config/jobs.json` with these host paths, the
layout the [quick start](../README.md#quick-start-with-docker) uses:

```json
{
  "version": 1,
  "project": "wtf-adsb-publisher",
  "compose_file": "/home/operator/src/wtf-adsb-publisher/deploy/container/compose.yaml",
  "env_file": "/srv/wtf-adsb-publisher/config/compose.env",
  "job_directory": "/srv/wtf-adsb-publisher/config/jobs",
  "activation_hold": "/srv/wtf-adsb-publisher/control/activation.hold",
  "publisher_state_directory": "/srv/wtf-adsb-publisher/state",
  "budget_database": "/srv/wtf-adsb-publisher/state/budget.sqlite3",
  "configuration_files": [
    "/srv/wtf-adsb-publisher/config/publisher.json",
    "/srv/wtf-adsb-publisher/config/ai.env",
    "/srv/wtf-adsb-publisher/secrets/ghost-admin-key",
    "/srv/wtf-adsb-publisher/secrets/openai-api-key",
    "/srv/wtf-adsb-publisher/ssh/config",
    "/srv/wtf-adsb-publisher/ssh/id_ed25519",
    "/srv/wtf-adsb-publisher/ssh/known_hosts"
  ]
}
```

List every runtime configuration, approved plugin schema, and credential input
in `configuration_files`; only hashes enter job receipts. `PUBLISHER_CONTROL_DIR`
must be the parent of `activation_hold`. Mounting the control directory makes
creation and removal of its hold file visible to running containers. Use a
dedicated control directory, never the directory holding cutover captures. Match the
service templates' `ConditionPathExists` to that same host path.

Freeze configuration and credential files while jobs exist. Fence and drain
before changing them, then validate the new configuration before removing the
hold. The runner checks their hashes twice during preparation; it cannot make
uncoordinated edits to bind-mounted files atomic with Docker creation.

After initializing or migrating state, verify the existing zero-byte lock files
`writer/status.json.lock`, `weekly/status.json.lock`, `ledger.json.lock`, and
`budget.ai.lock`. Create missing lock files only while all writers are stopped;
never replace or remove a lock inode while a worker may hold it. Drain requires
these files and an initialized ledger and spending database.

## Lifecycle and recovery

Jobs pin the image by its `sha256:` ID, and a build tags only `:local`, so a
later build drops the previous untagged ID and every job then fails to create
its container ("Container job held"). Give the pinned image a stable tag, for
example `docker tag <id> wtf-adsb-publisher:prod`, before writing its ID into
`PUBLISHER_IMAGE`, and do not rebuild on the job host while jobs may launch
without the hold. A launch that fails after its intent is written leaves
`jobs/active/<role>.json` in the `prepared` phase with no container; `status`
lists the role as pending and `drain` refuses. Confirm with `docker ps -a` that
no container of that name exists and that no receipt carries its run ID, move
the intent file aside, then drain again.

The publish, writer, and weekly timers run at :00 and :30, at :10 and :40, and
daily at 06:10 UTC. The
runner writes and fsyncs a launch intent before creating a detached, named job.
Image, configuration, role, and run IDs must agree before an existing container
can be reconciled. Each role has a host lock; all jobs also share the
application ledger and spending locks.

The wrapper waits without a deadline. At 20, 25, and 45 minutes respectively it
records an overdue alert and continues waiting. Stopping a wrapper does not stop
its job or permit a duplicate. A later invocation reconciles that existing run
and exits; it does not start a replacement in the same invocation. Lost Docker
responses and missing containers retain their launch intent and block automatic
replacement when the outcome is unknown.

Completion receipts contain bounded private log tails, exit status, and image
and configuration identities. They are fsynced before removing stopped
containers. Failed jobs keep their paid reservations and application receipts;
the runner does not retry their work immediately. Investigate uncertain outcomes
against those records and Ghost inventory before changing job metadata.

The [startup
reconciler](../deploy/ghost-preview/wtf-adsb-publisher-reconcile.service)
handles existing jobs only, including while launches are fenced. It never
creates or starts paid work and does not wait behind running jobs. Each normal
timer also reconciles the other roles before running its own job, so completed
orphan jobs are found without waiting for their own next scheduled time.
Successful weekly completion leaves a durable marker in `pending-followups/`
before removing its active intent. Recovery recreates a missing marker from that
intent's completion receipt; routine timer invocations inspect only the pending
index, without reading historical log tails. When unfenced, the common
completion path queues the autopilot service only after its previous intent has
been reconciled and systemd reports no active or queued job. An already-running
publisher leaves the marker pending because that pass may have captured its
input before the weekly work completed. The runner records systemd's
acknowledgment before removing the marker, so the weekly service needs no
`ExecStartPost`. A crash between enqueue and receipt update can enqueue twice;
normal publication locks and idempotence apply. A newly queued wrapper waits for
the brief global bookkeeping locks, while its role lock still rejects competing
invocations.

```sh
python3 tools/run_container_job.py --config /srv/wtf-adsb-publisher/config/jobs.json status
python3 tools/run_container_job.py --config /srv/wtf-adsb-publisher/config/jobs.json reconcile
python3 tools/run_container_job.py --config /srv/wtf-adsb-publisher/config/jobs.json drain
```

`drain` creates the durable launch hold and checks containers, host locks, writer
locks, the ledger lock, and the AI lock. It never kills a job. Exit 75 means a worker
is still active; wait and retry. Unknown containers or missing completion
evidence block transfer. A proven never-started container receives a cancellation
receipt before removal, so its old configuration cannot start after handoff.
A successful drain leaves the hold present; keep it
until the [cutover commitment](cutover.md#activation-and-recovery-boundary).
Stop timers before the final transfer and verify the private receipts. Do not
infer drainage from systemd's `MainPID` or delete a live container to clear a name.

The timers skip their runs while the hold exists, so remove it once the new
image ID is in `compose.env`. User timers stop when the account logs out unless
lingering is on; run `sudo loginctl enable-linger "$USER"` once for the account
that owns them.

Service installation and activation remain separate from building an image.
The Compose jobs use profiles so ordinary `compose up` does not launch writers.
Administrative commands are explicit maintenance operations; coordinate them
with the same fence and drain procedure when they can change shared state.
