from __future__ import annotations

import re
from pathlib import Path

from .base import UpdateResult


class RequirementsUpdater:
    name = "requirements.txt"
    ecosystem = "pip"

    def detect(self, repo_root: Path) -> Path | None:
        if any(repo_root.glob("requirements*.txt")):
            return repo_root
        return None

    async def update(self, workdir: Path, package: str, target_version: str) -> UpdateResult:
        files = sorted(workdir.glob("requirements*.txt"))
        if not files:
            return UpdateResult(success=False, changed_files=[], message="no requirements*.txt")

        # Match lines like: pkg==1.2.3 / pkg>=1.0,<2.0 / pkg
        line_re = re.compile(
            rf"^(?P<name>{re.escape(package)})(?P<spec>\s*[=<>!~][^\s#]*)?(?P<rest>.*)$",
            re.IGNORECASE,
        )
        changed: list[str] = []
        for f in files:
            text = f.read_text()
            new_lines = []
            modified = False
            for line in text.splitlines():
                stripped = line.lstrip()
                if not stripped or stripped.startswith("#"):
                    new_lines.append(line)
                    continue
                m = line_re.match(stripped)
                if m and m.group("name").lower() == package.lower():
                    indent = line[: len(line) - len(stripped)]
                    new_lines.append(f"{indent}{package}=={target_version}{m.group('rest') or ''}")
                    modified = True
                else:
                    new_lines.append(line)
            if modified:
                f.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
                changed.append(f.name)
        if not changed:
            return UpdateResult(
                success=False,
                changed_files=[],
                message=f"{package} not found in requirements*.txt",
            )
        return UpdateResult(
            success=True,
            changed_files=changed,
            message=f"requirements.txt regex bump {package}=={target_version} (shallow — no transitive resolution)",
        )
