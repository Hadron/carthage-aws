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

from .connection import AwsConnection, AwsManaged, AwsClientManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError
from ipaddress import IPv4Network

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet']


class AwsVirtualPrivateCloud(AwsManaged):

    stamp_type = 'vpc'
    resource_type = 'vpc'

    

    def __init__(self, cidrblock=None, **kwargs):

        super().__init__( **kwargs)

        c_aws = self.config_layout.aws

        if cidrblock is not None:
            assert not hasattr(self, 'model')
            self.cidrblock = cidrblock
        else:
            self.cidrblock = getattr(getattr(self, 'model', object()), 'cidrblock', None)

        if self.cidrblock is None:
            self.cidrblock = str(IPv4Network(c_aws.vpc_cidr))

        if self.name is None:
            self.name = getattr(getattr(self, 'model', object()), 'name', None)

        if self.name is None:
            self.name = c_aws.vpc_name 

        if self.id is None:
            self.id = getattr(getattr(self, 'model', object()), 'id', None)

        if self.id is None:
            self.id = c_aws.vpc_id

        if not (self.name or self.id):
            raise ValueError("You must specify either an AWS VPC ID or VPC name.")

        self.groups = []
        self._subnets = []

    def do_create(self):
        r = self.connection.client.create_vpc(
                InstanceTenancy='default',
                CidrBlock=self.cidrblock, 
                TagSpecifications=[self.resource_tags])
        self.id = r['Vpc']['VpcId']

    @property
    def route_tables(self):
        return [ self.ainjector(AwsRouteTable, id=x.id) for x in self.mob.route_tables.all() ]

    @property
    def subnets(self):
        return self._subnets

    def add_subnet(self, subnet):
        assert subnet.vpc.id == self.id,f"{subnet} does not belong to {self}"
        self._subnets.append(subnet)

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
        if hasattr(self.network, 'az'):
            self.az = self.network.az

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

