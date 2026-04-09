"""Skill interface and manifest schema for the GemmaForge skills system.

A skill is a self-contained demo scenario that the Ralph loop can
execute. Each skill lives in its own directory under `skills/` and
provides:
  - A `skill.yaml` manifest (name, description, tools, validators)
  - Agent prompts (one per role: architect, worker, auditor)
  - Optional: a `plugin.py` with custom Python logic

The skills system is hybrid: folder-per-skill manifest is the default,
with an optional Python plugin escape hatch for custom logic.

See ADR-0011 (planned) and docs/adding-a-skill.md.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ValidatorConfig(BaseModel):
    """Declarative health check / validation configuration."""

    name: str
    command: str = "/usr/local/bin/mission-healthcheck.sh"
    success_pattern: str = "HEALTHY"
    failure_pattern: str = "UNHEALTHY"


class StigConfig(BaseModel):
    """STIG-specific configuration (only for STIG-type skills)."""

    profile: str = "xccdf_org.ssgproject.content_profile_stig"
    datastream: str = "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml"
    results_dir: str = "/tmp/gemma-forge-stig"


class SkillManifest(BaseModel):
    """Schema for a skill.yaml manifest file."""

    name: str
    description: str
    version: str = "0.1.0"
    target_os: str = "Rocky Linux 9"

    # Agent prompts — paths relative to the skill directory
    prompts: dict[str, str] = {
        "architect": "prompts/architect.md",
        "worker": "prompts/worker.md",
        "auditor": "prompts/auditor.md",
    }

    # Which tools each agent gets — references tool function names
    # from gemma_forge.harness.tools
    tools: dict[str, list[str]] = {
        "architect": ["run_stig_scan"],
        "worker": ["apply_fix"],
        "auditor": ["check_health", "revert_last_fix"],
    }

    # Validators run after each fix
    validators: list[ValidatorConfig] = []

    # Optional STIG-specific config
    stig: Optional[StigConfig] = None

    # Optional plugin module (relative to skill directory)
    plugin: Optional[str] = None


class Skill:
    """A loaded, ready-to-use skill instance."""

    def __init__(self, manifest: SkillManifest, skill_dir: Path):
        self.manifest = manifest
        self.skill_dir = skill_dir
        self._prompts: dict[str, str] = {}
        self._plugin: Any = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    def get_prompt(self, role: str) -> str:
        """Load and cache a role's system prompt from the skill directory."""
        if role not in self._prompts:
            prompt_path = self.skill_dir / self.manifest.prompts.get(
                role, f"prompts/{role}.md"
            )
            if prompt_path.exists():
                self._prompts[role] = prompt_path.read_text().strip()
            else:
                logger.warning(
                    "Skill %s: no prompt file for role '%s' at %s",
                    self.name, role, prompt_path,
                )
                self._prompts[role] = f"You are the {role} in a remediation team."
        return self._prompts[role]

    def get_tools(self, role: str) -> list[str]:
        """Get the tool function names for a role."""
        return self.manifest.tools.get(role, [])

    def get_plugin(self) -> Any:
        """Load the optional plugin module if it exists."""
        if self._plugin is not None:
            return self._plugin

        if self.manifest.plugin:
            plugin_path = self.skill_dir / self.manifest.plugin
            if plugin_path.exists():
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    f"skill_plugin_{self.name}", plugin_path
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self._plugin = module
                    logger.info("Loaded plugin for skill '%s'", self.name)

        return self._plugin
