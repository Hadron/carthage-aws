# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
from pathlib import Path

import asyncio
import functools
import logging
import os

from carthage import *
from carthage.config import ConfigLayout
from carthage.dependency_injection import *

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsConnection', 'AwsManaged']

resource_factory_methods = dict(
    capacity_reservation='CapacityReservation',
    client_vpn_endpoint='ClientVpnEndpoint',
    customer_gateway='CustomerGateway',
    carrier_gateway='CarrierGateway',
    dedicated_host='DedicatedHost',
    dhcp_options='DhcpOptions',
    egress_only_internet_gateway='EgressOnlyInternetGateway',
    elastic_ip='ElasticIp',
    elastic_gpu='ElasticGpu',
    export_image_task='ExportImageTask',
    export_instance_task='ExportInstanceTask',
    fleet='Fleet',
    fpga_image='FpgaImage',
    host_reservation='HostReservation',
    image='Image',
    import_image_task='ImportImageTask',
    import_snapshot_task='ImportSnapshotTask',
    instance='Instance',
    instance_event_window='InstanceEventWindow',
    internet_gateway='InternetGateway',
    ipam='Ipam',
    ipam_pool='IpamPool',
    ipam_scope='IpamScope',
    ipv4pool_ec2='Ipv4PoolEc2',
    ipv6pool_ec2='Ipv6PoolEc2',
    key_pair='KeyPair',
    launch_template='LaunchTemplate',
    local_gateway='LocalGateway',
    local_gateway_route_table='LocalGatewayRouteTable',
    local_gateway_virtual_interface='LocalGatewayVirtualInterface',
    local_gateway_virtual_interface_group='LocalGatewayVirtualInterfaceGroup',
    local_gateway_route_table_vpc_association='LocalGatewayRouteTableVpcAssociation',
    local_gateway_route_table_virtual_interface_group_association='LocalGatewayRouteTableVirtualInterfaceGroupAssociation',
    natgateway='Natgateway',
    network_acl='NetworkAcl',
    network_interface='NetworkInterface',
    network_insights_analysis='NetworkInsightsAnalysis',
    network_insights_path='NetworkInsightsPath',
    network_insights_access_scope='NetworkInsightsAccessScope',
    network_insights_access_scope_analysis='NetworkInsightsAccessScopeAnalysis',
    placement_group='PlacementGroup',
    prefix_list='PrefixList',
    replace_root_volume_task='ReplaceRootVolumeTask',
    reserved_instances='ReservedInstances',
    route_table='RouteTable',
    security_group='SecurityGroup',
    security_group_rule='SecurityGroupRule',
    snapshot='Snapshot',
    spot_fleet_request='SpotFleetRequest',
    spot_instances_request='SpotInstancesRequest',
    subnet='Subnet',
    subnet_cidr_reservation='SubnetCidrReservation',
    traffic_mirror_filter='TrafficMirrorFilter',
    traffic_mirror_session='TrafficMirrorSession',
    traffic_mirror_target='TrafficMirrorTarget',
    transit_gateway='TransitGateway',
    transit_gateway_attachment='TransitGatewayAttachment',
    transit_gateway_connect_peer='TransitGatewayConnectPeer',
    transit_gateway_multicast_domain='TransitGatewayMulticastDomain',
    transit_gateway_route_table='TransitGatewayRouteTable',
    volume='Volume',
    vpc='Vpc',
    vpc_endpoint='VpcEndpoint',
    vpc_endpoint_service='VpcEndpointService',
    vpc_peering_connection='VpcPeeringConnection',
    vpn_connection='VpnConnection',
    vpn_gateway='VpnGateway',
    vpc_flow_log='VpcFlowLog'
)

async def run_in_executor(func, *args):
    return await asyncio.get_event_loop().run_in_executor(None, func, *args)
    
@inject_autokwargs(config_layout=ConfigLayout)
class AwsConnection(AsyncInjectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = self.config_layout.aws
        self.connection = None

    async def inventory(self):
        await run_in_executor(self._inventory)
    def _setup(self):
        if not self.config.access_key_id: breakpoint()
        if not self.config.secret_access_key: breakpoint()
        self.aws_access_key_id = self.config.access_key_id
        self.region = self.config.region
        self.connection = boto3.Session(
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name=self.region
        )
        self.client = self.connection.client('ec2', region_name=self.region)
        self.keys = []
        for key in self.client.describe_key_pairs()['KeyPairs']:
            self.keys.append(key['KeyName'])
        self.vpcs = []
        self.igs = []
        self.subnets = []
        self.groups = []
        self.run_vpc = None
        self._inventory()

    async def carthage_iter(self, cclass, kw, field, idfield):
        pass

    async def carthage_vpcs(self):
        from .network import AwsVirtualPrivateCloud
        for x in self.client.describe_vpcs()['Vpcs']:
            yield await self.ainjector(AwsVirtualPrivateCloud, id=x['VpcId'])

    async def carthage_subnets(self):
        from .network import AwsSubnet
        for x in self.client.describe_subnets()['Subnets']:
            yield await self.ainjector(AwsSubnet, id=x['SubnetId'])

    def _inventory(self):
        # Executor context
        self.names_by_resource_type = {}
        for rt in resource_factory_methods:
            if '_' in rt:
                rt='-'.join(rt.split('_'))
            self.names_by_resource_type[rt] = {}
            nbrt = self.names_by_resource_type
        r = self.client.describe_tags(Filters=[dict(Name='key', Values=['Name'])])
        for resource in r['Tags']:
            # This setdefault should be unnecessary but is defensive
            # in case someone has tagged a resource we do not normally
            # manage with a Name.
            rt, rv = resource['ResourceType'], resource['Value']
            nbrt.setdefault(rt, {})
            nbrt[rt].setdefault(rv, [])
            nbrt[rt][rv].append(resource)
            
        r = self.client.describe_vpcs()
        for v in r['Vpcs']:
            vpc = {'id': v['VpcId']}
            if 'Tags' in v:
                for t in v['Tags']:
                    if t['Key'] == 'Name':
                        vpc['name'] = t['Value']
            else: vpc['name'] = ''
            self.vpcs.append(vpc)
            if (self.config.vpc_id != None or self.config.vpc_id != '') and vpc['id'] == self.config.vpc_id:
                self.run_vpc = vpc
            elif (self.config.vpc_id == None or self.config.vpc_id == '') and 'Tags' in v:
                for t in v['Tags']:
                    if t['Key'] == 'Name' and t['Value'] == self.config.vpc_name:
                        self.run_vpc = vpc

        r = self.client.describe_internet_gateways()
        for ig in r['InternetGateways']:
            if len(ig['Attachments']) == 0:
                continue
            a = ig['Attachments'][0]
            if a['State'] == 'attached' or a['State'] == 'available':
                self.igs.append({'id': ig['InternetGatewayId'], 'vpc': a['VpcId']})

        r = self.client.describe_security_groups()
        for g in r['SecurityGroups']:
            self.groups.append(g)

        r = self.client.describe_subnets()
        for s in r['Subnets']:
            subnet = {'CidrBlock': s['CidrBlock'], 'id': s['SubnetId'], 'vpc': s['VpcId']}
            self.subnets.append(subnet)

    
    def set_running_vpc(self, vpc):
        for v in self.vpcs:
            if v['id'] == vpc:
                self.run_vpc = v

    async def async_ready(self):
        await run_in_executor(self._setup)
        return await super().async_ready()

    def invalid_ec2_resource(self, resource_type, id, *, name=None):
        '''Indicate that a given resource does not (and will not) exist.
Clean it out of our caches and untag it.
Run in executor context.
'''
        if name:
            names = self.names_by_resource_type.get(resource_type)
            if names and name in names:
                names[name] = list(filter(
                    lambda r: r['ResourceId'] != id, names[name]))

        self.client.delete_tags(Resources=[id])
        
            

@inject_autokwargs(config_layout=ConfigLayout,
                   connection=InjectionKey(AwsConnection),
                   readonly=InjectionKey("aws_readonly", _optional=True),
                   id=InjectionKey("aws_id", _optional=True),
                   )
class AwsManaged(SetupTaskMixin, AsyncInjectable):

    pass_name_to_super = False # True for machines
    name = None
    id = None
    def __init__(self, *, name=None, **kwargs):
        if name and self.pass_name_to_super: kwargs['name'] = name
        if name: self.name = name
        super().__init__(**kwargs)
        if self.id is None and self.__class__.id: self.id = self.__class__.id
        if not self.readonly: self.readonly = bool(self.id)
        self.mob = None
        

    @memoproperty
    def stamp_type(self):
        raise NotImplementedError(type(self))

    @property
    def resource_type(self):
        '''The resource type associated with tags'''
        raise NotImplementedError

    @memoproperty
    def service_resource(self):
        # override for non-ec2
        return self.connection.connection.resource('ec2', region_name=self.connection.region)

    @property
    def resource_tags(self):
        tags = []
        if self.name:
            tags.append(dict(Key="Name", Value=self.name))
        if '_' in self.resource_type:
            rt = '-'.join(self.resource_type.split('_'))
        else:
            rt = self.resource_type
        return dict(ResourceType=rt,
                    Tags=tags)
    
    def find_from_id(self):
        #called in executor context; create a mob from id
        assert self.id
        resource_factory = getattr(self.service_resource, resource_factory_methods[self.resource_type])
        self.mob = resource_factory(self.id)
        try:
            self.mob.load()
        except ClientError as e:
            if hasattr(self.mob, 'wait_until_exists'):
                logger.info(f'Waiting for {repr(self.mob)} to exist')
                self.mob.wait_until_exists()
                self.mob.load()
            else:
                logger.warning(f'Failed to load {self}', exc_info=e)
                breakpoint()
                self.mob = None
                if not self.readonly:
                    self.connection.invalid_ec2_resource(self.resource_type, self.id, name=self.name)
                return
        return self.mob


    async def find(self):
        '''Find ourself from a name or id
'''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            resource_type = '-'.join(self.resource_type.split('_')) if '_' in self.resource_type else self.resource_type
            names = self.connection.names_by_resource_type[resource_type]
            if self.name in names:
                objs = names[self.name]
                for obj in objs:
                    # use the first viable
                    self.id = obj['ResourceId']
                    await run_in_executor(self.find_from_id)
                    if self.mob:
                        return
            self.id = None

    @setup_task("construct")
    async def find_or_create(self):
        if self.mob: return
        # If we are called directly, rather than through setup_tasks,
        # then our check_completed will not have run, so we should
        # explicitly try find, because double creating is bad.
        await self.find()
        if self.mob:
            await self.ainjector(self.post_find_hook)
            return
        if not self.name: raise RuntimeError('You must specify a name for creation')
        if self.readonly:
            raise LookupError(f'{self.__class__.__name__} with name {self.name} not found and readonly enabled.')

        await self.ainjector(self.pre_create_hook)
        await run_in_executor(self.do_create)
        if not (self.mob or self.id):
            raise RuntimeError(f'created object for {self} provided neither mob nor id')
        if not self.mob:
            await self.find()
        else:
            if not self.id: self.id = self.mob.id
        await self.ainjector(self.post_create_hook)
        await self.ainjector(self.post_find_hook)
        return self.mob

    @find_or_create.check_completed()
    async def find_or_create(self):
        await self.find()
        if self.mob:
            await self.ainjector(self.post_find_hook)
            return True
        return False

    def _gfi(self, key, default="error"):
        '''
        get_from_injector.  Used to look up some configuration in the model or its enclosing injectors.
    '''
        k = InjectionKey(key, _optional=( default != "error"))
        res = self.injector.get_instance(k)
        if res is None and default != "error": res = default
        return res
    
    def do_create(self):
        '''Run in executor context.  Do the actual creation.  Cannot do async things.  Do any necessary async work in pre_create_hook.'''
        raise NotImplementedError

    async def pre_create_hook(self):
        '''Any async tasks that need to be performed before do_create is called in executor context.  May have injected dependencies.'''
        pass

    async def post_create_hook(self):
        '''Any tasks that should be performed in async context after creation.  May have injected dependencies.'''
        pass

    async def post_find_hook(self):
        '''Any tasks performed in async context after an object is found or created.  May have injected dependencies.  If you need to perform tasks before find, simply override :meth:`find`'''
        pass
    
    @memoproperty
    def stamp_path(self):
        p = Path(self.config_layout.state_dir)
        p = p.joinpath("aws_stamps", self.stamp_type+".stamps")
        os.makedirs(p, exist_ok=True)
        return p

class AwsManagedClient(AwsManaged):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    client_type = 'ec2'

    @memoproperty
    def resource_name(self):
        return "".join([x.title() for x in self.resource_type.split('_')])

    @memoproperty
    def client(self):
        return self.connection.connection.client(self.client_type, self.connection.region)

    async def delete(self):
        def callback():
            r = getattr(self.client, f'delete_{self.resource_type}').__call__(**{f'{self.resource_name}Ids':[self.id]})
        await run_in_executor(callback)

    def find_from_id(self):
        r = getattr(self.client, f'describe_{self.resource_type}s').__call__(**{f'{self.resource_name}Ids':[self.id]})
        for t in r[f'{self.resource_name}s'][0]['Tags']:
            if t['Key'] == 'Name':
                self.name = t['Value']
        self.mob = self
        return self.mob
