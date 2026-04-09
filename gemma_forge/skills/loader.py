"""Skill loader — discovers and loads skills from the skills/ directory.

Scans `skills/*/skill.yaml` at startup, validates each manifest, and
instantiates Skill objects. Adding a new demo is a `cp -r` + edit
prompts exercise.
"""

import logging
from pathlib import Path

import yaml

from .base import Skill, SkillManifest

logger = logging.getLogger(__name__)


def discover_skills(skills_dir: str = "skills") -> list[Skill]:
    """Discover all skills in the given directory.

    Looks for `skills/*/skill.yaml` and loads each one.

    Returns:
        A list of loaded Skill instances, sorted by name.
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        logger.warning("Skills directory not found: %s", skills_path)
        return []

    skills: list[Skill] = []

    for skill_dir in sorted(skills_path.iterdir()):
        if not skill_dir.is_dir():
            continue

        manifest_path = skill_dir / "skill.yaml"
        if not manifest_path.exists():
            logger.debug("Skipping %s — no skill.yaml", skill_dir.name)
            continue

        try:
            with open(manifest_path) as f:
                raw = yaml.safe_load(f)

            manifest = SkillManifest(**raw)
            skill = Skill(manifest=manifest, skill_dir=skill_dir)
            skills.append(skill)
            logger.info(
                "Loaded skill: %s (%s)", skill.name, skill.description
            )
        except Exception as e:
            logger.error(
                "Failed to load skill from %s: %s", skill_dir, e
            )

    logger.info("Discovered %d skill(s)", len(skills))
    return skills


def load_skill(skill_name: str, skills_dir: str = "skills") -> Skill:
    """Load a specific skill by name.

    Args:
        skill_name: The skill directory name (e.g., "stig-rhel9").
        skills_dir: The parent skills directory.

    Returns:
        The loaded Skill instance.

    Raises:
        FileNotFoundError: If the skill doesn't exist.
        ValueError: If the manifest is invalid.
    """
    skill_dir = Path(skills_dir) / skill_name
    manifest_path = skill_dir / "skill.yaml"

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Skill '{skill_name}' not found at {manifest_path}"
        )

    with open(manifest_path) as f:
        raw = yaml.safe_load(f)

    manifest = SkillManifest(**raw)
    return Skill(manifest=manifest, skill_dir=skill_dir)
