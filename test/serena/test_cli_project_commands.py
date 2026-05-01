"""Tests for CLI project commands (create, index)."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from serena.cli import ProjectCommands, TopLevelCommands, find_project_root
from serena.config.serena_config import ProjectConfig
from serena.workspace_selection import resolve_indexing_scope

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture
def temp_project_dir():
    """Create a temporary directory for testing."""
    tmpdir = tempfile.mkdtemp()
    try:
        yield tmpdir
    finally:
        # if windows, wait a bit to avoid PermissionError on cleanup
        if os.name == "nt":
            time.sleep(0.2)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_project_dir_with_python_file():
    """Create a temporary directory with a Python file for testing."""
    tmpdir = tempfile.mkdtemp()
    try:
        # Create a simple Python file so language detection works
        py_file = os.path.join(tmpdir, "test.py")
        with open(py_file, "w") as f:
            f.write("def hello():\n    pass\n")
        yield tmpdir
    finally:
        # if windows, wait a bit to avoid PermissionError on cleanup
        if os.name == "nt":
            time.sleep(0.2)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def cli_runner():
    """Create a CliRunner for testing Click commands."""
    return CliRunner()


class TestProjectCreate:
    """Tests for 'project create' command."""

    def test_create_basic_with_language(self, cli_runner, temp_project_dir):
        """Test basic project creation with explicit language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path), f"project.yml not found at {yml_path}"

    def test_create_auto_detect_language(self, cli_runner, temp_project_dir_with_python_file):
        """Test project creation with auto-detected language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir_with_python_file])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_create_with_name(self, cli_runner, temp_project_dir):
        """Test project creation with custom name and explicit language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--name", "my-custom-project", "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_create_with_language(self, cli_runner, temp_project_dir):
        """Test project creation with specified language."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "python" in result.output.lower()

    def test_create_with_multiple_languages(self, cli_runner, temp_project_dir):
        """Test project creation with multiple languages."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir, "--language", "python", "--language", "typescript"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output

    def test_create_with_invalid_language(self, cli_runner, temp_project_dir):
        """Test project creation with invalid language raises error."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir, "--language", "invalid-lang"],
        )
        assert result.exit_code != 0, "Should fail with invalid language"
        assert "Unknown language" in result.output or "invalid-lang" in result.output

    def test_create_already_exists(self, cli_runner, temp_project_dir):
        """Test that creating a project twice fails gracefully."""
        # Create once with explicit language
        result1 = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result1.exit_code == 0

        # Try to create again - should fail gracefully
        result2 = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result2.exit_code != 0, "Should fail when project.yml already exists"
        assert "already exists" in result2.output.lower()
        assert "Error:" in result2.output  # Should be user-friendly error

    def test_create_with_index_flag(self, cli_runner, temp_project_dir_with_python_file):
        """Test project creation with --index flag performs indexing."""
        result = cli_runner.invoke(
            ProjectCommands.create,
            [temp_project_dir_with_python_file, "--language", "python", "--index", "--log-level", "ERROR", "--timeout", "5"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Generated project" in result.output
        assert "Indexing project" in result.output

        # Verify project.yml was created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path)

        # Verify cache directory was created (proof of indexing)
        cache_dir = os.path.join(temp_project_dir_with_python_file, ".serena", "cache")
        assert os.path.exists(cache_dir), "Cache directory should exist after indexing"

    def test_create_without_index_flag(self, cli_runner, temp_project_dir):
        """Test that project creation without --index does NOT perform indexing."""
        result = cli_runner.invoke(ProjectCommands.create, [temp_project_dir, "--language", "python"])
        assert result.exit_code == 0
        assert "Generated project" in result.output
        assert "Indexing" not in result.output

        # Verify cache directory was NOT created
        cache_dir = os.path.join(temp_project_dir, ".serena", "cache")
        assert not os.path.exists(cache_dir), "Cache directory should not exist without --index"


class TestProjectIndex:
    """Tests for 'project index' command."""

    def test_index_auto_creates_project_with_files(self, cli_runner, temp_project_dir_with_python_file):
        """Test that index command auto-creates project.yml if it doesn't exist (with source files)."""
        result = cli_runner.invoke(ProjectCommands.index, [temp_project_dir_with_python_file, "--log-level", "ERROR", "--timeout", "5"])
        # Should succeed and perform indexing
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert "Auto-creating" in result.output or "Indexing" in result.output

        # Verify project.yml was auto-created
        yml_path = os.path.join(temp_project_dir_with_python_file, ".serena", "project.yml")
        assert os.path.exists(yml_path), "project.yml should be auto-created"

    def test_index_with_explicit_language(self, cli_runner, temp_project_dir):
        """Test index with explicit --language for empty directory."""
        result = cli_runner.invoke(
            ProjectCommands.index,
            [temp_project_dir, "--language", "python", "--log-level", "ERROR", "--timeout", "5"],
        )
        # Should succeed even without source files if language is explicit
        assert result.exit_code == 0, f"Command failed: {result.output}"

        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_index_with_language_auto_creates(self, cli_runner, temp_project_dir):
        """Test index with --language option for auto-creation."""
        result = cli_runner.invoke(
            ProjectCommands.index,
            [temp_project_dir, "--language", "python", "--log-level", "ERROR"],
        )
        assert result.exit_code == 0 or "Indexing" in result.output

        yml_path = os.path.join(temp_project_dir, ".serena", "project.yml")
        assert os.path.exists(yml_path)

    def test_index_is_equivalent_to_create_with_index(self, cli_runner, temp_project_dir_with_python_file):
        """Test that 'index' behaves like 'create --index' for new projects."""
        # Use manual temp directory creation with Windows-safe cleanup
        # to avoid PermissionError on Windows CI when language servers hold file locks
        dir1 = tempfile.mkdtemp()
        dir2 = tempfile.mkdtemp()

        try:
            # Setup both directories with same file
            for d in [dir1, dir2]:
                with open(os.path.join(d, "test.py"), "w") as f:
                    f.write("def hello():\n    pass\n")

            # Run 'create --index' on dir1
            result1 = cli_runner.invoke(
                ProjectCommands.create, [dir1, "--language", "python", "--index", "--log-level", "ERROR", "--timeout", "5"]
            )

            # Run 'index' on dir2
            result2 = cli_runner.invoke(ProjectCommands.index, [dir2, "--language", "python", "--log-level", "ERROR", "--timeout", "5"])

            # Both should succeed
            assert result1.exit_code == 0, f"create --index failed: {result1.output}"
            assert result2.exit_code == 0, f"index failed: {result2.output}"

            # Both should create project.yml
            assert os.path.exists(os.path.join(dir1, ".serena", "project.yml"))
            assert os.path.exists(os.path.join(dir2, ".serena", "project.yml"))

            # Both should create cache (proof of indexing)
            assert os.path.exists(os.path.join(dir1, ".serena", "cache"))
            assert os.path.exists(os.path.join(dir2, ".serena", "cache"))
        finally:
            # Windows-safe cleanup: wait for file handles to be released
            if os.name == "nt":
                time.sleep(0.2)
            # Use ignore_errors to handle lingering file locks on Windows
            shutil.rmtree(dir1, ignore_errors=True)
            shutil.rmtree(dir2, ignore_errors=True)


    def test_index_passes_explicit_scope_to_index_project(self, cli_runner, temp_project_dir, monkeypatch):
        """Test that the index command forwards the explicit scope to the indexing helper."""
        captured_scope: dict[str, str | None] = {"value": None}

        class StubSerenaConfig:
            def get_registered_project(self, project, autoregister=True):
                return object()

        def stub_index_project(registered_project, log_level, timeout, scope=None):
            captured_scope["value"] = scope

        monkeypatch.setattr("serena.cli.SerenaConfig.from_config_file", lambda: StubSerenaConfig())
        monkeypatch.setattr(ProjectCommands, "_index_project", staticmethod(stub_index_project))

        result = cli_runner.invoke(
            ProjectCommands.index,
            [temp_project_dir, "--scope", "src/Main/Main.sln", "--log-level", "ERROR"],
        )

        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert captured_scope["value"] == "src/Main/Main.sln"


class TestProjectIndexingScope:
    """Tests for workspace-aware indexing scope resolution."""

    @staticmethod
    def _stub_solution_listing(monkeypatch, output: str, return_code: int = 0):
        monkeypatch.setattr("serena.workspace_selection.shutil.which", lambda command: "dotnet" if command == "dotnet" else None)
        monkeypatch.setattr(
            "serena.workspace_selection.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=return_code, stdout=output, stderr=""),
        )

    def test_resolve_indexing_scope_uses_explicit_directory_scope(self, temp_project_dir):
        scoped_dir = Path(temp_project_dir) / "src" / "Main"
        scoped_dir.mkdir(parents=True)

        indexing_scope = resolve_indexing_scope(temp_project_dir, active_workspace=None, explicit_scope="src/Main")

        assert indexing_scope.relative_paths == ("src/Main",)
        assert indexing_scope.display_path == "src/Main"
        assert indexing_scope.source == "explicit_scope"
        assert indexing_scope.csharp_workspace_selection is not None
        assert indexing_scope.csharp_workspace_selection.active_workspace is None
        assert indexing_scope.csharp_workspace_selection.workspace_root == "src/Main"

    def test_resolve_indexing_scope_expands_active_workspace_solution_projects(self, monkeypatch, temp_project_dir):
        solution_file = Path(temp_project_dir) / "Main.sln"
        solution_file.touch()
        self._stub_solution_listing(monkeypatch, "Project(s)\n----------\nsrc/App/App.csproj\nlibs/Core/Core.csproj\n")

        indexing_scope = resolve_indexing_scope(temp_project_dir, active_workspace="Main.sln")

        assert indexing_scope.relative_paths == ("src/App", "libs/Core")
        assert indexing_scope.display_path == "Main.sln"
        assert indexing_scope.source == "active_workspace"
        assert indexing_scope.csharp_workspace_selection is not None
        assert indexing_scope.csharp_workspace_selection.active_workspace == "Main.sln"
        assert indexing_scope.csharp_workspace_selection.workspace_root is None

    def test_resolve_indexing_scope_falls_back_to_project_root_for_stale_workspace(self, temp_project_dir):
        indexing_scope = resolve_indexing_scope(temp_project_dir, active_workspace="src/Missing/Missing.sln")

        assert indexing_scope.relative_paths == ("",)
        assert indexing_scope.display_path == "."
        assert indexing_scope.source == "project_root"
        assert indexing_scope.csharp_workspace_selection is None

    def test_resolve_indexing_scope_rejects_invalid_explicit_file(self, temp_project_dir):
        invalid_file = Path(temp_project_dir) / "notes.txt"
        invalid_file.write_text("notes", encoding="utf-8")

        with pytest.raises(ValueError, match="must point to a directory or a .sln, .slnx, or .csproj file"):
            resolve_indexing_scope(temp_project_dir, active_workspace=None, explicit_scope="notes.txt")

    def test_index_project_expands_solution_projects_when_gathering_files(self, monkeypatch, temp_project_dir):
        solution_file = Path(temp_project_dir) / "Main.sln"
        solution_file.touch()
        self._stub_solution_listing(monkeypatch, "Project(s)\n----------\nsrc/App/App.csproj\nlibs/Core/Core.csproj\n")

        gather_source_files = Mock(side_effect=[[], []])
        language_server_manager = SimpleNamespace(save_all_caches=Mock(), stop_all=Mock())
        create_language_server_manager = Mock(return_value=language_server_manager)
        serena_data_folder = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_data_folder, exist_ok=True)
        project = SimpleNamespace(
            project_root=temp_project_dir,
            gather_source_files=gather_source_files,
            get_active_workspace=lambda: "Main.sln",
            create_language_server_manager=create_language_server_manager,
            path_to_serena_data_folder=lambda: serena_data_folder,
        )
        registered_project = SimpleNamespace(get_project_instance=lambda serena_config: project)

        monkeypatch.setattr("serena.cli.SerenaConfig.from_config_file", lambda: object())

        ProjectCommands._index_project(registered_project, "ERROR", timeout=5)

        assert gather_source_files.call_args_list == [(("src/App",),), (("libs/Core",),)]
        assert create_language_server_manager.call_args.kwargs["csharp_workspace_selection"].active_workspace == "Main.sln"

    def test_index_project_uses_explicit_solution_for_csharp_server(self, monkeypatch, temp_project_dir):
        solution_file = Path(temp_project_dir) / "Scoped.sln"
        solution_file.touch()
        self._stub_solution_listing(monkeypatch, "Project(s)\n----------\nsrc/App/App.csproj\n")

        serena_data_folder = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_data_folder, exist_ok=True)
        language_server_manager = SimpleNamespace(save_all_caches=Mock(), stop_all=Mock())
        create_language_server_manager = Mock(return_value=language_server_manager)
        project = SimpleNamespace(
            project_root=temp_project_dir,
            gather_source_files=Mock(return_value=[]),
            get_active_workspace=lambda: "Main.sln",
            create_language_server_manager=create_language_server_manager,
            path_to_serena_data_folder=lambda: serena_data_folder,
        )
        registered_project = SimpleNamespace(get_project_instance=lambda serena_config: project)

        monkeypatch.setattr("serena.cli.SerenaConfig.from_config_file", lambda: object())

        ProjectCommands._index_project(registered_project, "ERROR", timeout=5, scope="Scoped.sln")

        workspace_selection = create_language_server_manager.call_args.kwargs["csharp_workspace_selection"]
        assert workspace_selection.active_workspace == "Scoped.sln"
        assert workspace_selection.workspace_root is None


class TestProjectCreateHelper:
    """Tests for _create_project helper method."""

    def test_create_project_helper_returns_config(self, temp_project_dir):
        """Test that _create_project returns a ProjectConfig with explicit language."""
        config = ProjectCommands._create_project(temp_project_dir, "test-project", ("python",)).project_config
        assert isinstance(config, ProjectConfig)
        assert config.project_name == "test-project"

    def test_create_project_helper_with_auto_detect(self, temp_project_dir_with_python_file):
        """Test _create_project with auto-detected language."""
        config = ProjectCommands._create_project(temp_project_dir_with_python_file, "my-project", ()).project_config
        assert isinstance(config, ProjectConfig)
        assert config.project_name == "my-project"
        assert len(config.languages) >= 1

    def test_create_project_helper_with_languages(self, temp_project_dir):
        """Test _create_project with language specification."""
        config = ProjectCommands._create_project(temp_project_dir, None, ("python", "typescript")).project_config
        assert isinstance(config, ProjectConfig)
        assert len(config.languages) >= 1

    def test_create_project_helper_file_exists_error(self, temp_project_dir):
        """Test _create_project raises error if project.yml exists."""
        # Create project first with explicit language
        ProjectCommands._create_project(temp_project_dir, None, ("python",))

        # Try to create again - should raise FileExistsError
        with pytest.raises(FileExistsError):
            ProjectCommands._create_project(temp_project_dir, None, ("python",))


class TestFindProjectRoot:
    """Tests for find_project_root helper with virtual chroot boundary."""

    def test_finds_serena_from_subdirectory(self, temp_project_dir):
        """Test that .serena/project.yml is found when searching from a subdirectory."""
        serena_dir = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_dir)
        Path(os.path.join(serena_dir, "project.yml")).touch()
        subdir = os.path.join(temp_project_dir, "src", "nested")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_serena_preferred_over_git(self, temp_project_dir):
        """Test that .serena/project.yml takes priority over .git at the same level."""
        serena_dir = os.path.join(temp_project_dir, ".serena")
        os.makedirs(serena_dir)
        Path(os.path.join(serena_dir, "project.yml")).touch()
        os.makedirs(os.path.join(temp_project_dir, ".git"))

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_project_dir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.isdir(os.path.join(result, ".serena"))
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_git_used_as_fallback(self, temp_project_dir):
        """Test that .git is found when no .serena exists."""
        os.makedirs(os.path.join(temp_project_dir, ".git"))
        subdir = os.path.join(temp_project_dir, "src")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is not None
            assert os.path.samefile(result, temp_project_dir)
        finally:
            os.chdir(original_cwd)

    def test_falls_back_to_none_when_no_markers(self, temp_project_dir):
        """Test falls back to None when no markers exist within boundary."""
        subdir = os.path.join(temp_project_dir, "src")
        os.makedirs(subdir)

        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            result = find_project_root(root=temp_project_dir)
            assert result is None
        finally:
            os.chdir(original_cwd)


class TestProjectFromCwdMutualExclusivity:
    """Tests for --project-from-cwd mutual exclusivity."""

    def test_project_from_cwd_with_project_flag_fails(self, cli_runner):
        """Test that --project-from-cwd with --project raises error."""
        result = cli_runner.invoke(
            TopLevelCommands.start_mcp_server,
            ["--project-from-cwd", "--project", "/some/path"],
        )
        assert result.exit_code != 0
        assert "cannot be used with" in result.output


if __name__ == "__main__":
    # For manual testing, you can run this file directly:
    # uv run pytest test/serena/test_cli_project_commands.py -v
    pytest.main([__file__, "-v"])
