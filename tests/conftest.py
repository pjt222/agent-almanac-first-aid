"""Shared fixtures. Tests never import Hermes: register() runs against a recording fake ctx."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PACKAGE_UNDER_TEST = "agent_almanac_first_aid_under_test"


class RecordingContext:
    """Stands in for Hermes's PluginContext: records what register() asks for."""

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = dict(settings or {})
        self.skills: dict[str, Path] = {}
        self.skill_descriptions: dict[str, str] = {}
        self.commands: dict[str, dict] = {}
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, list] = {}

    def register_skill(self, name, path, description="", frontmatter=None):
        assert ":" not in name, "Hermes adds the plugin namespace itself"
        assert name not in self.skills, f"skill {name} registered twice"
        self.skills[name] = Path(path)
        self.skill_descriptions[name] = description

    def register_command(self, name, handler, description="", args_hint=""):
        assert name not in self.commands, f"command {name} registered twice"
        self.commands[name] = {"handler": handler, "description": description, "args_hint": args_hint}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, hook_name, callback, **kwargs):
        self.hooks.setdefault(hook_name, []).append(callback)

    def get_config(self, key, default=None):
        # Like Hermes: the manifest's schema defaults are NOT applied; unset keys return `default`.
        return self.settings.get(key, default)


@pytest.fixture(scope="session")
def plugin_package():
    """Import the repo root as a package, the way Hermes does (relative imports included)."""
    if PACKAGE_UNDER_TEST in sys.modules:
        return sys.modules[PACKAGE_UNDER_TEST]
    spec = importlib.util.spec_from_file_location(
        PACKAGE_UNDER_TEST, REPO_ROOT / "__init__.py", submodule_search_locations=[str(REPO_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_UNDER_TEST] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def recording_context_class():
    return RecordingContext


@pytest.fixture()
def registered_context(plugin_package):
    context = RecordingContext()
    plugin_package.register(context)
    return context
