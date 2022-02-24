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

from .connection import AwsConnection, AwsManaged
from .network import AwsVirtualPrivateCloud, AwsSubnet

__all__ = ['AwsVm']


@inject_autokwargs(connection=AwsConnection,  network=InjectionKey(NetworkModel), )
class AwsVm(Machine, AwsManaged):

    def __init__(self, name, **kwargs):
        super().__init__(name=name, **kwargs)
        self.running = False
        self.closed = False
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()
        self.key = self.model.key
        self.imageid = self.model.imageid
        self.size = self.model.size
        self.subnet = None

    @setup_task('construct')
    async def construct(self):
        try:
            self.subnet = self.injector(AwsSubnet)
            self.subnet.do_create()
            r = self.connection.client.run_instances(ImageId=self.imageid,
                                      MinCount=1,
                                      MaxCount=1,
                                      InstanceType=self.size,
                                      KeyName=self.key,
                                      NetworkInterfaces=[{
                                          'DeviceIndex': 0,
                                          'SubnetId': self.subnet.id,
                                          'AssociatePublicIpAddress': True,
                                          'Groups': self.subnet.groups
                                      }]
            )
            self.connection.client.create_tags(Resources=[r['Instances'][0]['InstanceId']], Tags=[{
                                                'Key': 'Name',
                                                'Value': self.name
                                                }])
            self.running = True
        except ClientError as e:
            logger.error(f'Could not create AWS VM for {self.model.name} because {e}.')
        

    async def stop_vm(self):
        pass

    start_machine = construct
    stop_machine = stop_vm

    @property

    stamp_descriptor = "vm"
