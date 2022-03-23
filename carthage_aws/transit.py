import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, AwsManagedClient, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

class AwsTransitGateway(AwsManagedClient):

    resource_type = 'transit_gateway'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.attachments = {}
        self.route_tables = {}

    async def associate(self, attachment, table):
        await self.route_tables[table.id].associate(self.attachments[attachment.id])

    async def propagate(self, attachment, table):
        await self.route_tables[table.id].propagate(self.attachments[attachment.id])

    async def create_route_table(self, name):
        pass

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

    async def create_attachment(self, attachment):
        pass

    async def create_foreign_attachment(self, attachment):
        '''Args: AwsTransitGatewayAttachment'''
        def callback():
            state = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[attachment.id])['TransitGatewayAttachments'][0]['State']
            if state not in ['pending', 'available']:
                r = self.client.accept_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=attachment.id)
            while state != 'available':
                state = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[attachment.id])['TransitGatewayAttachments'][0]['State']
                asyncio.sleep(5)
                print(f'waiting on {self} accepting foreign_attachment: {attachment}')
            r = self.client.create_tags(
                Resources=[attachment.id],
                Tags=[dict(Key='Name', Value=attachment.name)],
            )
            # TODO
            # associate route table
        await run_in_executor(callback)

    def do_create(self):
        try:
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

            self.id = r['TransitGateway']['TransitGatewayId']
            self.arn = r['TransitGateway']['TransitGatewayArn']
            self.state = r['TransitGateway']['State']
            self.asn = r['TransitGateway']['Options']['AmazonSideAsn']
        except ClientError as e:
            logger.error(f'Could not create TransitGatewayAttachment for {self.id} by id because {e}.')
        self.mob = self
        return self.mob

    async def post_find_hook(self):
        while True:
            state = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])['TransitGateways'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw: {self}')
            await asyncio.sleep(5)

    def find_from_id(self):
        r = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])
        for t in r['TransitGateways'][0]['Tags']:
            if t['Key'] == 'Name':
                self.name = t['Value']
        self.mob = self
        return self.mob
    
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
class AwsTransitGatewayRouteTable(AwsManagedClient):

    resource_type = 'transit_gateway_route_table'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.client = self.service_resource
        # self.routes = None
        # self.local_routes = None
        # self.foreign_routes = None

    @memoproperty
    def service_resource(self):
        return self.connection.connection.client('ec2', region_name=self.connection.region)

    async def associate(self, association):
        '''Associate route table with AwsTransitGatewayAttachment'''
        try:
            r = self.client.associate_transit_gateway_route_table(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=association.id
            )
            self.association = association
            association.association = self
        except ClientError as e:
            logger.error(f'{e}')

    async def create_route(self, cidrblock, attachment):
        def callback():
            try:
                _ = self.client.create_transit_gateway_route(
                        DestinationCidrBlock=cidrblock,
                        TransitGatewayRouteTableId=self.id,
                        TransitGatewayAttachmentId=attachment.id,
                        Blackhole=False
                    )
                logger.info(f"Created route {cidrblock}->{attachment} on {self}")
            except ClientError as e:
                logger.error(f"Could not create AwsTransitGatewayRoute on {self} due to {e}")
        await run_in_executor(callback)

    async def disassociate(self, association):
        '''Disassociate route table with AwsTransitGatewayAttachment'''
        assert self.association!=None,"You must have an association"
        try:
            r = self.client.disassociate_transit_gateway_route_table(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=association.id
            )
            association.association = None
            self.association = None
        except ClientError as e:
            logger.error(f'{e}')

    async def propagate(self, propagation):
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
            logger.error(f'{e}')

    async def depropagate(self, propagation):
        '''Disable propagation of routes to table from AwsTransitGatewayAttachment'''
        # assert hasattr(self, 'propagation'),"You must have propagations"
        try:
            r = self.client.disable_transit_gateway_route_table_propagation(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=propagation.id
            )
            # self.propagations.remove(propagation)
        except ClientError as e:
            logger.error(f'{e}')
        
    def do_create(self):
        try:
            r = self.client.create_transit_gateway_route_table(
                TransitGatewayId=self.tgw.id,
                TagSpecifications=[self.resource_tags]
            )
        except ClientError as e:
            logger.error(f'Could not create TransitGatewayAttachment for {self.id} because {e}.')
        self.mob = self
        return self.mob

    async def post_find_hook(self):
        while True:
            r = self.client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[self.id])
            state = r['TransitGatewayRouteTables'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw_route_table: {self}')
            await asyncio.sleep(5)
        self.tgw.route_tables[self.id] = self

@inject_autokwargs(tgw=AwsTransitGateway, vpc=AwsVirtualPrivateCloud, subnet=AwsSubnet)
class AwsTransitGatewayAttachment(AwsManagedClient):

    resource_type = 'transit_gateway_attachment'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.association = None
        # self.propagations = []
        
    def do_create(self):
        r = self.client.create_transit_gateway_vpc_attachment(
            TransitGatewayId=self.tgw.id,
            VpcId=self.vpc.id,
            SubnetIds=[
                self.subnet.id,
            ],
            Options={
                'DnsSupport': 'disable',
                'Ipv6Support': 'disable',
                'ApplianceModeSupport': 'disable'
            },
            TagSpecifications=[self.resource_tags]
        )['TransitGatewayVpcAttachment']
        self.id = r['TransitGatewayAttachmentId']
        self.mob = self
        return self.mob
        
    async def post_find_hook(self):
        try:
            r = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[self.id])['TransitGatewayAttachments'][0]
            self.attached_resource_type = r['ResourceType']
            self.attached_resource_id = r['ResourceId']

            # 'Association': {
            #     'TransitGatewayRouteTableId': 'string',
            #     'State': 'associating'|'associated'|'disassociating'|'disassociated'
            #     },
        except ClientError as e:
            logger.error(f'Could not find TransitGatewayAttachment for {self.id} by id because {e}.')

        while True:
            r = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[self.id])
            state = r['TransitGatewayAttachments'][0]['State']
            if state in ['available', 'pendingAcceptance']: break
            print(f'waiting on tgw_attach: {self}')
            await asyncio.sleep(5)
        self.tgw.attachments[self.id] = self
