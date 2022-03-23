# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *
from carthage_aws import *
from carthage_base import *
from carthage.modeling import *
from carthage.cloud_init import WriteAuthorizedKeysPlugin
@inject(injector=Injector)
async def dev_layout(injector):
    ainjector = injector(AsyncInjector)
    
    class layout(CarthageLayout, AwsDnsManagement, AnsibleModelMixin):

        layout_name = 'aws_development'
        config = base_injector(ConfigLayout)

        add_provider(machine_implementation_key, dependency_quote(AwsVm))
        add_provider(InjectionKey(AwsHostedZone),
                     when_needed(AwsHostedZone, name=config.developer.domain))
        add_provider(WriteAuthorizedKeysPlugin, allow_multiple=True)
        aws_ami = "ami-06ed7917b75fcaf17"
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
            copy_in_checkouts = False

            class install_software(MachineCustomization):
                @setup_task("Install useful software")
                async def install(self):
                    await self.ssh("apt -y install emacs-nox mailutils-",
                                   _bg=True,
                                   _bg_exc=False)

    return await ainjector(layout)
