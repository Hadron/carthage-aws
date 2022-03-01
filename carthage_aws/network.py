# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel

from .connection import AwsConnection, AwsManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet']


@inject_autokwargs()
class AwsVirtualPrivateCloud(AwsManaged):

    stamp_type = "vpc"
    resource_type = 'vpc'

    

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


    def do_create(self):
        try:
            r = self.connection.client.create_vpc(
                    InstanceTenancy='default',
                                                      CidrBlock=str(self.config_layout.aws.vpc_cidr), 
                    TagSpecifications=[self.resource_tags])
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
                self.connection.client.create_route(DestinationCidrBlock='0.0.0.0/0', GatewayId=self.ig, RouteTableId=self.main_route_table_id)
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
            


        except ClientError as e:
            logger.error(f'Could not create AWS VPC {self.name} due to {e}.')

    @memoproperty
    def main_route_table_id(self):
        r = self.connection.client.describe_route_tables(
            Filters=[
                dict(Name='vpc-id', Values=[self.id]),
                dict(Name='association.main',
                     Values=['true'])])
        return r['RouteTables'][0]['RouteTableId']
    
    async def post_find_hook(self):
        groups =self.connection.client.describe_security_groups(Filters=[
            dict(Name='vpc-id', Values=[self.id])])

        self.groups = list(filter(lambda g: g['GroupName'] != "default", groups['SecurityGroups']))
        
        
    def delete(self):
        for sn in self.mob.subnets.all():
            sn.delete()
        for g in self.mob.security_groups.all():
            try: g.delete()
            except: pass
        for gw in self.mob.internet_gateways.all():
            gw.detach_from_vpc(VpcId=self.id)
            gw.delete()
        for rt in self.mob.route_tables.all():
            try: rt.delete()
            except: pass
        self.mob.delete()


@inject_autokwargs(connection = InjectionKey(AwsConnection, _ready=True),
                   network=this_network,
                   vpc=InjectionKey(AwsVirtualPrivateCloud, _ready=True))
class AwsSubnet(TechnologySpecificNetwork, AwsManaged):

    stamp_type = "subnet"
    resource_type = 'subnet'
    
    def __init__(self,  **kwargs):
        super().__init__( **kwargs)
        self.groups = self.vpc.groups
        self.name = self.network.name
        


    async def find(self):
        if self.id: return await run_in_executor(self.find_from_id)
        for s in self.connection.subnets:
                if s['vpc'] == self.vpc.id and s['CidrBlock'] == str(self.network.v4_config.network):
                    self.id = s['id']
                    return await run_in_executor(self.find_from_id)


    def do_create(self):
        try:
            r = self.connection.client.create_subnet(VpcId=self.vpc.id,
                                                     CidrBlock=str(self.network.v4_config.network),
                                                     TagSpecifications=[self.resource_tags]
                                                     )
            self.id = r['Subnet']['SubnetId']
            # No need to associate subnet with main route table
            
        except ClientError as e:
            logger.error(f'Could not create AWS subnet {self.name} due to {e}.')

