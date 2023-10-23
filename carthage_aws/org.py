import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsClientManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from .utils import unpack

from botocore.exceptions import ClientError

__all__ = ['AwsOrganization']

class AwsOrganization(AwsClientManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'organization'
    client_type = 'organizations'

    def find_all(self):
        self.cache = unpack(self.client.describe_organization()['Organization'])
