# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio, contextlib, time
import yaml
from pathlib import Path
from ipaddress import IPv4Address

from carthage import *
from carthage.modeling import *
from carthage.dependency_injection import *
from carthage.vm import vm_image
from carthage.config import ConfigLayout
from carthage.machine import Machine
from carthage.network import NetworkConfig, NetworkLink
from carthage.cloud_init import generate_cloud_init_cloud_config

import boto3
from botocore.exceptions import ClientError

from .connection import AwsConnection, AwsManaged, run_in_executor
from .network import AwsVirtualPrivateCloud, AwsSubnet

__all__ = ['AwsVm']



@inject_autokwargs(connection=InjectionKey(AwsConnection,_ready=True),
                   )
class AwsVm(AwsManaged, Machine):

    pass_name_to_super = True

    def __init__(self, name, **kwargs):
        self.name = ""
        super().__init__(name=name, **kwargs)
        self.running = False
        self.closed = False
        self._operation_lock = asyncio.Lock()
        self._clear_ip_address = True

    def _find_ip_address(self):
        def async_cb():
            for network_link in updated_links:
                self.injector.emit_event(
                    InjectionKey(NetworkLink),
                    "public_address", network_link,
                    adl_keys=[InjectionKey(NetworkLink, host=self.name)])
                
        updated_links = []
        if self.__class__.ip_address is Machine.ip_address:
            try:
                self.ip_address
                update_ip_address = False
            except NotImplementedError:
                update_ip_address = True

        self.mob.wait_until_running()
        self.mob.reload()
        local_network_links = filter(lambda l: not l.local_type, self.network_links.values())
        for network_link, interface in zip(local_network_links, self.mob.network_interfaces):
            if network_link.net_instance.mob != interface.subnet:
                logger.warning(f'Instance {self.id}: network links do not match instance interface for {network_link.interface}')
                continue
            if not interface.association_attribute: continue
            association = interface.association_attribute
            if not ('PublicIp' in association and association['PublicIp']): continue
            address = IPv4Address(association['PublicIp'])
            if address != network_link.public_v4_address:
                network_link.public_v4_address = address
                updated_links.append(network_link)
                if update_ip_address:
                    self.ip_address = str(address)
                    self._clear_ip_address = True
        if updated_links:
            self.ainjector.loop.call_soon_threadsafe(async_cb)
            
    async def find(self):
        await self.resolve_networking()
        futures = []
        loop = asyncio.get_event_loop()
        res = await super().find()
        if not (self.network_links or self.mob):
            raise RuntimeError('AWS instances require a network link to create')
        for l in self.network_links.values():
            futures.append(loop.create_task(l.instantiate(AwsSubnet)))
        await asyncio.gather(*futures)
        return res

    async def pre_create_hook(self):
        # operation lock is held by overriding find_or_create
        if getattr(self.model, 'cloud_init',False):
            self.cloud_config = await self.ainjector(generate_cloud_init_cloud_config, model=self.model)
        else: self.cloud_config = None
        self.image_id = await self.ainjector.get_instance_async('aws_ami')
        await self.start_dependencies()
        await super().start_machine()

    @setup_task("Create VM", order=AwsManaged.find_or_create.order)
    async def find_or_create(self, already_locked=False):
        async with contextlib.AsyncExitStack() as stack:
            if not already_locked:
                await stack.enter_async_context(self._operation_lock)
            return await super().find_or_create()

    find_or_create.check_completed_func = AwsManaged.find_or_create.check_completed_func
    
            
    def do_create(self):
        network_interfaces = []
        device_index = 0
        for l in self.network_links.values():
            d = {
                'DeviceIndex': device_index,
                'Description': l.interface,
                'SubnetId': l.net_instance.id,
            }
            if l.merged_v4_config.address:
                assert l.merged_v4_config.address in l.net.v4_config.network.hosts(),f"{l.merged_v4_config.address} is not a hostaddr in {l.net.v4_config.network} for host {self.name}"
                d['PrivateIpAddress'] = l.merged_v4_config.address.compressed
            if len(self.network_links) == 1:
                d['AssociatePublicIpAddress'] = True
            if l.net_instance.vpc.groups:
                d['Groups'] = [ l.net_instance.vpc.groups[0]['GroupId'] ]
            network_interfaces.append(d)
            device_index += 1


        user_data = ""
        if self.cloud_config:
            user_data = "#cloud-config\n"
            user_data += yaml.dump(self.cloud_config.user_data, default_flow_style=False)
            
        logger.info(f'Starting {self.name} VM')

        try:
            extra = {}
            key_name = self._gfi('aws_key_name', default=None)
            if key_name: extra['KeyName'] = key_name

            r = self.connection.client.run_instances(
                ImageId=self.image_id,
                MinCount=1,
                MaxCount=1,
                InstanceType=self._gfi('aws_instance_type'),
                UserData=user_data,
                NetworkInterfaces=network_interfaces,
                TagSpecifications=[self.resource_tags],
                **extra
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
                return

    async def post_find_hook(self):
        await self.is_machine_running()
        return await super().post_find_hook()
    
    async def start_machine(self):
        async with self._operation_lock:
            await self.is_machine_running()
            if self.running: return
            await self.start_dependencies()
            await super().start_machine()
            if not self.mob:
                await self.find_or_create(already_locked=True) #presumably create since is_machine_running calls find already
                await self.is_machine_running()
                if self.running: return
                logger.info(f'Starting {self.name}')
            await run_in_executor(self.mob.start)
            await run_in_executor(self.mob.wait_until_running)
            await self.is_machine_running()
            return True
            

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            await run_in_executor(self.mob.stop)
            if self._clear_ip_address:
                del self.ip_address
            self.running = False
            await super().stop_machine()

    async def is_machine_running(self):
        if not self.mob: await self.find()
        if not self.mob:
            self.running = False
            return False
        self.running = self.mob.state['Name'] in ('pending', 'running')
        if self.running:
            await run_in_executor(self._find_ip_address)
        return self.running

    async def delete(self):
        await run_in_executor(self.mob.terminate)
        
    stamp_type = 'vm'
    resource_type = 'instance'
    
