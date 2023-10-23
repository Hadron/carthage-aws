import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsClientManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from .utils import unpack

from botocore.exceptions import ClientError

__all__ = ['AwsResourceShare']

class AwsResourceShare(AwsClientManaged):
    def __init__(self, **kwargs):
        if 'share' in kwargs.keys():    
            self.share = kwargs.pop('share')
            if not isinstance(self.share, list):
                self.share = [self.share]
        if 'org' in kwargs.keys():
            self.org = kwargs.pop('org')
        assert hasattr(self, 'org'),f"{self} must have attribute 'org'"
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'resource_share'
    client_type = 'ram'

    def do_create(self):
        assert len(self.share) == 1
        r = self.client.create_resource_share(
            name=self.name,
            resourceArns=self.share,
            principals=[self.org],
            tags=[dict(key='Name',value=self.name)]
        )
        r = r['resourceShare']
        self.cache = unpack(r)
        self.arn = self.cache.resourceShareArn
        return self.cache
    
