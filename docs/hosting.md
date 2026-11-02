# Hosting the site

The publisher runs on the host that holds the engine's feed directory, usually
the receiver's own machine or one beside it. What this guide covers is where the
website lives. Two pieces need a home:

- A Ghost instance. The publisher records every article in Ghost through its
  Admin API (`ghost_origin` and `ghost_key_file` in `publisher.json`), and the
  site owner edits or removes stories there. Ghost stays private: readers never
  see it. A publishing-target [plugin](plugins.md) could replace Ghost, but
  none ships.
- The public pages: the checked static release that each publication pass
  builds, with its maps, replays, journal data, feed, and search index. The
  built-in deployer sends it over SSH to `preview_ssh_host`, where
  [`receive-autopilot.py`](../deploy/ghost-preview/receive-autopilot.py) installs
  it. See [the static release host](#the-static-release-host).

## Options

Costs are rough monthly figures in US dollars when this guide was written; check
current prices. Every option also needs a domain, about $10 to $15 a year.

| Option | Cost per month | What you operate | Owner editing | TLS | Backups | How the publisher delivers |
| --- | --- | --- | --- | --- | --- | --- |
| Static host (GitHub Pages, Cloudflare Pages, or Netlify) with Ghost on a machine you already run | $0 on free tiers | A private Ghost and an upload step | Ghost Admin on your private network | Automatic from the static host | Ghost and publisher state on your machine; the host keeps past deploys | Ghost Admin API, plus a deployer plugin that uploads the release (none ships) |
| Small VPS with Docker and Caddy running both | $6 to $15, plus the provider's backup add-on | The server, Docker, Ghost, MySQL, Caddy, and Tailscale | Ghost Admin on your tailnet | Caddy obtains certificates; a CDN in front is optional | `deploy/ghost/backup.sh` daily, plus provider snapshots | Both: Ghost Admin API over the tailnet and the release over SSH |
| The receiver's own host behind Cloudflare Tunnel | $0 beyond the hardware | Ghost, Caddy, the static server, and the tunnel on that host | Ghost Admin on your tailnet | Cloudflare's edge certificate | `deploy/ghost/backup.sh` to another disk or off site | Both, with the SSH destination on the same host |

A hosted Ghost such as Ghost(Pro) is not an option here. `site_origin` must
match the `url` in Ghost's configuration, and a hosted Ghost serves its own
`url`, so the static pages could not answer at that address. Its admin is also
on the internet, while this guide keeps Ghost private.

### Static host with a private Ghost

The cheapest public side: the static host serves the pages, scales without
attention, and handles TLS. Ghost still has to run somewhere, such as the
receiver's host with `deploy/ghost/compose.yaml` minus its public proxy, reached
only over your private network. The built-in deployer speaks SSH, so this option
needs a deployer plugin that uploads the checked release to the static host's
API or Git repository. Static hosts publish everything you upload, so upload
only the release directory.

### Self-hosted Ghost on a small VPS

This is what the [example deployment](https://wtf-adsb.com) runs, and the one
the shipped templates cover end to end. A server with 2 GB of memory and a swap
file runs Ghost, MySQL, Caddy, the static site server, and Tailscale with room
to spare. [`deploy/vps/bootstrap.sh`](../deploy/vps/bootstrap.sh) prepares a new
Ubuntu 24.04 server as first-boot user data: an admin account with your SSH
key, no root or password logins, Docker, unattended upgrades, and
`/opt/wtf-adsb`. Then copy `deploy/ghost/` to `/opt/wtf-adsb` and
`deploy/ghost-preview/` to `/opt/wtf-adsb/theme-preview`, fill in the secrets
the [Compose file](../deploy/ghost/compose.yaml) names, set `DOMAIN` in `.env`,
and start both projects. Caddy serves only the static release; requests for
`/ghost`, `/content`, and `/members` get a 404. Ghost Admin and SSH are reached
through Tailscale, so the server needs only ports 80 and 443 open to the
internet. Install the backup and readership units from `deploy/ghost/` and turn
on the provider's snapshots.

### The receiver's own host

With 2 GB of memory to spare, the receiver's machine can run the same Ghost
templates and skip the server bill. Cloudflare Tunnel publishes the static site
under your domain without opening a port or revealing the host's address. Point
the tunnel at the static server only, never at Ghost, the engine viewer, or the
host's other services. The site goes down whenever the receiver does, and one
disk failure can take the receiver, the publisher, and the site together, so
copy backups elsewhere. Tailscale Funnel can also publish a service, but only
under a `ts.net` name, and the publisher's built-in privacy patterns treat every
`ts.net` name as private, so a Funnel address cannot be the public origin.

## The static release host

The built-in deployer connects to `preview_ssh_host` as the user its SSH
configuration names and runs
`sudo -n python3 /opt/wtf-adsb/theme-preview/receive-autopilot.py <digest>`,
where `<digest>` is the release archive's SHA-256, with the archive on standard
input. The script installs the release, then restarts the static server with
`docker compose -f /opt/wtf-adsb/theme-preview/compose.yaml`, so the host needs
Docker with Compose and `deploy/ghost-preview/` copied to
`/opt/wtf-adsb/theme-preview`. Allow that one command, and no other, with a
sudoers line such as this one for the account `deploy`; the script refuses a
digest that is not 64 lowercase hex characters:

```text
deploy ALL=(root) NOPASSWD: /usr/bin/python3 /opt/wtf-adsb/theme-preview/receive-autopilot.py *
```

Install it with `visudo -f /etc/sudoers.d/wtf-adsb-deploy`. Because that rule
runs the script as root, `/opt/wtf-adsb/theme-preview` and
`receive-autopilot.py` must be owned by root, or by an account that already has
full sudo, and must not be writable by `deploy`; otherwise `deploy` could change
the script and run anything as root.

## Privacy

Whichever option you choose:

- The public site never links the engine's viewer, the receiver host, or the
  receiver's coordinates. Keep the viewer and the receiver on your private
  network, and do not serve the website straight from the receiver's address:
  an IP address can reveal the area the site keeps private.
- Keep Ghost's admin origin private. Stock Ghost puts that origin in every page
  through its Portal, search, and comments scripts, and redirects `/ghost/` to
  it. The shipped
  [`ghost.production.example.json`](../deploy/ghost/ghost.production.example.json)
  turns all four off, and the theme brings its own search. Copy those settings
  into your Ghost configuration unchanged; `make test-ghost` checks them against
  the pinned Ghost image, not against your running site.
- List your own names, users, and hostnames in `private_terms_file`, so the
  publisher refuses any public text that contains them. See
  [operations](operations.md#configuration-and-state).

## Recommendation

Start with the VPS. It is the tested path and costs about $10 a month. If you
would rather not pay for or patch a server, and the receiver's machine has
memory to spare, run the same templates there behind Cloudflare Tunnel. Pick a
static host once a deployer plugin for it exists.
