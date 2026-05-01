import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sensai.util import logging

log = logging.getLogger(__name__)

WORKSPACE_FILE_SUFFIXES = (".sln", ".slnx", ".csproj")


@dataclass(frozen=True)
class ResolvedWorkspaceEntry:
    path: str
    workspace_root: str
    kind: Literal["solution", "project"]


@dataclass(frozen=True)
class CSharpWorkspaceSelectionSettings:
    active_workspace: str | None = None
    workspace_root: str | None = None


@dataclass(frozen=True)
class IndexingScope:
    relative_paths: tuple[str, ...]
    display_path: str
    source: Literal["explicit_scope", "active_workspace", "project_root"]
    csharp_workspace_selection: CSharpWorkspaceSelectionSettings | None = None


def resolve_active_workspace_entry(project_root_path: str | Path, active_workspace: str | None) -> ResolvedWorkspaceEntry | None:
    """
    Resolve the configured workspace entry against the project root.
    """
    # rejecting absent configuration
    if not isinstance(active_workspace, str) or not active_workspace:
        return None

    # normalising the selected path
    project_root = Path(project_root_path).resolve()
    selected_path = (project_root / active_workspace).resolve()

    # rejecting paths outside the project root
    try:
        selected_path.relative_to(project_root)
    except ValueError:
        log.warning(
            "Ignoring active_workspace '%s' because it is outside the project root %s",
            active_workspace,
            project_root_path,
        )
        return None

    # rejecting missing workspace files
    if not selected_path.is_file():
        log.warning(
            "Ignoring active_workspace '%s' because it does not resolve to a file under %s",
            active_workspace,
            project_root_path,
        )
        return None

    # resolving supported workspace entry kinds
    suffix = selected_path.suffix.lower()
    if suffix in (".sln", ".slnx"):
        return ResolvedWorkspaceEntry(path=str(selected_path), workspace_root=str(selected_path.parent), kind="solution")
    if suffix == ".csproj":
        return ResolvedWorkspaceEntry(path=str(selected_path), workspace_root=str(selected_path.parent), kind="project")

    log.warning(
        "Ignoring active_workspace '%s' because only .sln, .slnx, and .csproj entries are supported",
        active_workspace,
    )
    return None


def resolve_indexing_scope(
    project_root_path: str | Path,
    active_workspace: str | None,
    explicit_scope: str | None = None,
) -> IndexingScope:
    """
    Resolve the indexing scope relative to the project root.
    """
    # honoring an explicit CLI scope first
    if explicit_scope is not None:
        return _resolve_explicit_indexing_scope(project_root_path, explicit_scope)

    # honoring a configured active workspace next
    resolved_workspace = resolve_active_workspace_entry(project_root_path, active_workspace)
    if resolved_workspace is not None:
        try:
            return _resolve_workspace_indexing_scope(project_root_path, resolved_workspace, active_workspace)
        except ValueError:
            log.warning(
                "Falling back to project-root indexing because active_workspace '%s' could not be expanded",
                active_workspace,
            )

    # falling back to the project root
    return IndexingScope(relative_paths=("",), display_path=".", source="project_root")


def _resolve_explicit_indexing_scope(project_root_path: str | Path, explicit_scope: str) -> IndexingScope:
    # validating the explicit scope input
    if not isinstance(explicit_scope, str) or not explicit_scope.strip():
        raise ValueError("Indexing scope must be a non-empty relative path.")

    project_root = Path(project_root_path).resolve()
    selected_path = (project_root / explicit_scope).resolve()

    try:
        selected_path.relative_to(project_root)
    except ValueError as error:
        raise ValueError(f"Indexing scope '{explicit_scope}' is outside the project root.") from error

    if not selected_path.exists():
        raise ValueError(f"Indexing scope '{explicit_scope}' does not exist under the project root.")

    # resolving the indexing roots
    if selected_path.is_dir():
        relative_directory = _relative_path_from_root(project_root, selected_path)
        relative_paths = (relative_directory,)
        csharp_workspace_selection = CSharpWorkspaceSelectionSettings(workspace_root=relative_directory)
    elif selected_path.is_file() and selected_path.suffix.lower() == ".csproj":
        relative_paths = (_relative_path_from_root(project_root, selected_path.parent),)
        csharp_workspace_selection = CSharpWorkspaceSelectionSettings(
            active_workspace=_relative_path_from_root(project_root, selected_path)
        )
    elif selected_path.is_file() and selected_path.suffix.lower() in (".sln", ".slnx"):
        relative_paths = _resolve_solution_project_directories(project_root, selected_path)
        csharp_workspace_selection = CSharpWorkspaceSelectionSettings(
            active_workspace=_relative_path_from_root(project_root, selected_path)
        )
    else:
        raise ValueError(
            f"Indexing scope '{explicit_scope}' must point to a directory or a .sln, .slnx, or .csproj file."
        )

    return IndexingScope(
        relative_paths=relative_paths,
        display_path=explicit_scope,
        source="explicit_scope",
        csharp_workspace_selection=csharp_workspace_selection,
    )


def _resolve_workspace_indexing_scope(
    project_root_path: str | Path,
    workspace_entry: ResolvedWorkspaceEntry,
    active_workspace: str,
) -> IndexingScope:
    # normalizing the selected workspace path
    normalized_active_workspace = _relative_path_from_root(project_root_path, workspace_entry.path)
    csharp_workspace_selection = CSharpWorkspaceSelectionSettings(active_workspace=normalized_active_workspace)

    # reusing project directories for selected projects
    if workspace_entry.kind == "project":
        return IndexingScope(
            relative_paths=(_relative_path_from_root(project_root_path, workspace_entry.workspace_root),),
            display_path=active_workspace,
            source="active_workspace",
            csharp_workspace_selection=csharp_workspace_selection,
        )

    # expanding selected solutions to member project directories
    return IndexingScope(
        relative_paths=_resolve_solution_project_directories(project_root_path, Path(workspace_entry.path)),
        display_path=active_workspace,
        source="active_workspace",
        csharp_workspace_selection=csharp_workspace_selection,
    )


def _resolve_solution_project_directories(project_root_path: str | Path, solution_path: Path) -> tuple[str, ...]:
    # ensuring dotnet CLI is available
    dotnet_path = shutil.which("dotnet")
    if dotnet_path is None:
        raise ValueError("The dotnet CLI is required to expand solution indexing scope.")

    # listing project files from the solution
    result = subprocess.run(
        [dotnet_path, "sln", str(solution_path), "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Failed to list projects for solution '{solution_path}'.")

    # collecting unique project directories
    project_root = Path(project_root_path).resolve()
    relative_paths: list[str] = []
    for project_entry in result.stdout.splitlines():
        project_entry = project_entry.strip()
        if not project_entry or project_entry in {"Project(s)", "----------"}:
            continue
        if not project_entry.lower().endswith(".csproj"):
            continue
        project_path = (solution_path.parent / project_entry).resolve()
        try:
            project_path.relative_to(project_root)
        except ValueError:
            raise ValueError(f"Solution '{solution_path}' references a project outside the project root.") from None
        relative_path = _relative_path_from_root(project_root, project_path.parent)
        if relative_path not in relative_paths:
            relative_paths.append(relative_path)

    if not relative_paths:
        raise ValueError(f"Solution '{solution_path}' does not contain any indexable project entries.")

    return tuple(relative_paths)


def _relative_path_from_root(project_root_path: str | Path, target_path: str | Path) -> str:
    relative_path = os.path.relpath(target_path, start=project_root_path)
    if relative_path == ".":
        return ""
    return Path(relative_path).as_posix()
