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

__all__ = [
    'AwsVirtualPrivateCloud',
    'AwsSubnet',
    'AwsInternetGateway',
    'AwsRouteTable',
    'AwsNetworkInterface'
]

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
        return [ x for x in self.mob.subnets.all() ]

    async def main_route_table(self):
        r = self.connection.client.describe_route_tables(
            Filters=[
                dict(Name='vpc-id', Values=[self.id]),
                dict(Name='association.main',
                     Values=['true'])])
        rid = r['RouteTables'][0]['RouteTableId']
        return await self.ainjector(AwsRouteTable, id=rid, subnet=None)

    async def post_create_hook(self):

        def callback():

            rts = list(self.mob.route_tables.all())
            assert len(rts) == 1
            rt = rts[0]
            rt.create_tags(Tags=[dict(Key='Name', Value=f'rt-{self.name}')])

            sgs = list(self.mob.security_groups.all())
            assert len(sgs) == 1
            sg = sgs[0]
            sg.create_tags(Tags=[dict(Key='Name', Value=f'sg-{self.name}')])

            sg.revoke_ingress(IpPermissions=sg.ip_permissions)
            sg.revoke_egress(IpPermissions=sg.ip_permissions_egress)
            sg.authorize_ingress(IpPermissions=[{'IpProtocol': '-1', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]}])
            sg.authorize_egress(IpPermissions=[{'IpProtocol': '-1', 'IpRanges':[{'CidrIp': '0.0.0.0/0'}]}])

            sns = list(self.mob.subnets.all())
            assert len(sns) == 0

        await run_in_executor(callback)

    async def post_find_hook(self):
        if hasattr(self, 'model'):
            if IPv4Network(self.mob.cidr_block) != IPv4Network(self.model.cidrblock):
                raise RuntimeError(f'current CIDR for {self.name} ({self.mob.cidr_block}) does not match requested ({self.model.cidrblock})')
        
        groups = self.connection.client.describe_security_groups(
            Filters=[dict(Name='vpc-id', Values=[self.id])])
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

@inject_autokwargs(network=this_network,
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
        kwargs = dict(
            VpcId=self.vpc.id,
            CidrBlock=str(self.network.v4_config.network),
            TagSpecifications=[self.resource_tags]
        )
        if hasattr(self.network, 'az'):
            kwargs.update(dict(AvailabilityZone=self.network.az))
        r = self.connection.client.create_subnet(**kwargs)
        self.id = r['Subnet']['SubnetId']

    async def post_create_hook(self):
        return
        self.mob.route_tables.all()

    async def post_find_hook(self):
        return
        self.mob.association.delete()

@inject_autokwargs(vpc=InjectionKey(AwsVirtualPrivateCloud, _ready=True))
class AwsSecurityGroup(AwsManaged):

    stamp_type = "security_group"
    resource_type = "security_group"

    def __init__(self,  **kwargs):
        super().__init__( **kwargs)

        self.name = f'{self.subnet.name}-sg'

    def do_create(self):
        try:
            r = self.connection.client.create_security_group(
                    VpcId=self.vpc.id,
                    TagSpecifications=[self.resource_tags]
            )
            self.id = r['SecurityGroup']['SecurityGroupId']
        except ClientError as e:
            logger.error(f'Could not create AwsSecurityGroup {self.name} due to {e}.')

    async def post_create_hook(self):
        # self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)
        pass
        
    async def post_find_hook(self): 
        if len(self.mob.associations) > 0:
            self.association = self.mob.associations[0]
        else:
            self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)

@inject_autokwargs(vpc=InjectionKey(AwsVirtualPrivateCloud),
                   subnet=InjectionKey(AwsSubnet))
class AwsRouteTable(AwsManaged):

    stamp_type = "route_table"
    resource_type = "route_table"

    def __init__(self,  **kwargs):
        super().__init__( **kwargs)

    def _add_route(self, net, target, kind=None):

        from .transit import AwsTransitGateway

        if kind is None:
            if isinstance(target, AwsInternetGateway):
                kind = 'Gateway'
            elif target.__class__.__name__ == 'AwsVpcEndpoint':
                kind = 'VpcEndpoint'
            elif isinstance(target, AwsTransitGateway):
                kind = 'TransitGateway'
            elif getattr(target, 'interface_type', None) == 'interface':
                kind = 'NetworkInterface'
            else:
                raise ValueError(f'unknown target type for: {target}')

        kwargs = {
            'DestinationCidrBlock': net,
            f'{kind}Id': target.id
        }
        try:
            r = self.mob.create_route(**kwargs)
        except ClientError as e:
            logger.error(f'Could not create route {net}->{target} due to {e}.')

    async def add_route(self, cidrblock, target, target_type, exists_ok=False):
        await run_in_executor(self.add_route, cidrblock, target)

    async def associate_subnet(self, subnet):
        def callback():
            self.mob.associate_with_subnet(SubnetId=subnet.id)
        await run_in_executor(callback)

    async def set_routes(self, *routes, exists_ok=False):
        def callback(routes):
            numlocal = 0
            for r in list(reversed(self.mob.routes)):
                if r.gateway_id == 'local':
                    numlocal += 1
                else:
                    r.delete()
            assert numlocal == 1
            self.mob.load()
            for v in routes:
                self._add_route(*v)
            self.mob.load()
        await run_in_executor(callback, routes)

    async def delete(self):
        if hasattr(self, 'association'):
            logger.info(f"Deleting association for {self} and {self.association}")
            run_in_executor(self.association.delete)
        logger.info(f"Deleting {self}")
        await run_in_executor(self.delete)

    def do_create(self):
        try:
            r = self.connection.client.create_route_table(
                    VpcId=self.vpc.id,
                    TagSpecifications=[self.resource_tags]
            )
            self.id = r['RouteTable']['RouteTableId']
        except ClientError as e:
            logger.error(f'Could not create AwsRouteTable {self.name} due to {e}.')

    async def post_create_hook(self):
        self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)

    async def post_find_hook(self): 
        if len(self.mob.associations) > 0:
            self.association = self.mob.associations[0]
        else:
            self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)
        
class AwsInternetGateway(AwsManaged):
    
    stamp_type = "internet_gateway"
    resource_type = "internet_gateway"

    def __init__(self,  **kwargs):
        super().__init__( **kwargs)
        self.attachment_id = None

    async def set_attachment(self, *, vpc=None, readonly=False):

        if vpc: await vpc.async_become_ready()

	# If they match, we are done.
        if vpc and self.attachment_id and (vpc.id == self.attachment_id):
            return

        # If not, we start by deleting the current (incorrect)
        # attachment.
        if self.attachment_id:
            if readonly:
                raise ValueError(f'attachment for {self} is {self.attachment_id} instead of {vpc.id}')
            def callback():
                self.mob.detach_from_vpc(VpcId=self.attachment_id)
                self.attachment_id = None
            await run_in_executor(callback)
            
        # Set the correct attachment if requested.
        if vpc.id:
            if readonly:
                raise ValueError(f'unable to attach {self} to {vpc.id}')
            def callback():
                self.mob.attach_to_vpc(VpcId=vpc.id)
                self.attachment_id = vpc.id
            await run_in_executor(callback)

    async def attach(self, vpc=None, readonly=False):
        if not vpc:
            vpc = await self.ainjector.get_instance_async(AwsVirtualPrivateCloud)
        return await self.set_attachment(vpc=vpc, readonly=readonly)
        
    async def detach(self, readonly=False):
        return await self.set_attachment(vpc=None, readonly=readonly)
        
    def delete(self):
        raise NotImplementedError
        if hasattr(self, 'attachment'):
            self.detatch()
        def callback():
            _ = self.mob.detach_from_vpc(VpcId=self.vpc.id)
        run_in_executor(callback)

    def do_create(self):
        r = self.connection.client.create_internet_gateway(
                TagSpecifications=[self.resource_tags]
        )
        self.id = r['InternetGateway']['InternetGatewayId']

    async def post_find_hook(self): 
        if len(getattr(self.mob, 'attachments', [])) > 0:
            self.attachment_id = self.mob.attachments[0]['VpcId']
        else:
            self.attachment_id = None

@inject_autokwargs(subnet=AwsSubnet)
class AwsNetworkInterface(AwsManaged):
    
    stamp_type = "network_interface"
    resource_type = "network_interface"

    def __init__(self, name, disable_src_dst_check=False, **kwargs):
        super().__init__( **kwargs)
        self.disable_src_dst_check = disable_src_dst_check
        self.name = name

    async def attach(self, instance):
        def callback():
            try:
                _ = self.mob.attach(InstanceId=instance.id)
            except ClientError as e:
                logger.error(f"Could not attach {self} to {instance} because {e}")
        await run_in_executor(callback)

    async def detatch(self):
        def callback():
            try:
                _ = self.mob.detatch(Force=True)
            except ClientError as e:
                logger.error(f"Could not detach {self} from {instance} because {e}")
        await run_in_executor(callback)

    async def delete(self):
        if self.attachment:
            self.detatch()
        def callback():
            try:
                self.mob.delete()
            except ClientError as e:
                    logger.error(f"Could not delete {self} because {e}")
        await run_in_executor(callback)

    def do_create(self):
        try:
            r = self.connection.client.create_network_interface(
                    SubnetId=self.subnet.id,
                    TagSpecifications=[self.resource_tags]
            )
            self.id = r['NetworkInterface']['NetworkInterfaceId']
        except ClientError as e:
            logger.error(f'Could not create AwsNetworkInterface {self.name} due to {e}.')

    async def post_create_hook(self):
        if self.disable_src_dst_check:
            self.mob.modify_attribute(SourceDestCheck={'Value':False})
