from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network

from .connection import AwsConnection

import boto3
from botocore.exceptions import ClientError


@inject(connection = AwsConnection, injector = Injector)
class AwsVirtualPrivateCloud(TechnologySpecificNetwork, SetupTaskMixin):

    def __init__(self, connection, injector):
        self.injector = injector
        self.connection = connection
        super().__init__()

    @setup_task('Construct')
    def do_create(self):
        try:
            r = self.connection.resource.create_vpc(InstanceTenancy='default',
                                                                                   TagSpecifications=[{
                                                                                    'ResourceType': 'vpc',
                                                                                        'Tags': [{}]
                                                                                    }])
        except ClientError:
            logger.error(f'Could not create AWS VPC for {self.name}.')

    def delete(self):
        pass

    @memoproperty
    def id(self):
        return self.id


@inject_autokwargs(connection = AwsConnection, vpc = AwsVirtualPrivateCloud, injector = Injector, network=NetworkModel)
class AwsSubnet(TechnologySpecificNetwork, SetupTaskMixin):
    
    def __init__(self, connection, vpc, injector, network, **kwargs):
        self.connection = connection
        self.vpc = vpc
        self.injector = injector
        self.network = network
        if 'name' not in kwargs:
            kwargs['name'] = kwargs['network'].name
        super().__init__(**kwargs)

    @setup_task('Construct')
    def do_create(self):
        try:
            r = self.resource.create_subnet(VpcId=self.vpc.id,
                                            CidrBlock=str(self.network.v4_config.network),
                                            TagSpecifications=[{
                                            'ResourceType': 'subnet',
                                            'Tags': [{
                                                'Key': 'Name',
                                                'Value': self.name
                                            }]
                }]
            )
            self.network.subnetid = r['subnet']['SubnetId']
        except ClientError:
            logger.error(f'Could not create AWS subnet for {self.network.v4_config.v4_network}.')

