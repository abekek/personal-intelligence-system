from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import yaml

from pis.schemas.events import CanonicalEvent


@dataclass
class PolicyEngine:
    projects: dict[str, dict]
    denied_path_patterns: list[str]
    denied_repo_patterns: list[str]
    default_sensitivity: str = "confidential-personal"

    @classmethod
    def load(cls, config_dir: Path) -> "PolicyEngine":
        projects_cfg = yaml.safe_load((config_dir / "projects.yaml").read_text()) or {}
        denied_cfg = yaml.safe_load((config_dir / "denied_paths.yaml").read_text()) or {}
        sens_cfg = yaml.safe_load((config_dir / "sensitivity.yaml").read_text()) or {}
        return cls(
            projects={p["id"]: p for p in projects_cfg.get("projects", [])},
            denied_path_patterns=denied_cfg.get("denied_path_patterns", []),
            denied_repo_patterns=denied_cfg.get("denied_repo_patterns", []),
            default_sensitivity=sens_cfg.get("default_sensitivity", "confidential-personal"),
        )

    def is_denied_path(self, path: str) -> bool:
        return any(fnmatch(path, pat) for pat in self.denied_path_patterns)

    def is_denied_repo(self, remote: str) -> bool:
        return any(fnmatch(remote, pat) for pat in self.denied_repo_patterns)

    def check_event(self, event: CanonicalEvent) -> str | None:
        remote = event.metadata.get("git_remote") or event.metadata.get("repository_full_name")
        if remote and self.is_denied_repo(str(remote)):
            return "denied_repository"
        paths = [str(p) for p in (event.metadata.get("changed_files") or [])]
        cwd = event.metadata.get("cwd")
        if cwd:
            paths.append(str(cwd))
        if any(self.is_denied_path(p) for p in paths):
            return "denied_path"
        return None
