"""Unit tests for database metrics."""

import time
from unittest.mock import Mock, patch

import pytest

from observe_kit.metrics.db import QueryRecorder, wrap_connections


def test_query_recorder_counts_queries() -> None:
    """Test that QueryRecorder counts queries."""
    recorder = QueryRecorder()
    mock_execute = Mock(return_value="result")

    result = recorder(mock_execute, "SELECT 1", None, False, None)

    assert result == "result"
    assert recorder.count == 1
    mock_execute.assert_called_once_with("SELECT 1", None, False, None)


def test_query_recorder_tracks_time() -> None:
    """Test that QueryRecorder tracks query time."""
    recorder = QueryRecorder()

    def slow_execute(sql, params, many, context):
        time.sleep(0.01)
        return "result"

    result = recorder(slow_execute, "SELECT 1", None, False, None)

    assert result == "result"
    assert recorder.count == 1
    assert recorder.total_time > 0


def test_query_recorder_handles_exception() -> None:
    """Test that QueryRecorder handles exceptions in execute."""
    recorder = QueryRecorder()

    def failing_execute(sql, params, many, context):
        raise ValueError("Test error")

    with pytest.raises(ValueError):
        recorder(failing_execute, "SELECT 1", None, False, None)

    # Should still increment count and track time
    assert recorder.count == 1
    assert recorder.total_time > 0


def test_query_recorder_multiple_queries() -> None:
    """Test that QueryRecorder tracks multiple queries."""
    recorder = QueryRecorder()
    mock_execute = Mock(return_value="result")

    recorder(mock_execute, "SELECT 1", None, False, None)
    recorder(mock_execute, "SELECT 2", None, False, None)
    recorder(mock_execute, "SELECT 3", None, False, None)

    assert recorder.count == 3
    assert mock_execute.call_count == 3


def test_wrap_connections_wraps_all_connections() -> None:
    """Test that wrap_connections wraps all database connections."""
    with patch("observe_kit.metrics.db.connections") as mock_connections:
        mock_conn1 = Mock()
        mock_conn2 = Mock()
        mock_conn3 = Mock()
        mock_remover1 = Mock()
        mock_remover2 = Mock()
        mock_remover3 = Mock()
        mock_conn1.execute_wrapper.return_value = mock_remover1
        mock_conn2.execute_wrapper.return_value = mock_remover2
        mock_conn3.execute_wrapper.return_value = mock_remover3
        mock_connections.all.return_value = [mock_conn1, mock_conn2, mock_conn3]

        recorder = QueryRecorder()
        remover = wrap_connections(recorder)

        assert mock_conn1.execute_wrapper.call_count == 1
        assert mock_conn2.execute_wrapper.call_count == 1
        assert mock_conn3.execute_wrapper.call_count == 1
        assert mock_conn1.execute_wrapper.call_args[0][0] == recorder
        assert mock_conn2.execute_wrapper.call_args[0][0] == recorder
        assert mock_conn3.execute_wrapper.call_args[0][0] == recorder

        # Test remover function
        remover()
        mock_remover1.assert_called_once()
        mock_remover2.assert_called_once()
        mock_remover3.assert_called_once()


def test_wrap_connections_empty_connections() -> None:
    """Test that wrap_connections handles empty connections list."""
    with patch("observe_kit.metrics.db.connections") as mock_connections:
        mock_connections.all.return_value = []

        recorder = QueryRecorder()
        remover = wrap_connections(recorder)

        # Should not raise
        remover()


def test_wrap_connections_remover_returns_list() -> None:
    """Test that wrap_connections remover calls all removers."""
    with patch("observe_kit.metrics.db.connections") as mock_connections:
        mock_conn = Mock()
        mock_remover = Mock()
        mock_conn.execute_wrapper.return_value = mock_remover
        mock_connections.all.return_value = [mock_conn]

        recorder = QueryRecorder()
        remover = wrap_connections(recorder)

        remover()
        mock_remover.assert_called_once()

