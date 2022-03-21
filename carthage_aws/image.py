# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import datetime
from carthage import *
from carthage.modeling import InjectableModel
from .connection import AwsConnection, run_in_executor
from .ebs import AwsVolume

__all__ = []


def image_provider(
        owner, *,
        name,
        architecture="x86_64",
        ):
    def callback(connection):
        r = connection.client.describe_images(
            Owners=[owner],
            Filters=[dict(
                Name='name',
                Values=[name])])
        images = r['Images']
        for i in images:
            creation_date = i['CreationDate']
            #AWS uses trailing Z rather than offset; datetime.datetime cannot deal with that
            creation_date = creation_date[:-1]+'+00:00'
            i['CreationDate'] = datetime.datetime.fromisoformat(creation_date)
        images.sort(key=lambda i: i['CreationDate'], reverse=True)
        return images

    @inject(connection=AwsConnection)
    async def image_provider_inner(connection):
        images = await run_in_executor(callback, connection)
        return images[0]['ImageId']
    return image_provider_inner

__all__ += ['image_provider']

debian_ami_owner ='136693071363'

__all__ += ['debian_ami_owner']

@inject(model=AbstractMachineModel,
        volume_size=InjectionKey("aws_image_size"))
class ImageBuilderVolume(AwsVolume):

    async def pre_create_hook(self):
        await super().pre_create_hook()
        machine =  await self.model.ainjector.get_instance_async(InjectionKey(Machine, _ready=False))
        await machine.start_machine()
        self.injector.add_provider(InjectionKey("aws_availability_zone"),
                               machine.mob.placement['AvailabilityZone'])
    

    @memoproperty
    def name(self):
        return f'{self.model.name}/image'

__all__ += ['ImageBuilderVolume']

class AttachImageBuilderVolume(MachineCustomization, InjectableModel):

    description = "Attach image builder volume"

    image_builder_volume = ImageBuilderVolume
    
    @setup_task("Attach  image builder Volume")
    @inject(volume=InjectionKey("image_builder_volume"))
    async def attach_volume(self, volume):
        await volume.attach(self.host, "/dev/xvdi", delete_on_termination=True)

    @attach_volume.check_completed()
    def attach_volume(self):
        return any(filter(
            lambda mapping: mapping['DeviceName'] == "/dev/xvdi", self.host.mob.block_device_mappings))
    
__all__ += ['AttachImageBuilderVolume']
