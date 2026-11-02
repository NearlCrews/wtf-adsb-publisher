# Production cutover

This runbook moves a site from a single-process collector that also wrote and
published its stories to the engine and this publisher. The previous project
remains the production writer until these gates pass. Keep its checkout,
service definitions, configuration and checked static release for recovery.
The engine owns collection and factual evidence; the publisher owns writing,
policy, Ghost and site deployment. The receiver's parent Compose project
continues to own networks and secret declarations.

## Rehearse against retained history

Use the publisher's [command environment](operations.md#command-environment)
for the commands below so subprocesses use the pinned Python and Node runtimes.

Run `make check`, `make image` and `make test-image` in both repositories and
record source hashes, wheel hashes, locked dependencies and both container image
IDs. Check current compatible
dependencies with each repository's audit tool. Test the installed engine wheel
and private viewer as well as the source checkout.

Capture the journal using SQLite's backup API, including committed WAL. Export
a complete read-only Ghost inventory. Capture old publisher state under its
writer, weekly and ledger locks, in that order. Preserve `live_since`,
`frozen_through`, the weekly boundary, site policy and provider budgets.

The Linux comparison helper requires Docker, `bwrap` (Bubblewrap), the old
application's installed source environment, the publisher's host check
environment, and the production media caches. Supply immutable local image IDs
and the journal capture time in UTC:

```sh
.venv/bin/python tools/compare_cutover.py compare \
  --legacy /path/to/previous/project \
  --engine-image sha256:REPLACE_WITH_TESTED_ENGINE_IMAGE_ID \
  --publisher-image sha256:REPLACE_WITH_TESTED_PUBLISHER_IMAGE_ID \
  --config /private/previous/config.json \
  --journal /private/rehearsal/journal.sqlite3 \
  --inventory /private/rehearsal/ghost-inventory.json \
  --work /private/rehearsal/comparison \
  --at REPLACE_WITH_THE_CAPTURE_TIME_UTC
```

Use a new work directory for each comparison. The helper projects
the same backup through the engine image into a separate stream. The publisher
image ingests the feed and checks its complete, fresh, live head. Each
implementation gets copied state and the same inventory, clock and media
caches. The old process tree uses Bubblewrap network isolation. The publisher
image runs with disabled networking and a read-only root filesystem; only its
isolated rendering directory is writable during simulation. It receives no host
source, dependency directories, engine journal, receiver archives or credentials.
No Ghost, SSH or paid request is possible inside either simulation.

Retained IDs, URLs, ownership, history, withdrawals, research bindings, facts,
media and intended Ghost actions must match. Refreshed current sources pass
through the engine's reviewed projection outside the publisher process. Prior
history stays exact except for added engine bindings. New media caches are
checked against the same projected source. New candidates are matched by factual
identity because their first article IDs are random. Zero missing map backgrounds
and successful build/privacy checks are required. Results stay private.
If a build subprocess fails, its return code and bounded stderr are saved in the private
build directory's `subprocess-failure.json`. Preserve that failed checkpoint;
a later successful diagnostic does not turn it into a passing sample.

Dry runs do not import new feed data. Ingest explicitly first. Only autopilot
`--dry-run`, writer `--dry-run` and weekly `--plan` are read-only policy checks.
`--no-deploy` and `production: false` still permit real Ghost writes.

## Shadow operation

Run the native engine in separate state, stream, feed and inbox directories.
Give it the same receiver and reviewed references, but no AI or FlightAware
credentials. It must not write the production journal, site or ledger. The old
collector and publishers remain active throughout this phase.

Measure initial transfer, cold and warm projection, peak memory and disk growth.
Set memory limits with room for live collection and output workers. Shutdown
grace must exceed the worst projection plus worker shutdown. Require a first
receiver poll within two minutes. Start the soak after initial delivery and a
second fresh pass.

Every 30 minutes, record process health, receiver and analysis timestamps,
stream/generation, delivery and import cursors, quarantines, resource use and an
old/new comparison. Require 24 continuous hours crossing UTC midnight, with no
gap over 35 minutes. A failed checkpoint, missing interval or artifact change
starts a new clean window. An old failure does not permanently block recovery.
Never promote shadow state into production: provider options are part of the
engine's durable configuration binding.

`tools/check_cutover_shadow.py --config .private/cutover/shadow.json` captures
these checkpoints. Its configuration pins the three checkout roots, approved
artifact hash, production configuration/journal, shadow container and both image
IDs, feed/inbox and evidence directory. Keep the shadow inbox named
`inbox.sqlite3` inside a dedicated directory: that directory is mounted into the
publisher image for imports. The bundled shadow service and timer only
observe; a successful gate does not activate production automatically. They run
at :20 and :50 UTC: after the :10 and :40 writer pass, and ten minutes ahead of
the :00 and :30 autopilot, so a checkpoint's sandboxed legacy build does not
render social images while the autopilot renders a new story's media in its own
browser. Inspect private
`status.json` and each checkpoint report before final handoff. Keep enough disk
space for full rehearsal copies throughout the window.

Copy [examples/cutover-shadow.json](../examples/cutover-shadow.json) to the private
configuration and replace all paths and image IDs. Include every runtime
configuration, AI environment file, private reference pin, and the shadow
Compose file with its `.env` in `configuration_files`. After checks and both
reviews pass, obtain the source hash with the same command plus
`--print-artifact-hash` and save it as `approved_artifacts_sha256`. Changes
invalidate the window. The hash covers the source trees and skips dependencies,
caches, private and runtime state, build output, local tool caches, and the
sync tool's report directory and staged `*.sync-merge` files. Each checkpoint copies the journal
and the publisher state; the default free-space guard holds further copies
below 20 GiB. Stop the shadow timer after collecting the
gate evidence and preserve it through final handoff. It never prunes evidence.

Packaging changes start a new acceptance window; host-only publisher samples do
not qualify an image. Failed readiness commands save bounded private stdout,
stderr, return status and container identity. A brief commit/delivery gap may be
sampled again only while analysis stays fresh and complete, output cursors
advance, and stream identity stays unchanged. The observer requires an actual
ready result within 30 seconds. Stale analysis, failed output or stalled progress
fails the checkpoint. Saved failed samples remain failed.

Exercise isolated failure recovery: interrupted journal transfer, stale or
missing feed, output backlog, repeated delivery, cold restart, uncertain
spending reservations and the persistent service activation fence. Validate
saved weekly responses offline. Preserve evidence failures, receipts and retry
state rather than buying replacement calls during testing.

## Fence and final handoff

Keep the previous deployment's publish, writer, and weekly service names and
one timer set. If its weekly service starts autopilot through `ExecStartPost`,
stopping timers alone is insufficient. Install this condition on all three
services before draining:

```ini
[Unit]
ConditionPathExists=!/srv/wtf-adsb-publisher/control/activation.hold
```

Create the file durably, reload systemd, stop timers and let current work finish.
Verify no `MainPID`, `ControlPID` or pending job remains and every service is
inactive or failed. Do not kill a paid request to meet a deadline. The 30-minute
transfer deadline begins after draining; weekly may still have a 45-minute
request. Preserve failure evidence and reservations.

For container jobs, also use the [runner's drain check](container-jobs.md).
It accounts for labeled containers and shared state locks. Stopping the Docker
client does not stop its container. The runner reconciles exit receipts and
uncertain outcomes before another job starts. Overdue thresholds are alerts;
they do not kill paid work.

Also stop any previous collector container that runs its own AI or FlightAware
workers. Take final consistent copies of its journal and the complete publisher
state, a Ghost inventory and the existing static release. Retain a standalone
read-only journal archive for saved private notes and drafts at its capture
time. The new factual viewer serves these only in its archive namespace.

Use the [state and budget migration commands](migration.md) and the engine's
`migrate-journal` command for fresh production state. Whole-tree hashes and a
distinct receipt verify publisher copies. Budget migration retains completed,
cumulative and uncertain spending and leaves the new store paused, including on
failure. Keep FlightAware accounting in the journal and preserve production caps.

Configure the production native input with FlightAware enabled and its final
`flightaware_pause_file` path from its first start. Create that hold before startup
and keep it present through the final comparison. The engine checks it before
credentials or spending reservations, including direct enrichment calls. The
publisher AI pause does not pause FlightAware. Remove this separate hold only
after the commitment receipt; do not change bound provider options to activate it.

Install the engine and factual viewer into the parent receiver deployment.
Preserve the viewer's private route and any metric endpoint your monitoring
reads. Update both health
observers for the separate services. Give each application only its own required
provider credentials. Keep private reference data out of public distributions.

Stage the [publisher image configuration](docker.md), then replace the three
user services while the fence remains present. Verify exactly
one `ExecStart` per service and no old paths in executable, working directory,
PATH or environment files. A drop-in `ExecStart` replacement must first clear
the old list. Preserve schedules. The container runner handles the weekly
follow-up after confirmed success; remove the old `ExecStartPost` so it does not
also launch autopilot. Install the reconcile-only startup service without
enabling a second publisher timer set.

Start the new engine, verify a receiver poll within two minutes, import the full
production feed and require readiness plus a caught-up inbox. Run final offline
comparison and writer/weekly plans with final copied state and inventory. Verify
archive immutability, private viewer/browser behavior, retained URLs, media and
corrected mismatch articles before external activation.

## Activation and recovery boundary

Before any real Ghost attempt, deployment or paid reservation, persist an
exclusive commitment receipt with artifact, configuration, inventory, state,
spending and feed hashes. Its existence prohibits automatic rollback even when
a request's outcome is unknown. Remove the fence only at the verified release
point, resume the migrated budget according to retained settings, run one real
publication pass and enable the single timer set. Stories remain unattended.

Before commitment, a failed transfer may return to the old runtime. Stop the new
collector first and copy its latest compatible journal back through SQLite's
backup API so new observations and FlightAware accounting survive. Keep AI paused
until one old worker set and the correct accounting store are verified. After
commitment, preserve current state and repair forward against Ghost inventory.
Restoring a stale ledger can duplicate posts or erase owner edits. Keep the last
checked static release serving during repair.

Observe production for another 24 hours, including UTC rollover: collection,
freshness, cursor continuity, service failures, site routes, publication counts,
ownership and spending. Retain old code and recovery artifacts throughout.
Record the actual activation and verification times.
