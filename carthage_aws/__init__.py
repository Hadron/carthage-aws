# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from ipaddress import IPv4Network
import carthage
from carthage.dependency_injection import *
from carthage.config import ConfigSchema
from carthage.config.types import ConfigString

__all__ = []

from .connection import AwsConnection

__all__ += ["AwsConnection"]

from .network import (
    AwsVirtualPrivateCloud,
    AwsSubnet,
    SgRule,
    AwsSecurityGroup,
    VpcAddress,
    network_for_existing_vm,
    AwsRouteTable,
    AwsInternetGateway,
    AwsNatGateway,
)

__all__ += [
    "AwsVirtualPrivateCloud",
    "AwsSubnet",
    "AwsSecurityGroup",
    "SgRule",
    "VpcAddress",
    "network_for_existing_vm",
    "AwsRouteTable",
    "AwsInternetGateway",
    "AwsNatGateway",
]

from .dns import AwsHostedZone, AwsPrivateHostedZone, AwsDnsManagement

__all__ += ["AwsHostedZone", "AwsPrivateHostedZone", "AwsDnsManagement"]

from .vm import AwsVm, MaybeLocalAwsVm

__all__ += ["AwsVm", "MaybeLocalAwsVm"]

from .image import (
    AwsImage,
    image_provider,
    debian_ami_owner,
    ImageBuilderVolume,
    AttachImageBuilderVolume,
    build_ami,
)

__all__ += [
    "AwsImage",
    "image_provider",
    "debian_ami_owner",
    "AttachImageBuilderVolume",
    "ImageBuilderVolume",
    "build_ami",
]

from .ebs import AwsVolume, attach_volume_task, AwsSnapshot

__all__ += ["AwsVolume", "attach_volume_task", "AwsSnapshot"]


class AwsConfig(ConfigSchema, prefix="aws"):
    #:aws_access_key_id
    access_key_id: ConfigString

    #:aws_secret_access_key
    secret_access_key: ConfigString

    #: Profile name from ~/.aws/credentials
    profile: ConfigString
    #:AWS region
    region: ConfigString

    #:AWS VPC name
    vpc_name: ConfigString

    #: AWS VPC ID
    vpc_id: ConfigString
    #: CIDR block to allocate to created VPC
    vpc_cidr: IPv4Network = IPv4Network("192.168.0.0/16")
    #: DnsHostnamesEnable setting for VPCs
    # https://docs.aws.amazon.com/vpc/latest/userguide/vpc-dns.html#vpc-dns-support
    vpc_dns_hostnames_enabled: bool = False


@inject(injector=Injector)
def enable_new_aws_connection(injector):
    conn = AwsConnection
    injector.add_provider(InjectionKey(AwsConnection), conn)


@inject(injector=Injector)
def carthage_plugin(injector):
    # avoid circular imports
    # pylint: disable=import-outside-toplevel
    from . import connection

    injector.add_provider(connection.AwsDeployableFinder)
    injector.add_provider(AwsVirtualPrivateCloud)
    injector.add_provider(AwsSubnet, allow_multiple=True)
    injector(enable_new_aws_connection)
