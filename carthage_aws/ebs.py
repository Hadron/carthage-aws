# Copyright (C) 2022, Hadron Industries, Inc.
 # Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *
from carthage.machine import AbstractMachineModel
from carthage.modeling import *
from .connection import AwsManaged, AwsConnection, run_in_executor

__all__ = []

class AwsVolume(AwsManaged, InjectableModel):

    resource_type = 'volume'
    
    role = None
    volume_size = None
    volume_type = 'gp2'
    
    async def pre_create_hook(self):
        await super().pre_create_hook()
        if not self.volume_size: self.volume_size = self._gfi('volume_size')
        

    def do_create(self):
        self.mob = self.service_resource.create_volume(
            VolumeType=self.volume_type,
            Size=self.volume_size,
            TagSpecifications=[self.resource_tags],
            AvailabilityZone=self._gfi('aws_availability_zone')
            )

    async def delete(self):
        await run_in_executor(self.mob.delete)

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
        await run_in_executor(callback)
        
__all__ += ['AwsVolume']
