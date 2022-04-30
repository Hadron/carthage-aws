from carthage import *
import carthage
from carthage.config import ConfigLayout
from carthage.dependency_injection import *
from carthage.modeling import *
from carthage.modeling.base import MachineImplementation
from carthage.config.schema import *
from carthage.modeling.implementation import ModelingContainer, ModelingBase
from carthage.machine import BaseCustomization
from carthage.network import V4Config, TechnologySpecificNetwork, this_network

from carthage_aws.network import *
from carthage_aws.connection import *
from carthage_aws.vm import *
from carthage_aws.dns import *
from carthage_aws.keypair import *
from carthage_aws.transit import *

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
            obj = self.vpc.vpc
        elif isinstance(self.vpc, str):
            # key = InjectionKey(AwsVirtualPrivateCloudModel, name=self.vpc, _ready=False)
            # obj = await self.ainjector.get_instance_async(key)
            key = InjectionKey(AwsVirtualPrivateCloud, name=self.vpc, _ready=False)
            obj = await self.ainjector.get_instance_async(key)
        else:
            breakpoint()
        self.ainjector.add_provider(InjectionKey(AwsVirtualPrivateCloud), obj)
        await super().async_ready()

    # self_provider(InjectionKey(AwsVpcNetworkModel))

    # @provides(InjectionKey(AwsVirtualPrivateCloud))
    # @inject(model=AwsVpcNetworkModel)
    # async def _find_vpc(model):
    #     # look at self.vpc, figure out what you want and return it
    #     breakpoint()
    #     pass   
