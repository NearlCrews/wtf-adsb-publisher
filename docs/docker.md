# Publisher image

The Linux x64 image includes Python, Node, Chromium, SSH, the publisher, and its
site assets. Docker builds it from this repository's source and lockfiles. The
host does not need an application Python environment or Node installation to
build or run it. The systemd job runner uses the host's standard-library Python.

## Build and check

From this repository, with Docker Engine, Compose, GNU Make, and host Python 3.11
or newer installed:

```sh
make image
make test-image
docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local
```

Builds download pinned base images, locked dependencies, and the
checksum-verified browser. Image checks use constructed fixtures and disabled
networking. Source checks still use the [development
setup](../DEVELOPMENT.md#development-setup). CI runs both sets of checks; only
the release workflow pushes an image. `test-image` uses host `python3`; set
`HOST_PYTHON` to select another standard-library Python interpreter.

Record the image ID, source hash, dependency locks, and build verification before
deployment. Set `PUBLISHER_IMAGE` to the full local `sha256:` image ID. Jobs never
pull a replacement. Keep the accepted image while a new candidate is checked.
The repositories' version audits check current compatible stable dependencies;
refresh pins deliberately and run checks before changing a deployed image.
GScan requires the supported Node 24 line. Images target Linux x64; ARM64 is not
qualified by these browser checks.

The image commands are `publish`, `write`, `weekly`, and `admin`. Each forwards
arguments to the matching publisher command. Invoking the image without a
command displays help. For example:

```sh
docker run --rm --network none wtf-adsb-publisher:local
docker run --rm --network none wtf-adsb-publisher:local admin plugins
docker run --rm --network none wtf-adsb-publisher:local publish --help
```

`publish --dry-run`, `write --dry-run`, and `weekly --plan` perform read-only
policy checks. Through the Compose file, the service name comes before the
command:

```sh
docker compose --env-file /srv/wtf-adsb-publisher/config/compose.env -f deploy/container/compose.yaml \
  run --rm write write --config /config/publisher.json --dry-run
```

`--no-deploy` still permits Ghost writes. Use isolated copies of
state and saved inventory for rendering comparisons.

## Storage and credentials

The [job Compose file](../deploy/container/compose.yaml) defines three explicitly
invoked jobs using one image. Copy its [environment example](../deploy/container/.env.example)
into a protected directory and replace every placeholder with an absolute host
path. Set the publisher JSON paths to their container destinations:

| Container path | Contents and access |
| --- | --- |
| `/config/publisher.json` | Publisher configuration, read-only |
| `/config/private-terms.txt` | Optional private terms file from `PUBLISHER_PRIVATE_TERMS_FILE`, read-only |
| `/config/about.md` | Optional About page text from `PUBLISHER_ABOUT_FILE`, read-only |
| `/state` | Complete publisher state, read/write |
| `/state/inbox.sqlite3` | Imported JSON evidence and cursor |
| `/state/budget.sqlite3` | Shared spending and uncertain reservations |
| `/feed` | Engine JSON output, read-only |
| `/references` | Optional operator-supplied reference files from `PUBLISHER_REFERENCE_DIR`, read-only |
| `/app/artifacts/ghost-theme` | Persistent media caches and checked releases |
| `/control/activation.hold` | Persistent launch hold, visible through a read-only directory mount |
| `/run/secrets` | Individually mounted provider keys, read-only |
| `/home/publisher/.ssh` | Scoped SSH configuration, identity, and verified host keys |

The [example configuration](../examples/publisher.json) holds host paths. In
the copy the jobs mount, set `feed_directory: /feed`, `state_dir: /state`,
`inbox_database: /state/inbox.sqlite3`, and `budget_database: /state/budget.sqlite3`.
With `PUBLISHER_REFERENCE_DIR` set, add `reference_dir: /references`; the
[reference data notes](../src/wtf_adsb_publisher/data/README.md#reference-data)
list the files it holds. Left empty, an empty named volume takes its place and
the built-in rules apply. Create the directory before use, since Compose creates
a missing one as an empty root-owned directory. Keep whole database directories
mounted so WAL files and lock files are shared by all jobs. Keep host
directories owned by UID/GID 1000, private directories mode 0700, and credentials
mode 0600. The image contains a named user for SSH. Mount the deployment
identity only; do not mount an operator's whole home. Preserve any existing
`HostKeyAlias` used by the verified known-hosts records. Require strict SSH
host-key verification, and verify private network DNS and routing (Tailscale,
for example) and read-only Ghost/SSH access from the image before activation.
The publish job also looks up uncached watchlist entry links on Wikipedia and
Wikidata over public HTTPS (`entity_context.resolve` in `publication_pass`), so
verify outbound HTTPS to `en.wikipedia.org`, `www.wikidata.org`, and the
`w.wiki` short-link host from the image as well; without it the pass reports
`entity_lookup_failed` and stories use only cached answers.

Application code and root filesystems are read-only. Jobs drop capabilities,
publish no ports, and have no Docker socket or engine journal. Initial publisher
limits are 4 GiB memory, 512 MiB temporary storage, and 256 MiB shared memory.
Measure cold rendering and simultaneous scheduled work before accepting these
limits for another deployment. The Docker daemon and host job runner manage
container lifecycle.

## Schedule and recover

Follow [container jobs](container-jobs.md) to configure the runner and the three
host timers for publication, writing, and weekly research. Installing templates
does not authorize a production cutover; a site moving from a previous
collector must complete the [cutover procedure](cutover.md).

The runner preserves run identities and completion receipts, reports overdue
jobs, and reconciles existing containers before launching work. An inactive
systemd service does not establish that its container stopped. Do not use
`docker compose down`, forced removal, or a stale state restore to recover an
uncertain paid request or publication. Fence new launches, drain containers and
shared locks, and preserve the current ledger and reservations.

Back up the complete stopped state, rendering artifacts, private configuration,
credentials, the runner's job directory (intents, receipts, and pending
follow-ups), activation hold, and accepted image identity together. The engine
owns its own state and backups. Ghost and the serving host keep a separate
backup procedure ([Ghost backup template](../deploy/ghost/backup.sh)). After the
commitment receipt described in the cutover guide, reconcile against Ghost and
repair forward using current publisher state.

## Plugins and distribution

Build a derived image to add trusted, pinned plugin packages, then enable them
through the [plugin configuration](plugins.md). Preserve the base image
identity and pin the added packages and dependencies. Run dependency and plugin
checks plus image qualification on the derived image. Installing or upgrading
plugins at container startup would change the accepted artifact.

Build contexts exclude private configuration, credentials, databases, caches,
and receiver archives. Runtime state is supplied through mounts. Keep component
licenses and notices with redistributed images; the application license does
not replace Chromium, Python, Node, Debian, or dependency licenses. `make image`
builds a local image. A `v*` tag runs the release workflow, which publishes
`ghcr.io/<owner>/wtf-adsb-publisher` with the version tag and attaches the
image's `build.json` inventory to the GitHub release. Only after that release
exists does it move `latest`, and it never moves `latest` for a pre-release.
