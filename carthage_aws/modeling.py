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

from carthage_base import *

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
