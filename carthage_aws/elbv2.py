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

    def private_ip_address(self, subnet_id):
        ip = [ x.PrivateIpAddress for x in self.interfaces if subnet_id == x.SubnetId ]
        assert len(ip) == 1,"There should only be one interface in the subnet"
        return ip[0]

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
        self.interfaces = [ unpack(x) for x in self.connection.client.describe_network_interfaces()['NetworkInterfaces'] if self.arn.split('/')[-1] in x['Description'] ]
        return self.cache

    async def post_find_hook(self):
        r = await super().post_find_hook()
        self.interfaces = [ unpack(x) for x in self.connection.client.describe_network_interfaces()['NetworkInterfaces'] if self.arn.split('/')[-1] in x['Description'] ]
        return r

    @callback
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
    def enable_cross_zone_load_balancing(self):
        r = self.client.modify_load_balancer_attributes(
            Attributes=[dict(Key='load_balancer.cross_zone.enabled',Value='true')],
            LoadBalancerArn=self.arn
        )

    def delete(self):
        r = self.client.delete_load_balancer(LoadBalancerArn=self.arn)

@inject_autokwargs(vpc=AwsVirtualPrivateCloud)
class AwsLoadBalancerTargetGroup(AwsClientManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.arn = None

    resource_type = 'target_group'
    client_type = 'elbv2'
    allproto = ['HTTP','HTTPS','TCP','TLS','UDP','TCP_UDP','GENEVE']

    def do_create(self):
        r = self.client.create_target_group(
            Name=self.name,
            Protocol='GENEVE',
            Port=6081,
            VpcId=self.vpc.id,
            HealthCheckProtocol='HTTPS',
            HealthCheckPort='443',
            HealthCheckEnabled=True,
            HealthCheckPath='/',
            HealthCheckIntervalSeconds=8,
            HealthCheckTimeoutSeconds=3,
            HealthyThresholdCount=3,
            UnhealthyThresholdCount=3,
            TargetType='ip'
        )
        r = r['TargetGroups'][0]
        self.cache = unpack(r)
        self.arn = self.cache.TargetGroupArn
        return self.cache

    @callback
    def register_targets(self, *targets):
        kwargs = dict(
            TargetGroupArn=self.arn,
            Targets=[dict(Id=x) for x in targets]
        )
        r = self.client.register_targets(**kwargs)
        self.targets = targets

@inject_autokwargs(lb=AwsLoadBalancer, tg=AwsLoadBalancerTargetGroup)
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

    resource_type = 'listener'
    client_type = 'elbv2'
    allproto = ['HTTP','HTTPS','TCP','TLS','UDP','TCP_UDP','GENEVE']

    def do_create(self):
        r = self.client.create_listener(
            LoadBalancerArn=self.lb.arn,
            DefaultActions=[dict(Type='forward',TargetGroupArn=self.tg.arn)]
        )
        r = r['Listeners'][0]
        self.cache = unpack(r)
        self.arn = self.cache.ListenerArn
        return self.cache
