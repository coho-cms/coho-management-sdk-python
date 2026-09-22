"""Profiles and context: ``~/.config/coho/config.toml``.

Borrowed from ``aws``: a **profile** is a named environment with its own BFF URL
and identity-provider settings. Borrowed from ``git``: a **context** — the current
account, project and ref — sticks until changed, so every command does not need
three flags.

Layout::

    [context]
    profile = "staging"                       # which profile commands use by default

    [profiles.staging]
    url = "https://staging.coho.example"      # the BFF
    oidc_domain = "https://acme.auth.us-east-1.amazoncognito.com"
    client_id = "1h57kf5cpq17m0eml12EXAMPLE"  # the CLI's public app client
    scopes = ["openid", "coho-auth/self", "coho-auth/accounts"]
    callback_port = 8765                      # must match the app client's callback URL
    token_store = "keyring"                   # or "file"

    [profiles.staging.context]
    account = "0192…"                         # account id
    project = "0192…"                         # project id
    ref = "dev"

    [profiles.staging.projects."0192…"]       # keyed by account id: names the CLI knows
    marketing = "0192…"                       # because the API has no project listing

Location: ``$COHO_CONFIG_DIR`` if set, else ``$XDG_CONFIG_HOME/coho``, else
``~/.config/coho``.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

DEFAULT_SCOPES = ["openid", "coho-auth/self", "coho-auth/accounts"]
DEFAULT_CALLBACK_PORT = 8765
DEFAULT_PROFILE = "default"

ENV_PROFILE = "COHO_PROFILE"
ENV_URL = "COHO_URL"
ENV_ACCOUNT = "COHO_ACCOUNT"
ENV_PROJECT = "COHO_PROJECT"
ENV_REF = "COHO_REF"
ENV_CONFIG_DIR = "COHO_CONFIG_DIR"


def restrict_to_owner(target: int | Path) -> None:
    """Make a file readable and writable by its owner alone.

    Takes an open file descriptor — which closes the window where a freshly created
    file is still world-readable — or a path. A no-op where POSIX mode bits do not
    exist (Windows), where a file inherits the parent directory's ACL instead.
    """
    try:
        if isinstance(target, int):
            os.fchmod(target, stat.S_IRUSR | stat.S_IWUSR)
        else:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except (AttributeError, NotImplementedError, OSError):
        pass


def config_dir() -> Path:
    if override := os.environ.get(ENV_CONFIG_DIR):
        return Path(override).expanduser()
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg).expanduser() / "coho"
    return Path.home() / ".config" / "coho"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass(slots=True)
class Context:
    """The sticky part: which account, project and ref commands act on."""

    account: str | None = None
    project: str | None = None
    ref: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            k: v
            for k, v in (("account", self.account), ("project", self.project), ("ref", self.ref))
            if v
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Context:
        return cls(account=raw.get("account"), project=raw.get("project"), ref=raw.get("ref"))

    def with_env(self) -> Context:
        """The context after ``COHO_ACCOUNT`` / ``COHO_PROJECT`` / ``COHO_REF`` overrides."""
        return Context(
            account=os.environ.get(ENV_ACCOUNT) or self.account,
            project=os.environ.get(ENV_PROJECT) or self.project,
            ref=os.environ.get(ENV_REF) or self.ref,
        )


@dataclass(slots=True)
class Profile:
    """One environment: where the BFF is and how to get a token for it."""

    name: str
    url: str = ""
    oidc_domain: str | None = None
    client_id: str | None = None
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    callback_port: int = DEFAULT_CALLBACK_PORT
    token_store: str = "keyring"
    context: Context = field(default_factory=Context)
    projects: dict[str, dict[str, str]] = field(default_factory=dict)
    """``{account_id: {project_name: project_id}}`` — a local registry, see module doc."""

    @property
    def can_login(self) -> bool:
        return bool(self.oidc_domain and self.client_id)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url}
        if self.oidc_domain:
            out["oidc_domain"] = self.oidc_domain
        if self.client_id:
            out["client_id"] = self.client_id
        out["scopes"] = list(self.scopes)
        out["callback_port"] = self.callback_port
        out["token_store"] = self.token_store
        if ctx := self.context.to_dict():
            out["context"] = ctx
        if self.projects:
            out["projects"] = {a: dict(p) for a, p in self.projects.items() if p}
        return out

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> Profile:
        return cls(
            name=name,
            url=str(raw.get("url", "")),
            oidc_domain=raw.get("oidc_domain"),
            client_id=raw.get("client_id"),
            scopes=list(raw.get("scopes") or DEFAULT_SCOPES),
            callback_port=int(raw.get("callback_port", DEFAULT_CALLBACK_PORT)),
            token_store=str(raw.get("token_store", "keyring")),
            context=Context.from_dict(raw.get("context") or {}),
            projects={a: dict(p) for a, p in (raw.get("projects") or {}).items()},
        )

    # -- the local project registry ----------------------------------------------

    def remember_project(self, account_id: str, name: str, project_id: str) -> None:
        self.projects.setdefault(account_id, {})[name] = project_id

    def forget_project(self, account_id: str, name_or_id: str) -> bool:
        table = self.projects.get(account_id, {})
        for name, pid in list(table.items()):
            if name_or_id in (name, pid):
                del table[name]
                return True
        return False

    def resolve_project(self, account_id: str, name_or_id: str) -> str:
        """A project name from the registry becomes its id; anything else passes through."""
        return self.projects.get(account_id, {}).get(name_or_id, name_or_id)

    def project_name(self, account_id: str, project_id: str) -> str | None:
        for name, pid in self.projects.get(account_id, {}).items():
            if pid == project_id:
                return name
        return None


@dataclass(slots=True)
class Config:
    """The whole file. Load with `Config.load`, mutate, `save`."""

    profiles: dict[str, Profile] = field(default_factory=dict)
    current_profile: str | None = None
    path: Path = field(default_factory=config_path)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls(path=path)
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        profiles = {
            name: Profile.from_dict(name, body)
            for name, body in (raw.get("profiles") or {}).items()
        }
        return cls(
            profiles=profiles,
            current_profile=(raw.get("context") or {}).get("profile"),
            path=path,
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw: dict[str, Any] = {}
        if self.current_profile:
            raw["context"] = {"profile": self.current_profile}
        raw["profiles"] = {name: p.to_dict() for name, p in self.profiles.items()}
        tmp = self.path.with_suffix(".toml.tmp")
        with tmp.open("wb") as fh:
            restrict_to_owner(fh.fileno())
            tomli_w.dump(raw, fh)
        tmp.replace(self.path)

    def profile(self, name: str | None = None, *, create: bool = False) -> Profile:
        """The named profile, else ``$COHO_PROFILE``, else the current one, else ``default``.

        Raises ``KeyError`` if it does not exist and ``create`` is false.
        """
        name = name or os.environ.get(ENV_PROFILE) or self.current_profile or DEFAULT_PROFILE
        if name not in self.profiles:
            if not create:
                raise KeyError(name)
            self.profiles[name] = Profile(name=name)
        return self.profiles[name]

    def effective_url(self, profile: Profile) -> str:
        return os.environ.get(ENV_URL) or profile.url
