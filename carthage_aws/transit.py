import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

class AwsTransitGateway(AwsManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.client = self.service_resource

    @memoproperty
    def service_resource(self):
        # override because ec2 resource does not support transit gateway
        return self.connection.connection.client('ec2', region_name=self.connection.region)

    def do_create(self):
        class mock: pass
        self.mob = mock()
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
                TagSpecifications=[
                    {
                        'ResourceType':'transit-gateway',
                        'Tags':[
                            {'Key':'Name', 'Value':self.name}
                        ]
                    },
                ]
            )

            self.id = r['TransitGateway']['TransitGatewayId']
            self.mob.id = self.id

            self.arn = r['TransitGateway']['TransitGatewayArn']
            self.state = r['TransitGateway']['State']
            self.asn = r['TransitGateway']['Options']['AmazonSideAsn']
        except ClientError as e:
            logger.error(f'Could not create TransitGatewayAttachment for {self.id} by id because {e}.')
        return self.mob

    async def post_find_hook(self):
        while True:
            r = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])
            state = r['TransitGateways'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw: {self}')
            await asyncio.sleep(1)

    def find_from_name(self):
        from .utils import has_tag_matching
        r = self.client.describe_transit_gateways()['TransitGateways']
        for o in r:
            if o['State'] == 'deleted': continue
            if has_tag_matching(o['Tags'], 'Name', self.name):
                self.id = o['TransitGatewayId']

    def find_from_id(self):
        class mock: pass
        self.mob = mock()
        try:
            r = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])
            for t in r['TransitGateways'][0]['Tags']:
                if t['Key'] == 'Name':
                    self.name = t['Value']

            # self.route_tables = []
            # r = self.client.describe_transit_gateway_route_tables(
            #     Filters=[{'Name': 'transit-gateway-id','Values': [self.id,]},]
            # )
            # for x in r['TransitGatewayRouteTables']:
            #     name = ''
            #     for t in x['Tags']:
            #         if t['Key'] == 'Name':
            #             name = t['Value']
            #     associated = False
            #     association_id = ''
            #     association_type = ''
            #     association = None
            #     for a in self.attachments:
            #         if x['TransitGatewayRouteTableId'] == a.association_id:
            #             associated = True
            #             association_id = a.id
            #             association_type = 'TransitGatewayAttachment'
            #             association = a
            #             break
            #     self.route_tables.append(
            #         AwsTransitGatewayRouteTable(
            #             id = x['TransitGatewayRouteTableId'],
            #             name = name,
            #             associated = associated,
            #             association_id = association_id,
            #             association_type = association_type,
            #             association = association
            #         )
            #     )
            # for x in self.route_tables:
            #     local = ''
            #     if x.association_id != '':
            #         local = x.association.id
            #         for a in self.attachments:
            #             if x.association.association_id == a.association_id:
            #                 a.association = x
            #     r = self.client.search_transit_gateway_routes(
            #         TransitGatewayRouteTableId=x.id,
            #         Filters=[{'Name': 'state','Values': ['active',]},]
            #     )
            #     for y in r['Routes']:
            #         if local == y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId']:
            #             x.local_routes.append(
            #                 AwsTransitGatewayRoute(
            #                     cidrblock = y['DestinationCidrBlock'],
            #                     attachments = y['TransitGatewayAttachments'],
            #                     # FIXME: assuming len(y['TransitGatewayAttachments']) == 1 here may not be thorough
            #                     resource_id = y['TransitGatewayAttachments'][0]['ResourceId'],
            #                     attachment_id = y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId'],
            #                     resource_type = y['TransitGatewayAttachments'][0]['ResourceType'],
            #                     route_type = y['Type'],
            #                     state = y['State']
            #                 )
            #             )
            #         else:
            #             x.foreign_routes.append(
            #                 AwsTransitGatewayRoute(
            #                     cidrblock = y['DestinationCidrBlock'],
            #                     attachments = y['TransitGatewayAttachments'],
            #                     # FIXME: assuming len(y['TransitGatewayAttachments']) == 1 here may not be thorough
            #                     resource_id = y['TransitGatewayAttachments'][0]['ResourceId'],
            #                     attachment_id = y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId'],
            #                     resource_type = y['TransitGatewayAttachments'][0]['ResourceType'],
            #                     route_type = y['Type'],
            #                     state = y['State']
            #                 )
            #             )
            #         x.routes = x.local_routes + x.foreign_routes
            # for a in self.attachments:
            #     r = self.client.get_transit_gateway_attachment_propagations(TransitGatewayAttachmentId=a.id)['TransitGatewayAttachmentPropagations']
            #     for p in r:
            #         if a.association:
            #             if p['TransitGatewayRouteTableId'] != a.association.id:
            #                 for t in self.route_tables:
            #                     if t.id == p['TransitGatewayRouteTableId']:
            #                         table = t
            #                 a.propagations.append(t)
                
        except ClientError as e:
            logger.error(f'Could not find TransitGateway for {self.id} by id because {e}.')
        return self.mob
    
    async def find(self):
        '''Find ourself from a name or id
'''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            await run_in_executor(self.find_from_name)
            if self.id:
                return await run_in_executor(self.find_from_id)
        return

@dataclass(repr=False)
class AwsTransitGatewayRoute:
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
class AwsTransitGatewayRouteTable(AwsManaged):

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
        assert hasattr(self, 'propagation'),"You must have propagations"
        try:
            r = self.client.disable_transit_gateway_route_propagation(
                TransitGatewayRouteTableId=self.id,
                TransitGatewayAttachmentId=propagation.id
            )
            self.propagations.remove(propagation)
        except ClientError as e:
            logger.error(f'{e}')
        
    def do_create(self):
        class mock: pass
        self.mob = mock()
        try:
            r = self.client.create_transit_gateway_route_table(
                TransitGatewayId=self.tgw.id,
                TagSpecifications=[
                    {
                        'ResourceType':'transit-gateway-route-table',
                        'Tags':[
                            {
                                'Key':'Name',
                                'Value': self.name
                            }
                        ]
                    }
                ]
            )
        except ClientError as e:
            logger.error(f'Could not create TransitGatewayAttachment for {self.id} because {e}.')
        return self.mob
        
    async def post_find_hook(self):
        while True:
            r = self.client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[self.id])
            state = r['TransitGatewayRouteTables'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw_route_table: {self}')
            await asyncio.sleep(1)

    def find_from_name(self):
        from .utils import has_tag_matching
        r = self.client.describe_transit_gateway_route_tables()['TransitGatewayRouteTables']
        for o in r:
            if o['State'] == 'deleted': continue
            if has_tag_matching(o['Tags'], 'Name', self.name):
                self.id = o['TransitGatewayRouteTableId']

    def find_from_id(self):
        class mock: pass
        self.mob = mock()
        try:
            r = self.client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[self.id])['TransitGatewayRouteTables'][0]
            # should we bother
            # we already know the id and the tgw id
        except ClientError as e:
            logger.error(f'Could not find TransitGatewayRouteTable for {self.id} by id because {e}.')
        return self.mob

    async def find(self):
        '''Find ourself from a name or id
'''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            await run_in_executor(self.find_from_name)
            if self.id:
                return await run_in_executor(self.find_from_id)
        return


@inject_autokwargs(tgw=AwsTransitGateway, vpc=AwsVirtualPrivateCloud, subnet=AwsSubnet)
class AwsTransitGatewayAttachment(AwsManaged):

    resource_type = 'transit-gateway-attachment'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.client = self.service_resource
        self.association = None
        # self.propagations = []

    @memoproperty
    def service_resource(self):
        return self.connection.connection.client('ec2', region_name=self.connection.region)
        
    # self.attachments = []
    # r = self.client.describe_transit_gateway_attachments(
    #     Filters=[{'Name': 'transit-gateway-id','Values': [self.id,]},]
    # )
    # for x in r['TransitGatewayAttachments']:
    #     name = ''
    #     for t in x['Tags']:
    #         if t['Key'] == 'Name':
    #             name = t['Value']
    #     association_id = ''
    #     associated = False
    #     association_type = ''
    #     if 'Association' in x.keys():
    #         association_id = x['Association']['TransitGatewayRouteTableId']
    #         associated = (x['Association']['State'] == 'associated')
    #         association_type = 'TransitGatewayRouteTable'
    #     self.attachments.append(
    #         AwsTransitGatewayAttachment(
    #             id = x['TransitGatewayAttachmentId'],
    #             name = name,
    #             resource_id = x['ResourceId'],
    #             resource_type = x['ResourceType'],
    #             association_type = 'TransitGatewayRouteTable',
    #             association_id = association_id,
    #             associated = associated
    #         )
    #     )

    def do_create(self):
        class mock: pass
        self.mob = mock()
        try:
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
                TagSpecifications=[
                    {
                        'ResourceType': 'transit-gateway-attachment',
                        'Tags': [
                            {
                                'Key': 'Name',
                                'Value': self.name
                            },
                        ]
                    },
                ]
            )['TransitGatewayVpcAttachment']
            self.id = r['TransitGatewayAttachmentId']
        except ClientError as e:
            logger.error(f'Could not create TransitGatewayAttachment for {self.id} because {e}.')
        return self.mob
        
    async def post_find_hook(self):
        while True:
            r = self.client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[self.id])
            state = r['TransitGatewayAttachments'][0]['State']
            if state == 'available': break
            print(f'waiting on tgw_attach: {self}')
            await asyncio.sleep(1)

    def find_from_name(self):
        from .utils import has_tag_matching
        r = self.client.describe_transit_gateway_attachments()['TransitGatewayAttachments']
        for o in r:
            if o['State'] == 'deleted': continue
            if has_tag_matching(o['Tags'], 'Name', self.name):
                self.id = o['TransitGatewayAttachmentId']

    def find_from_id(self):
        class mock: pass
        self.mob = mock()
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
        return self.mob

    async def find(self):
        '''Find ourself from a name or id
'''
        if self.id:
            return await run_in_executor(self.find_from_id)
        elif self.name:
            await run_in_executor(self.find_from_name)
            if self.id:
                return await run_in_executor(self.find_from_id)
        return
