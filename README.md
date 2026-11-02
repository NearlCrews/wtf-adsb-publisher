# WTF ADS-B Publisher

WTF ADS-B Publisher reads the JSON feed from
[WTF ADS-B Engine](https://github.com/NearlCrews/wtf-adsb-engine), picks the
observations worth a story, writes them with an OpenAI model under a daily
budget, and publishes them to a Ghost site and a checked static copy. It ships
its own Ghost theme with maps and flight replays. Example deployment:
[wtf-adsb.com](https://wtf-adsb.com).

Three user systemd timers publish, write stories, and research a weekly
feature, each through a short container job. One daily cap covers every AI
request, and stories that pass the evidence and privacy checks publish without
an approval step. Upkeep is checking your spending and upgrading the image.

To have an AI assistant set it up, open this repository in Claude Code or a
similar harness and say "set this up"; [AGENTS.md](AGENTS.md) guides it.

## What you need

- Linux x64 with Docker and Compose, GNU Make, Python 3.11 or newer, and user
  systemd, run as a user whose UID and GID are 1000 (the image's account).
- A running engine whose feed directory is on this host.
- A Ghost site with an Admin API key (a custom integration in Ghost's settings)
  and an SSH host that serves the static copy. [Hosting the site](docs/hosting.md)
  lists what the host needs.
- An OpenAI API key for the writers, the only paid service. Without one, set
  `AI_ENABLED=false` in `ai.env` and leave the key file empty: stories still
  publish in their deterministic wording.

## Quick start with Docker

The timer templates expect the checkout at `~/src/wtf-adsb-publisher` and the
configuration under `/srv/wtf-adsb-publisher`, as below.

```sh
git clone https://github.com/NearlCrews/wtf-adsb-publisher ~/src/wtf-adsb-publisher
cd ~/src/wtf-adsb-publisher
make image
P=/srv/wtf-adsb-publisher
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" $P
install -d -m 0700 $P/config $P/state $P/artifacts $P/control $P/secrets $P/ssh
install -m 0600 examples/publisher.json $P/config/publisher.json
install -m 0600 deploy/ghost-preview/ai.env.example $P/config/ai.env
install -m 0600 deploy/container/.env.example $P/config/compose.env
cat > $P/config/jobs.json <<EOF
{"version": 1, "project": "wtf-adsb-publisher",
 "compose_file": "$PWD/deploy/container/compose.yaml",
 "env_file": "$P/config/compose.env", "job_directory": "$P/config/jobs",
 "activation_hold": "$P/control/activation.hold",
 "publisher_state_directory": "$P/state", "budget_database": "$P/state/budget.sqlite3",
 "configuration_files": ["$P/config/publisher.json", "$P/config/ai.env",
  "$P/secrets/ghost-admin-key", "$P/secrets/openai-api-key",
  "$P/ssh/config", "$P/ssh/id_ed25519", "$P/ssh/known_hosts"]}
EOF
```

Then fill in these files under `$P`:

- `config/publisher.json`: the example holds host paths, so change them to the
  paths the jobs see: `feed_directory: /feed`, `state_dir: /state`,
  `inbox_database: /state/inbox.sqlite3`, and
  `budget_database: /state/budget.sqlite3`. Keep the `/run/secrets`, `/config`,
  and `/references` paths it already has. Set `ghost_origin`,
  `preview_ssh_host` (the `Host` alias in `ssh/config`), `live_since` (when
  publishing starts, in UTC), `site_origin`, and `site_name`, and replace or
  delete `contact_email`, `author_name`, `site_tagline`, `site_description`,
  and `support_link`. With `production` false, the build makes noindex pages.
- The example also keeps `private_terms_file` and `about_file`. Delete
  `private_terms_file` unless you mount a terms file (one expression) through
  `PUBLISHER_PRIVATE_TERMS_FILE` in `compose.env`; literal names can go in
  `private_terms`. Delete `about_file` unless you mount your own Markdown About
  text through `PUBLISHER_ABOUT_FILE`. List a mounted file in
  `configuration_files` in `jobs.json`.
- `config/ai.env`: `AI_DAILY_BUDGET_USD` is the daily cap (default $1.00); keep
  it at or above `AI_PUBLIC_RESERVE_USD`, or the writers refuse to start.
- `config/compose.env`: `PUBLISHER_IMAGE` is the ID that
  `docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local` prints,
  and `ENGINE_FEED_DIR` is the engine's feed directory.
- The keys in `secrets/` and `ssh/`, mode 0600, and an `ssh/config` that names
  the paths the publish job sees:

```text
Host site
  HostName site.example.ts.net
  User deploy
  IdentityFile /home/publisher/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking yes
  UserKnownHostsFile /home/publisher/.ssh/known_hosts
```

Initialize the state and its shared lock files, check the runner, and start the
timers. Use the `live_since` date for `--available-from`:

```sh
docker compose --env-file $P/config/compose.env -f deploy/container/compose.yaml \
  run --rm admin admin init --state-dir /state --available-from 2026-11-01
install -d -m 0700 $P/state/writer $P/state/weekly
touch $P/state/writer/status.json.lock $P/state/weekly/status.json.lock \
  $P/state/ledger.json.lock $P/state/budget.ai.lock
python3 tools/run_container_job.py --config $P/config/jobs.json status
mkdir -p ~/.config/systemd/user
cp deploy/ghost-preview/wtf-adsb-{autopilot,writer,weekly}.* \
  deploy/ghost-preview/wtf-adsb-publisher-reconcile.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable wtf-adsb-publisher-reconcile.service
systemctl --user enable --now wtf-adsb-autopilot.timer wtf-adsb-writer.timer wtf-adsb-weekly.timer
sudo loginctl enable-linger "$USER"
```

To run the jobs from a source checkout instead, see
[run without Docker](DEVELOPMENT.md#run-without-docker).

## Upkeep

Check spending on your OpenAI usage page and the jobs with `status`. To
upgrade, drain the jobs, rebuild, record the new image ID, and lift the hold:

```sh
P=/srv/wtf-adsb-publisher
python3 tools/run_container_job.py --config $P/config/jobs.json drain
git pull && make image
sed -i "s/^PUBLISHER_IMAGE=.*/PUBLISHER_IMAGE=$(docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local)/" \
  $P/config/compose.env
rm $P/control/activation.hold
```

If the pull changed the unit templates in `deploy/ghost-preview/`, copy them
again and run `systemctl --user daemon-reload` before you remove the hold.

## More

The [documentation index](docs/README.md) links every guide, and
[DEVELOPMENT.md](DEVELOPMENT.md) covers development, checks, image builds,
plugins, and releases. For help, read [SUPPORT.md](SUPPORT.md); report
vulnerabilities through [SECURITY.md](SECURITY.md). The code, documentation,
contracts, and theme use the [MIT license](LICENSE). Dependencies, fonts,
photographs, and data keep their own terms; see the
[third-party notices](THIRD_PARTY_NOTICES.md) and [authors](AUTHORS.md).
