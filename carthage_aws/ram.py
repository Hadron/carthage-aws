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
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'resource_share'
    client_type = 'ram'

    # dev-org 'arn:aws-us-gov:organizations::807241311045:organization/o-yy8ngalms6'
    # prod-org 'arn:aws-us-gov:organizations::627530914327:organization/o-d1lk8mha82'

    def do_create(self):
        r = self.client.create_resource_share(
            name=self.name,
            resourceArns=[self.share],
            # TODO: Pull principal string from config.yml
            principals=['arn:aws-us-gov:organizations::627530914327:organization/o-d1lk8mha82'],
            tags=[dict(key='Name',value=self.name)]
        )
        r = r['resourceShare']
        self.cache = unpack(r)
        self.arn = self.cache.resourceShareArn
        return self.cache
