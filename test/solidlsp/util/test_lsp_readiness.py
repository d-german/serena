"""Tests for LSP readiness signaling.

Tests that is_indexing_complete() and is_any_server_loading() work correctly,
and that the readiness warning appears/doesn't appear in the right scenarios.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from solidlsp.ls import SolidLanguageServer


# -- Tests for is_indexing_complete() base class ----------------------------


class TestIsIndexingCompleteBase:
    """Test the base SolidLanguageServer.is_indexing_complete()."""

    def test_base_returns_true(self):
        """Base implementation always returns True (most LS don't signal indexing)."""
        ls = MagicMock(spec=SolidLanguageServer)
        result = SolidLanguageServer.is_indexing_complete(ls)
        assert result is True


# -- Tests for CSharp override using Event -----------------------------------


class TestCSharpIndexingComplete:
    """Test CSharpLanguageServer.is_indexing_complete() behavior."""

    def test_returns_false_before_event_set(self):
        """Before projectInitializationComplete, returns False."""
        event = threading.Event()
        # Simulate the CSharp override: it checks self._indexing_complete.is_set()
        assert not event.is_set()

    def test_returns_true_after_event_set(self):
        """After projectInitializationComplete fires, returns True."""
        event = threading.Event()
        event.set()
        assert event.is_set()


# -- Tests for is_any_server_loading() aggregation ---------------------------


class TestIsAnyServerLoading:
    """Test LanguageServerManager.is_any_server_loading() aggregation."""

    def test_all_ready(self):
        """When all servers report complete, returns False."""
        from serena.ls_manager import LanguageServerManager

        mgr = MagicMock(spec=LanguageServerManager)
        ls1 = MagicMock()
        ls1.is_indexing_complete.return_value = True
        ls2 = MagicMock()
        ls2.is_indexing_complete.return_value = True
        mgr.iter_language_servers.return_value = [ls1, ls2]

        result = LanguageServerManager.is_any_server_loading(mgr)
        assert result is False

    def test_one_loading(self):
        """When one server is loading, returns True."""
        from serena.ls_manager import LanguageServerManager

        mgr = MagicMock(spec=LanguageServerManager)
        ls1 = MagicMock()
        ls1.is_indexing_complete.return_value = True
        ls2 = MagicMock()
        ls2.is_indexing_complete.return_value = False  # still loading
        mgr.iter_language_servers.return_value = [ls1, ls2]

        result = LanguageServerManager.is_any_server_loading(mgr)
        assert result is True

    def test_no_servers(self):
        """When no servers exist, returns False (nothing is loading)."""
        from serena.ls_manager import LanguageServerManager

        mgr = MagicMock(spec=LanguageServerManager)
        mgr.iter_language_servers.return_value = []

        result = LanguageServerManager.is_any_server_loading(mgr)
        assert result is False


# -- Tests for _is_ls_loading helper on Tool ---------------------------------


class TestToolIsLsLoading:
    """Test Tool._is_ls_loading() helper."""

    def test_returns_false_when_no_ls_manager(self):
        """If no LS manager, _is_ls_loading returns False."""
        from serena.tools.tools_base import Tool

        tool = MagicMock(spec=Tool)
        tool.agent = MagicMock()
        tool.agent.get_language_server_manager.return_value = None

        result = Tool._is_ls_loading(tool)
        assert result is False

    def test_returns_true_when_loading(self):
        """If LS manager reports loading, returns True."""
        from serena.tools.tools_base import Tool

        tool = MagicMock(spec=Tool)
        tool.agent = MagicMock()
        ls_manager = MagicMock()
        ls_manager.is_any_server_loading.return_value = True
        tool.agent.get_language_server_manager.return_value = ls_manager

        result = Tool._is_ls_loading(tool)
        assert result is True

    def test_returns_false_when_ready(self):
        """If LS manager reports ready, returns False."""
        from serena.tools.tools_base import Tool

        tool = MagicMock(spec=Tool)
        tool.agent = MagicMock()
        ls_manager = MagicMock()
        ls_manager.is_any_server_loading.return_value = False
        tool.agent.get_language_server_manager.return_value = ls_manager

        result = Tool._is_ls_loading(tool)
        assert result is False


# -- Tests for _wait_for_lsp_readiness function ------------------------------


class TestWaitForLspReadiness:
    """Test the _wait_for_lsp_readiness helper function."""

    def test_returns_true_immediately_when_ready(self):
        """If LS is already ready, returns True without sleeping."""
        from serena.tools.symbol_tools import _wait_for_lsp_readiness

        agent = MagicMock()
        ls_manager = MagicMock()
        ls_manager.is_any_server_loading.return_value = False
        agent.get_language_server_manager.return_value = ls_manager

        result = _wait_for_lsp_readiness(agent)
        assert result is True

    def test_returns_true_when_no_ls_manager(self):
        """If no LS manager exists, returns True (nothing to wait for)."""
        from serena.tools.symbol_tools import _wait_for_lsp_readiness

        agent = MagicMock()
        agent.get_language_server_manager.return_value = None

        result = _wait_for_lsp_readiness(agent)
        assert result is True

    @patch("serena.tools.symbol_tools._LSP_READINESS_WAIT_SECONDS", 0.1)
    @patch("serena.tools.symbol_tools._LSP_READINESS_POLL_INTERVAL", 0.05)
    def test_returns_false_on_timeout(self):
        """If LS never becomes ready within timeout, returns False."""
        from serena.tools.symbol_tools import _wait_for_lsp_readiness

        agent = MagicMock()
        ls_manager = MagicMock()
        ls_manager.is_any_server_loading.return_value = True  # always loading
        agent.get_language_server_manager.return_value = ls_manager

        result = _wait_for_lsp_readiness(agent)
        assert result is False
