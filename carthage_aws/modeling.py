# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage
from carthage import *
from carthage.modeling import *
from carthage.modeling.base import MachineImplementation
from carthage.modeling.implementation import ModelingContainer, ModelingBase
from carthage.config import ConfigLayout

from carthage_aws.network import *
from carthage_aws.connection import *
from carthage_aws.vm import *
from carthage_aws.dns import *
from carthage_aws.keypair import *

class AwsConnectionModel(InjectableModel, metaclass=ModelingBase):

    connection = injector_access(InjectionKey(MachineImplementation))

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.machine_type = AwsConnection

        self.injector.claim()
        self.injector.add_provider(ConfigLayout)
        self.injector.add_provider(config_key('aws.access_key_id'), self.access_key_id)
        self.injector.add_provider(config_key('aws.secret_access_key'), self.secret_access_key)
        self.injector.add_provider(InjectionKey(AwsConnection), MachineImplementation)

class AwsVirtualPrivateCloudModel(InjectableModel, metaclass=ModelingBase):

    vpc = injector_access(InjectionKey(MachineImplementation))

    @classmethod
    def our_key(self):
        return InjectionKey(AwsVirtualPrivateCloudModel, name=self.name)

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.machine_type = AwsVirtualPrivateCloud

        self.injector.claim()
        self.injector.add_provider(ConfigLayout)
        self.injector.add_provider(config_key('aws.vpc_name'), self.name)
        self.injector.add_provider(config_key('aws.vpc_cidr'), self.cidrblock)
        self.injector.add_provider(InjectionKey(MachineModel), self)
        self.injector.add_provider(InjectionKey(AwsVirtualPrivateCloud), MachineImplementation)

class AwsDhcpOptionSetModel(InjectableModel, metaclass=ModelingBase):

    dhcp_option_set = injector_access(InjectionKey(MachineImplementation))

    @classmethod
    def our_key(self):
        return InjectionKey(AwsDhcpOptionSetModel, name=self.name)

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.machine_type = AwsDhcpOptionSet

        self.injector.claim()
        self.injector.add_provider(ConfigLayout)
        self.injector.add_provider(InjectionKey(MachineModel), self)
        self.injector.add_provider(InjectionKey(AwsVirtualPrivateCloud), self.injector.get_instance(InjectionKey(AwsVirtualPrivateCloud, name=self.vpc)))
        self.injector.add_provider(InjectionKey(AwsDhcpOptionSet), MachineImplementation)

class AwsKeyPairModel(InjectableModel, metaclass=ModelingBase):

    keypair = injector_access(InjectionKey(MachineImplementation))

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.machine_type = AwsKeyPair

        self.injector.claim()
        self.injector.add_provider(ConfigLayout)
        self.injector.add_provider(InjectionKey('config/aws.keypair_keyfile'), self.keyfile)
        self.injector.add_provider(InjectionKey(MachineModel), self)
        self.injector.add_provider(InjectionKey(AwsKeyPair), MachineImplementation)

class AwsVpcNetworkModel(NetworkModel):

    @classmethod
    def our_key(self):
        return InjectionKey(AwsVpcNetworkModel, name=self.name)

    async def async_ready(self):

        if isinstance(self.vpc, AwsVirtualPrivateCloudModel):
            key = InjectionKey(MachineImplementation)
            ainjector = self.vpc.injector(AsyncInjector)
            obj = await ainjector.get_instance_async(key)
        elif isinstance(self.vpc, str):
            key = InjectionKey(AwsVirtualPrivateCloud, name=self.vpc, _ready=False)
            obj = await self.ainjector.get_instance_async(key)
        else:
            raise ValueError

        self.ainjector.add_provider(InjectionKey(AwsVirtualPrivateCloud), obj)
        await super().async_ready()
