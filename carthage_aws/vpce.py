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

__all__ = ['AwsVpcEndpointService', 'AwsVpcEndpoint']

@inject_autokwargs(vpc=AwsVirtualPrivateCloud, lb=AwsLoadBalancer)
class AwsVpcEndpointService(AwsClientManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.id = None

    resource_type = 'vpc_endpoint_service'

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
        r = self.client.create_vpc_endpoint_service_configuration(
            AcceptanceRequired=False,
            GatewayLoadBalancerArns=[self.lb.arn],
            TagSpecifications=[self.resource_tags]
        )
        r = r['ServiceConfiguration']
        self.cache = unpack(r)
        self.id = self.cache.ServiceId
        return self.cache

@inject_autokwargs(vpcsvc=AwsVpcEndpointService)
class AwsVpcEndpoint(AwsClientManaged):
    def __init__(self, **kwargs):
        if ('subnet' in kwargs) and ('subnets' in kwargs):
            raise ValueError(f"call to AwsVpcEndpoint should not specify both 'subnet' and 'subnets'")
        elif ('subnet' in kwargs):
            self.subnets = [kwargs.pop('subnet')]
        elif ('subnets' in kwargs):
            self.subnets = kwargs.pop('subnets')
        else:
            self.subnets = False
        super().__init__(**kwargs)
        self.id = None

    resource_type = 'vpc_endpoint'

    def do_create(self):
        r = self.client.create_vpc_endpoint(
            VpcEndpointType='GatewayLoadBalancer',
            VpcId=self.vpcsvc.vpc.id,
            ServiceName=self.vpcsvc.cache.ServiceName,
            SubnetIds=[x.id for x in self.subnets],
            TagSpecifications=[self.resource_tags]
        )
        r = r['VpcEndpoint']
        self.cache = unpack(r)
        self.id = self.cache.VpcEndpointId
        return self.cache
