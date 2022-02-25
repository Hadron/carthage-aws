# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio
from pathlib import Path

from carthage import *
from carthage.modeling import *
from carthage.dependency_injection import *
from carthage.vm import vm_image
from carthage.config import ConfigLayout
from carthage.machine import Machine

import boto3
from botocore.exceptions import ClientError

from .connection import AwsConnection, AwsManaged, run_in_executor
from .network import AwsVirtualPrivateCloud, AwsSubnet

__all__ = ['AwsVm']



@inject_autokwargs(connection=InjectionKey(AwsConnection,_ready=True),  network=InjectionKey(NetworkModel),
                   subnet=InjectionKey(AwsSubnet, _ready=True))
class AwsVm(AwsManaged, Machine):

    pass_name_to_super = True

    def __init__(self, name, **kwargs):
        self.name = ""
        print(super().__init__)
        super().__init__(name=name, **kwargs)
        self.running = False
        self.closed = False
        self._operation_lock = asyncio.Lock()
        self.key = self.model.key
        self.imageid = self.model.imageid
        self.size = self.model.size
        #self.subnet = None
        self.id = None
        self.vpc = self.connection.run_vpc
        found_vm = []
        if self.vpc != None:
            found_vm = [vm for vm in self.connection.vms if vm['vpc'] == self.vpc['id'] and vm['name'] == self.name]
        if len(found_vm) > 0:
            self.id = found_vm[0]['id']
            self.running = True

    def do_create(self):
        self.vpc = self.connection.run_vpc
        logger.info(f'Starting {self.name} VM')

        self.vpc = self.subnet.vpc
        try:
            r = self.connection.client.run_instances(
                ImageId=self.imageid,
                MinCount=1,
                MaxCount=1,
                InstanceType=self.size,
                KeyName=self.key,
                NetworkInterfaces=[{
                    'DeviceIndex': 0,
                                                'SubnetId': self.subnet.id,
                    'AssociatePublicIpAddress': True,
                    'Groups': [ self.vpc.groups[0]['GroupId'] ]
                }],
                TagSpecifications=[self.resource_tags],
            )
            self.id = r['Instances'][0]['InstanceId']
        except ClientError as e:
            logger.error(f'Could not create AWS VM for {self.model.name} because {e}.')




            return True

    def find_from_id(self):
        # terminated instances do not count
        super().find_from_id()
        if self.mob:
            if self.mob.state['Name'] == 'terminated':
                self.mob = None

    async def start_machine(self):
        async with self._operation_lock:
            if self.running is True: return
            await self.start_dependencies()
            await super().start_machine()
            if not self.mob:
                await self.find_or_create()
                await self.is_machine_running
                if self.running: return
                logger.info(f'Starting {self.name}')
            await run_in_executor(self.mob.start)
            self.running = True

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            await run_in_executor(self.mob.stop)
            self.running = False
            awaitsuper().stop_machine()

    async def is_machine_running(self):
        if not self.mob: await self.find()
        if not self.mob:
            self.running = False
            return False
        await run_in_executor(self.mob.load)
        self.running = self.mob.state['Name'] in ('pending', 'running')
        return self.running
    
        
            
    


    stamp_type = 'vm'

    resource_type = 'instance'
    
