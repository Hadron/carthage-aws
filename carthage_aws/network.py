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
        self.groups = []

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
            ig = self.connection.client.create_internet_gateway()
            self.ig = ig['InternetGateway']['InternetGatewayId']
            self.connection.client.attach_internet_gateway(InternetGatewayId=self.ig, VpcId=self.id)
            routetable = self.connection.client.create_route_table(VpcId=self.id)
            self.routetable = routetable['RouteTable']['RouteTableId']
            self.connection.client.create_route(DestinationCidrBlock='0.0.0.0/0', GatewayId=self.ig, RouteTableId=self.routetable)
            sg = self.connection.client.create_security_group(GroupName='demo', VpcId=self.id, Description='Demo')
            self.groups.append(sg['GroupId'])
            self.connection.client.authorize_security_group_ingress(GroupId=self.groups[0], IpPermissions=[
                {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'tcp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'udp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmpv6', 'Ipv6Ranges':[{'CidrIpv6': '::/0'}]}
            ])
            self.connection.client.authorize_security_group_egress(GroupId=self.groups[0], IpPermissions=[
                {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'tcp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'udp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmpv6', 'Ipv6Ranges':[{'CidrIpv6': '::/0'}]}
            ])
            self.create_stamp(self.name, '')
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
        self.groups = self.vpc.groups
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
            self.connection.client.associate_route_table(RouteTableId=self.vpc.routetable, SubnetId=self.id)
            self.create_stamp(self.name, '')
            
        except ClientError as e:
            logger.error(f'Could not create AWS subnet {self.name} due to {e}.')

