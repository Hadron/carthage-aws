# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import ipaddress
import typing
import warnings
from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel, InjectableModel, ModelContainer, provides, no_inject_name
from carthage.utils import when_needed
import carthage.machine

from .connection import AwsConnection, AwsManaged, run_in_executor, wait_for_state_change

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsVirtualPrivateCloud', 'AwsSubnet', 'AwsSecurityGroup',
           'SgRule']


@inject_autokwargs()
class AwsVirtualPrivateCloud(AwsManaged, ModelContainer):

    stamp_type = "vpc"
    resource_type = 'vpc'
    resource_factory_method = 'Vpc'

    vpc_cidr:str = None #: String representation of the v4 CIDR block for the VPC
    dns_hostnames_enabled:bool = False

    def __init__(self,  vpc_cidr=None, **kwargs):
        super().__init__( **kwargs)
        config = self.config_layout
        if self.name is None:
            if config.aws.vpc_name == None:
                self.name = ''
            else: 
                self.name = config.aws.vpc_name
            self.dns_hostnames_enabled = config.aws.vpc_dns_hostnames_enabled
        if self.id is None:
            if config.aws.vpc_id == None:
                self.id = ''
            else: 
                self.id = config.aws.vpc_id
            self.dns_hostnames_enabled = config.aws.vpc_dns_hostnames_enabled
        if vpc_cidr: self.vpc_cidr = vpc_cidr
        if self.vpc_cidr is None:
            self.vpc_cidr = str(config.aws.vpc_cidr)
        self.vms = []
        self.injector.add_provider(InjectionKey(AwsVirtualPrivateCloud), dependency_quote(self))
        if  not (self.name or self.id):
            # We do not want to accidentally reconfigure or delete the default VPC
            self.readonly = True
        

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
                                                      CidrBlock=self.vpc_cidr,
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

    async def delete(self):
        for sn in self.mob.subnets.all():
            await run_in_executor(sn.delete)
        for g in self.mob.security_groups.all():
            try: await run_in_executor(g.delete)
            except: pass
        for gw in self.mob.internet_gateways.all():
            gw.detach_from_vpc(VpcId=self.id)
            gw.delete()
        for rt in self.mob.route_tables.all():
            try: rt.delete()
            except: pass
        await run_in_executor(self.mob.delete)

    async def read_write_hook(self):
        def callback():
            result = self.mob.describe_attribute(Attribute="enableDnsHostnames")
            is_dns_hostnames_enabled = result["EnableDnsHostnames"]["Value"]
            if is_dns_hostnames_enabled != self.dns_hostnames_enabled:
                resp = self.mob.modify_attribute(
                    EnableDnsHostnames={"Value":self.dns_hostnames_enabled},
                )
        await run_in_executor(callback)


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
    resource_factory_method='SecurityGroup'

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

    async def read_write_hook(self):
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
        # Make sure the vpc is instantiated
        # If it does not exist, then we cannot possibly exist
        if not self.vpc.id: await self.vpc.find()
        if not self.vpc.id: return []
        return await run_in_executor(callback)

    @property
    def resource_tags(self):
        if self.include_tags and self.name:
            return super().resource_tags
        return []



# Decorated also with injection for route table after it is defined.
@inject_autokwargs(connection = InjectionKey(AwsConnection, _ready=True),
                   network=this_network,
                   vpc=InjectionKey(AwsVirtualPrivateCloud))
class AwsSubnet(TechnologySpecificNetwork, AwsManaged):

    stamp_type = "subnet"
    resource_type = 'subnet'
    resource_factory_method = 'Subnet'

    route_table:AwsRouteTable = None #: Route table to associate
    
    def __init__(self,  **kwargs):
        super().__init__( **kwargs)
        self.name = self.network.name

    def __str__(self):
        return f'AwsSubnet:{self.name} ({self.network.v4_config.network})'
    

    async def find(self):
        await self.vpc.async_become_ready()
        if self.id: return await run_in_executor(self.find_from_id)
        for s in self.connection.subnets:
                if s['vpc'] == self.vpc.id and s['CidrBlock'] == str(self.network.v4_config.network):
                    self.id = s['id']
                    return await run_in_executor(self.find_from_id)


    def do_create(self):
        availability_zone = self._gfi("aws_availability_zone", default=None)
        extra_args = {}
        if availability_zone:
            extra_args['AvailabilityZone'] = availability_zone
        try:
            r = self.connection.client.create_subnet(VpcId=self.vpc.id,
                                                     CidrBlock=str(self.network.v4_config.network),
                                                     TagSpecifications=[self.resource_tags],
                                                     **extra_args
                                                     )
            self.id = r['Subnet']['SubnetId']
            # No need to associate subnet with main route table

        except ClientError as e:
            raise RuntimeError(f'unable to create AWS subnet for {self}: {e}')

    async def read_write_hook(self):
        if self.route_table:
            await self.route_table.async_become_ready()
            await self.route_table.associate_subnet(self)

    async def delete(self):
        await self.find()
        if not self.mob: return
        logger.info('Deleting %s', self)
        await run_in_executor(self.mob.delete)
        
@inject_autokwargs(
    ip_address = InjectionKey('ip_address', _optional=NotPresent))
class VpcAddress(AwsManaged):

    resource_type = 'elastic_ip'
    resource_factory_method = 'VpcAddress'

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



@inject(vm=InjectionKey(carthage.machine.Machine, _ready=False),
        security_groups=InjectionKey('aws_vm_network_security_groups', _optional=True))
async def network_for_existing_vm(vm, security_groups):
    '''
    Usage typically within a machine model::

        add_provider(InjectionKe("instance_network"), network_for_existing_vm)

        class network_config(NetworkConfigModel):
            add('eth0', mac=None, net=InjectionKey("instance_network"))

    This will look up the network associated with an existing VM and instantiate it in the model.  It can be used for example as an up-propagation to put another new instance on the same network.
    '''
    from .vm import AwsVm
    from carthage.modeling import NetworkModel, injector_access
    if not isinstance(vm, AwsVm):
        raise TypeError(f'{vm} did not end up being an AwsVm')
    await vm.find()
    if not vm.mob: raise LookupError(f'Failed to find existing {vm}')
    vpc_id =vm.mob.subnet.vpc_id
    try:
        vpc = await vm.ainjector.get_instance_async(InjectionKey(AwsVirtualPrivateCloud, id=vpc_id, _ready=False))
    except KeyError: vpc = None
    class vm_network(NetworkModel):
        v4_config = V4Config(network=vm.mob.subnet.cidr_block)
        for sg in security_groups or []:
            add_provider(sg, force_multiple_instantiate=True)
        try: del sg
        except NameError: pass
        if vpc:
            add_provider(InjectionKey(AwsVirtualPrivateCloud), injector_xref(
                InjectionKey(AwsVirtualPrivateCloud, id=vpc_id)))
        else:
            add_provider(InjectionKey(AwsVirtualPrivateCloud),
                         when_needed(AwsVirtualPrivateCloud, id=vpc_id))

    return await vm.ainjector(vm_network)

__all__ += ['network_for_existing_vm']

@inject_autokwargs(vpc=InjectionKey(AwsVirtualPrivateCloud),
                   )
class AwsRouteTable(AwsManaged):


    stamp_type = "route_table"
    resource_type = "route_table"
    resource_factory_method = 'RouteTable'

    #: A list or tuple of routes (destination, target, kind) to
    #install.  If set, then whenever this changes, all routes besides
    #the local route are deleted and these routes are installed.
    routes:typing.Sequence = tuple()

    def _add_route(self, destination, target, kind=None):

        from .transit import AwsTransitGateway

        if kind is None:
            if isinstance(target, AwsInternetGateway):
                kind = 'Gateway'
            elif isinstance(target, AwsNatGateway):
                kind = 'NatGateway'
            elif target.__class__.__name__ == 'AwsVpcEndpoint':
                kind = 'VpcEndpoint'
            elif isinstance(target, AwsTransitGateway):
                kind = 'TransitGateway'
            elif getattr(target, 'interface_type', None) == 'interface':
                kind = 'NetworkInterface'
            else:
                raise ValueError(f'unknown target type for: {target}')

        kwargs = {
            'DestinationCidrBlock': destination,
            f'{kind}Id': target.id
        }
        try:
            r = self.mob.create_route(**kwargs)
        except ClientError as e:
            logger.error(f'Could not create route {destination}->{target} due to {e}.')

    async def add_route(self, destination, target, kind=None, exists_ok=False):
        destination = await resolve_deferred(self.ainjector, destination, args=dict(target=target, kind=kind))
        target = await resolve_deferred(self.ainjector, target, args=dict(destination=destination, kind=kind))
        await run_in_executor(self._add_route, destination, target, kind)

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
        await run_in_executor(callback, routes)
        for v in routes:
            await self.add_route(*v)
        await run_in_executor(self.mob.load)

    async def delete(self):
        if hasattr(self, 'association'):
            logger.info(f"Deleting association for {self} and {self.association}")
            await run_in_executor(self.association.delete)
        logger.info(f"Deleting {self}")
        await run_in_executor(self.mob.delete)

    def do_create(self):
        try:
            r = self.connection.client.create_route_table(
                    VpcId=self.vpc.id,
                    TagSpecifications=[self.resource_tags]
            )
            self.id = r['RouteTable']['RouteTableId']
        except ClientError as e:
            logger.error(f'Could not create AwsRouteTable {self.name} due to {e}.')

    @setup_task("Configure routes")
    async def configure_routes(self):
        if self.readonly: raise SkipSetupTask
        if not self.routes: raise SkipSetupTask
        # This is a separate setup task so that we can turn it off in
        # subclasses and so that we can have a hash
        await self.set_routes(*self.routes)

    @configure_routes.hash()
    def configure_routes(self):
        return repr(self.routes)

    async def dynamic_dependencies(self):
        '''
        See :func:`carthage.deployment.Deployable.dynamic_dependencies` for documentation.
        Returns dependencies for any routes
        '''
        results = []
        with instantiation_not_ready():
            for destination, target, *rest in self.routes:
                target = await resolve_deferred(self.ainjector, target, args=dict(destination=destination, kind=rest[0] if len(rest) else None))
                results.append(target)
        return results
    

inject(route_table=InjectionKey(AwsRouteTable, _optional=NotPresent))(AwsSubnet)

__all__ += ['AwsRouteTable']

        
@inject_autokwargs(
    vpc=InjectionKey(AwsVirtualPrivateCloud, _optional=NotPresent, _ready=False))
class AwsInternetGateway(AwsManaged):
                
    stamp_type = "internet_gateway"
    resource_type = "internet_gateway"
    resource_factory_method = 'InternetGateway'

    vpc:AwsVirtualPrivateCloud = None

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

    async def read_write_hook(self): 
        if self.vpc: await self.vpc.async_become_ready()
        await self.attach(self.vpc)
        if len(getattr(self.mob, 'attachments', [])) > 0:
            self.attachment_id = self.mob.attachments[0]['VpcId']
        else:
            self.attachment_id = None

__all__ += ['AwsInternetGateway']

async def aws_link_handle_eip(model, link):
    if link.merged_v4_config.public_address:
        try:
            vpc_address = await model.ainjector(VpcAddress, ip_address=str(link.merged_v4_config.public_address))
            link.vpc_address_allocation = vpc_address.id
        except LookupError:
            logger.warning(f'{model} interface {link.interface} has public address that cannot be assigned')

@inject_autokwargs(vpc=InjectionKey(AwsVirtualPrivateCloud),
                   )
class AwsNatGateway(carthage.machine.NetworkedModel, AwsManaged, InjectableModel):

    '''
    Represents a AWS NAT Gateway.
    If the connectivity_type is public (the default), AWS requires an EIP (a :class:`VpcAddress`) to be allocated.
    Do this either by:

    * Setting public_address on the v4_config of the link

    * set vpc_address_allocation to an allocation ID on the link

    * Or as a default operation, the injector associated with the NatGateway can directly provide VpcAddress.  This class provides such a default.

    '''
    
    stamp_type ='nat_gateway'
    resource_type = 'natgateway'
    resource_factory_method = NotImplemented
    network_implementation_class = no_inject_name(AwsSubnet)

    connectivity_type = 'public' #: public or private

    def __init__(self, connectivity_type=None, **kwargs):
        super().__init__(**kwargs)
        self.network_links = {}
        if connectivity_type: self.connectivity_type = connectivity_type
        if self.connectivity_type == 'public' and VpcAddress not in self.injector:
            self.injector.add_provider(InjectionKey(VpcAddress),
                                       when_needed(VpcAddress, name=self.name+ ' address'))

    async def  dynamic_dependencies(self):
        results = await super().dynamic_dependencies()
        if VpcAddress in self.injector:
            results.append(await self.ainjector.get_instance_async(
                InjectionKey(VpcAddress, _ready=False)))
        return results
        
    def find_from_id(self):
        try:
            r = self.connection.client.describe_nat_gateways(NatGatewayIds=[self.id])
        except ClientError: return
        gateways = r['NatGateways']
        for g in gateways:
            if g['State'] == 'deleted':
                self.connection.invalid_ec2_resource(self.resource_type, g['NatGatewayId'])
                continue
            self.mob = g
            break


    async def pre_create_hook(self):
        # Note this hook is also called in delete to populate
        # self.link; if that becomes inappropriate, then split
        # functionality.
        await self.resolve_networking()
        if not len(self.network_links) > 0:
            raise ValueError('At least one link required')
        link = next(iter(self.network_links.values()))
        await link.instantiate(AwsSubnet)
        await aws_link_handle_eip(self, link)
        if self.connectivity_type == 'public' and not hasattr(link, 'vpc_address_allocation'):
            if InjectionKey(VpcAddress) in self.injector:
                vpc_address = await self.ainjector.get_instance_async(VpcAddress)
                link.vpc_address_allocation = vpc_address.id
            else:
                raise ValueError('AwsNatGateway must either be connectivity_type private, or have a VpcAddress either as the public_address of the link, or directly in the injector of the nat gateway.')
        self.subnet = link.net_instance
        self.link = link

    def do_create(self):
        extras = {}
        if self.connectivity_type == 'public':
            extras['AllocationId'] = self.link.vpc_address_allocation
        if self.link.merged_v4_config.address:
            extras['PrivateIpAddress'] = str(self.link.merged_v4_config.address)
        r = self.connection.client.create_nat_gateway(
            ConnectivityType=self.connectivity_type,
            SubnetId=self.subnet.id,
            TagSpecifications=[self.resource_tags],
            **extras)
        self.id = r['NatGateway']['NatGatewayId']

    async def post_find_hook(self):
        try: await wait_for_state_change(
                self, lambda obj: obj.mob['State'],
                'available', ['pending'])
        finally:
            if self.mob['State'] == 'failed':
                logger.error(f'{self} failed: {self.mob["FailureMessage"]}')
                if not self.readonly:
                    logger.info(f'Deleting failed NAT gateway {self}')
                    await self.delete()
                    raise RuntimeError(f'{self} failed: {self.mob["FailureMessage"]}')

    async def delete(self, delete_vpc_address=None):
        def callback():
            self.connection.client.delete_nat_gateway(
                NatGatewayId=self.id)
        await self.find()
        if not self.mob: return
        logger.info('Deleting NAT Gateway %s', self.id)
        await run_in_executor(callback)
        await wait_for_state_change(
            self, lambda obj: obj.mob['State'],
            'deleted', ['available', 'pending', 'deleting'])
        if delete_vpc_address is None:
            await self.pre_create_hook()
            delete_vpc_address = not self.link.merged_v4_config.public_address
        if self.connectivity_type == 'public' and delete_vpc_address:
            try:
                vpc_address = getattr(self.link, 'vpc_address', None)
                if not vpc_address:
                    vpc_address = await self.ainjector.get_instance_async(InjectionKey(VpcAddress, _ready=False))
                    await vpc_address.find()
                if vpc_address.mob: await vpc_address.delete()
            except Exception as e:
                logger.exception(f'Deleting VPCAddress for {self}')

__all__ += ['AwsNatGateway']
