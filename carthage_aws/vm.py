import asyncio
from pathlib import Path

from carthage import *
from carthage.modeling import *
from carthage.dependency_injection import *
from carthage.vm import vm_image
from carthage.config import ConfigLayout
from carthage.machine import Machine
from carthage.image import SetupTaskMixin

import boto3
from botocore.exceptions import ClientError

from .connection import AwsConnection
from .network import AwsVirtualPrivateCloud, AwsSubnet


@inject_autokwargs(connection=AwsConnection, injector=Injector, network=InjectionKey(NetworkModel), config_layout=ConfigLayout)
class AwsVm(Machine, SetupTaskMixin):

    def __init__(self, connection, injector, network, *args, **kwargs):
        self.connection = connection
        self.network = network
        super().__init__(connection=connection, injector=injector, network=network, *args, **kwargs)
        self.running = False
        self.closed = False
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()
        self.key = self.model.key
        self.imageid = self.model.imageid
        self.size = self.model.size

    @setup_task('Construct')
    async def construct(self):
        try:
            r = self.connection.client.run_instances(ImageId=self.imageid,
                                      MinCount=1,
                                      MaxCount=1,
                                      InstanceType=self.size,
                                      KeyName=self.key
            )
            self.connection.client.create_tags(Resources=[r['Instances'][0]['InstanceId']], Tags=[{
                                                'Key': 'Name',
                                                'Value': self.name
                                                }])
        except ClientError as e:
            logger.error(f'Could not create AWS VM for {self.model.name} because {e}.')
        

    async def stop_vm(self):
        pass

    start_machine = construct
    stop_machine = stop_vm

    @property
    def stamp_path(self):
        return Path(self.config_layout.state_dir)/'aws'/self.name


