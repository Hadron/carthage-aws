# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import datetime
import warnings
import collections.abc

from botocore.exceptions import ClientError

from carthage import *
from carthage.dependency_injection import *
from carthage.modeling import *
from carthage.dns import DnsZone

from .connection import AwsManaged, run_in_executor
from .network import AwsVirtualPrivateCloud


__all__ = ['AwsHostedZone', 'AwsDnsManagement']

class AwsHostedZone(AwsManaged, DnsZone):

    pass_name_to_super = False
    private = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.allrrtype = ['SOA','A','TXT','NS','CNAME','MX','NAPTR','PTR','SRV','SPF','AAAA','CAA','DS']

        self.region = self.config_layout.aws.region

        self.client = self.service_resource
        self.config = None
        self.nameservers = None


    @memoproperty
    def service_resource(self):
        return self.connection.connection.client('route53', region_name=self.connection.region)

    def aws_propagate_key(cls): # pylint: disable=no-self-argument
        # We need to define this ourselves because we do not set
        # resource_type
        if not cls.name:
            raise AttributeError('name not yet set')
        return InjectionKey(AwsHostedZone, zone_name=cls.name)


    def find_from_name(self):
        try:
            # we look for a hosted zone with our exact name
            r = self.client.list_hosted_zones_by_name(DNSName=self.name)
            # But DNSName is not an exact match; it is a starting point
            # so we need to make sure that the zone name is ours.
            if len(r['HostedZones']) > 0 \
               and r['HostedZones'][0]['Name'] == self.name+'.':
                # [12:] is because we want to trim `/hostedzone/` off of the zone Id
                self.id = r['HostedZones'][0]['Id'][12:]
        except ClientError as e:
            logger.error('Could not find hostedzone for %s by name because %s.', self.name, e)

    def find_from_id(self):
        try:
            r = self.client.get_hosted_zone(Id=self.id)
            # perhaps we want to wrap mob as dict of attrs
            self.mob = r
            self.config = r['HostedZone']['Config']
            self.nameservers = r['DelegationSet']['NameServers']
            self.name = r['HostedZone']['Name']
            if self.name.endswith('.'):
                self.name = self.name[:-1]
        except ClientError as e:
            logger.error('Could not find hostedzone for %s by id because %s.', self.id, e)
        return self.mob

    async def find(self):
        '''
        Find ourself from a name or id
        '''
        if self.id:
            return await run_in_executor(self.find_from_id)
        if self.name:
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
                CallerReference=str(datetime.datetime.now().timestamp()),
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
            logger.error(
                'Could not create AwsHostedZone for %s because %s.',
                self.name, e
            )

    async def delete(self):
        # before we can delete the hosted zone all non-required records must be deleted.
        await self.clear()
        def callback():
            try:
                self.client.delete_hosted_zone(Id=self.id)
            except ClientError as e:
                logger.error("Failed to delete Private Hosted Zone %s", self.id)
                raise e
        await run_in_executor(callback)

    async def clear(self):
        """
        Delete all non required DNS records from the zone.
        """
        def callback():
            try:
                paginator = self.client.get_paginator('list_resource_record_sets')
                source_record_sets = paginator.paginate(HostedZoneId=self.id)

                changes = []
                for record_set in source_record_sets:
                    for record in record_set['ResourceRecordSets']:
                        if record["Type"] not in ["NS", "SOA"]:
                            changes.append({
                                'Action': 'DELETE',
                                'ResourceRecordSet': record
                            })

                if changes:
                    change_batch = {'Changes': changes}
                    self.client.change_resource_record_sets(
                        HostedZoneId=self.id,
                        ChangeBatch=change_batch
                    )

            except ClientError as e:
                logger.error("An error occurred: %s", e)
                raise e
        await run_in_executor(callback)

    async def delegate_zone(self, parent):
        def callback():
            assert isinstance(AwsHostedZone, parent)
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
                    ('foo.zone.org', 'A', '1.2.3.4'),
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
            logger.error('Could not upsert %s because %s.', args, e)


@inject_autokwargs(vpc=AwsVirtualPrivateCloud)
class AwsPrivateHostedZone(AwsHostedZone):
    private = True
    vpc_id = ""

    async def pre_create_hook(self):
        await self.vpc.async_become_ready()
        self.vpc_id = self.vpc.id

    def find_from_id(self):
        try:
            r = self.client.get_hosted_zone(Id=self.id)
            # perhaps we want to wrap mob as dict of attrs
            self.mob = r
            self.config = r['HostedZone']['Config']
            self.name = r['HostedZone']['Name']
            if self.name.endswith('.'):
                self.name = self.name[:-1]
        except ClientError as e:
            logger.error('Could not find hostedzone for %s by id because %s.', self.id, e)
        return self.mob

    def do_create(self):
        if not self.vpc_id:
            raise ValueError("vpc_id must be set before creating a hosted zone.")

        result = self.vpc.mob.describe_attribute(Attribute="enableDnsHostnames")
        if result["EnableDnsHostnames"]["Value"] is False:
            raise ValueError(f"EnableDnsHostnames must be enabled for vpc_id {self.vpc_id} for private hosted zones.")

        try:
            r = self.client.create_hosted_zone(
                Name=self.name,
                VPC={
                    "VPCRegion": self.region,
                    "VPCId": self.vpc_id
                },
                CallerReference=str(datetime.datetime.now().timestamp()),
                HostedZoneConfig={
                    'Comment': 'Created by Carthage',
                    'PrivateZone': self.private
                }
            )
            self.mob = r
            # [12:] is because we want to trim `/hostedzone/` off of the zone Id
            self.id = r['HostedZone']['Id'][12:]
            self.config = r['HostedZone']['Config']
            self.name = r['HostedZone']['Name'][:-1]
        except ClientError as e:
            logger.error(
                'Could not create AwsHostedZone for %s because %s.', 
                self.name, e
            )


class AwsDnsManagement(InjectableModel):

    def __init__(self, **kwargs):
        # pylint: disable=super-init-not-called
        raise TypeError(
            'Use the core PublicDnsManagement class from carthage.dns instead. '
            'Note that it requires slightly different configuration'
        )
