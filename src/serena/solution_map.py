"""
Maps solutions to their member project directories and vice versa.

Enables answering 'which solution contains a given file?' without running ``dotnet sln list``
at query time. Built from solution/project data during indexing and persisted to disk.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Hashable

from solidlsp.util.cache import load_cache, save_cache

log = logging.getLogger(__name__)

SOLUTION_MAP_VERSION: tuple[str, int] = ("SolutionMembershipMap", 1)


@dataclass(frozen=True, slots=True)
class SolutionEntry:
    """Represents a solution with its member project directories.

    :param solution_path: solution file path relative to the project root.
    :param project_directories: tuple of project directory paths relative to the project root.
    """

    solution_path: str
    project_directories: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "solution_path": self.solution_path,
            "project_directories": list(self.project_directories),
        }


class SolutionMembershipMap:
    """Maps solutions to project directories and provides reverse lookups.

    Built from ``dotnet sln list`` output at index time and persisted for
    instant startup queries.
    """

    def __init__(self) -> None:
        # forward: solution path → SolutionEntry
        self._by_solution: dict[str, SolutionEntry] = {}
        # reverse: project directory → set of solution paths
        self._by_project_dir: dict[str, set[str]] = {}

    # -- build interface -------------------------------------------------------

    def add(self, solution_path: str, project_directories: tuple[str, ...] | list[str]) -> None:
        """Register a solution with its member project directories.

        :param solution_path: solution file path relative to the project root.
        :param project_directories: project directory paths relative to the project root.
        """
        dirs = tuple(self._normalise_path(d) for d in project_directories)
        sol = self._normalise_path(solution_path)
        entry = SolutionEntry(solution_path=sol, project_directories=dirs)
        self._by_solution[sol] = entry
        for d in dirs:
            self._by_project_dir.setdefault(d, set()).add(sol)

    @property
    def total_solutions(self) -> int:
        """Number of registered solutions."""
        return len(self._by_solution)

    @property
    def total_project_dirs(self) -> int:
        """Number of distinct project directories."""
        return len(self._by_project_dir)

    @property
    def solution_paths(self) -> list[str]:
        """All registered solution paths."""
        return list(self._by_solution.keys())

    # -- query interface -------------------------------------------------------

    def solutions_for_file(self, relative_path: str) -> list[str]:
        """Return solution paths whose project directories contain the given file.

        A file is considered to belong to a project directory if the file's path
        starts with that directory (prefix match with path separator awareness).

        :param relative_path: file path relative to the project root.
        :return: list of matching solution paths, sorted alphabetically.
        """
        normalised = self._normalise_path(relative_path)
        matches: set[str] = set()
        for project_dir, solutions in self._by_project_dir.items():
            if self._path_is_under(normalised, project_dir):
                matches.update(solutions)
        return sorted(matches)

    def solutions_for_directory(self, relative_dir: str) -> list[str]:
        """Return solution paths whose project directories are or are under the given directory.

        :param relative_dir: directory path relative to the project root.
        :return: list of matching solution paths, sorted alphabetically.
        """
        normalised = self._normalise_path(relative_dir)
        matches: set[str] = set()
        for project_dir, solutions in self._by_project_dir.items():
            if self._path_is_under(project_dir, normalised) or self._path_is_under(normalised, project_dir):
                matches.update(solutions)
        return sorted(matches)

    def project_directories_for_solution(self, solution_path: str) -> tuple[str, ...]:
        """Return the project directories belonging to a solution.

        :param solution_path: solution file path relative to the project root.
        :return: tuple of project directory paths.
        """
        normalised = self._normalise_path(solution_path)
        entry = self._by_solution.get(normalised)
        return entry.project_directories if entry is not None else ()

    def get_entry(self, solution_path: str) -> SolutionEntry | None:
        """Return the SolutionEntry for a solution, or None if not found."""
        return self._by_solution.get(self._normalise_path(solution_path))

    def iter_entries(self) -> list[SolutionEntry]:
        """Return all solution entries."""
        return list(self._by_solution.values())

    # -- persistence -----------------------------------------------------------

    def save(self, path: str, extra_version: Hashable = None) -> None:
        """Persist the map to disk.

        :param path: file path for the cache file.
        :param extra_version: optional extra version component.
        """
        version = (SOLUTION_MAP_VERSION, extra_version) if extra_version is not None else SOLUTION_MAP_VERSION
        serialisable = {sol: list(entry.project_directories) for sol, entry in self._by_solution.items()}
        save_cache(path, version, serialisable)
        log.info("Saved solution membership map with %d solutions (%d project dirs) to %s",
                 self.total_solutions, self.total_project_dirs, path)

    @classmethod
    def load(cls, path: str, extra_version: Hashable = None) -> SolutionMembershipMap | None:
        """Load a previously persisted map from disk.

        :param path: file path of the cache file.
        :param extra_version: must match the value used when saving.
        :return: the loaded map, or ``None`` if the file is missing or version-mismatched.
        """
        version = (SOLUTION_MAP_VERSION, extra_version) if extra_version is not None else SOLUTION_MAP_VERSION
        try:
            data = load_cache(path, version)
        except (FileNotFoundError, OSError):
            return None
        if data is None:
            return None

        smap = cls()
        for sol, dirs in data.items():
            smap.add(sol, dirs)

        log.info("Loaded solution membership map with %d solutions (%d project dirs) from %s",
                 smap.total_solutions, smap.total_project_dirs, path)
        return smap

    # -- internal helpers ------------------------------------------------------

    @staticmethod
    def _normalise_path(p: str) -> str:
        """Normalize path separators to forward slashes for consistent matching."""
        return p.replace(os.sep, "/").rstrip("/")

    @staticmethod
    def _path_is_under(child: str, parent: str) -> bool:
        """Check if *child* is under the *parent* directory (or is the same path).

        Both paths should already be normalised (forward slashes, no trailing slash).
        """
        if child == parent:
            return True
        return child.startswith(parent + "/")
