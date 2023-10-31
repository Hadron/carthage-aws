# Copyright (C) 2022, 2023, Hadron Industries, Inc.
 # Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio

from carthage import *
from carthage.machine import AbstractMachineModel
from carthage.modeling import *
from .connection import AwsManaged, AwsConnection, run_in_executor

__all__ = []

@inject_autokwargs(
    snapshot=InjectionKey('aws_volume_snapshot', _optional=NotPresent, _ready=False),
    )
class AwsVolume(AwsManaged, InjectableModel):

    resource_type = 'volume'
    
    role = None
    volume_size = None
    volume_type = 'gp2'
    snapshot = None #: Snapshot from which volume will be created
    
    async def pre_create_hook(self):
        await super().pre_create_hook()
        if not self.volume_size: self.volume_size = self._gfi(
                'volume_size', default=None if self.snapshot else 'error')
        self.snapshot_id = None
        if self.snapshot and isinstance(self.snapshot, AsyncInjectable):
            await self.snapshot.async_become_ready()
            await self.snapshot.wait_for_available()
        if self.snapshot and isinstance(self.snapshot,str):
            self.snapshot_id = self.snapshot
        else: self.snapshot_id = self.snapshot.id
            
        

    def do_create(self):
        create_args = dict(
            VolumeType=self.volume_type,
            TagSpecifications=[self.resource_tags],
            AvailabilityZone=self._gfi('aws_availability_zone')
            )
        if self.snapshot_id: create_args['SnapshotId'] = self.snapshot_id
        if self.volume_size: create_args['Size'] = self.volume_size
        self.mob = self.service_resource.create_volume(**create_args)

    async def delete(self):
        await run_in_executor(self.mob.delete)


    async def wait_for_available(self):
        tries = 0
        max_tries = self._gfi('aws_volume_timeout', 150)//5
        if self.mob.state == 'available': return
        if self.mob.state != 'creating':
            raise RuntimeError('Unexpected state')
        
        while tries < max_tries:
            if self.mob.state != 'creating': return
            await asyncio.sleep(5)
            await run_in_executor(self.mob.reload)
            tries += 1
            
    async def attach(self, instance, device, delete_on_termination=True):
        from .vm import AwsVm
        def callback():
            self.connection.client.attach_volume(
                VolumeId=self.id,
                InstanceId=instance_id,
                Device=device)
            if delete_on_termination:
                self.connection.client.modify_instance_attribute(
                    InstanceId=instance_id,
                    BlockDeviceMappings=[dict(
                        DeviceName=device,
                        Ebs=dict(DeleteOnTermination=delete_on_termination))])
            self.mob.reload()

        if isinstance(instance,AwsVm):
            instance_id = instance.id
        elif isinstance(instance,AbstractMachineModel):
            assert isinstance(instance.machine,AwsVm)
            instance_id = instance.machine.id
        else: instance_id = instance
        await self.wait_for_available()
        await run_in_executor(callback)

    async def detach(self, instance, device):
        from .vm import AwsVm
        def callback():
            self.connection.client.detach_volume(
                VolumeId=self.id,
                InstanceId=instance_id,
                Device=device)
            self.mob.reload()

        if isinstance(instance,AwsVm):
            instance_id = instance.id
        elif isinstance(instance,AbstractMachineModel):
            assert isinstance(instance.machine,AwsVm)
            instance_id = instance.machine.id
        else: instance_id = instance
        await self.wait_for_available()
        await run_in_executor(callback)
        
__all__ += ['AwsVolume']

def attach_volume_task(*, device, volume, delete_on_termination=True):
    '''
    Typical usage inside a :class:`MachineCustomization`::

        attach_xvdb = attach_volume_task(device="/dev/xvdb", volume=InjectionKey("our_secondary_volume"))

    That will attach a volume providing the ``our_secondary_volume`` dependency as ``/dev/xvdb``.

    '''
    from .vm import AwsVm
    assert isinstance(volume,InjectionKey), "Currently only InjectionKeys for volumes are supported"
    @setup_task(f"Attach {device}")
    @inject(vm=InjectionKey(AwsVm, _ready=False), volume=InjectionKey(volume, _ready=False))
    async def attach_volume(self, vm, volume):
        if not vm.mob: await vm.find_or_create()
        volume.injector.add_provider(InjectionKey('aws_availability_zone'), vm.mob.subnet.availability_zone)
        await volume.async_become_ready()
        return await volume.attach(instance=vm, device=device, delete_on_termination=delete_on_termination)
    @attach_volume.check_completed()
    @inject(vm=InjectionKey(AwsVm, _ready=False))
    async def attach_volume(self, vm):
        if not vm.mob: await vm.find()
        if not vm.mob: return False
        return any(filter(
            lambda mapping: mapping['DeviceName'] == device, vm.mob.block_device_mappings))

    return attach_volume

__all__ += ['attach_volume_task']

@inject_autokwargs(
    volume=InjectionKey('aws_snapshot_source', _optional=NotPresent, _ready=False),
    )
class AwsSnapshot(AwsManaged, InjectableModel):

    resource_type = 'snapshot'

    @memoproperty
    def description(self):
        '''Description of the snapshot; defaults to name.
        If the description needs to be set, either subclass and override, or instantiate _ready=False and update the description before calling :meth:`async_become_ready`.'''
        return self.name

    async def pre_create_hook(self):
        if isinstance(self.volume, AsyncInjectable):
            await self.volume.async_become_ready()
        if isinstance(self.volume, str):
            self.volume_id = self.volume
        else: self.volume_id = self.volume.id

    def do_create(self):
        self.mob = self.service_resource.create_snapshot(
            Description=self.description,
            VolumeId=self.volume_id,
            TagSpecifications=[self.resource_tags],
            )

    async def wait_for_available(self):
        max_tries = self._gfi('aws_volume_timeout', 150)//5
        tries = 0
        if self.mob.state == 'completed': return
        if self.mob.state != 'pending':
            raise RuntimeError('Unexpected state')
        
        while tries < max_tries:
            if self.mob.state != 'pending': return
            await asyncio.sleep(5)
            await run_in_executor(self.mob.reload)
            tries += 1

    async def delete(self):
        if not self.mob: await self.find()
        if not self.mob: return
        await run_in_executor(self.mob.delete)
        
__all__ += ['AwsSnapshot']
        
