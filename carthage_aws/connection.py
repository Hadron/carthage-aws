# Copyright (C) 2022, 2023, Hadron Industries, Inc.
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
    instance='Instance',
    vpc='Vpc',
    elastic_ip='VpcAddress',
    subnet='Subnet',
    snapshot='Snapshot',
    volume='Volume',
    image='Image',
    security_group='SecurityGroup',
    route_table='RouteTable',
    internet_gateway='InternetGateway',
    natgateway='error', # There is no service resource
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
        self.connection = boto3.Session(
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            profile_name=self.config.profile
        )
        self.region = self.config.region
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

    def _inventory(self):
        # Executor context
        self.names_by_resource_type = {}
        for rt in resource_factory_methods:
            self.names_by_resource_type[rt] = {}
            nbrt = self.names_by_resource_type
        r = self.client.describe_tags(Filters=[dict(Name='key', Values=['Name'])])
        for resource in r['Tags']:
            # This setdefault should be unnecessary but is defensive
            # in case someone has tagged a resource we do not normally
            # manage with a Name.
            rt, rv = resource['ResourceType'], resource['Value']
            rt = rt.replace('-','_')
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
                   connection=InjectionKey(AwsConnection, _ready=True),
                                      readonly = InjectionKey("aws_readonly", _optional=True),
                   id=InjectionKey("aws_id", _optional=NotPresent),
                   )
class AwsManaged(SetupTaskMixin, AsyncInjectable):

    pass_name_to_super = False # True for machines
    name = None
    id = None

    def __init__(self, *, name=None, **kwargs):
        if name and self.pass_name_to_super: kwargs['name'] = name
        if name: self.name = name
        super().__init__(**kwargs)
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
        return dict(ResourceType=self.resource_type.replace('_','-'),
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
            for id in await self.possible_ids_for_name():
                # use the first viable
                self.id = id
                await run_in_executor(self.find_from_id)
                if self.mob:
                    return
            self.id = None

    async def possible_ids_for_name(self):
        resource_type = self.resource_type
        names = self.connection.names_by_resource_type[resource_type]
        if self.name in names:
            objs = names[self.name]
            return [obj['ResourceId'] for obj in objs]
        return []

    def __repr__(self):
        if self.name:
            return f'<{self.__class__.__name__} ({self.name}) at 0x{id(self):0x}>'
        else:
            return f'<anonymous {self.__class__.__name__} at 0x{id(self):0x}>'

    @setup_task("construct", order=700)
    async def find_or_create(self):

        if self.mob:
            return

        # If we are called directly, rather than through setup_tasks,
        # then our check_completed will not have run, so we should
        # explicitly try find, because double creating is bad.

        await self.find()

        if self.mob:
            if not self.readonly: await self.ainjector(self.read_write_hook)
            await self.ainjector(self.post_find_hook)
            return

        if not self.name:
            raise RuntimeError(f'unable to create AWS resource for {self} without a name')
        if self.readonly:
            raise LookupError(f'unable to find AWS resource for {self} and creation was not enabled')

        await self.ainjector(self.pre_create_hook)
        await run_in_executor(self.do_create)

        if not (self.mob or self.id):
            raise RuntimeError(f'do_create failed to create AWS resource for {self}')

        if not self.mob:
            await self.find()
        elif not self.id:
            self.id = self.mob.id

        await self.ainjector(self.post_create_hook)
        await self.ainjector(self.read_write_hook)
        await self.ainjector(self.post_find_hook)
        return self.mob

    @find_or_create.check_completed()
    async def find_or_create(self):
        await self.find()
        if self.mob:
            if not self.readonly: await self.ainjector(self.read_write_hook)
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
        '''Any tasks performed in async context after an object is found or created.  May have injected dependencies.  If you need to perform tasks before find, simply override :meth:`find`.  This hook MUST NOT modify the object if self.readonly is True.  In general, any tasks that may modify the object should be run in :meth:`read_write_hook` which runs before *post_find_hook*.'''
        pass
    
    async def read_write_hook(self):
        '''
        A hook for performing tasks that may modify the state of an object such as reconciling expected configuration with actual configuration.  Called before :meth:`post_find_hook` when an object is found or created, but only when *readonly* is not true.
        '''
        pass
    

    @memoproperty
    def stamp_path(self):
        p = Path(self.config_layout.state_dir)
        p = p.joinpath("aws_stamps", self.stamp_type,str(self.id)+".stamps")
        os.makedirs(p, exist_ok=True)
        return p



async def wait_for_state_change(obj, get_state_func, desired_state:str, wait_states: list[str]):
    '''
    Wait for a state transition, generally for objects without a boto3 resource implementation.  So *get_state_func* typically decomposes whatever :meth:``find_from_id` puts in *mob*.

    :param obj: An :class:`AwsManaged` implementing *find_from_id* which we will use as a reload function.

    :param get_state_func: A function to get the current state from *mob*, possibly something like `lambda obj:obj.mob['State']`

    :param desired_state: The state that counts as success.

    :param wait_states:  If one of these states persists, then continue to wait.

    '''
    timeout = 90 # Turn this into a parameter if we need to adjust.
    state = get_state_func(obj)
    if state == desired_state: return
    logged = False
    while timeout > 0:
        if state not in wait_states:
            raise RuntimeError(f'Unexpected state for {obj}: {state}')
        if not logged:
            logger.info(f'Waiting for {obj} to enter {desired_state} state')
            logged=True
        await asyncio.sleep(5)
        timeout -= 5
        await run_in_executor(obj.find_from_id)
        state = get_state_func(obj)
        
