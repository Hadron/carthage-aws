# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import os.path
import carthage.ssh

from carthage import *
from carthage.modeling import *
from carthage.cloud_init import WriteAuthorizedKeysPlugin

from carthage_base import *
from carthage_base.proxy import *

from carthage_aws import *


class CarthageViewerSupport(ProxyServerRole, CertbotCertRole, ProxyServiceRole):
    add_provider(ProxyConfig)

    async def register_proxy_map(self):
        config = await self.ainjector.get_instance_async(ProxyConfig)
        config.add_proxy_service(
            ProxyService(
                service="carthage_viewer",
                upstream="http://127.0.0.1:3838/entanglement_ws",
                downstream=f"https://{self.name}/carthage_viewer/entanglement_ws",
            )
        )

    @property
    def certbot_email(self):
        return f'admin@{self.injector.get_instance("domain")}'


@inject(injector=Injector)
async def dev_layout(injector):
    ainjector = injector(AsyncInjector)

    class layout(CarthageLayout, PublicDnsManagement, AnsibleModelMixin):
        layout_name = "aws_development"
        config = base_injector(ConfigLayout)

        if config.developer.carthage_viewer:
            bases = (CarthageViewerSupport,)
        else:
            bases = tuple()

        add_provider(InjectionKey(carthage.ssh.SshKey), dependency_quote(None))

        class dev_sg(AwsSecurityGroup):
            ingress_rules = (
                SgRule(cidr="0.0.0.0/0", port=22),
                SgRule(cidr="0.0.0.0/0", port=443),
                SgRule(cidr="0.0.0.0/0", port=80),
            )
            name = "dev"

        async def register_carthage_debian(self):
            await self.ainjector(build_ami, name="Carthage-Debian", add_time_to_name=True)

        add_provider(DebianImage)
        add_provider(machine_implementation_key, MaybeLocalAwsVm)
        add_provider(
            InjectionKey(DnsZone, name=config.developer.domain, addressing="public"),
            when_needed(AwsHostedZone, name=config.developer.domain),
        )
        add_provider(WriteAuthorizedKeysPlugin, allow_multiple=True)
        add_provider(
            InjectionKey("aws_ami"),
            image_provider(
                name="Carthage-Debian*",
                fallback=image_provider(owner=debian_ami_owner, name="debian-12-amd64-*"),
            ),
        )

        class our_net(NetworkModel):
            v4_config = V4Config(network="192.168.100.0/24")
            aws_security_groups = ["dev"]

        class net_config(NetworkConfigModel):
            add("eth0", mac=None, net=InjectionKey("our_net"))

        domain = config.developer.domain

        @dynamic_name(config.developer.machine)
        class dev(CarthageServerRole, *bases, MachineModel, AsyncInjectable):
            cloud_init = True
            aws_instance_type = "t3.medium"
            name = config.developer.machine
            copy_in_checkouts = True
            layout_source = os.path.dirname(__file__)
            layout_destination = "carthage_aws"
            aws_image_size = 20
            if config.developer.iam_profile:
                aws_iam_profile = config.developer.iam_profile

            class install_software(MachineCustomization):
                @setup_task("Install useful software")
                async def install(self):
                    await self.ssh(
                        "apt -y install emacs-nox mailutils- python3-boto3 podman containers-storage",
                        _bg=True,
                        _bg_exc=False,
                    )

        class other(MachineModel):
            cloud_init = True
            aws_instance_type = "t3.micro"

    return await ainjector(layout)
