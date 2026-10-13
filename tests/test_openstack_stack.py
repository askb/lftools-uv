# SPDX-License-Identifier: EPL-1.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Tests for OpenStack stack operations."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from openstack.orchestration.v1.stack import Stack

from lftools_uv.openstack import stack as os_stack


@patch("lftools_uv.openstack.stack.openstack.connect")
def test_cost_with_no_servers_reports_zero(mock_connect, capsys):
    """A stack holding no servers costs nothing and succeeds."""
    mock_cloud = MagicMock()
    mock_connect.return_value = mock_cloud
    mock_cloud.orchestration.resources.return_value = []

    os_stack.cost("test-cloud", "empty-stack")

    assert capsys.readouterr().out.strip() == "total: 0.0"


@patch("lftools_uv.openstack.stack.openstack.connect")
def test_cost_exits_non_zero_when_enumeration_fails(mock_connect, capsys, caplog):
    """Stack enumeration failing must fail the command, not report 0.0.

    The per-server pricing lookup already falls back to 0.0 when the pricing
    API misbehaves, so reaching the outer handler means no total can be
    derived at all. Reporting 0.0 there would be indistinguishable from a
    genuinely idle stack, and a pipeline reading the figure would silently
    under-report.
    """
    mock_cloud = MagicMock()
    mock_connect.return_value = mock_cloud
    mock_cloud.orchestration.resources.side_effect = RuntimeError("enumeration failed")

    with pytest.raises(SystemExit) as exc_info:
        os_stack.cost("test-cloud", "broken-stack")

    assert exc_info.value.code == 1
    assert "total:" not in capsys.readouterr().out
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def _heat_stack(stack_id: str, name: str, status: str) -> Stack:
    """Build a Stack as openstacksdk returns it from the Heat API."""
    return Stack.existing(id=stack_id, stack_name=name, stack_status=status)


@patch("lftools_uv.openstack.stack.delete")
@patch("lftools_uv.openstack.stack.Jenkins")
@patch("lftools_uv.openstack.stack.openstack.connection.from_config")
def test_delete_stale_matches_by_name_and_deletes_by_id(mock_from_config, mock_jenkins, mock_delete):
    """Stacks of running builds are kept; orphans are deleted by id.

    openstacksdk exposes the Heat stack name as ``Stack.name``, leaving
    ``stack_name`` as None. Matching on ``stack_name`` marked every
    stack, including those of running builds, for deletion with a None
    identifier.
    """
    mock_cloud = MagicMock()
    mock_from_config.return_value = mock_cloud
    mock_cloud.search_stacks.return_value = [
        _heat_stack("id-active", "releng-csit-job-112", "CREATE_COMPLETE"),
        _heat_stack("id-orphan", "releng-csit-job-99", "CREATE_COMPLETE"),
        _heat_stack("id-failed", "releng-other-job-7", "CREATE_FAILED"),
        _heat_stack("id-busy", "releng-csit-job-113", "CREATE_IN_PROGRESS"),
    ]
    jenkins = mock_jenkins.return_value
    jenkins.url = "https://jenkins.example.org/releng/"
    jenkins.server.get_running_builds.return_value = [{"name": "csit-job", "number": 112}]

    os_stack.delete_stale("test-cloud", ["https://jenkins.example.org/releng"])

    assert [c.args for c in mock_delete.call_args_list] == [
        ("test-cloud", "id-orphan"),
        ("test-cloud", "id-failed"),
    ]


@patch("lftools_uv.openstack.stack.delete")
@patch("lftools_uv.openstack.stack.openstack.connection.from_config")
def test_delete_stale_with_no_stacks_exits_cleanly(mock_from_config, mock_delete):
    """No stacks means nothing to delete and a zero exit."""
    mock_from_config.return_value.search_stacks.return_value = []

    with pytest.raises(SystemExit) as exc_info:
        os_stack.delete_stale("test-cloud", ["https://jenkins.example.org/releng"])

    assert exc_info.value.code == 0
    mock_delete.assert_not_called()
