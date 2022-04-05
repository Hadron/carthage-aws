import asyncio
import time
import logging

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, AwsClientManaged, callback, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from .utils import unpack

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

class AwsTransitGateway(AwsClientManaged):

    resource_type = 'transit_gateway'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.id = None
        self.attachments = {}
        self.route_tables = {}

    async def associate(self, attachment, table):
        await self.route_tables[table.id].associate(self.attachments[attachment.id])

    async def propagate(self, attachment, table):
        await self.route_tables[table.id].propagate(self.attachments[attachment.id])

    async def create_route(self, cidrblock, attachment, table):
        await self.route_tables[table.id].create_route(cidrblock, attachment)

    async def disassociate(self, attachment, table):
        await self.route_tables[table.id].disassociate(self.attachments[attachment.id])

    async def depropagate(self, attachment, table):
        await self.route_tables[table.id].depropagate(self.attachments[attachment.id])

    async def delete_route_table(self, name):
        pass

    async def delete_route(self, cidrblock, attachment, tablename):
        pass

    async def create_attachment(self, resource):
        pass

    async def delete_attachment(self, attachment):
        pass

    async def create_foreign_attachment(self, attachment):
        '''Args: AwsTransitGatewayAttachment'''
        state = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[attachment.id])['TransitGatewayAttachments'][0]['State']
        if state not in ['pending', 'available']:
            r = self.client.accept_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=attachment.id)
        while state != 'available':
            state = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[attachment.id])['TransitGatewayAttachments'][0]['State']
            time.sleep(5)
            print(f'waiting on {self.name} accepting foreign_attachment: {attachment.name}')
        r = self.client.create_tags(
            Resources=[attachment.id],
            Tags=[dict(Key='Name', Value=attachment.name)],
        )
        # TODO
        # associate route table

    def do_create(self):
        r = self.client.create_transit_gateway(
            Description='Created by Carthage',
            Options={
                'AmazonSideAsn': 64513,
                'AutoAcceptSharedAttachments': 'disable',
                'DefaultRouteTableAssociation': 'disable',
                'DefaultRouteTablePropagation': 'disable',
                'VpnEcmpSupport': 'disable',
                'DnsSupport': 'disable',
                'MulticastSupport': 'disable',
            },
            TagSpecifications=[self.resource_tags]
        )

        r = r['TransitGateway']
        self.id = r['TransitGatewayId']
        self.arn = r['TransitGatewayArn']
        self.state = r['State']
        self.asn = r['Options']['AmazonSideAsn']
        self.cache = unpack(r)
        return self.cache

    async def post_find_hook(self):
        while True:
            state = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])['TransitGateways'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw: {self}')
            await asyncio.sleep(5)

@dataclass(repr=False)
class AwsTransitGatewayRoute:
    resource_type = 'transit-gateway-route'

    cidrblock: str = ''
    attachments: str = ''
    attachment_id: str = ''
    route_type: str = ''
    state: str = ''
    resource_id: str = ''
    resource_type: str = ''

    def __repr__(self):
        return f'<{self.__class__.__name__}: cidrblock: {self.cidrblock}>'

@inject_autokwargs(tgw=AwsTransitGateway)
class AwsTransitGatewayRouteTable(AwsClientManaged):

    resource_type = 'transit_gateway_route_table'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.client = self.service_resource
        self.id = None

    @memoproperty
    def service_resource(self):
        return self.connection.connection.client('ec2', region_name=self.connection.region)

    @callback
    def associate(self, association):
        '''Associate route table with AwsTransitGatewayAttachment'''
        try:
            r = self.client.associate_transit_gateway_route_table(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=association.id
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'Resource.AlreadyAssociated':
                return
            raise
        self.association = association
        association.association = self

    @callback
    def create_route(self, cidrblock, attachment):
        try:
            _ = self.client.create_transit_gateway_route(
                    DestinationCidrBlock=cidrblock,
                    TransitGatewayRouteTableId=self.id,
                    TransitGatewayAttachmentId=attachment.id,
                    Blackhole=False
                )
        except ClientError as e:
            if e.response['Error']['Code'] == 'RouteAlreadyExists':
                return
            raise

    @callback
    def disassociate(self, association):
        '''Disassociate route table with AwsTransitGatewayAttachment'''
        assert self.association!=None,"You must have an association"
        r = self.client.disassociate_transit_gateway_route_table(
            TransitGatewayRouteTableId=self.id,
            TransitGatewayAttachmentId=association.id
        )
        association.association = None
        self.association = None

    @callback
    def propagate(self, propagation):
        '''Propagate routes to table from AwsTransitGatewayAttachment'''
        try:
            r = self.client.enable_transit_gateway_route_table_propagation(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=propagation.id
            )
            if not hasattr(self, 'propagations'):
                self.propagations = []
            else:
                self.propagations.append(propagation)
        except ClientError as e:
            if e.response['Error']['Code'] == 'TransitGatewayRouteTablePropagation.Duplicate':
                return
            raise

    @callback
    def depropagate(self, propagation):
        '''Disable propagation of routes to table from AwsTransitGatewayAttachment'''
        # assert hasattr(self, 'propagation'),"You must have propagations"
        r = self.client.disable_transit_gateway_route_table_propagation(
            TransitGatewayRouteTableId=self.id,
            TransitGatewayAttachmentId=propagation.id
        )
        # self.propagations.remove(propagation)
        
    def do_create(self):
        r = self.client.create_transit_gateway_route_table(
            TransitGatewayId=self.tgw.id,
            TagSpecifications=[self.resource_tags]
        )
        self.id = r['TransitGatewayRouteTable']['TransitGatewayRouteTableId']
        self.cache = unpack(r)
        return self.cache

    async def post_find_hook(self):
        while True:
            r = self.client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[self.id])
            state = r['TransitGatewayRouteTables'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw_route_table: {self}')
            await asyncio.sleep(5)
        self.tgw.route_tables.update({self.id:self})
        r = self.client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[self.id])
        self.cache = unpack(r)

@inject_autokwargs(tgw=AwsTransitGateway, vpc=AwsVirtualPrivateCloud)
class AwsTransitGatewayAttachment(AwsClientManaged):

    resource_type = 'transit_gateway_attachment'

    def __init__(self, **kwargs):
        if ('subnet' in kwargs) and ('subnets' in kwargs):
            raise ValueError(f"call to AwsTransitGatewayAttachment should not specify both 'subnet' and 'subnets'")
        elif ('subnet' in kwargs):
            self.subnets = [kwargs.pop('subnet')]
        elif ('subnets' in kwargs):
            self.subnets = kwargs.pop('subnets')
        else:
            self.subnets = False
        super().__init__(**kwargs)
        self.id = None
        if self.subnets is False:
            self.subnets = [self.injector.get_instance(AwsSubnet)]

    def do_create(self):
        r = self.client.create_transit_gateway_vpc_attachment(
            TransitGatewayId=self.tgw.id,
            VpcId=self.vpc.id,
            SubnetIds=[subnet.id for subnet in self.subnets],
            Options={
                'DnsSupport': 'disable',
                'Ipv6Support': 'disable',
                'ApplianceModeSupport': 'enable'
            },
            TagSpecifications=[self.resource_tags]
        )['TransitGatewayVpcAttachment']
        self.id = r['TransitGatewayAttachmentId']
        self.cached = unpack(r)

    def reload(self):
        r = self.client.describe_transit_gateway_vpc_attachments(TransitGatewayAttachmentIds=[self.id])
        r = r['TransitGatewayVpcAttachments'][0]
        self.cached = unpack(r)

    def _update_attachment_subnets(self):
        try:
            reqids = [s.id for s in self.subnets]
            add = list(set(reqids) - set(self.cached.SubnetIds))
            remove = list(set(self.cached.SubnetIds) - set(reqids))
            if add or remove:
                r = self.client.modify_transit_gateway_vpc_attachment(
                    TransitGatewayAttachmentId=self.id,
                    AddSubnetIds=add,
                    RemoveSubnetIds=remove
                )

        finally:
            self.reload()

    def _wait_for_ready(self):
        while True:
            r = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[self.id])
            state = r['TransitGatewayAttachments'][0]['State']
            if state in ['available', 'pendingAcceptance']: break
            logging.info(f'waiting on tgw_attach: {self}')
            time.sleep(5)

    @callback
    def async_find_hook(self):
        self.reload()
        self._wait_for_ready()
        self._update_attachment_subnets()

    async def post_find_hook(self):
        await self.async_find_hook()
        self.tgw.attachments.update({self.id:self})
