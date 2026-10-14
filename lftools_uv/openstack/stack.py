# -*- code: utf-8 -*-
# SPDX-License-Identifier: EPL-1.0
##############################################################################
# Copyright (c) 2018 The Linux Foundation and others.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Eclipse Public License v1.0
# which accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
##############################################################################
"""stack related sub-commands for openstack command."""

from __future__ import annotations

__author__ = "Thanh Ha"

import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

# aislop-ignore-file hallucinated-import -- the declared openstacksdk dependency provides the `openstack` import package
import openstack
import openstack.connection
from openstack.cloud.exc import OpenStackCloudHTTPError

from lftools_uv.jenkins import Jenkins
from lftools_uv.output import echo

log = logging.getLogger(__name__)

# Vexxhost publishes per-flavour pricing used by the `stack cost` command.
# Format arguments are the flavour name and the elapsed run time in seconds.
_PRICING_API_URL = "https://pricing.vexxhost.net/v1/pricing/%s/cost?seconds=%d"

# python-jenkins' get_running_builds() silently treats a node as idle when the
# node query returns HTTP 500 or when the node disappears mid-query. Both are
# routine with ephemeral cloud agents, so the build list can come back short
# without raising. Stacks younger than the longest build-timeout are therefore
# never safe to reap on the strength of that list alone.
STALE_MIN_AGE_HOURS = 24


def create(
    os_cloud: str, name: str, template_file: str, parameter_file: str, timeout: int = 900, tries: int = 2
) -> None:
    """Create a heat stack from a template_file and a parameter_file."""
    cloud = openstack.connection.from_config(cloud=os_cloud)
    stack_success = False
    stack = None

    log.info("Creating stack %s", name)
    for _attempt in range(tries):
        try:
            stack = cloud.create_stack(
                name, template_file=template_file, environment_files=[parameter_file], timeout=timeout, rollback=False
            )
        except OpenStackCloudHTTPError as e:
            if cloud.search_stacks(name):
                log.error("Stack with name %s already exists.", name)
            else:
                log.error("%s", e)
            sys.exit(1)

        stack_id = stack.id  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
        t_end = time.time() + timeout
        while time.time() < t_end:
            time.sleep(10)
            stack = cloud.get_stack(stack_id)

            if stack.status == "CREATE_IN_PROGRESS":  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
                log.info("Waiting to initialize infrastructure...")
            elif stack.status == "CREATE_COMPLETE":  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
                log.info("Stack initialization successful.")
                stack_success = True
                break
            elif stack.status == "CREATE_FAILED":  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
                log.warning("Failed to initialize stack. Reason: %s", stack.status_reason)  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
                if delete(os_cloud, stack_id):
                    break
            else:
                log.warning("Unexpected status: %s", stack.status)  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]

        if stack_success:
            break

    echo("------------------------------------")
    echo("Stack Details")
    echo("------------------------------------")
    cloud.pprint(stack)
    echo("------------------------------------")

    if not stack_success:
        log.error("Failed to create stack %s after %d tries.", name, tries)
        sys.exit(1)


def cost(os_cloud: str, stack_name: str, timeout: int = 60) -> None:
    """Get current cost info for the stack.

    Return the cost in dollars & cents (x.xx).

    Args:
        os_cloud: OpenStack cloud name from clouds.yaml
        stack_name: Name of the stack to calculate cost for
        timeout: Timeout in seconds for network operations (default: 60)
    """

    def get_server_cost(server_id: str) -> float:
        try:
            flavor, seconds = get_server_info(server_id)
            with urllib.request.urlopen(_PRICING_API_URL % (flavor, seconds), timeout=timeout) as response:  # nosec
                data = json.loads(response.read())
            return float(data["cost"])
        except (TimeoutError, urllib.error.URLError) as e:
            log.warning("Failed to get cost for server %s: %s", server_id, e)
            log.warning("Returning 0 cost for this server")
            return 0.0
        except Exception:
            log.exception("Unexpected error getting cost for server %s", server_id)
            return 0.0

    def parse_iso8601_time(time: str) -> datetime:
        return datetime.strptime(time, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)

    def get_server_info(server_id: str) -> tuple[str, float]:
        server = cloud.compute.find_server(server_id)  # pyright: ignore[reportAttributeAccessIssue]
        if server is None:
            raise RuntimeError(f"Server {server_id} not found")
        diff = datetime.now(UTC) - parse_iso8601_time(server.launched_at)
        return server.flavor["original_name"], diff.total_seconds()

    def get_server_ids(stack_name: str) -> list[str]:
        servers = get_resources_by_type(stack_name, "OS::Nova::Server")
        return [s["physical_resource_id"] for s in servers]

    def get_resources_by_type(stack_name: str, resource_type: str) -> list[Any]:
        resources = get_stack_resources(stack_name)
        return [r for r in resources if r.resource_type == resource_type]

    def get_stack_resources(stack_name: str) -> list[Any]:
        resources = []

        def _is_nested(resource: Any) -> bool:
            link_types = [link["rel"] for link in resource.links]
            if "nested" in link_types:
                return True
            return False

        for r in cloud.orchestration.resources(stack_name):  # pyright: ignore[reportAttributeAccessIssue]
            if _is_nested(r):
                resources += get_stack_resources(r.physical_resource_id)
                continue
            resources.append(r)
        return resources

    cloud = openstack.connect(os_cloud)

    try:
        total_cost = 0.0
        server_ids = get_server_ids(stack_name)

        if not server_ids:
            log.info("No servers found in stack %s", stack_name)
            echo("total: 0.0")
            return

        for server in server_ids:
            total_cost += get_server_cost(server)
        echo("total: " + str(total_cost))
    except Exception:
        # The per-server pricing lookup above already falls back to 0.0 on a
        # flaky pricing API, so reaching here means stack enumeration itself
        # failed and no total can be derived. Reporting 0.0 would be
        # indistinguishable from a genuinely idle stack, so fail instead.
        log.exception("Unable to calculate cost for stack %s", stack_name)
        sys.exit(1)


def delete(os_cloud: str, name_or_id: str, force: bool = False, timeout: int = 900) -> bool | None:
    """Delete a stack.

    Return True if delete was successful.
    """
    cloud = openstack.connection.from_config(cloud=os_cloud)
    log.info("Deleting stack %s", name_or_id)
    cloud.delete_stack(name_or_id)

    t_end = time.time() + timeout
    while time.time() < t_end:
        time.sleep(10)
        stack = cloud.get_stack(name_or_id)

        if not stack or stack.status == "DELETE_COMPLETE":  # pyright: ignore[reportAttributeAccessIssue]
            log.info("Successfully deleted stack %s", name_or_id)
            return True
        elif stack.status == "DELETE_IN_PROGRESS":  # pyright: ignore[reportAttributeAccessIssue]
            log.info("Waiting for stack to delete...")
        elif stack.status == "DELETE_FAILED":  # pyright: ignore[reportAttributeAccessIssue]
            log.warning("Failed to delete stack %s. Reason: %s", name_or_id, stack.status_reason)  # pyright: ignore[reportAttributeAccessIssue]
            log.info("Retrying delete...")
            cloud.delete_stack(name_or_id)
        else:
            log.warning("Unexpected delete status: %s", stack.status)  # pyright: ignore[reportAttributeAccessIssue]
            log.info("Retrying delete...")
            cloud.delete_stack(name_or_id)

    log.error("Failed to delete stack %s", name_or_id)
    if not force:
        return False
    return None


def _stack_age_hours(stack: Any) -> float | None:
    """Return the age of a stack in hours, or None if it cannot be determined."""
    created = getattr(stack, "created_at", None)
    if not created:
        return None
    try:
        created_dt = datetime.fromisoformat(created)
    except ValueError:
        return None
    if created_dt.tzinfo is None:  # Heat reports UTC
        created_dt = created_dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created_dt).total_seconds() / 3600


def delete_stale(os_cloud: str, jenkins_servers: list[str], min_age_hours: int = STALE_MIN_AGE_HOURS) -> None:
    """Search Jenkins and OpenStack for orphaned stacks and remove them.

    An orphaned stack is a stack that is not known in any of the Jenkins
    servers passed into this function.
    """
    cloud = openstack.connection.from_config(cloud=os_cloud)
    stacks = cloud.search_stacks()
    if not stacks:
        log.debug("No stacks to delete.")
        sys.exit(0)

    builds = []
    for server in jenkins_servers:
        jenkins = Jenkins(server)
        jenkins_url = jenkins.url.rstrip("/")
        silo_parts = jenkins_url.split("/")

        if len(silo_parts) == 4:  # https://jenkins.opendaylight.org/releng
            silo = silo_parts[3]
        elif len(silo_parts) == 3:  # https://jenkins.onap.org
            silo = "production"
        else:
            log.error("Unexpected URL pattern, could not detect silo.")
            sys.exit(1)

        log.debug(f"Fetching running builds from {jenkins_url}")
        running_builds = jenkins.server.get_running_builds()
        for build in running_builds:
            build_name = "{}-{}-{}".format(silo, build.get("name"), build.get("number"))
            log.debug(f"    {build_name}")
            builds.append(build_name)

    log.debug("Active stacks")
    for stack in stacks:
        if stack.status == "CREATE_COMPLETE" or stack.status == "CREATE_FAILED" or stack.status == "DELETE_FAILED":
            # openstacksdk maps Heat's stack_name to Stack.name; the
            # stack_name attribute is always None.
            log.debug(f"    {stack.name}")

            if stack.status == "DELETE_FAILED":
                cloud.pprint(stack)

            if stack.name not in builds:
                age_hours = _stack_age_hours(stack)
                if age_hours is None:
                    log.warning(f"Skipping {stack.name}, could not determine its age.")
                elif age_hours < min_age_hours:
                    log.debug(f"        Skipping, only {age_hours:.1f}h old (< {min_age_hours}h)")
                else:
                    log.debug("        >>>> Marked for deletion <<<<")
                    delete(os_cloud, stack.id)

        else:
            continue
