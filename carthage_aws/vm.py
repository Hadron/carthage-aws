from carthage import *
from carthage.modeling import *
from carthage.dependency_injection import *
from carthage.vm import vm_image
from carthage.config import ConfigLayout
from carthage.machine import Machine
from carthage.image import SetupTaskMixin

from .connection import AwsConnection
from .network import AwsVirtualPrivateCloud


@inject(connection = AwsConnection, vpc = AwsVirtualPrivateCloud, injector = Injector)
class AwsVm(Injectable):

    def __init__(self, name, connection, vpc, injector):
        self.injector = injector
        self.connection = connection
        self.name = name
        self.vpc = vpc
        super().__init__()
        self.running = False
        self.closed = False
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()
        self.client = self.connection.client('ec2', self.connection.region)
        self.key = self.config.config_keypair


    async def start_vm(self):
        try:
            r = self.client.run_instances(ImageId=self.vm_config.image.id,
                                      MinCount=1,
                                      MaxCount=1,
                                      InstanceType=self.size,
                                      KeyName=self.key,
                                      SubnetId=vpc.subnetid
            )
        except ClientError:
            logger.error(f'Could not create AWS VM for {self.name}.')
        

    async def stop_vm(self):
        pass

    start_machine = start_vm
    stop_machine = stop_vm

