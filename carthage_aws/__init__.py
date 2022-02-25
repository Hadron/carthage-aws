# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage
from carthage.dependency_injection import *
from carthage.config import ConfigSchema
from carthage.config.types import ConfigString

from .connection import AwsConnection
from .network import AwsVirtualPrivateCloud, AwsSubnet


class AwsConfig(ConfigSchema, prefix = "aws"):
    #:aws_access_key_id
    access_key_id: ConfigString

    #:aws_secret_access_key
    secret_access_key: ConfigString

    #:AWS region
    region: ConfigString

    #:AWS VPC name
    vpc_name: ConfigString

    #: AWS VPC ID
    vpc_id: ConfigString

@inject(injector=Injector)
def enable_new_aws_connection(injector):
    conn = AwsConnection
    injector.add_provider(InjectionKey(AwsConnection), conn)

@inject(injector=Injector)
def carthage_plugin(injector):
    injector.add_provider(AwsVirtualPrivateCloud, allow_multiple=True)
    injector.add_provider(AwsSubnet, allow_multiple = True)
    injector(enable_new_aws_connection)
    
