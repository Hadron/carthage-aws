import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, AwsClientManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from .utils import unpack

from botocore.exceptions import ClientError

class AwsGatewayLoadBalancer(AwsClientManaged):
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

    resource_type = 'gateway_load_balancer'
    client_type = 'elbv2'

    @memoproperty
    def resource_name(self):
        return "".join([x.title() for x in self.resource_type.split('_')])

    @memoproperty
    def client(self):
        return self.connection.connection.client(self.client_type, self.connection.region)

    def do_create(self):
        r = create_load_balancer(
            Subnets=[
                self.subnet,
            ],
            Scheme='internal',
            Tags=[self.resource_tags]
        )
        r = r['LoadBalancers'][0]
        self.cache = unpack(r)
        breakpoint()
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
        breakpoint()

    # add_listener_certificates()
    # add_tags()
    # can_paginate()
    # create_listener()
    # create_rule()
    # create_target_group()
    # delete_listener()
    # delete_load_balancer()
    # delete_rule()
    # delete_target_group()
    # deregister_targets()
    # describe_account_limits()
    # describe_listener_certificates()
    # describe_listeners()
    # describe_load_balancer_attributes()
    # describe_load_balancers()
    # describe_rules()
    # describe_ssl_policies()
    # describe_tags()
    # describe_target_group_attributes()
    # describe_target_groups()
    # describe_target_health()
    # get_paginator()
    # get_waiter()
    # modify_listener()
    # modify_load_balancer_attributes()
    # modify_rule()
    # modify_target_group()
    # modify_target_group_attributes()
    # register_targets()
    # remove_listener_certificates()
    # remove_tags()
    # set_ip_address_type()
    # set_rule_priorities()
    # set_security_groups()
    # set_subnets()
