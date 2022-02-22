from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel

from .connection import AwsConnection, AwsManaged

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet']


@inject_autokwargs(connection = AwsConnection, injector = Injector, config = ConfigLayout, network=NetworkModel)
class AwsVirtualPrivateCloud(AwsManaged):

    stamp_type = "vpc"

    def __init__(self, connection, injector, config, network, *args, **kwargs):
        self.injector = injector
        self.connection = connection
        self.network = network
        super().__init__(connection=connection, injector=injector, config=config, network=network, *args, **kwargs)
        self.name = config.aws.vpc
        self.id = ''

    @setup_task('construct')
    def do_create(self):
        try:
            r = self.connection.client.create_vpc(InstanceTenancy='default', CidrBlock=str(self.network.v4_config.network), 
                                                                                TagSpecifications=[{
                                                                                'ResourceType': 'vpc',
                                                                                    'Tags': [{
                                                                                        'Key': 'Name',
                                                                                        'Value': self.name
                                                                                    }]
                                                                                }])
            self.id = r['Vpc']['VpcId']
        except ClientError as e:
            logger.error(f'Could not create AWS VPC {self.name} due to {e}.')

    def delete(self):
        pass


@inject_autokwargs(connection = AwsConnection, injector = Injector, network=NetworkModel)
class AwsSubnet(TechnologySpecificNetwork, AwsManaged):

    stamp_type = "subnet"
    
    def __init__(self, connection, injector, network, *args, **kwargs):
        self.connection = connection
        self.injector = injector
        self.vpc = self.injector(AwsVirtualPrivateCloud)
        self.network = network
        super().__init__(connection=connection, injector=injector, network=network, *args, **kwargs)
        self.name = self.network.name
        

    @setup_task('construct')
    def do_create(self):
        try:
            self.vpc.do_create()
            r = self.connection.client.create_subnet(VpcId=self.vpc.id,
                                            CidrBlock=str(self.network.v4_config.network),
                                            TagSpecifications=[{
                                            'ResourceType': 'subnet',
                                            'Tags': [{
                                                'Key': 'Name',
                                                'Value': self.name
                                            }]
                }]
            )
            self.id = r['Subnet']['SubnetId']
            
        except ClientError as e:
            logger.error(f'Could not create AWS subnet {self.network.v4_config.v4_network} due to {e}.')

