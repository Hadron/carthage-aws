# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *
from carthage_aws import *
from carthage.modeling import *

class test_layout(CarthageLayout, AwsDnsManagement):

    layout_name = 'aws_test'

    add_provider(machine_implementation_key, dependency_quote(AwsVm))
    add_provider(InjectionKey(AwsHostedZone),
                 when_needed(AwsHostedZone, name="autotest.photon.ac"))

    class our_net(NetworkModel):
        v4_config = V4Config(network="192.168.100.0/24")

    class net_config(NetworkConfigModel):
        add('eth0', mac=None,
            net=InjectionKey("our_net"))

    class test_vm(MachineModel):
        name="test-vm"
        cloud_init = True
        key = 'main'
        imageid = "ami-06ed7917b75fcaf17"
        size = "t2.micro"
