# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import functools
import logging
import os

from carthage import *
from carthage.config import ConfigLayout
from carthage.dependency_injection import *

import boto3
from botocore.exceptions import ClientError

__all__ = ['AwsConnection', 'AwsManaged']



@inject_autokwargs(config_layout=ConfigLayout)
class AwsManaged(AsyncInjectable, SetupTaskMixin):


    @memoproperty
    def stamp_descriptor(self):
        raise NotImplementedError(type(self))

    @memoproperty
    def stamp_path(self):
        p = Path(self.config_layout.state_dir)
        p = p.joinpath("aws_stamps", self.stamp_type+".stamps")
        os.makedirs(p, exist_ok=True)
        return p


class AwsConnection(AwsManaged):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = self.config_layout.aws
        self.connection = None


    def _setup(self):
        self.connection = boto3.Session(
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key
        )
        self.region = self.config.region
        self.client = self.connection.client('ec2', region_name=self.region)
        self.keys = []
        for key in self.client.describe_key_pairs()['KeyPairs']:
            self.keys.append(key['KeyName'])
        self.vpcs = []
        self.igs = []
        self.subnets = []
        self.inventory()

    def inventory(self):
        r = self.client.describe_vpcs()
        for v in r['Vpcs']:
            vpc = {'id': v['VpcId']}
            if 'Tags' in v:
                for t in v['Tags']:
                    if t['Key'] == 'Name':
                        vpc['name'] = t['Value']
            else: vpc['name'] = ''
            self.vpcs.append(vpc)

        r = self.client.describe_internet_gateways()
        for ig in r['InternetGateways']:
            if len(ig['Attachments']) == 0:
                continue
            a = ig['Attachments'][0]
            if a['State'] == 'attached' or a['State'] == 'available':
                self.igs.append({'id': ig['InternetGatewayId'], 'vpc': a['VpcId']})

        r = self.client.describe_subnets()
        for s in r['Subnets']:
            subnet = {'CidrBlock': s['CidrBlock'], 'id': s['SubnetId'], 'vpc': s['VpcId']}
            self.subnets.append(subnet)

    async def async_ready(self):
        await asyncio.get_event_loop().run_in_executor(None, self._setup)
        return await super().async_ready()
    
