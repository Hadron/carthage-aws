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

from .connection import AwsConnection, AwsManaged
from .network import AwsVirtualPrivateCloud, AwsSubnet

__all__ = ['AwsVm']


@inject_autokwargs(connection=AwsConnection, injector=Injector, network=InjectionKey(NetworkModel), config_layout=ConfigLayout)
class AwsVm(Machine, AwsManaged):

    def __init__(self, connection, injector, network, *args, **kwargs):
        self.connection = connection
        self.network = network
        super().__init__(connection=connection, injector=injector, network=network, *args, **kwargs)
        self.running = False
        self.closed = False
        self._operation_lock = asyncio.Lock()
        self.key = self.model.key
        self.imageid = self.model.imageid
        self.size = self.model.size
        self.running = False
        self.subnet = None
        self.id = None
        self.vpc = self.connection.run_vpc
        found_vm = []
        if self.vpc != None:
            found_vm = [vm for vm in self.connection.vms if vm['vpc'] == self.vpc['id'] and vm['name'] == self.name]
        if len(found_vm) > 0:
            self.id = found_vm[0]['id']
            self.running = True

    @setup_task('construct')
    async def start_machine(self):
        async with self._operation_lock:
            self.vpc = self.connection.run_vpc
            logger.info(f'Starting {self.name} VM')

            if self.id == None:
                try:
                    self.subnet = self.injector(AwsSubnet)
                    self.subnet.do_create()
                    self.vpc = self.subnet.vpc
                    r = self.connection.client.run_instances(ImageId=self.imageid,
                                            MinCount=1,
                                            MaxCount=1,
                                            InstanceType=self.size,
                                            KeyName=self.key,
                                            NetworkInterfaces=[{
                                                'DeviceIndex': 0,
                                                'SubnetId': self.subnet.id,
                                                'AssociatePublicIpAddress': True,
                                                'Groups': [ self.vpc.groups[0]['GroupId'] ]
                                            }]
                    )
                    self.connection.client.create_tags(Resources=[r['Instances'][0]['InstanceId']], Tags=[{
                                                        'Key': 'Name',
                                                        'Value': self.name
                                                        }])
                    self.id = r['Instances'][0]['InstanceId']
                except ClientError as e:
                    logger.error(f'Could not create AWS VM for {self.model.name} because {e}.')
            else:
                logger.info(f"Skipping creating existing VM {self.name}")
            self.running = True
            return self.running

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running: return
            logger.info(f'Terminating {self.name} VM')
            try:
                r = self.connection.client.terminate_instances(InstanceIds=[self.id])

                # Update inventory's list of VMs
                self.connection.inventory()

            except ClientError as e:
                logger.error(f'Could not terminate AWS VM {self.model.name} because {e}.')

            return True

    

    @property
    def stamp_path(self):
        return Path(self.config_layout.state_dir)/'aws'/self.name


