# Setup interview

These are the questions an AI assistant asks before it configures the publisher
with `tools/setup.py`. A person can answer them just as well by writing the
answers file by hand. [AGENTS.md](../AGENTS.md) describes the full protocol.

Ask the groups in order, one message per group, and show each default. Leave a
key out of the answers file, or set it to `null`, to accept its default.
`python3 tools/setup.py plan` prints the same questions as JSON.

## Where the answers go

The user names one private directory outside this repository, such as
`/srv/wtf-adsb-publisher/config`. It becomes the configuration directory. It
holds the answers file, `publisher.json`, `ai.env`, `compose.env`, `jobs.json`,
`setup.json`, `ssh_config`, the rendered systemd units, and the job runner's
`jobs` directory. Compose mounts only `publisher.json` from it, read-only, at
`/config/publisher.json`. Key files can live anywhere outside the state,
artifacts, control, and feed directories; the setup tool records their paths and
checks their owner and mode, but never reads them.

`configure` creates the configuration directory with mode 0750 and the state,
artifacts, and control directories with mode 0700. It writes `publisher.json`,
`ai.env`, `compose.env`, and the units with mode 0640, `jobs.json` and
`setup.json` with mode 0600, and gives an answers file kept in the configuration
directory mode 0600. `ssh_config`, the empty placeholder `no-openai-key`, and the
activation hold get mode 0600 and belong to `uid`, because SSH in the container
and the container's job checks read them as that account. When run with `sudo`,
configure gives the state, artifacts, and control directories to `uid` and
`gid`. The configuration directory and its other files stay with the account
that ran `sudo` (root when there is none), with group `gid`, so the container
can read `publisher.json` but cannot change anything root reads later. Under
`sudo` the answers file must belong to root or to the account that ran `sudo`.

Every path must be at least two levels below `/`, even after following symbolic
links, outside this repository, and must not contain it. configure changes the
mode or owner of an existing directory only when it is empty, holds only the
files configure itself writes or the answers name, or already has the mode and
owner configure would give it. Under `sudo` it also refuses an existing
directory that belongs to neither `uid` nor the account that ran `sudo`, and a
directory with a parent that belongs to another account or that group or others
can write. A root-owned sticky directory such as `/tmp` is allowed as a parent.

`check` and `deploy` refuse when the configuration directory, `compose.env`, or
`setup.json` belongs to an account other than root, the account running the
tool, or the account that ran `sudo`, or when group or others can write it.
`deploy` writes `compose.env` and `jobs.json` again from the validated
`setup.json` and installs units rendered from it, not the copies on disk. It
installs user units, so run it as your own account, without `sudo`.

The job runner runs as your account under user systemd. It hashes every key and
configuration file and opens the lock files in the state directory, so `uid`
and `gid` must be your own account's numbers (`id -u` and `id -g`), and `check`
fails when `uid` is another account's. The image's account is 1000:1000, and SSH
in the publish job needs an account with the container's uid, so another uid
needs a derived image that adds one.

configure also creates the activation hold, `activation.hold` in the control
directory, the first time it runs and whenever it changes a job input.
`deploy` creates it too before it changes `compose.env` or `jobs.json`. While
the hold exists, the timers skip their runs and the jobs refuse to start. Remove
it yourself after `check` passes, then run `deploy --start`.

The answers file is one JSON object keyed by the names below:

```json
{
  "feed_dir": "/srv/wtf-adsb/feed",
  "site_origin": "https://example.com",
  "live_since": "2026-11-01T00:00:00Z",
  "private_terms_file": "/srv/wtf-adsb-publisher/secrets/private-terms.txt",
  "ghost_origin": "https://ghost.example.ts.net",
  "ghost_key_file": "/srv/wtf-adsb-publisher/secrets/ghost-admin-key",
  "ssh.host": "site",
  "ssh.hostname": "site.example.ts.net",
  "ssh.user": "deploy",
  "ssh.identity_file": "/srv/wtf-adsb-publisher/ssh/id_ed25519",
  "ssh.known_hosts_file": "/srv/wtf-adsb-publisher/ssh/known_hosts",
  "image": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

## Feed

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `feed_dir` | path | `/srv/wtf-adsb/feed` | An absolute path at least two levels below `/`, outside this repository and not containing it, using letters, digits, `.`, `_`, `-`, and `/`. configure never creates or changes it; `check` requires a `manifest.json` in it. | The engine's feed directory. Every job mounts it read-only at `/feed`, and the publisher imports new batches from it. |

## Directories

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `state_dir` | path | `/srv/wtf-adsb-publisher/state` | The same rules as `feed_dir`. It must be separate from the configuration, artifacts, control, feed, and reference directories and not inside one of them. | Holds the article ledger, the evidence inbox, the spending store, and their lock files, mounted at `/state`. Back it up. |
| `artifacts_dir` | path | `/srv/wtf-adsb-publisher/artifacts` | The same rules as `state_dir`. | Keeps the built site, verified media, social images, and map caches between runs. |
| `control_dir` | path | `/srv/wtf-adsb-publisher/control` | The same rules as `state_dir`. | Holds the activation hold. Jobs see it read-only at `/control`, and the units skip their runs while the hold exists. |
| `reference_dir` | path | none | Optional. The same rules as `state_dir`, except that the engine's reference directory is allowed. configure never creates or changes it; `check` requires it to exist. | The operator-supplied reference files, mounted read-only at `/references`; see [reference data](../src/wtf_adsb_publisher/data/README.md#reference-data). Without it, the built-in rules apply. |

## Site

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `site_origin` | url | `https://example.com` | An `https://` origin in lower case with no port, path, or trailing slash, whose last host label starts with a letter, so no IP address. It must be the real address when `production` is true. | Canonical links, feeds, sitemaps, and structured data name this origin. |
| `site_name` | string | none | Optional, 1 to 60 characters on one line with no leading or trailing spaces; required when `production` is true. configure writes it only when set. | The site's name in the footer, page titles, and social cards. Without it, the build uses a generic name. |
| `site_tagline` | string | none | Optional, 1 to 120 characters on one line with no leading or trailing spaces. configure writes it only when set. | The heading on the home page. |
| `site_description` | string | none | Optional, 1 to 160 characters on one line with no leading or trailing spaces. configure writes it only when set. | The line under the home heading, in the footer, and in the home page's meta description. Without it, the theme's neutral default applies. |
| `author_name` | string | none | Optional, 1 to 60 characters on one line with no leading or trailing spaces, with at least one letter or digit from A to Z or 0 to 9. configure writes it only when set. | The name on the author page and story bylines; its lower-case, hyphenated form is the author page's address. Without it, "the site". |
| `about_file` | path | none | Optional. The same path rules as `feed_dir`, outside the state, artifacts, control, and feed directories. A Markdown file, which may hold HTML blocks, of 1 to 65536 bytes of UTF-8 text that the container account can read; `check` loads it. | The About page's opening text, such as who runs the receiver and with what. Compose mounts it read-only at `/config/about.md`. Without it, About keeps a generic introduction that names no place, hardware, or host. |
| `support_link.url` | url | none | Optional. An `https://` address with a lower-case host name, no port, user, or IP address, and up to 2000 characters; set it together with `support_link.label`. configure writes both as `support_link` only when set. | A support or donation link. The page partial renders only when it is set. |
| `support_link.label` | string | none | Optional, 1 to 40 characters on one line with no leading or trailing spaces; set it together with `support_link.url`. | The link's text. |
| `contact_email` | email | none | Optional. A plain address such as `contact@example.com`, with no name or angle brackets. | The footer and the structured data show it only when set. |
| `production` | boolean | `false` | `true` only after the user confirms the site is public. | With `false`, the build makes noindex preview pages. |
| `live_since` | timestamp | none (required) | An ISO 8601 time with an explicit offset, such as `2026-11-01T00:00:00Z`. | Publication starts with observations from this time. Its UTC date also starts the ledger. |
| `private_terms_file` | path | none | Optional. The same path rules as `feed_dir`. A file with one regular expression on one line, owned by `uid` with no group or other permissions; `check` loads it. | Names, users, and hosts no public text may contain. Compose mounts it read-only at `/config/private-terms.txt`. |
| `private_terms` | list | `[]` | A list of literal strings, each 1 to 200 characters on one line with no leading or trailing spaces. | More literal terms refused in public text, alongside the file, by the Python checks. |

## Ghost

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `ghost_origin` | url | none (required) | An `https://` origin with no user name, password, path, query, or fragment. Keep it on a private network. | The Ghost Admin API address. Readers never see it. |
| `ghost_key_file` | path | none (required) | The same path rules as `feed_dir`, outside the state, artifacts, control, and feed directories. `check` requires a regular file owned by `uid` with no group or other permissions (0600 or 0400). Neither tool reads the key. | The Admin API key from a Ghost custom integration, given to the publish job as a Compose secret. Ask for the path only, never the key. |

## Delivery

See [hosting the site](hosting.md) for what each option needs from the host.

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `deploy_target` | choice | `ghost-ssh` | `ghost-ssh` or `ghost`. A static host that takes an upload instead of SSH needs a deployer plugin, and none ships. | `ghost-ssh` records stories in Ghost and sends the checked static release over SSH. `ghost` records them in Ghost only, with no static release, for when Ghost serves the site or the static host is not ready. |
| `ssh.host` | string | none | Required for `ghost-ssh`. A letter or digit, then letters, digits, `.`, `_`, or `-`. | The SSH host alias the publish job connects to. It also names the destination in the release fingerprint. |
| `ssh.hostname` | string | none | Optional. A host name or IP address. Without it, `ssh.host` is the host name. | The real address behind the alias, written to `ssh_config`. |
| `ssh.user` | string | none | Required for `ghost-ssh`. A lowercase letter or `_`, then up to 31 lowercase letters, digits, `_`, or `-`. | The account on the static host that runs the release receiver with `sudo -n`. |
| `ssh.identity_file` | path | none | Required for `ghost-ssh`. The same path rules as `ghost_key_file`. `check` requires a regular file owned by `uid` with no group or other permissions. | The deployment's private key, mounted into the publish job. Ask for the path only. |
| `ssh.known_hosts_file` | path | none | Required for `ghost-ssh`. The same path rules as `ghost_key_file`. A regular file that group and others cannot write. | The verified host keys. The job uses strict host key checking. |
| `ssh.host_key_alias` | string | none | Optional. The same rules as `ssh.host`. | Keep an existing `HostKeyAlias` when the verified key is stored under that name. |

## AI

Paid writing is off by default. Turn it on only when the user says yes.

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `ai.enabled` | boolean | `false` | `true` only after the user says yes to paid OpenAI requests. | With `false`, stories still publish in their deterministic wording, and the writers make no paid request. |
| `ai.public_model` | choice | `gpt-6.1-sol` | `gpt-6-luna`, `gpt-6-sol`, `gpt-6.1-sol`, or `gpt-6-astra`, the models with a pinned price. | The model for public story prose and research. |
| `ai.reasoning_effort` | choice | `low` | `low`, `medium`, or `high`. | More effort costs more per story. |
| `ai.daily_budget_usd` | number | `1.00` | 0 to 1000, and at least `ai.public_reserve_usd`. | One cap for every AI request in a UTC day. The writers wait for the next day once it is spent. |
| `ai.public_reserve_usd` | number | `0.75` | 0 to 1000, no more than the daily budget, and at least the largest public request reservation for the chosen model and effort ($0.595 for `gpt-6.1-sol` at `low`); `check` computes it. | Held for public prose and research, so other requests stop at the cap less this amount. |
| `openai_key_file` | path | none | Required when `ai.enabled` is true. The same path rules and `check` rules as `ghost_key_file`. Without it, configure points Compose at an empty `no-openai-key` file. | The OpenAI API key, given to the two writers as a Compose secret. Ask for the path only, never the key. |

## Schedule

The publish and write timers leave ten minutes between runs, so a story written
at :10 publishes at the next half hour.

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `schedule.publish` | calendar | `*-*-* *:00,30:00` | A systemd `OnCalendar` expression of letters, digits, spaces, and `*`, `,`, `:`, `.`, `/`, `~`, or `-`. `check` runs `systemd-analyze calendar` on it when that command exists. | When the publish job imports the feed, publishes to Ghost, and delivers the site. |
| `schedule.write` | calendar | `*-*-* *:10,40:00` | The same rules as `schedule.publish`. | When the writer upgrades story prose within the AI budget. |
| `schedule.weekly` | calendar | `*-*-* 06:10:00 UTC` | The same rules as `schedule.publish`. | When the weekly researcher picks and researches features, with daily catch-up. |

## Runtime

| Key | Type | Default | Validation | Why |
| --- | --- | --- | --- | --- |
| `image` | string | none (required) | A local image ID: `sha256:` and 64 lowercase hex digits. `docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local` prints it after `make image`. | Jobs never pull an image, and the runner records this ID in every receipt. |
| `uid` | integer | `1000` | 1 to 4294967294, and your own account's number; `check` fails otherwise. `check` warns when it is not 1000, the image's account. | The jobs run as this user, which owns the state, artifacts, and control directories. |
| `gid` | integer | `1000` | 1 to 4294967294. | The jobs' group. Through it the container reads `publisher.json`. |
| `memory_limit` | string | `4g` | A whole number followed by `b`, `k`, `m`, or `g`. | The Compose memory limit for each job. Cold rendering needs about 4 GiB. |
