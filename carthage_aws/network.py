from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel

from .connection import AwsConnection, AwsManaged

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet']


@inject_autokwargs(connection = InjectionKey(AwsConnection, _ready=True), network=NetworkModel)
class AwsVirtualPrivateCloud(AwsManaged):

    stamp_type = "vpc"

    def __init__(self,  **kwargs):
        super().__init__( **kwargs)
        config = self.config_layout
        if config.aws.vpc_name == None and config.aws.vpc_id == None:
            raise Error("You must specify either an AWS VPC ID or VPC name.")
        if config.aws.vpc_name == None:
            self.name = ''
        else: self.name = config.aws.vpc_name
        if config.aws.vpc_id == None:
            self.id = ''
        else: self.id = config.aws.vpc_id
        self.groups = []
        self.vms = []

    @setup_task('construct')
    def do_create(self):
        try:
            # Only build a VPC if it doesn't already exist
            for v in self.connection.vpcs:
                # Prefer ID match
                if self.config_layout.aws.vpc_id == v['id']:
                    self.id = v['id']
                    break
                if self.name == v['name']:
                    self.id = v['id']
            if self.id == '':
                r = self.connection.client.create_vpc(InstanceTenancy='default', CidrBlock=str(self.network.v4_config.network), 
                                                                                    TagSpecifications=[{
                                                                                    'ResourceType': 'vpc',
                                                                                        'Tags': [{
                                                                                            'Key': 'Name',
                                                                                            'Value': self.name
                                                                                        }]
                                                                                    }])
                self.id = r['Vpc']['VpcId']
            
            make_ig = True
            for ig in self.connection.igs:
                if ig['vpc'] == self.id:
                    make_ig = False
                    break
            if make_ig:
                ig = self.connection.client.create_internet_gateway()
                self.ig = ig['InternetGateway']['InternetGatewayId']
                self.connection.client.attach_internet_gateway(InternetGatewayId=self.ig, VpcId=self.id)
                routetable = self.connection.client.create_route_table(VpcId=self.id)
                self.routetable = routetable['RouteTable']['RouteTableId']
                self.connection.client.create_route(DestinationCidrBlock='0.0.0.0/0', GatewayId=self.ig, RouteTableId=self.routetable)
                sg = self.connection.client.create_security_group(GroupName=f'{self.name} open', VpcId=self.id, Description=f'{self.name} open')
                self.groups.append(sg)
            
                self.connection.client.authorize_security_group_ingress(GroupId=self.groups[0]['GroupId'], IpPermissions=[
                    {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'tcp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'udp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmpv6', 'Ipv6Ranges':[{'CidrIpv6': '::/0'}]}
                ])
                self.connection.client.authorize_security_group_egress(GroupId=self.groups[0]['GroupId'], IpPermissions=[
                    {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'tcp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 1, 'ToPort': 65535, 'IpProtocol': 'udp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmp', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]},
                    {'FromPort': 8, 'ToPort': -1, 'IpProtocol': 'icmpv6', 'Ipv6Ranges':[{'CidrIpv6': '::/0'}]}
                ])
            
            self.vms = []
            for vm in self.connection.vms:
                if vm['vpc'] == self.id:
                    self.vms.append(vm)

            for sg in self.connection.groups:
                if sg['VpcId'] == self.id:
                    self.groups.append(sg)
            
            # Set this as the VPC for this run
            self.connection.set_running_vpc(self.id)

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
            self.id = ''
            for s in self.connection.subnets:
                if s['vpc'] == self.vpc.id and s['CidrBlock'] == str(self.network.v4_config.network):
                    self.id = s['id']
                    break
            if self.id == '':
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

