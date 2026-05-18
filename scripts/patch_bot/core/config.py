from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Target:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse(cls, full_name: str) -> Target:
        owner, _, name = full_name.partition("/")
        if not owner or not name:
            raise ValueError(f"target must be 'owner/name', got {full_name!r}")
        return cls(owner=owner, name=name)


@dataclass(frozen=True)
class RepoConfig:
    """Per-repo .patchbot.yml — read after clone."""

    base_branch: str | None = None
    runtime: str | None = None  # "gae", "generic", or None for autodetect
    coverage_paths: list[str] = field(default_factory=list)
    skip_packages: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, repo_root: Path) -> RepoConfig:
        path = repo_root / ".patchbot.yml"
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(
            base_branch=data.get("base_branch"),
            runtime=data.get("runtime"),
            coverage_paths=list(data.get("coverage_paths", [])),
            skip_packages=list(data.get("skip_packages", [])),
        )


@dataclass(frozen=True)
class Settings:
    github_token: str
    anthropic_api_key: str
    targets: list[Target]
    severity_floor: tuple[str, ...] = ("high", "critical")
    work_dir: Path = Path("/tmp/work")
    token_budget_input: int = 500_000
    token_budget_output: int = 100_000

    @classmethod
    def from_env(cls) -> Settings:
        # Strip whitespace — pasted-into-secret-manager values often end up with
        # a trailing newline that makes HTTP headers reject them.
        github_token = _required_env("PATCHBOT_GH_TOKEN").strip()
        anthropic_api_key = _required_env("ANTHROPIC_API_KEY").strip()

        # Resolution order: Secret Manager → file → inline env var.
        # Secret Manager is fetched on every call so updates take effect on the next scan
        # without redeploying the service.
        secret_resource = os.environ.get("TARGET_REPOS_SECRET")
        targets_file = os.environ.get("TARGET_REPOS_FILE")
        targets_inline = os.environ.get("TARGET_REPOS")
        if secret_resource:
            targets = _load_targets_from_secret(secret_resource)
        elif targets_file:
            targets = _load_targets_yaml(Path(targets_file))
        elif targets_inline:
            targets = [Target.parse(t.strip()) for t in targets_inline.split(",") if t.strip()]
        else:
            raise RuntimeError("set TARGET_REPOS_SECRET, TARGET_REPOS_FILE, or TARGET_REPOS")

        return cls(
            github_token=github_token,
            anthropic_api_key=anthropic_api_key,
            targets=targets,
        )


def _required_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"{key} env var is required")
    return val


def _load_targets_yaml(path: Path) -> list[Target]:
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get("repos", [])
    return [Target.parse(item) for item in raw]


def _load_targets_from_secret(resource_name: str) -> list[Target]:
    """Fetch the targets YAML from Secret Manager.

    `resource_name` should be a fully-qualified resource like
    `projects/<id>/secrets/patchbot-targets/versions/latest`.
    """
    # Imported lazily so local-dev / unit tests don't pay the import cost
    # when they're using the file-based fallback.
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=resource_name)
    payload = response.payload.data.decode("utf-8")
    data = yaml.safe_load(payload) or {}
    raw = data.get("repos", [])
    return [Target.parse(item) for item in raw]
