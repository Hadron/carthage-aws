import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsClientManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from .utils import unpack

from botocore.exceptions import ClientError

import yaml

__all__ = ['AwsAccessKey']

@inject_autokwargs(vault=Vault)
class AwsAccessKey(AwsManaged):

    resource_type = 'access_key'
    client_type = 'iam'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @memoproperty
    def service_resource(self):
        # override for non-ec2
        return self.connection.connection.resource('iam', region_name=self.connection.region)
    
    def do_create(self):
        r = self.client.create_access_key(username=self.username)
        self.id = r['AccessKey']['AccessKeyId']
        self.cache = unpack(r)
    
    async def post_create_hook(self):
        from os import environ
        env = environ['TARGET_ENVIRONMENT']
        self.vault.client.write(f'aws/{env}-{username}-access-key', **dict(data=dict(UserName=self.cache.UserName, AccessKeyId=self.cache.AccessKeyId, SecretAccessKey=self.cache.SecretAccessKey)))
