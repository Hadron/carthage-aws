# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
from carthage import *
from carthage.dependency_injection import *
from carthage.network import NetworkLink
from carthage.config import ConfigLayout
from carthage.modeling import *
from carthage.dns import DnsZone
import collections.abc

from pathlib import Path
import os
import warnings

from .connection import AwsConnection, AwsManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError

from datetime import datetime

__all__ = ['AwsHostedZone', 'AwsDnsManagement']

class AwsHostedZone(AwsManaged, DnsZone):
    
    pass_name_to_super = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.allrrtype = ['SOA','A','TXT','NS','CNAME','MX','NAPTR','PTR','SRV','SPF','AAAA','CAA','DS']

        self.region = self.config_layout.aws.region
        self.private = False

        self.client = self.service_resource

    @memoproperty
    def service_resource(self):
        return self.connection.connection.client('route53', region_name=self.connection.region)

    def aws_propagate_key(cls):
        # We need to define this ourselves because we do not set
        # resource_type
        if not cls.name: raise AttributeError('name not yet set')
        return InjectionKey(AwsHostedZone, zone_name=cls.name)
    

    def find_from_name(self):
        try:
            # we look for a hosted zone with our exact name
            r = self.client.list_hosted_zones_by_name(DNSName=self.name)
            if len(r['HostedZones']) > 0:
                # [12:] is because we want to trim `/hostedzone/` off of the zone Id
                self.id = r['HostedZones'][0]['Id'][12:]
        except ClientError as e:
            logger.error(f'Could not find hostedzone for {self.name} by name because {e}.')
        return

    def find_from_id(self):
        try:
            r = self.client.get_hosted_zone(Id=self.id)
            # perhaps we want to wrap mob as dict of attrs
            self.mob = r
            self.config = r['HostedZone']['Config']
            self.nameservers = r['DelegationSet']['NameServers']
            self.name = r['HostedZone']['Name']
            if self.name.endswith('.'): self.name = self.name[:-1]
        except ClientError as e:
            logger.error(f'Could not find hostedzone for {self.id} by id because {e}.')
        return self.mob
    
    async def find(self):
        '''
        Find ourself from a name or id
        '''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            await run_in_executor(self.find_from_name)
            if self.id:
                return await run_in_executor(self.find_from_id)
        return

    def do_create(self):
        try:
            r = self.client.create_hosted_zone(
                Name=self.name,
                # this will be necessary for private hosted zone
                # VPC={
                #     'VPCRegion': self.region,
                #     'VPCId': self.vpc_id
                # },
                CallerReference=str(datetime.now().timestamp()),
                HostedZoneConfig={
                    'Comment': 'Created by Carthage',
                    'PrivateZone': self.private
                }
            )
            self.mob = r
            # [12:] is because we want to trim `/hostedzone/` off of the zone Id
            self.id = r['HostedZone']['Id'][12:]
            self.config = r['HostedZone']['Config']
            self.nameservers = r['DelegationSet']['NameServers']
            self.name = r['HostedZone']['Name'][:-1]
        except ClientError as e:
            logger.error(f'Could not create AwsHostedZone for \
{self.name} because {e}.')

    async def delegate_zone(self, parent):
        def callback():
            assert type(parent) is AwsHostedZone
            assert self.name.partition('.')[2] == parent.name
            parent.update_records((self.name, 'NS', self.nameservers))
        return await run_in_executor(callback)

    # could decorate for other actions
    async def update_records(self, *args, ttl=300):
        '''
        Updates aws route53 record(s)
        Arguments::
            *args : must be sequences representing records
            record (sequence) : (Name, type, Value) must be specified
                Value may be list or str

        Typical usage::
            zone.update_records(
                [
                    ('foo.zone.org', 'A', '1.2.3.4''),
                    ('bar.zone.org', 'NS', ['ns1.zone.org', 'ns2.zone.org'])
                ]
            )
        '''
        changes = []
        for a in args:
            assert isinstance(a, collections.abc.Sequence),ValueError(f"{a} must be a sequence")
            name, rrtype, values = a
            if isinstance(values, str) and values in self.allrrtype:
                warnings.warn('update_records now takes name, type, value not name, value, type')
                values, rrtype = rrtype, values
            if isinstance(values, str) or not isinstance(values, collections.abc.Sequence):
                values = (values,)
            assert rrtype in self.allrrtype,ValueError(f"{rrtype} must be a valid rrtype {self.allrrtype}")
            records = []
            for r in values:
                records.append(
                    { 'Value': r }
                )
            changes.append(
                {
                    'Action': 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': name,
                        'Type': rrtype,
                        'TTL': ttl,
                        'ResourceRecords': records
                    }
                }
            )
        try:
            _ = self.client.change_resource_record_sets(
                HostedZoneId=self.id,
                ChangeBatch={
                    'Comment': 'Created by Carthage',
                    'Changes': changes,
                }
            )
        except ClientError as e:
            logger.error(f'Could not upsert {args} because {e}.')


class AwsDnsManagement(InjectableModel):

    def __init__(self, **kwargs):
        raise TypeError('Use the core PublicDnsManagement Classfrom carthage.dns  instead.  Note that it requires slightly different configuration')
    
