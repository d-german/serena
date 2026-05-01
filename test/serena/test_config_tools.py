import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from serena.config.serena_config import ProjectConfig
from serena.project import Project
from serena.tools.config_tools import ListWorkspaceEntriesTool, SetActiveWorkspaceTool
from solidlsp.ls_config import Language
from test.conftest import create_default_serena_config


class StubAgent:
    def __init__(self, project: Project):
        self._project = project
        self.reset_calls = 0
        self.serena_config = SimpleNamespace(default_max_tool_answer_chars=50000)

    def get_active_project_or_raise(self) -> Project:
        return self._project

    def reset_language_server_manager(self) -> None:
        self.reset_calls += 1



def _create_csharp_project(tmp_path: Path) -> Project:
    main_dir = tmp_path / "src" / "Main"
    main_dir.mkdir(parents=True)
    (main_dir / "Main.sln").touch()
    (main_dir / "Main.csproj").touch()

    other_dir = tmp_path / "src" / "Other"
    other_dir.mkdir(parents=True)
    (other_dir / "Other.sln").touch()

    serena_config = create_default_serena_config()
    ProjectConfig.autogenerate(tmp_path, serena_config, languages=[Language.CSHARP], save_to_disk=True)
    return Project.load(tmp_path, serena_config)


class TestWorkspaceConfigTools:
    def test_list_workspace_entries_marks_selected_entry(self, tmp_path: Path) -> None:
        project = _create_csharp_project(tmp_path)
        project.project_config.active_workspace = "src/Main/Main.sln"

        entries = json.loads(ListWorkspaceEntriesTool(StubAgent(project)).apply())

        assert {"path": "src/Main/Main.sln", "kind": "solution", "selected": True} in entries
        assert {"path": "src/Main/Main.csproj", "kind": "project", "selected": False} in entries
        assert {"path": "src/Other/Other.sln", "kind": "solution", "selected": False} in entries

    def test_set_active_workspace_persists_to_project_local_and_restarts(self, tmp_path: Path) -> None:
        project = _create_csharp_project(tmp_path)
        agent = StubAgent(project)
        result = SetActiveWorkspaceTool(agent).apply("src/Main/Main.sln")
        reloaded = Project.load(tmp_path, project.serena_config)

        assert project.get_active_workspace() == "src/Main/Main.sln"
        assert project.project_config.active_workspace == "src/Main/Main.sln"
        assert "active_workspace" in project.project_config._local_override_keys
        assert reloaded.project_config.active_workspace == "src/Main/Main.sln"
        assert "active_workspace" in reloaded.project_config._local_override_keys
        assert agent.reset_calls == 1
        assert "src/Main/Main.sln" in result

    def test_set_active_workspace_creates_project_local_when_missing(self, tmp_path: Path) -> None:
        project = _create_csharp_project(tmp_path)
        project_local_path = tmp_path / ".serena" / ProjectConfig.SERENA_LOCAL_PROJECT_FILE
        project_local_path.unlink()

        SetActiveWorkspaceTool(StubAgent(project)).apply("src/Main/Main.sln", restart=False)
        reloaded = Project.load(tmp_path, project.serena_config)

        assert project_local_path.exists()
        assert reloaded.project_config.active_workspace == "src/Main/Main.sln"
        assert "active_workspace" in reloaded.project_config._local_override_keys

    def test_set_active_workspace_session_mode_does_not_write_to_disk(self, tmp_path: Path) -> None:
        project = _create_csharp_project(tmp_path)
        agent = StubAgent(project)
        SetActiveWorkspaceTool(agent).apply("src/Main/Main.sln", persist_mode="session", restart=False)
        project.add_language(Language.TYPESCRIPT)
        reloaded = Project.load(tmp_path, project.serena_config)

        assert project.get_active_workspace() == "src/Main/Main.sln"
        assert project.project_config.active_workspace is None
        assert reloaded.project_config.active_workspace is None
        assert Language.TYPESCRIPT in reloaded.project_config.languages
        assert agent.reset_calls == 0

    def test_set_active_workspace_rejects_omnisharp_projects(self, tmp_path: Path) -> None:
        (tmp_path / "Main.sln").touch()
        serena_config = create_default_serena_config()
        ProjectConfig.autogenerate(tmp_path, serena_config, languages=[Language.CSHARP_OMNISHARP], save_to_disk=True)
        project = Project.load(tmp_path, serena_config)

        with pytest.raises(ValueError, match="Roslyn-based C# projects"):
            SetActiveWorkspaceTool(StubAgent(project)).apply("Main.sln", restart=False)

    def test_set_active_workspace_rejects_path_outside_project_root(self, tmp_path: Path) -> None:
        project = _create_csharp_project(tmp_path)
        outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "Outside.sln"
        outside_file.touch()

        with pytest.raises(ValueError, match="inside the active project root"):
            SetActiveWorkspaceTool(StubAgent(project)).apply(str(outside_file))

    def test_list_workspace_entries_shortens_to_paths_only(self, tmp_path: Path) -> None:
        """When max_answer_chars is too small for the full JSON, paths-only is returned."""
        project = _create_csharp_project(tmp_path)
        project.project_config.active_workspace = "src/Main/Main.sln"
        agent = StubAgent(project)
        # Full JSON is ~269 chars; paths-only candidate with "too long" prefix is ~198 chars
        # Set limit so full doesn't fit but paths-only candidate does
        result = ListWorkspaceEntriesTool(agent).apply(max_answer_chars=210)
        assert "Workspace entry paths:" in result
        assert "src/Main/Main.sln" in result

    def test_list_workspace_entries_shortens_to_too_long_message(self, tmp_path: Path) -> None:
        """When all tiers are too long, the fallback 'too long' message is returned."""
        project = _create_csharp_project(tmp_path)
        project.project_config.active_workspace = "src/Main/Main.sln"
        agent = StubAgent(project)
        # Set limit very small so no candidate fits
        result = ListWorkspaceEntriesTool(agent).apply(max_answer_chars=50)
        assert "too long" in result
        assert "Workspace entry paths:" not in result

    def test_list_workspace_entries_no_shortening_for_large_limit(self, tmp_path: Path) -> None:
        """When max_answer_chars is large enough, full JSON is returned."""
        project = _create_csharp_project(tmp_path)
        project.project_config.active_workspace = "src/Main/Main.sln"
        agent = StubAgent(project)
        entries = json.loads(ListWorkspaceEntriesTool(agent).apply(max_answer_chars=50000))
        assert len(entries) == 3
        assert {"path": "src/Main/Main.sln", "kind": "solution", "selected": True} in entries


    def test_list_workspace_entries_name_contains_filter(self, tmp_path: Path) -> None:
        """name_contains filters entries by substring match."""
        project = _create_csharp_project(tmp_path)
        agent = StubAgent(project)
        entries = json.loads(ListWorkspaceEntriesTool(agent).apply(name_contains="Main"))
        assert len(entries) == 2  # Main.sln and Main.csproj
        assert all("Main" in e["path"] for e in entries)

    def test_list_workspace_entries_max_entries(self, tmp_path: Path) -> None:
        """max_entries caps the number of returned entries."""
        project = _create_csharp_project(tmp_path)
        agent = StubAgent(project)
        entries = json.loads(ListWorkspaceEntriesTool(agent).apply(max_entries=1))
        assert len(entries) == 1

    def test_list_workspace_entries_name_contains_case_insensitive(self, tmp_path: Path) -> None:
        """name_contains is case-insensitive."""
        project = _create_csharp_project(tmp_path)
        agent = StubAgent(project)
        entries = json.loads(ListWorkspaceEntriesTool(agent).apply(name_contains="main"))
        assert len(entries) == 2
