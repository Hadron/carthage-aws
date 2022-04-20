# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import os.path
from carthage import *
from carthage_aws import *
from carthage_aws.connection import run_in_executor
from carthage_base import *
from carthage.modeling import *
from carthage.cloud_init import WriteAuthorizedKeysPlugin
@inject(injector=Injector)
async def dev_layout(injector):
    ainjector = injector(AsyncInjector)
    
    class layout(CarthageLayout, AwsDnsManagement, AnsibleModelMixin):

        layout_name = 'aws_development'
        config = base_injector(ConfigLayout)

        async def register_carthage_debian(self):
            await self.ainjector(
                build_ami,
                name="Carthage-Debian",
                add_time_to_name=True)
            
        add_provider(DebianImage)
        add_provider(machine_implementation_key, MaybeLocalAwsVm)
        add_provider(InjectionKey(AwsHostedZone),
                     when_needed(AwsHostedZone, name=config.developer.domain))
        add_provider(WriteAuthorizedKeysPlugin, allow_multiple=True)
        add_provider(InjectionKey("aws_ami"),
                     image_provider(name='Carthage-Debian*',
                                    fallback=image_provider(owner=debian_ami_owner, name='debian-11-amd64-20220310-944')))
        
        domain = "autotest.photon.ac"
        class our_net(NetworkModel):
            v4_config = V4Config(network="192.168.100.0/24")

        class net_config(NetworkConfigModel):
            add('eth0', mac=None,
                net=InjectionKey("our_net"))

        domain = config.developer.domain

        @dynamic_name(config.developer.machine)
        class dev(CarthageServerRole, MachineModel, AsyncInjectable):
            cloud_init = True
            aws_instance_type = 't3.medium'
            name = config.developer.machine
            copy_in_checkouts = True
            layout_source = os.path.dirname(__file__)
            layout_destination = "carthage_aws"
            aws_image_size = 8
            
            class install_software(MachineCustomization):
                @setup_task("Install useful software")
                async def install(self):
                    await self.ssh("apt -y install emacs-nox mailutils- python3-boto3",
                                   _bg=True,
                                   _bg_exc=False)
            

                    
        class other(MachineModel):
            cloud_init = True
            aws_instance_type = 't3.micro'

    return await ainjector(layout)
