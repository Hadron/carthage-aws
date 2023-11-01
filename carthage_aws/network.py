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
import warnings
from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel, InjectableModel, provides

from .connection import AwsConnection, AwsManaged, AwsClientManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError
from ipaddress import IPv4Network

__all__ = [
    'AwsVirtualPrivateCloud',
    'AwsSubnet',
    'AwsInternetGateway',
    'AwsRouteTable',
    'AwsNetworkInterface',
    'AwsSecurityGroup',
    'SgRule',
    'AwsDhcpOptionSet'
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

        self._subnets = []

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

    async def main_route_table(self):
        r = self.connection.client.describe_route_tables(
            Filters=[
                dict(Name='vpc-id', Values=[self.id]),
                dict(Name='association.main',
                     Values=['true'])])

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
        allzeros_32 = ipaddress.IPv4Network('0.0.0.0/32')
        for e in cidr_out:
            if e == allzeros_32:
                warnings.warn("You selected a cidr address of 0.0.0.0/32 not 0.0.0.0/0; you almost certainly do not want this", stacklevel=4)
                break
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
    
            
@inject_autokwargs(vpc=InjectionKey(AwsVirtualPrivateCloud, _ready=True))
class AwsDhcpOptionSet(AwsManaged):

    stamp_type = 'dhcp_option'
    resource_type = 'dhcp_options'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def do_create(self):
        kwargs = dict(
            DhcpConfigurations = [
                dict(Key='domain-name-servers',Values=self.model.domain_name_servers),
                dict(Key='domain-name',Values=[self.model.domain_name]),
                dict(Key='ntp-servers',Values=self.model.ntp_servers)
            ],
            TagSpecifications = [self.resource_tags]
        )
        r = self.connection.client.create_dhcp_options(**kwargs)
        self.id = r['DhcpOptions']['DhcpOptionsId']

    async def post_find_hook(self):
        if self.id != self.vpc.mob.dhcp_options_id:
            self.mob.associate_with_vpc(VpcId=self.vpc.id)

@inject_autokwargs(network=this_network,
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
        if self not in self.vpc.subnets:
            self.vpc.add_subnet(self)
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
            raise RuntimeError(f'unable to create AWS subnet for {self}: {e}')

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


            logger.error(f'Could not create AwsSecurityGroup {self.name} due to {e}.')

    async def post_create_hook(self):
        # self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)
        pass
        
    async def post_find_hook(self): 
        if len(self.mob.associations) > 0:
            self.association = self.mob.associations[0]
        else:
            self.association = self.mob.associate_with_subnet(SubnetId=self.subnet.id)


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
