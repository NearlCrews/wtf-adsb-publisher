# Setting up WTF ADS-B Publisher with an AI assistant

This file is for an AI assistant that a user has opened in this repository.
When the user asks you to set the publisher up, follow this protocol. It
configures the scheduled container jobs in `deploy/container/compose.yaml`,
checks them, and installs their user systemd timers. AGENTS.md and CLAUDE.md
hold the same text, for harnesses that read either name.

## Setup protocol

1. Read [docs/setup-interview.md](docs/setup-interview.md). It lists every
   question with its key, type, default, validation rule, and reason.
   `python3 tools/setup.py plan` prints the same list as JSON.
2. Confirm the prerequisites: Linux on x86-64, Docker with the Compose plugin,
   GNU Make, Python 3.11 or newer, user systemd, a running engine whose feed
   directory is on this host, and a Ghost site with an Admin API key. Read
   [hosting the site](docs/hosting.md) with the user if the site has no home
   yet. If the image does not exist, `make image` builds
   `wtf-adsb-publisher:local`, and
   `docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local` prints
   the ID the `image` answer needs.
3. Ask the user to name a private directory outside this repository for the
   configuration, such as `/srv/wtf-adsb-publisher/config`. It holds the
   answers and the generated files.
4. Ask the interview groups in order: Feed, Directories, Site, Ghost,
   Delivery, AI, Schedule, and Runtime. Send one message per group. Show each
   question's default and accept "defaults" as an answer. Validate each answer
   against the rule in the document and ask again when one fails. Set `uid`
   and `gid` to the numbers `id -u` and `id -g` print for the account that will
   own the timers. For `about_file`, ask whether the user has written their own
   About page text, and if so, for the path of that Markdown file; without it,
   the page keeps a generic introduction.
5. For a key, ask only for the path of a file the user has already created
   with mode 0600. This covers the Ghost Admin API key, the OpenAI API key, and
   the SSH identity. Never ask for, accept, or repeat a key itself. If the user
   pastes one, tell them not to, do not store or echo it, and ask them to put
   it in a file themselves.
6. Write the answers as one JSON object to `answers.json` in the private
   directory. Create the directory first if it does not exist; it needs no
   particular mode, because configure sets it to 0750. If its parent needs
   root, such as `/srv`, offer
   `sudo install -d -o "$(id -u)" -g "$(id -g)" /srv/wtf-adsb-publisher` first.
7. Run `python3 tools/setup.py configure --answers DIR/answers.json --output DIR`,
   where `DIR` is the private directory. Read every warning in the result to
   the user. configure also creates the activation hold, so no job runs yet.
8. Run `python3 tools/setup.py check --output DIR`. It validates
   `publisher.json` with the publisher's own loader when this checkout can
   import it, checks the directories, owners, and modes, runs
   `docker compose config`, and makes read-only probes: Ghost's public site
   endpoint, `ssh ... true` on the static host when delivery uses SSH, and the
   feed's `manifest.json`. Fix each failed check with the user, then run check
   again until `ok` is true.
9. Run `python3 tools/setup.py deploy --output DIR` as the user's own account,
   without `sudo`. It installs the units into `~/.config/systemd/user/`,
   initializes new state, enables the startup reconciler, and prints the job
   runner's status. It does not start the timers.
10. Report the status to the user. Ask whether to start publishing. Only after
    a clear yes, ask the user to remove the hold themselves with
    `rm CONTROL/activation.hold`, where `CONTROL` is the control directory, then
    run `python3 tools/setup.py deploy --output DIR --start`. It runs check
    again and enables the publish, write, and weekly timers only when check
    passes and the hold is gone. Offer
    `sudo loginctl enable-linger "$USER"` so the timers keep running after the
    user logs out.

Every subcommand prints one JSON object and exits nonzero on failure. Rerunning
configure with the same answers changes nothing. When configure changes a job
input, it sets the hold again, and deploy sets it too before it rewrites
`compose.env` or `jobs.json` with new content, so changes never reach a job
before the user lifts it. configure refuses to take over an existing directory that holds other
files unless it already has the mode and owner configure would give it.
`deploy --print-only` shows the commands without running them.

## Upkeep to tell the user

In these commands, `DIR` is the private directory and `CONTROL` is the control
directory.

- Status: `python3 tools/run_container_job.py --config DIR/jobs.json status`.
  Check spending now and then on the OpenAI usage page.
- Upgrade: drain the jobs with
  `python3 tools/run_container_job.py --config DIR/jobs.json drain`, which
  leaves the hold in place. Update the checkout, run `make image`, put the new
  ID from `docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local`
  in the `image` answer, and run configure, which updates `PUBLISHER_IMAGE` in
  `compose.env`. Run check and deploy, then remove `CONTROL/activation.hold`.
- Change settings: drain first, edit `DIR/answers.json`, then run configure,
  check, and deploy again, and remove the hold.

## Never do these

- Commit, paste, or upload the answers file, `publisher.json`, `ai.env`,
  `compose.env`, any key file, or anything from the private directories.
- Print, log, or read the contents of a key file.
- Set `ai.enabled` to true without an explicit yes from the user in this
  conversation. OpenAI requests bill the user's account.
- Raise `ai.daily_budget_usd` or any other budget without asking, or beyond
  what the user stated.
- Set `production` to true before the user confirms the site is public.
- Expose the engine's viewer, its feed or state, the receiver host, or Ghost's
  admin origin to the internet, or put any of them in public settings.
- Remove the activation hold or run `deploy --start` without the user's yes.
- Write configuration inside this repository.

## Working on the code

If the user wants to change the publisher rather than run it, read
[DEVELOPMENT.md](DEVELOPMENT.md). It covers the checkout setup, `make check`,
image builds, the theme, plugins, releases, and the rules for changes.
