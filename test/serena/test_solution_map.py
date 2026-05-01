"""Unit tests for SolutionMembershipMap."""
import os
import tempfile

import pytest

from serena.solution_map import SolutionMembershipMap, SolutionEntry


@pytest.fixture
def sample_map() -> SolutionMembershipMap:
    """A map with two solutions sharing some project directories."""
    smap = SolutionMembershipMap()
    smap.add("Core.sln", ["src/Core/Data", "src/Core/Domain", "src/Shared/Utils"])
    smap.add("Web.sln", ["src/Web/Api", "src/Web/Frontend", "src/Shared/Utils"])
    return smap


class TestBasics:
    """Tests for add, counts, and simple queries."""

    def test_empty_map(self) -> None:
        smap = SolutionMembershipMap()
        assert smap.total_solutions == 0
        assert smap.total_project_dirs == 0
        assert smap.solutions_for_file("src/anything.cs") == []
        assert smap.project_directories_for_solution("Nonexistent.sln") == ()

    def test_add_and_counts(self, sample_map: SolutionMembershipMap) -> None:
        assert sample_map.total_solutions == 2
        # 5 unique dirs: Core/Data, Core/Domain, Shared/Utils, Web/Api, Web/Frontend
        assert sample_map.total_project_dirs == 5

    def test_solution_paths(self, sample_map: SolutionMembershipMap) -> None:
        paths = sorted(sample_map.solution_paths)
        assert paths == ["Core.sln", "Web.sln"]


class TestSolutionsForFile:
    """Tests for solutions_for_file."""

    def test_file_in_single_solution(self, sample_map: SolutionMembershipMap) -> None:
        solutions = sample_map.solutions_for_file("src/Core/Data/Models/Entity.cs")
        assert solutions == ["Core.sln"]

    def test_file_in_shared_project(self, sample_map: SolutionMembershipMap) -> None:
        solutions = sample_map.solutions_for_file("src/Shared/Utils/StringHelper.cs")
        assert sorted(solutions) == ["Core.sln", "Web.sln"]

    def test_file_in_no_solution(self, sample_map: SolutionMembershipMap) -> None:
        assert sample_map.solutions_for_file("src/Tests/SomeTest.cs") == []

    def test_file_at_exact_project_dir(self, sample_map: SolutionMembershipMap) -> None:
        # a file AT the project directory itself (edge case: path == directory)
        # This should NOT match because it's not a file under the directory
        # Actually the project_dir IS the directory, so file AT that path is valid
        solutions = sample_map.solutions_for_file("src/Core/Data")
        assert solutions == ["Core.sln"]

    def test_backslash_normalisation(self, sample_map: SolutionMembershipMap) -> None:
        """Verify Windows backslash paths work correctly."""
        solutions = sample_map.solutions_for_file("src\\Core\\Data\\Models\\Entity.cs")
        assert solutions == ["Core.sln"]


class TestProjectDirectories:
    """Tests for project_directories_for_solution."""

    def test_known_solution(self, sample_map: SolutionMembershipMap) -> None:
        dirs = sample_map.project_directories_for_solution("Core.sln")
        assert set(dirs) == {"src/Core/Data", "src/Core/Domain", "src/Shared/Utils"}

    def test_unknown_solution(self, sample_map: SolutionMembershipMap) -> None:
        assert sample_map.project_directories_for_solution("Unknown.sln") == ()


class TestSolutionsForDirectory:
    """Tests for solutions_for_directory."""

    def test_parent_dir_matches_children(self, sample_map: SolutionMembershipMap) -> None:
        """A parent directory should match solutions whose project dirs are under it."""
        solutions = sample_map.solutions_for_directory("src/Core")
        assert solutions == ["Core.sln"]

    def test_shared_parent(self, sample_map: SolutionMembershipMap) -> None:
        solutions = sample_map.solutions_for_directory("src/Shared")
        assert sorted(solutions) == ["Core.sln", "Web.sln"]


class TestEntries:
    """Tests for get_entry and iter_entries."""

    def test_get_entry(self, sample_map: SolutionMembershipMap) -> None:
        entry = sample_map.get_entry("Core.sln")
        assert entry is not None
        assert entry.solution_path == "Core.sln"
        assert "src/Core/Data" in entry.project_directories

    def test_get_entry_miss(self, sample_map: SolutionMembershipMap) -> None:
        assert sample_map.get_entry("Unknown.sln") is None

    def test_iter_entries(self, sample_map: SolutionMembershipMap) -> None:
        entries = sample_map.iter_entries()
        assert len(entries) == 2

    def test_entry_to_dict(self) -> None:
        entry = SolutionEntry(solution_path="X.sln", project_directories=("a", "b"))
        d = entry.to_dict()
        assert d["solution_path"] == "X.sln"
        assert d["project_directories"] == ["a", "b"]


class TestPersistence:
    """Tests for save/load round-tripping."""

    def test_roundtrip(self, sample_map: SolutionMembershipMap) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "solmap.pkl")
            sample_map.save(path)

            loaded = SolutionMembershipMap.load(path)
            assert loaded is not None
            assert loaded.total_solutions == sample_map.total_solutions
            assert loaded.total_project_dirs == sample_map.total_project_dirs

            # verify data
            assert sorted(loaded.solution_paths) == sorted(sample_map.solution_paths)
            for sol in sample_map.solution_paths:
                assert set(loaded.project_directories_for_solution(sol)) == set(sample_map.project_directories_for_solution(sol))

    def test_roundtrip_with_extra_version(self, sample_map: SolutionMembershipMap) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "solmap.pkl")
            sample_map.save(path, extra_version="v1")
            assert SolutionMembershipMap.load(path, extra_version="v1") is not None
            assert SolutionMembershipMap.load(path, extra_version="v2") is None

    def test_load_nonexistent(self) -> None:
        assert SolutionMembershipMap.load("/nonexistent/path.pkl") is None

    def test_empty_roundtrip(self) -> None:
        smap = SolutionMembershipMap()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "solmap.pkl")
            smap.save(path)
            loaded = SolutionMembershipMap.load(path)
            assert loaded is not None
            assert loaded.total_solutions == 0
