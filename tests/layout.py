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
from carthage.cloud_init import WriteAuthorizedKeysPlugin

class test_layout(CarthageLayout, AwsDnsManagement, AnsibleModelMixin):

    layout_name = 'aws_test'

    add_provider(machine_implementation_key, dependency_quote(AwsVm))
    add_provider(InjectionKey(AwsHostedZone),
                 when_needed(AwsHostedZone, name="autotest.photon.ac"))
    add_provider(WriteAuthorizedKeysPlugin, allow_multiple=True)
    #aws_key_name = 'main'
    add_provider(InjectionKey('aws_ami'), image_provider(owner=debian_ami_owner, name='debian-11-amd64-20220310-944'))

    domain = "autotest.photon.ac"
    class our_net(NetworkModel):
        v4_config = V4Config(network="192.168.100.0/24")

    class net_config(NetworkConfigModel):
        add('eth0', mac=None,
            net=InjectionKey("our_net"))

    class test_vm(MachineModel):
        name="test-vm"
        cloud_init = True
        aws_instance_type = "t2.micro"

    class does_not_exist(MachineModel):
        aws_readonly = True
        
    class some_volume(AwsVolume):
        volume_size = 4
        aws_availability_zone = 'us-east-1a'
        name = "some_volume"
        
