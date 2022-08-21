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
import dataclasses

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet', 'AwsSecurityGroup']


@inject_autokwargs()
class AwsVirtualPrivateCloud(AwsManaged):

    stamp_type = "vpc"
    resource_type = 'vpc'

    

    def __init__(self,  **kwargs):
        super().__init__( **kwargs)
        config = self.config_layout
        if config.aws.vpc_name == None:
            self.name = ''
        else: self.name = config.aws.vpc_name
        if config.aws.vpc_id == None:
            self.id = ''
        else: self.id = config.aws.vpc_id
        self.groups = []
        self.vms = []


    async def find(self):
        def find_default():
            r = self.connection.client.describe_vpcs()['Vpcs']
            for v in r:
                if v['IsDefault']:
                    self.id = v['VpcId']
                    return self.find_from_id()
        if not self.name and not self.id:
            await run_in_executor(find_default)
            if self.mob: return
        return await super().find()

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

@dataclasses.dataclass
class AwsIpRange:
    CidrIp: str = dataclasses.field(default='0.0.0.0/0')
    Description: str = dataclasses.field(default='ALL_IPV4')

    def __repr__(self):
        return f'{dict(CidrIp=self.CidrIp, Description=self.Description)}'

@dataclasses.dataclass
class AwsIpPermission:
    IpRanges: list[AwsIpRange] = dataclasses.field(default_factory=AwsIpRange)
    IpProtocol: str = dataclasses.field(default=-1)
    FromPort: int = dataclasses.field(default=-1)
    ToPort: int = dataclasses.field(default=-1)

    def __repr__(self):
        return f'{dict(IpRanges=self.IpRanges, IpProtocol=self.IpProtocol, ToPort=self.ToPort, FromPort=self.FromPort)}'

@inject_autokwargs(vpc=AwsVirtualPrivateCloud)
class AwsSecurityGroup(AwsManaged):
    '''A class to represent a security group and its rulesets in an AWS VPC.

    :param description: A description for the security group, if unspecified `name` is used.
    :type description: str

    :param ingress_rules: A list of tuples to represent the ingress rules (see FORMAT).
        If unspecified anywhere all is allowed.

    :type ingress_rules: list

    :param egress_rules: A list of tuples to represent the egress rules (see FORMAT).
        If unspecified anywhere all is allowed.

    :type egress_rules: list

    * FORMAT: The format for passing rules is [ ( ( <'cidr'<, 'description' >>), <'protocol'<, to_port<, from_port>>>), .. ]
        Note: <,> used to denote optional parameters so not to be confused with the python [] list syntax

    * If you wanted to allow ALL TCP traffic to port 22, you should provide a rule as follows:
        ingress_rules = [ ((), 'tcp', '22') ]

    * If you wanted to allow TCP from a SPECIFIED BLOCK to port 25 from port 25 you should provide a rule as follows:
        ingress_rules = [ (('192.168.1.0/24',), 'tcp', 25, 25) ]

    * If you wanted to allow TCP from a SPECIFIED BLOCK to ANY port from port 25 you should provide a rule as follows:
        ingress_rules = [ (('192.168.1.0/24',), 'tcp', -1, 25) ]

    * To optionally provide a rule description, provide text as the second argument of the `cidr` tuple:
        ingress_rules = [ (('192.168.1.0/24', 'the mail rule'), 'tcp', 25, 25) ]

    '''

    stamp_type = "security-group"
    resource_type = "security-group"

    def __init__(self,  **kwargs):
        if 'description' in kwargs:
            self.description = kwargs.pop('description')
        else:
            self.description = self.name

        if 'ingress_rules' in kwargs:
            self.ingress_rules = [ AwsIpPermission(AwsIpRange(*x[0]), *x[1:]) for x in kwargs.pop('ingress_rules') ]
        else:
            self.ingress_rules = AwsIpPermission()

        if 'egress_rules' in kwargs:
            self.egress_rules = [ AwsIpPermission(AwsIpRange(*x[0]), *x[1:]) for x in kwargs.pop('egress_rules') ]
        else:
            self.egress_rules = AwsIpPermission()

        super().__init__(**kwargs)

    def do_create(self):
        self.mob = self.client.create_security_group(
            Description=self.description,
            GroupName=self.name,
            VpcId=self.vpc,
            TagSpecifications=[self.resource_tags]
        )

    async def delete(self):
        await run_in_executor(self.mob.delete)

    async def post_create_hook(self):
        breakpoint()
        r = self.mob.authorize_egress(
            IpPermissions=[ x for x in self.egress_rules ],
            TagSpecifications=[self.resource_tags]
        )
        if r['Return'] == False:
            raise ValueError("Error in egress rules")

        r = self.mob.authorize_ingress(
            GroupName=self.name,
            IpPermissions=[ x for x in self.ingress_rules ],
            TagSpecifications=[self.resource_tags]
        )
        if r['Return'] == False:
            raise ValueError("Error in ingress rules")

    async def post_find_hook(self):
        # we should check to make sure rules match ?
        # perhaps we should hash the dict from the mob such that we could determine easily if the ruleset differs
        # if we did .. we could add the hash to a stamp and  ....
        pass

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

