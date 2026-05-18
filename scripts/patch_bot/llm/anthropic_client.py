from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib.resources import files

import anthropic

from ..analysis.changelog import ChangelogSummary
from ..analysis.dependency_delta import DepDelta
from ..analysis.importers import ImportSite
from ..analysis.project_surface import ProjectSurface
from ..core.remediation import Remediation
from ..runtimes.base import CompatVerdict
from .budget import BudgetExceeded, TokenBudget

log = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-6"

_RISK_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bump_category": {"type": "string", "enum": ["patch", "minor", "major"]},
        "breaking_change_uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "deprecation_to_error": {"type": "array", "items": {"type": "string"}},
        "default_changes": {"type": "array", "items": {"type": "string"}},
        "new_required_config": {"type": "array", "items": {"type": "string"}},
        "risk_score": {"type": "string", "enum": ["low", "medium", "high"]},
        "test_plan": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "bump_category",
        "breaking_change_uncertainty",
        "key_findings",
        "deprecation_to_error",
        "default_changes",
        "new_required_config",
        "risk_score",
        "test_plan",
        "summary",
    ],
}


@dataclass
class RiskInputs:
    rem: Remediation
    current_version: str | None
    target_version: str
    runtime: CompatVerdict
    changelog: ChangelogSummary
    dep_delta: DepDelta
    surface: ProjectSurface
    import_sites: list[ImportSite] = field(default_factory=list)


@dataclass
class RiskAssessment:
    bump_category: str
    breaking_change_uncertainty: str
    key_findings: list[str]
    deprecation_to_error: list[str]
    default_changes: list[str]
    new_required_config: list[str]
    risk_score: str
    test_plan: list[str]
    summary: str


def _load_prompt() -> str:
    return (files(__package__) / "prompts" / "risk_summary.md").read_text()


def _load_gae_table() -> str:
    return (files("scripts.patch_bot.runtimes") / "data" / "gae_runtimes.yaml").read_text()


def _serialize_inputs(inputs: RiskInputs) -> str:
    rem = inputs.rem
    return json.dumps(
        {
            "package": rem.bump_package,
            "ecosystem": rem.ecosystem,
            "root_cause_bump": rem.root_cause,
            "vulnerable_transitive_packages": rem.via_packages,
            "advisories": [
                {
                    "ghsa_id": a.ghsa_id,
                    "severity": a.severity,
                    "cvss": a.cvss_score,
                    "summary": a.summary,
                    "description": a.description[:4000],
                }
                for a in rem.alerts
            ],
            "current_version": inputs.current_version,
            "target_version": inputs.target_version,
            "runtime": {
                "id": inputs.runtime.runtime_id,
                "verdict": inputs.runtime.verdict,
                "detail": inputs.runtime.detail,
            },
            "changelog": {
                "source": inputs.changelog.source_url,
                "commit_count": inputs.changelog.commit_count,
                "pr_titles": inputs.changelog.pr_titles[:50],
                "release_notes": inputs.changelog.release_notes[:5],
                "breaking_signals": inputs.changelog.breaking_change_signals,
            },
            "dep_delta": {
                "added": inputs.dep_delta.added,
                "removed": inputs.dep_delta.removed,
                "bumped": inputs.dep_delta.bumped,
            },
            "project_surface": {
                "spread_or_wrapped": inputs.surface.spread_or_wrapped,
                "direct_import_files": inputs.surface.direct_import_files,
                "monkey_patch_count": len(inputs.surface.monkey_patch_sites),
                "monkey_patch_examples": [
                    f"{s.file}:{s.line} {s.text[:120]}"
                    for s in inputs.surface.monkey_patch_sites[:5]
                ],
                "uncovered_call_sites": [
                    f"{s.file}:{s.line}" for s in inputs.surface.uncovered_call_sites[:10]
                ],
                "open_issues": [
                    {"title": i.title, "url": i.url, "reactions": i.reactions}
                    for i in inputs.surface.open_issues
                ],
            },
            "import_sites": [
                f"{s.file}:{s.line} {s.text[:120]}" for s in inputs.import_sites[:30]
            ],
        },
        ensure_ascii=False,
    )


class LLMClient:
    def __init__(self, api_key: str, budget: TokenBudget):
        self._anthropic = anthropic.AsyncAnthropic(api_key=api_key)
        self._budget = budget
        self._instructions = _load_prompt()
        self._gae_table = _load_gae_table()

    async def aclose(self) -> None:
        await self._anthropic.close()

    async def assess(self, inputs: RiskInputs) -> RiskAssessment | None:
        try:
            self._budget.check()
        except BudgetExceeded as e:
            log.warning("budget exceeded, skipping LLM call: %s", e)
            return None

        model = self._select_model(inputs)
        try:
            return await self._call(model, inputs)
        except (anthropic.APIError, json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("LLM error on %s (%s): %s", model, type(e).__name__, e)
            return None

    def _select_model(self, inputs: RiskInputs) -> str:
        if _is_major_bump(inputs.current_version, inputs.target_version):
            return SONNET
        return HAIKU

    async def _call(self, model: str, inputs: RiskInputs) -> RiskAssessment | None:
        # Cache the static prefix (instructions + GAE table). Volatile per-alert
        # JSON goes in the final user block with no cache_control.
        system = [
            {
                "type": "text",
                "text": self._instructions,
            },
            {
                "type": "text",
                "text": f"# GAE runtime compatibility table\n\n```yaml\n{self._gae_table}\n```",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        payload = _serialize_inputs(inputs)
        response = await self._anthropic.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Produce one risk assessment JSON for this package "
                                "upgrade, which resolves all the advisories listed. "
                                "Inputs:\n```json\n" + payload + "\n```"
                            ),
                        }
                    ],
                }
            ],
            output_config={
                "format": {"type": "json_schema", "schema": _RISK_SCHEMA},
            },
        )
        self._budget.record(model, response.usage)
        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning(
                "LLM stop_reason=%s on %s — falling back to template-only PR",
                response.stop_reason,
                model,
            )
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text.strip():
            log.warning("LLM returned no text content on %s", model)
            return None
        data = json.loads(text)
        return RiskAssessment(
            bump_category=data["bump_category"],
            breaking_change_uncertainty=data["breaking_change_uncertainty"],
            key_findings=data["key_findings"],
            deprecation_to_error=data["deprecation_to_error"],
            default_changes=data["default_changes"],
            new_required_config=data["new_required_config"],
            risk_score=data["risk_score"],
            test_plan=data["test_plan"],
            summary=data["summary"],
        )


def _is_major_bump(current: str | None, target: str) -> bool:
    if not current:
        return False
    try:
        cur_major = int(current.lstrip("v").split(".", 1)[0])
        tgt_major = int(target.lstrip("v").split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return tgt_major > cur_major
