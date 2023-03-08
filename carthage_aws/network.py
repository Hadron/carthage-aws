# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import dataclasses
import ipaddress
import typing
from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel, InjectableModel, provides

from .connection import AwsConnection, AwsManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet', 'AwsSecurityGroup',
           'SgRule']


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
        await run_in_executor(lambda: self.groups)

    @memoproperty
    def groups(self):
        groups =self.connection.client.describe_security_groups(Filters=[
            dict(Name='vpc-id', Values=[self.id])])

        self.groups = list( groups['SecurityGroups'])
        return self.groups

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


@dataclasses.dataclass(frozen=True)
class SgRule:

    cidr: frozenset[ipaddress.IPv4Network]
    port: typing.Union[int, tuple[int,int]] = (-1, -1)
    proto: typing.Union[str,int] = 'tcp'
    description: str = ""
    @staticmethod
    def _handle_cidr(cidr_in):
        if isinstance(cidr_in, (ipaddress.IPv4Network, str)):
            cidr_in = [cidr_in]
        cidr_out = frozenset(map(lambda cidr: ipaddress.IPv4Network(cidr), cidr_in))
        return cidr_out

    @staticmethod
    def _handle_port(port):
        if isinstance(port, int):
            port = (port, port)
        return (int(port[0]), int(port[1]))

    def __post_init__(self):
        self.__dict__['cidr'] = self._handle_cidr(self.cidr)
        self.__dict__['port'] = self._handle_port(self.port)
        self.__dict__['proto'] = str(self.proto)

    def to_ip_permission(self):
        ip_ranges = []
        for i, ip in enumerate(self.cidr):
            ip_ranges.append(dict(CidrIp=str(ip)))
            if i == 0 and self.description:
                ip_ranges[0]['Description'] = self.description
        return dict(
            IpProtocol=str(self.proto),
            FromPort=self.port[0],
            ToPort=self.port[1],
            IpRanges=ip_ranges)

    @classmethod
    def from_ip_permission(cls, permission):
        for k in ('IpProtocol', 'IpRanges'):
            if k not in permission:
                raise ValueError(f'IpPermission requires {k}')
        description = ""
        for k in ('FromPort', 'ToPort'):
            if k not in permission: permission[k] = -1
        if permission['IpRanges'] and permission['IpRanges'][0].get('Description'):
            description = permission['IpRanges'][0]['Description']
        return cls(
            cidr=map(lambda i: i['CidrIp'], permission['IpRanges']),
            proto=permission['IpProtocol'],
            port=(permission['FromPort'], permission['ToPort']),
            description=description)


@inject_autokwargs(vpc=AwsVirtualPrivateCloud)
class AwsSecurityGroup(AwsManaged, InjectableModel):
    '''A class to represent a security group and its rulesets in an AWS VPC.

    :param description: A description for the security group, if unspecified `name` is used.
    :type description: str

    :param ingress_rules: A list of ingress rules
        If unspecified no ingress rules are allowed.

    :type ingress_rules: list[SgRule]

    :param egress_rules: A list of egress rules.
        If unspecified anywhere all is allowed.

    :type egress_rules: list[SgRule]


    '''

    #: If true, create tags when the resource is created
    include_tags = True
    
    stamp_type = "security-group"
    resource_type = "security_group"

    def __init__(self,  **kwargs):
        if 'description' in kwargs:
            self.description = kwargs.pop('description')
        else:
            self.description = self.name

        if 'ingress_rules' in kwargs:
            self.ingress_rules = kwargs.pop('ingress_rules')

        if 'egress_rules' in kwargs:
            self.egress_rules = kwargs.pop('egress_rules')

        super().__init__(**kwargs)

    ingress_rules: list[SgRule] = []
    egress_rules: list[SgRule] = [SgRule(cidr='0.0.0.0/0', proto='-1', port=-1)]

    @classmethod
    def our_key(cls):
        return InjectionKey(AwsSecurityGroup, name=cls.name)

    def __init_subclass__(cls, **kwargs):
        # Modeling only handles our_key for containers, but
        # AwsSecurityGroup doesn't need to be a container.
        try:
            provides(cls.our_key())(cls)
        except AttributeError: pass
        super().__init_subclass__(**kwargs)
        

    def do_create(self):
        self.mob = self.service_resource.create_security_group(
            Description=self.description,
            GroupName=self.name,
            VpcId=self.vpc.id,
            TagSpecifications=[self.resource_tags]
        )

        # refresh groups at vpc level
        try: del self.vpc.groups
        except Exception: pass

    async def delete(self):
        assert not self.readonly
        await run_in_executor(self.mob.delete)
        try: del self.vpc.groups
        except Exception: pass


    @memoproperty
    def existing_egress(self):
        return         set(map(
            lambda permission: SgRule.from_ip_permission(permission),
            self.mob.ip_permissions_egress))

    @memoproperty
    def existing_ingress(self):
        return set(map(
            lambda permission:SgRule.from_ip_permission(permission),
            self.mob.ip_permissions))

    async def post_find_hook(self):
        def callback():
            existing_egress = self.existing_egress
            existing_ingress = self.existing_ingress
            expected_ingress = set(self.ingress_rules)
            expected_egress = set(self.egress_rules)

            if expected_egress-existing_egress:
                self.mob.authorize_egress(
                IpPermissions=[ x.to_ip_permission() for x in expected_egress-existing_egress],
                    )

            if expected_ingress-existing_ingress:
                self.mob.authorize_ingress(
                IpPermissions=[ x.to_ip_permission() for x in expected_ingress-existing_ingress ],
                )

            if existing_egress-expected_egress:
                self.mob.revoke_egress(
                    IpPermissions=[x.to_ip_permission() for x in existing_egress-expected_egress],
                    )

            if existing_ingress-expected_ingress:
                self.mob.revoke_ingress(
                                IpPermissions=[x.to_ip_permission() for x in existing_ingress-expected_ingress],
                            )

        if not self.readonly:
            await run_in_executor(callback)
            try: del self.existing_ingress
            except Exception: pass
            try: del self.existing_egress
            except Exception: pass

    async def possible_ids_for_name(self):
        def callback():
            results = []
            for g in self.vpc.groups:
                if g['GroupName'] == self.name: results.append(g['GroupId'])
            return results
        return await run_in_executor(callback)

    @property
    def resource_tags(self):
        if self.include_tags and self.name:
            return super().resource_tags
        return []
    
            

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

@inject_autokwargs(
    ip_address = InjectionKey('ip_address', _optional=NotPresent))
class VpcAddress(AwsManaged):

    resource_type = 'elastic_ip'
    stamp_type = 'elastic_ip'
    ip_address = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name:
            provides(InjectionKey(VpcAddress, name=cls.name))(cls)

    async def find(self):
        '''If ip_address is set and id is not, then try to find an ip_address matching.
        '''
        def callback():
            return self.connection.client.describe_addresses(
                PublicIps=[self.ip_address])

        if self.ip_address and not self.id:
            try:
                r = await run_in_executor(callback)
            except ClientError:
                raise LookupError('IP address specified but does not exist')
            self.id = r['Addresses'][0]['AllocationId']
        res =  await super().find()
        if self.mob:
            self.ip_address = self.mob.public_ip
        return res

    def do_create(self):
        #executor context
        r = self.connection.client.allocate_address(Domain='vpc',
                                                    TagSpecifications=[self.resource_tags])
        self.id = r['AllocationId']


    async def delete(self):
        if self.mob:
            try:
                await run_in_executor(self.mob.load)
                if self.mob.association: await run_in_executor(self.mob.association.delete)
            except Exception: pass
            await run_in_executor(self.mob.release)

__all__ += ['VpcAddress']


