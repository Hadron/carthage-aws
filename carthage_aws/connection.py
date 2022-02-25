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
    instance='Instance',
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
            aws_secret_access_key=self.config.secret_access_key
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
        self.vms = []
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
            nbrt.setdefault(resource['ResourceType'], {})
            nbrt[resource['ResourceType']][resource['Value']] = resource
            
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



@inject_autokwargs(config_layout=ConfigLayout,
                   connection=InjectionKey(AwsConnection, _ready=True),
                   )
class AwsManaged(SetupTaskMixin, AsyncInjectable):

    pass_name_to_super = False # True for machines

    def __init__(self, *, name=None, id=None, **kwargs):
        if name and self.pass_name_to_super: kwargs['name'] = name
        self.name = name or ""
        self.id = id
        super().__init__(**kwargs)
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
        return dict(ResourceType=self.resource_type,
                    Tags=tags)
    
    def find_from_id(self):
        #called in executor context; create a mob from id
        assert self.id
        resource_factory = getattr(self.service_resource, resource_factory_methods[self.resource_type])
        self.mob = resource_factory(self.id)
        self.mob.load()
        return self.mob


    async def find(self):
        '''Find ourself from a name or id
'''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            resource_type = self.resource_type
            names = self.connection.names_by_resource_type[resource_type]
            if self.name in names:
                self.id = names[self.name]['ResourceId']
                return await run_in_executor(self.find_from_id)
        return

    @setup_task("construct")
    async def find_or_create(self):
        if self.mob: return
        # If we are called directly, rather than through setup_tasks,
        # then our check_completed will not have run, so we should
        # explicitly try find, because double creating is bad.
        await self.find()
        if self.mob: return
        if not self.name: raise RuntimeError('You must specify a name for creation')
        await run_in_executor(self.do_create)
        await self.find()
        return self.mob

    @find_or_create.check_completed()
    async def find_or_create(self):
        await self.find()
        if self.mob: return True
        return False
    
    def do_create(self):
        # run in executor context
        raise NotImplementedError



    @memoproperty
    def stamp_path(self):
        p = Path(self.config_layout.state_dir)
        p = p.joinpath("aws_stamps", self.stamp_type+".stamps")
        os.makedirs(p, exist_ok=True)
        return p


