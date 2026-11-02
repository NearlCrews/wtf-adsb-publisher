"""Validate the Ghost and theme-preview Compose files and the Caddyfile each one mounts."""

import json
import subprocess
from pathlib import Path

# Compose arguments, the service that runs Caddy, and its Caddyfile.
DEPLOYMENTS = [
    (
        ["--env-file", "deploy/ghost/.env.example", "-f", "deploy/ghost/compose.yaml"],
        "caddy",
        "deploy/ghost/Caddyfile",
    ),
    (["-f", "deploy/ghost-preview/compose.yaml"], "preview", "deploy/ghost-preview/Caddyfile"),
]
CADDY_CHECKS = "caddy fmt --diff /etc/caddy/Caddyfile && caddy validate --config /etc/caddy/Caddyfile"


def main():
    root = Path(__file__).resolve().parents[1]
    for compose, service, caddyfile in DEPLOYMENTS:
        # Rendering the configuration validates the Compose file and yields the pinned image.
        config = subprocess.run(
            ["docker", "compose", *compose, "config", "--format", "json"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        image = json.loads(config.stdout)["services"][service]["image"]
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                "--env",
                "DOMAIN=example.invalid",
                "--mount",
                f"type=bind,src={root / caddyfile},dst=/etc/caddy/Caddyfile,readonly",
                image,
                "sh",
                "-c",
                CADDY_CHECKS,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            print(result.stdout + result.stderr, end="")
            raise SystemExit(result.returncode)
        print(f"{compose[-1]} and {caddyfile} passed.")


if __name__ == "__main__":
    main()
