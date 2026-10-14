# SPDX-License-Identifier: EPL-1.0
##############################################################################
# Copyright (c) 2024 The Linux Foundation and others.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Eclipse Public License v1.0
# which accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
##############################################################################
"""Typer CLI configuration for openstack command."""

import logging
import re
import subprocess

import typer

from lftools_uv.openstack import image as os_image
from lftools_uv.openstack import object as os_object
from lftools_uv.openstack import server as os_server
from lftools_uv.openstack import stack as os_stack
from lftools_uv.openstack import volume as os_volume

log = logging.getLogger(__name__)

openstack_app = typer.Typer(
    name="openstack",
    help="Provide an interface to OpenStack.",
    rich_markup_mode="markdown",
)

image_app = typer.Typer(name="image", help="Command for manipulating images.")
object_app = typer.Typer(name="object", help="Command for manipulating objects.")
server_app = typer.Typer(name="server", help="Command for manipulating servers.")
stack_app = typer.Typer(name="stack", help="Command for manipulating stacks.")
volume_app = typer.Typer(name="volume", help="Command for manipulating volumes.")

# Add sub-apps to main app
openstack_app.add_typer(image_app, name="image")
openstack_app.add_typer(object_app, name="object")
openstack_app.add_typer(server_app, name="server")
openstack_app.add_typer(stack_app, name="stack")
openstack_app.add_typer(volume_app, name="volume")


# Global state to store os_cloud
class OpenStackState:
    os_cloud: str = ""


state = OpenStackState()


@openstack_app.callback()
def openstack_callback(
    os_cloud: str = typer.Option(..., "--os-cloud", envvar="OS_CLOUD", help="OpenStack cloud name"),
) -> None:
    """Provide an interface to OpenStack."""
    state.os_cloud = os_cloud


# Image commands
@image_app.command("cleanup")
def image_cleanup(
    ci_managed: bool = typer.Option(
        False, "--ci-managed", help="Filter only images that have the ci_managed=yes metadata set."
    ),
    days: int = typer.Option(0, "--days", help="Find images older than or equal to days."),
    hide_public: bool = typer.Option(False, "--hide-public", help="Ignore public images."),
    clouds: str | None = typer.Option(
        None,
        "--clouds",
        help="Clouds (as defined in clouds.yaml) to remove images from. If not passed will assume from os-cloud parameter. (optional)",
    ),
) -> None:
    """Cleanup old images."""
    os_image.cleanup(state.os_cloud, ci_managed=ci_managed, days=days, hide_public=hide_public, clouds=clouds)


@image_app.command("list")
def image_list(
    ci_managed: bool = typer.Option(
        False, "--ci-managed", help="Filter only images that have the ci_managed=yes metadata set."
    ),
    days: int = typer.Option(0, "--days", help="Find images older than or equal to days."),
    hide_public: bool = typer.Option(False, "--hide-public", help="Ignore public images."),
) -> None:
    """List cloud images."""
    os_image.list(state.os_cloud, ci_managed=ci_managed, days=days, hide_public=hide_public)


@image_app.command("share")
def image_share(
    image: str = typer.Argument(..., help="Image to share"),
    dest: list[str] = typer.Argument(..., help="Destination tenants"),
) -> None:
    """Share image with another tenant."""
    os_image.share(state.os_cloud, image, dest)


@image_app.command("upload")
def image_upload(
    image: str = typer.Argument(..., help="Image file to upload"),
    name: list[str] = typer.Argument(..., help="Name for the uploaded image"),
    disk_format: str = typer.Option("raw", "--disk-format", help="Disk format of image. (default: raw)"),
) -> None:
    """Upload image to OpenStack cloud."""
    name_str = " ".join(name)

    disktype = subprocess.check_output(["qemu-img", "info", image]).decode("utf-8")
    pattern = disk_format
    result = re.search(pattern, disktype)
    if result:
        log.info("PASS Image format matches %s", disk_format)
    else:
        log.error("Image is not in %s format", disk_format)
        raise typer.Exit(1)

    os_image.upload(state.os_cloud, image, name_str, disk_format)


# Object commands
@object_app.command("list")
def object_list() -> None:
    """List available containers."""
    os_object.list_containers(state.os_cloud)


# Server commands
@server_app.command("cleanup")
def server_cleanup(
    days: int = typer.Option(0, "--days", help="Find servers older than or equal to days."),
) -> None:
    """Cleanup old servers."""
    os_server.cleanup(state.os_cloud, days=days)


@server_app.command("list")
def server_list(
    days: int = typer.Option(0, "--days", help="Find servers older than or equal to days."),
) -> None:
    """List cloud servers."""
    os_server.list(state.os_cloud, days=days)


@server_app.command("remove")
def server_remove(
    server: str = typer.Argument(..., help="Server to remove"),
    minutes: int = typer.Option(0, "--minutes", help="Delete server if older than x minutes."),
) -> None:
    """Remove servers."""
    os_server.remove(state.os_cloud, server_name=server, minutes=minutes)


# Stack commands
@stack_app.command("create")
def stack_create(
    name: str = typer.Argument(..., help="Stack name"),
    template_file: str = typer.Argument(..., help="Template file"),
    parameter_file: str = typer.Argument(..., help="Parameter file"),
    timeout: int = typer.Option(900, "--timeout", help="Stack create timeout in seconds."),
    tries: int = typer.Option(2, "--tries", help="Number of tries before giving up."),
) -> None:
    """Create stack."""
    os_stack.create(state.os_cloud, name, template_file, parameter_file, timeout, tries)


@stack_app.command("delete")
def stack_delete(
    name_or_id: str = typer.Argument(..., help="Stack name or ID"),
    force: bool = typer.Option(False, "--force", help="Ignore timeout and continue with next stack."),
    timeout: int = typer.Option(900, "--timeout", help="Stack delete timeout in seconds."),
) -> None:
    """Delete stack."""
    os_stack.delete(state.os_cloud, name_or_id, force=force, timeout=timeout)


@stack_app.command("cost")
def stack_cost(
    stack_name: str = typer.Argument(..., help="Stack name"),
    timeout: int = typer.Option(60, "--timeout", help="Timeout in seconds for cost retrieval operations (default: 60)"),
) -> None:
    """Get Total Stack Cost."""
    os_stack.cost(state.os_cloud, stack_name, timeout=timeout)


@stack_app.command("delete-stale")
def stack_delete_stale(
    jenkins_urls: list[str] = typer.Argument(..., help="Jenkins URLs"),
    min_age_hours: int = typer.Option(
        os_stack.STALE_MIN_AGE_HOURS,
        "--min-age-hours",
        min=0,
        help="Protect stacks younger than this many hours from deletion.",
    ),
) -> None:
    """Delete stale stacks.

    This command checks Jenkins and OpenStack for stacks that do not appear in
    both places. If a stack is no longer available in Jenkins but is in
    OpenStack then it is considered stale. Stale stacks are then deleted.
    """
    os_stack.delete_stale(state.os_cloud, jenkins_urls, min_age_hours=min_age_hours)


# Volume commands
@volume_app.command("cleanup")
def volume_cleanup(
    days: int = typer.Option(0, "--days", help="Find volumes older than or equal to days."),
) -> None:
    """Cleanup old volumes."""
    os_volume.cleanup(state.os_cloud, days=days)


@volume_app.command("list")
def volume_list(
    days: int = typer.Option(0, "--days", help="Find volumes older than or equal to days."),
) -> None:
    """List cloud volumes."""
    os_volume.list(state.os_cloud, days=days)


@volume_app.command("remove")
def volume_remove(
    volume_id: str = typer.Argument(..., help="Volume ID"),
    minutes: int = typer.Option(0, "--minutes", help="Delete volumes if older than x minutes."),
) -> None:
    """Remove volumes."""
    os_volume.remove(state.os_cloud, volume_id=volume_id, minutes=minutes)


def get_openstack_app():
    """Return the OpenStack Typer app."""
    return openstack_app
