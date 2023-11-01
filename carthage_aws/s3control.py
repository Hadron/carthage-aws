import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsClientManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet
from carthage_aws.elbv2 import AwsLoadBalancer

from dataclasses import dataclass, field

from .utils import unpack

from botocore.exceptions import ClientError

__all__ = ['S3Control']

class S3Control(AwsClientManaged):

    resource_type = 's_3_control'

    def find_from_id(self):
        try:
            r = self.client.describe_vpc_endpoint_service_configurations(ServiceIds=[self.id])
            self.cache = unpack(r['ServiceConfigurations'][0])
        except ClientError as e:
            logger.warning(f'Failed to load {self}', exc_info=e)
            self.cache = None
            if not self.readonly:
                self.connection.invalid_ec2_resource(self.resource_type, self.id, name=self.name)
            return
        return self.cache

    def do_create(self):
        #: returns None
        self.client.put_public_access_block(
            PublicAccessBlockConfiguration=dict(BlockPublicAcls=True, IgnorePublicAcls=True, BlockPublicPolicy=True, RestrictPublicBuckets=True),
            AccountId=self.account_id
        )
        r = self.client.get_public_access_block(AccountId=self.account_id)
            
        r = r['PublicAccessBlockConfiguration']
        self.cache = unpack(r)
        self.id = self.cache.ServiceId
        return self.cache

    def can_paginate():

    def delete_public_access_block():

    def generate_presigned_url():

    def get_paginator():

    def get_public_access_block():

    def get_waiter():

    def put_public_access_block():


