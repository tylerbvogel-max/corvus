"""Tenant configuration loader.

Reads TENANT_ID env var, loads config from tenants/{id}/.
Uses importlib to load Python modules from tenant dirs for complex data
(compiled regex, nested dicts, multi-line prompts).
"""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import yaml


_BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
_TENANTS_DIR = _BACKEND_DIR / "tenants"


def _load_module_from_file(name: str, path: Path) -> ModuleType:
    """Load a Python module from an arbitrary file path."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None, f"Could not load module spec from {path}"
    assert spec.loader is not None, f"Module spec has no loader: {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TenantConfig:
    """Lazily-loaded, singleton tenant configuration."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.tenant_dir = _TENANTS_DIR / tenant_id
        assert self.tenant_dir.is_dir(), (
            f"Tenant directory not found: {self.tenant_dir}. "
            f"Available tenants: {[d.name for d in _TENANTS_DIR.iterdir() if d.is_dir()]}"
        )

        # Load tenant.yaml
        yaml_path = self.tenant_dir / "tenant.yaml"
        assert yaml_path.exists(), f"tenant.yaml not found in {self.tenant_dir}"
        with open(yaml_path) as f:
            self._yaml = yaml.safe_load(f)

        # Eagerly load all domain modules
        self._modules: dict[str, ModuleType] = {}
        for py_file in sorted(self.tenant_dir.glob("*.py")):
            mod_name = f"tenant__{self.tenant_id}__{py_file.stem}"
            self._modules[py_file.stem] = _load_module_from_file(mod_name, py_file)

    # ── YAML properties ──

    @property
    def display_name(self) -> str:
        return self._yaml["display_name"]

    @property
    def description(self) -> str:
        return self._yaml["description"]

    @property
    def system_use_banner(self) -> str:
        return self._yaml["system_use_banner"]

    @property
    def semantic_prefilter_enabled(self) -> bool:
        return self._yaml.get("semantic_prefilter_enabled", False)

    @property
    def regulatory_department_name(self) -> str:
        return self._yaml["regulatory_department_name"]

    @property
    def reseed_threshold(self) -> int:
        return self._yaml.get("reseed_threshold", 150)

    @property
    def output_policies(self) -> dict:
        """Runtime output-policy-gate configuration (Pattern #7).

        Returns the raw policy dict keyed by rule_id. Missing rules default
        to disabled so a tenant can opt-in per policy. output_guard is the
        sole consumer; never read this from pipeline code directly.
        """
        raw = self._yaml.get("output_policies") or {}
        assert isinstance(raw, dict), "output_policies must be a mapping"
        return raw

    # ── Domain module properties ──

    @property
    def classify_system_prompt(self) -> str:
        return self._modules["classifier_prompt"].CLASSIFY_SYSTEM_PROMPT

    @property
    def intent_voice_map(self):
        return self._modules["voices"].INTENT_VOICE_MAP

    @property
    def regulatory_patterns(self):
        return self._modules["patterns"].REGULATORY_PATTERNS

    @property
    def technical_patterns(self):
        return self._modules["patterns"].TECHNICAL_PATTERNS

    @property
    def seed_sources(self) -> list:
        return self._modules["provenance_seeds"].SEED_SOURCES

    @property
    def concept_definitions(self) -> list:
        return self._modules["concepts"].CONCEPT_DEFINITIONS

    @property
    def regulatory_tree(self):
        return self._modules["regulatory_tree"].REGULATORY_TREE

    @property
    def regulatory_department(self) -> str:
        return self._modules["regulatory_tree"].DEPARTMENT

    @property
    def risk_categories(self) -> dict:
        return self._modules["risk_categories"].RISK_CATEGORIES

    @property
    def grounding_ref_pattern(self):
        return self._modules["risk_categories"].GROUNDING_REF_PATTERN

    @property
    def known_apps(self) -> list:
        return self._modules["known_apps"].KNOWN_APPS

    @property
    def baseline_prompt(self) -> str:
        return self._yaml.get("baseline_prompt", "")

    @property
    def seed_prompts(self) -> list[dict]:
        return self._yaml.get("seed_prompts", [])

    @property
    def engram_seeds(self) -> list:
        mod = self._modules.get("engram_seeds")
        return mod.ENGRAM_SEEDS if mod else []

    @property
    def org_yaml_path(self) -> Path:
        return self.tenant_dir / "corvus_org.yaml"


def _get_tenant_id() -> str:
    """Read TENANT_ID from environment, defaulting to corvus-aero."""
    return os.environ.get("TENANT_ID", "corvus-aero")


# Singleton — initialized on first import
tenant = TenantConfig(_get_tenant_id())
