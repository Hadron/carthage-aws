from carthage import *
from carthage.dependency_injection import *

from .connection import AwsConnection

import boto3
from botocore.exceptions import ClientError


@inject(connection = AwsConnection, injector = Injector)
class AwsVirtualPrivateCloud(Injectable):

    def __init__(self, connection, injector):
        breakpoint()
        self.injector = injector
        self.connection = connection
        super().__init__()
        self.do_create()

    def do_create(self):
        try:
            r = self.connection.resource("ec2", self.connection.region).create_vpc(InstanceTenancy='default',
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


@inject_autokwargs(connection = AwsConnection, vpc = AwsVirtualPrivateCloud, injector = Injector, v4_config = V4Config)
class AwsSubnet(Injectable):
    
    def __init__(self, connection, vpc, injector, v4_config):
        self.connection = connection
        self.vpc = vpc
        self.injector = injector
        self.v4_config = v4_config
        super().__init__()
        self.do_create()

    def do_create(self):
        try:
            r = self.resource.create_subnet(VpcId=self.vpc.id,
                                            CidrBlock=str(v4_config.v4_network),
                                            TagSpecifications=[{
                                            'ResourceType': 'subnet',
                                            'Tags': [{}]
                }]
            )
        except ClientError:
            logger.error(f'Could not create AWS subnet for {v4_config.v4_network}.')

