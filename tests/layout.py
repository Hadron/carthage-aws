# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import os
from pathlib import Path
from carthage import *
from carthage_aws import *
from carthage_base import CarthageServerRole, DebianImage
from carthage.modeling import *
from carthage.cloud_init import WriteAuthorizedKeysPlugin

class test_layout(CarthageLayout, AwsDnsManagement, AnsibleModelMixin):

    layout_name = 'aws_test'

    add_provider(DebianImage)
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
        disk_sizes = (20,80)

        class static_ip_net_config(NetworkConfigModel):
            add('eth0', mac=None, net=InjectionKey("our_net"),
                v4_config=V4Config(
                    dhcp=False,
                    address='192.168.100.237'
                )
            )

    class test_no_ready(MachineModel):
        name="test-vm-no-ready"
        cloud_init = True
        aws_instance_type = "t2.micro"

    class does_not_exist(MachineModel):
        aws_readonly = True
        
    class some_volume(AwsVolume):
        volume_size = 4
        aws_availability_zone = 'us-east-1a'
        name = "some_volume"
    class instance_for_volume(MachineModel, AsyncInjectable):
        aws_instance_type = 't3.micro'

        class volume(AwsVolume):
            volume_size = 4
            name = "attach_me"
            

    # The layout already has AnsibleModelMixin, but this will force a
    # new inventory generation when image_builder is built.
    # Alternatively we would need to wait for dns to catch up and have
    # a non-dns option when tests are run by individual developers who
    # don't have access to the carthage-ci dns zone.
    class image_builder(CarthageServerRole, MachineModel, AnsibleModelMixin, SetupTaskMixin, AsyncInjectable):
        add_provider(machine_implementation_key, MaybeLocalAwsVm)
        cloud_init = True

        aws_instance_type = 't3.medium'
        name = 'image-builder'
        layout_source = os.path.dirname(__file__)
        layout_destination = "carthage_aws"
        aws_image_size = 8
        aws_iam_profile = "ec2_full"
        config_info = mako_task("config.yml.mako", output="carthage_aws/config.yml", config=InjectionKey(ConfigLayout))

        class install(MachineCustomization):

            @setup_task("install software")
            async def install_software(self):
                await self.ssh("apt -y install python3-pip rsync python3-pytest ansible",
                               _bg=True, _bg_exc=False)
                await self.ssh("pip3 install boto3", _bg=True, _bg_exc=False)
                await self.ssh('systemctl enable --now systemd-resolved', _bg=True, _bg_exc=False)

            install_mako = install_mako_task('model')

