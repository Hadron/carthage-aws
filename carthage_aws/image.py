# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import datetime
from pathlib import Path
from carthage import *
from carthage.debian import *
from carthage.modeling import *
from carthage import sh
from .connection import AwsConnection, run_in_executor, AwsManaged
from .ebs import AwsVolume

__all__ = []


def image_provider(
        name,
        *, owner='self',
        architecture="x86_64",
        all_images=False,
        fallback=None
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

    @inject(connection=AwsConnection, injector=Injector)
    async def image_provider_inner(connection, injector):
        images = await run_in_executor(callback, connection)
        if len(images) == 0:
            if fallback:
                ainjector = injector(AsyncInjector)
                return await ainjector(fallback)
            return None
        if all_images:
            return list(map(lambda i: i['ImageId'], images))
        else:
            return images[0]['ImageId']
    return image_provider_inner

__all__ += ['image_provider']

debian_ami_owner ='136693071363'

__all__ += ['debian_ami_owner']

class AwsImage(AwsManaged):
    resource_type = 'image'
    resource_factory_method = 'Image'
    readonly = True

    async def possible_ids_for_name(self):
        objs = self.connection.client.describe_images(
            Owners=['self'],
            Filters=[dict(
                Name='name',
                Values=[self.name])])
        return map(lambda o: o['ImageId'], objs['Images'])

    async def get_snapshots(self):
        def callback():
            mappings = self.mob.block_device_mappings
            results = []
            for m in mappings:
                if 'Ebs' in m:
                    results.append(self.service_resource.Snapshot(m['Ebs']['SnapshotId']))
            return results
        return await run_in_executor(callback)

    async def delete(self):
        snapshots = await self.get_snapshots()
        await run_in_executor(self.mob.deregister)
        for s in snapshots:
            await run_in_executor(s.delete)

__all__ += ['AwsImage']

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

    def __repr__(self):
        return "ainjector(AttachImageBuilderVolume)"

    # InjectableModel.__str__ gives bad results especially with BaseCustomization.__getattr__
    __str__ = __repr__
    
    
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

@inject(model=AbstractMachineModel,
        config_layout=ConfigLayout,
        boot_mode = InjectionKey("aws_boot_mode", _optional=True),
        ena_support=InjectionKey('aws_ena_support', _optional=True),
        architecture=InjectionKey("aws_image_architecture", _optional=True),
        )
class ImageBuilderPipeline(MachineCustomization, InjectableModel):

    attach_volume = customization_task(AttachImageBuilderVolume)
    aws_image_size = injector_access('aws_image_size')

    __str__ = MachineCustomization.__repr__
    def __init__(self,
                 name,
                 description=None,
                 add_time_to_name=False,
                 **kwargs):
        if add_time_to_name:
            now = datetime.datetime.now(datetime.timezone.utc)
            name += f'-{now.year}{now.month:02}{now.day:02}{now.hour:02}{now.minute:02}'

        super().__init__(**kwargs)
        self.name = name
        self.image_description = description
        if self.ena_support is None: self.ena_support = True
        if self.boot_mode is None: self.boot_mode = 'uefi'
        if self.architecture is None: self.architecture = "x86_64"


    @setup_task("Build image")
    @inject(image=InjectionKey(DebianContainerImage, _ready=False),
            connection=InjectionKey(AwsConnection, _ready=True),
            )
    async def build_image(self, image, connection):
        import socket
        if socket.gethostname() != self.model.name:
            raise SkipSetupTask
        await image.async_become_ready()
        volume = await self.ainjector(ImageBuilderVolume)
        await self.ainjector(
            debian_container_to_vm,
            image, "aws_image.raw",
            f'{self.aws_image_size}G',
            classes='+CLOUD_INIT,EC2,GROW')
        await sh.dd(
        f'if={Path(self.config_layout.vm_image_dir)/"aws_image.raw"}',
"of=/dev/xvdi",
            'bs=1024k',
            'oflag=direct',
_bg = True, _bg_exc = False)

        def callback():
            snap = volume.mob.create_snapshot(Description=self.image_description or self.name)
            snap.wait_until_completed()
            extra = {}
            if self.image_description: extra['Description'] = self.image_description
            client = connection.client
            return client.register_image(
                        Name=self.name,
                Architecture=self.architecture,
                        EnaSupport=self.ena_support,
                        BootMode=self.boot_mode,
                BlockDeviceMappings=[dict(
                    DeviceName='/dev/xvda',
                    Ebs=dict(
                        DeleteOnTermination=True,
                        SnapshotId=snap.id,
                        VolumeType='gp2',
                    ))],
                RootDeviceName="/dev/xvda",
                VirtualizationType='hvm',
            )
        return await run_in_executor(callback)




@inject(
    injector=Injector,
    )
async def build_ami(
        name,
        *, injector,
        local_model=None,
        **kwargs):
    ainjector = injector(AsyncInjector)
    import socket
    if local_model is None:
        local_model = await ainjector.get_instance_async(InjectionKey(MachineModel, host=socket.gethostname()))
    machine = await local_model.ainjector.get_instance_async(InjectionKey(Machine))
    await machine.apply_customization(ImageBuilderPipeline,
                                      model=local_model,
                                      name=name,
                                      **kwargs)

__all__ += ['build_ami']
