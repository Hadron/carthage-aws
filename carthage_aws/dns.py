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

from pathlib import Path
import os

from .connection import AwsConnection, AwsManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError

from datetime import datetime

__all__ = ['AwsHostedZone', 'AwsDnsManagement']

class AwsHostedZone(AwsManaged):
    
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

    def contains(self, name):
        '''
        Returns `bool` representing whether or not zone should contain name
        '''
        # we trim the trailing dot that is returned from the API
        # so we just trim the dot on the fqdn we are passed if it has one
        if name.endswith('.'):
            name = name[:-1]
        return name.endswith(self.name)

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
            self.name = r['HostedZone']['Name']
        except ClientError as e:
            logger.error(f'Could not create AwsHostedZone for \
{self.name} because {e}.')

    async def delegate_zone(self, parent):
        def callback():
            assert type(parent) is AwsHostedZone
            assert self.name.partition('.')[2] == parent.name
            parent.update_record((self.name, self.nameservers, 'NS'))
        return await run_in_executor(callback)

    # could decorate for other actions
    async def update_record(self, *args):
        '''
        Updates aws route53 record(s)
        Arguments::
            *args : must be tuples representing records
            record (tuple) : (Name, Value, Type) must be specified
                Value may be list or str

        Typical usage::
            zone.update_records(
                [
                    ('foo.zone.org', '1.2.3.4', 'A'),
                    ('bar.zone.org', ['ns1.zone.org', 'ns2.zone.org'] 'NS')
                ]
            )
        '''
        changes = []
        for a in args:
            assert type(a) is tuple,ValueError(f"{a} must be a tuple")
            assert a[2] in self.allrrtype,ValueError(f"{a[2]} must be a valid rrtype {self.allrrtype}")
            records = []
            if type(a[1]) is list:
                for r in a[1]:
                    records.append(
                        { 'Value': a[1] }
                    )
            else:
                records.append(
                    { 'Value': a[1] }
                )
            changes.append(
                {
                    'Action': 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': a[0],
                        'Type': a[2],
                        'TTL': 30,
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

    '''
    A Carthage modeling mixin that updates DNS records in a given zone when included models  gain an IP address.  Typical usage::

        class some_enclave(Enclave, AwsDnsManagement):

            domain = "machines.example.com"
            add_provider(InjectionKey(AwsHostedZone), when_needed(AwsHostedZone, name=domain))

            class some_machine(MachineModel): ...

    Then, when `some_machine` gains an IP address, an `A` record will be created.

    '''

    async def public_ip_updated(self, target, **kwargs):
        link = target
        model = link.machine
        zone = await self.ainjector.get_instance_async(InjectionKey(AwsHostedZone, _ready=True))
        name = link.dns_name or model.name
        if not zone.contains(name):
            logger.warning(f'Not setting DNS for {model}: {name} does not fall within {zone.name}')
        else:
            logger.debug(f'{name} is at {str(link.public_v4_address)}')
            await zone.update_record((name, str(link.public_v4_address), 'A'))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_event_listener(InjectionKey(NetworkLink), 'public_address', self.public_ip_updated)
        
