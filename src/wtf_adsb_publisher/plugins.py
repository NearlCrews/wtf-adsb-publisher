"""Explicitly configured publisher extensions, discovered without importing them."""

import json
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol

from packaging.specifiers import SpecifierSet

API_VERSION = "1.0"
DEFAULTS = {
    "reader": "json-inbox",
    "policy": "site-policy",
    "writer": "openai",
    "renderer": "site-media",
    "publisher": "ghost",
    "deployer": "static-ssh",
}
METHODS = {
    "reader": "read",
    "policy": "select",
    "writer": "send",
    "renderer": "render",
    "publisher": "connect",
    "deployer": "deploy",
}


@dataclass(frozen=True)
class Descriptor:
    api: str
    role: str


class Reader(Protocol):
    descriptor: Descriptor

    def read(self, config: dict) -> dict: ...


class Policy(Protocol):
    descriptor: Descriptor

    def select(self, state: dict, now: float, *, since: float, ledger: dict) -> list[dict]: ...


class Writer(Protocol):
    descriptor: Descriptor

    def send(self, encoded: bytes, key: str) -> dict: ...


def discover(role):
    if role not in DEFAULTS:
        raise ValueError("Unsupported publisher plugin role")
    entries = list(metadata.entry_points(group=f"wtf_adsb_publisher.{role}"))
    names = [entry.name for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate publisher plugin names")
    return {entry.name: entry for entry in entries}


def _configured(role, config):
    if role not in DEFAULTS:
        raise ValueError("Unsupported publisher plugin role")
    configurations = config.get("plugins", {})
    if not isinstance(configurations, dict) or set(configurations) - DEFAULTS.keys():
        raise ValueError("Unknown or malformed publisher plugin role configuration")
    configured = configurations.get(role, {})
    if not isinstance(configured, dict) or set(configured) - {"plugin", "settings"}:
        raise ValueError("Plugin configuration needs plugin and optional settings")
    name = configured.get("plugin", DEFAULTS[role])
    settings = configured.get("settings", {})
    if not isinstance(name, str) or not isinstance(settings, dict):
        raise ValueError("Invalid publisher plugin configuration")
    entry = discover(role).get(name)
    if entry is None:
        raise ValueError(f"Publisher plugin is not installed: {role}/{name}")
    return entry, settings


def identity(role, config):
    """The selected implementation and settings that can change a cached release."""
    entry, settings = _configured(role, config)
    return {
        "plugin": entry.name,
        "entry_point": entry.value,
        "distribution": entry.dist.metadata["Name"],
        "version": entry.dist.version,
        "settings": settings,
    }


def load(role, config):
    entry, settings = _configured(role, config)
    plugin = entry.load()(dict(settings))
    descriptor = getattr(plugin, "descriptor", None)
    if not isinstance(descriptor, Descriptor) or descriptor.role != role:
        raise ValueError("Publisher plugin declared the wrong role")
    if API_VERSION not in SpecifierSet(descriptor.api):
        raise ValueError("Publisher plugin API is incompatible")
    if not callable(getattr(plugin, METHODS[role], None)):
        raise ValueError("Publisher plugin does not implement its required operation")
    return plugin


def preflight(config, roles):
    """Validate selected integrations before any publication or deployment write."""
    return {role: load(role, config) for role in dict.fromkeys((*roles, *config.get("plugins", {})))}


def approved_extensions(config):
    """Consumer schema approvals are local, digest-pinned deployment configuration."""
    from pathlib import Path

    from .ingest import load_extension_schemas

    entries = config.get("extension_schemas", [])
    if not isinstance(entries, list):
        raise ValueError("extension_schemas must be a list of reviewed local contracts")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not Path(entry["path"]).is_absolute()
        ):
            raise ValueError("Approved extension schema paths must be absolute")
    return load_extension_schemas(entries)


class _Builtin:
    def __init__(self, settings):
        if settings:
            raise ValueError("This built-in publisher plugin has no private settings")


class JsonInbox(_Builtin):
    descriptor = Descriptor(">=1,<2", "reader")

    def read(self, config):
        from .ingest import read_state

        return read_state(config["inbox_database"])


class SitePolicy(_Builtin):
    descriptor = Descriptor(">=1,<2", "policy")

    def select(self, state, now, *, since, ledger):
        from .autopilot import collect_state

        return collect_state(state, now, since=since, ledger=ledger)


class OpenAIWriter(_Builtin):
    descriptor = Descriptor(">=1,<2", "writer")

    def send(self, encoded, key):
        # Research keeps its bounded search timeout and response allowance;
        # story requests keep the configured public effort's timeout.
        if json.loads(encoded).get("tools"):
            from .weekly_research import request as send
        else:
            from .public_writer import send

        return send(encoded, key)


class SiteMedia(_Builtin):
    descriptor = Descriptor(">=1,<2", "renderer")

    def render(self, render, *args, **kwargs):
        return render(*args, **kwargs)


class Ghost(_Builtin):
    descriptor = Descriptor(">=1,<2", "publisher")

    def connect(self, config):
        from pathlib import Path

        from .ghost_client import GhostClient

        return GhostClient(config["ghost_origin"], Path(config["ghost_key_file"]).read_text())


class StaticSSH(_Builtin):
    descriptor = Descriptor(">=1,<2", "deployer")

    def deploy(self, deploy, *args, **kwargs):
        return deploy(*args, **kwargs)
