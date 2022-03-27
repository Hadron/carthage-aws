import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, AwsClientManaged, callback, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from .utils import unpack

from botocore.exceptions import ClientError

class AwsLoadBalancer(AwsClientManaged):
    def __init__(self, **kwargs):
        if ('subnet' in kwargs) and ('subnets' in kwargs):
            raise ValueError(f"call to AwsGatewayLoadBalancer should not specify both 'subnet' and 'subnets'")
        elif ('subnet' in kwargs):
            self.subnets = [kwargs.pop('subnet')]
        elif ('subnets' in kwargs):
            self.subnets = kwargs.pop('subnets')
        else:
            self.subnets = False
        super().__init__(**kwargs)

        self.arn = None

    resource_type = 'load_balancer'
    client_type = 'elbv2'

    def do_create(self):
        r = self.client.create_load_balancer(
            Name=self.name,
            Subnets=[x.id for x in self.subnets],
            Tags=[dict(Key='Name',Value=self.name)],
            Type='gateway',
            IpAddressType='ipv4'
        )
        r = r['LoadBalancers'][0]
        self.cache = unpack(r)
        self.arn = self.cache.LoadBalancerArn
        return self.cache

    def set_subnets(self, *args):
        ''':param: *args must be AwsSubnet(s)
                This must be the full set of connected subnets
        '''
        r = self.client.set_subnets(
            LoadBalanceArn=self.arn,
            Subnets=[x.id for x in args],
            IpAddressType='ipv4'
        )

    @callback
    def delete(self):
        self.client.delete_load_balancer(LoadBalancerArn=self.cache.LoadBalancerArn)

@inject_autokwargs(vpc=AwsVirtualPrivateCloud)
class AwsLoadBalancerTargetGroup(AwsClientManaged):
    def __init__(self, **kwargs):
        if ('target' in kwargs) and ('targets' in kwargs):
            raise ValueError(f"call to AwsGatewayLoadBalancer should not specify both 'target' and 'targets'")
        elif ('target' in kwargs):
            self.targets = [kwargs.pop('target')]
        elif ('targets' in kwargs):
            self.targets = kwargs.pop('targets')
        else:
            self.subnets = False
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'target_group'
    client_type = 'elbv2'
    allproto = ['HTTP'|'HTTPS'|'TCP'|'TLS'|'UDP'|'TCP_UDP'|'GENEVE']

    def do_create(self):
        assert self.proto in allproto,f"{self.proto} must be one of {allproto}"
        response = client.create_target_group(
            Name=self.name,
            Protocol='GENEVE',
            ProtocolVersion='string',
            Port=6081,
            VpcId=self.vpc,
            TargetType='instance',
            Tags=[dict(Key='Name',Value=self.name)],
            IpAddressType='ipv4'
        )
        r = r['TargetGroups'][0]
        self.cache = unpack(r)
        self.arn = self.cache.TargetGroupArn
        return self.cache

@inject_autokwargs(vpc=AwsVirtualPrivateCloud, lb=AwsLoadBalancer, tg=AwsLoadBalancerTargetGroup)
class AwsLoadBalancerListener(AwsClientManaged):
    def __init__(self, **kwargs):
        if ('target' in kwargs) and ('targets' in kwargs):
            raise ValueError(f"call to AwsGatewayLoadBalancer should not specify both 'target' and 'targets'")
        elif ('target' in kwargs):
            self.targets = [kwargs.pop('target')]
        elif ('targets' in kwargs):
            self.targets = kwargs.pop('targets')
        else:
            self.subnets = False
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'target_group'
    client_type = 'elbv2'
    allproto = ['HTTP'|'HTTPS'|'TCP'|'TLS'|'UDP'|'TCP_UDP'|'GENEVE']

    def do_create(self):
        r = self.client.create_listener(
            LoadBalancerArn=lb.arn,
            Protocol='GENEVE',
            Port=6081,
            DefaultActions=[
                {
                'Type': 'forward',
                'TargetGroupArn': tg.arn
                },
            ],
            'ForwardConfig': {
                'TargetGroups': [
                    {
                            'TargetGroupArn': 'string',
                                'Weight': 123
                        },
                ],
            }
            Tags=[
        )


        r = r['Listeners'][0]
        self.cache = unpack(r)
        self.arn = self.cache.ListenerArn
        return self.cache
