from carthage import *
from carthage.dependency_injection import *

from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, run_in_executor

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

@dataclass(repr=False)
class AwsTransitGatewayElement:
    id: str
    name: str = ''
    resource_id: str = ''
    resource_type: str = ''

    def __repr__(self):
        return f'<{self.__class__.__name__}: id: {self.id}>'

@dataclass(repr=False)
class AwsTransitGatewayElementAssociation(AwsTransitGatewayElement):
    associated: bool = False
    association_id: str = ''
    association_type: str = ''

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

@dataclass(repr=False)
class AwsTransitGatewayRouteTable(AwsTransitGatewayElementAssociation):
    association: AwsTransitGatewayElementAssociation = None
    routes: list[AwsTransitGatewayRoute] = field(default_factory=lambda: [])
    local_routes: list[AwsTransitGatewayRoute] = field(default_factory=lambda: [])
    foreign_routes: list[AwsTransitGatewayRoute] = field(default_factory=lambda: [])

@dataclass(repr=False)
class AwsTransitGatewayAttachment(AwsTransitGatewayElementAssociation):
    association: AwsTransitGatewayElementAssociation = None
    propagations: list[AwsTransitGatewayRouteTable] = field(default_factory=lambda: [])

class AwsTransitGateway(AwsManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.client = self.service_resource

    @memoproperty
    def service_resource(self):
        # override because ec2 resource does not support transit gateway
        return self.connection.connection.client('ec2', region_name=self.connection.region)

    def find_from_name(self):
        try:
            # we look for a hosted zone with our exact name
            r = self.client.describe_transit_gateways()['TransitGateways']
            for t in r:
                if 'Name' in r['Tags'].keys():
                    print(r['Tags']['Name'])
                    if self.name == r['Tags']['Name']:
                        self.id = r['TransitGatewayId']
        except ClientError as e:
            logger.error(f'Could not find TransitGateway for {self.name} by name because {e}.')
        return

    def do_create(self):
        # we do not want to mess with this right now
        return

    def find_from_id(self):
        try:
            r = self.client.describe_transit_gateways(TransitGatewayIds=[self.id])
            for t in r['TransitGateways'][0]['Tags']:
                if t['Key'] == 'Name':
                    self.name = t['Value']
            self.mob = r

            self.attachments = []
            r = self.client.describe_transit_gateway_attachments(
                Filters=[{'Name': 'transit-gateway-id','Values': [self.id,]},]
            )
            for x in r['TransitGatewayAttachments']:
                name = ''
                for t in x['Tags']:
                    if t['Key'] == 'Name':
                        name = t['Value']
                association_id = ''
                associated = False
                association_type = ''
                if 'Association' in x.keys():
                    association_id = x['Association']['TransitGatewayRouteTableId']
                    associated = (x['Association']['State'] == 'associated')
                    association_type = 'TransitGatewayRouteTable'
                self.attachments.append(
                    AwsTransitGatewayAttachment(
                        id = x['TransitGatewayAttachmentId'],
                        name = name,
                        resource_id = x['ResourceId'],
                        resource_type = x['ResourceType'],
                        association_type = 'TransitGatewayRouteTable',
                        association_id = association_id,
                        associated = associated
                    )
                )

            self.route_tables = []
            r = self.client.describe_transit_gateway_route_tables(
                Filters=[{'Name': 'transit-gateway-id','Values': [self.id,]},]
            )
            for x in r['TransitGatewayRouteTables']:
                name = ''
                for t in x['Tags']:
                    if t['Key'] == 'Name':
                        name = t['Value']
                associated = False
                association_id = ''
                association_type = ''
                association = None
                for a in self.attachments:
                    if x['TransitGatewayRouteTableId'] == a.association_id:
                        associated = True
                        association_id = a.id
                        association_type = 'TransitGatewayAttachment'
                        association = a
                        break
                self.route_tables.append(
                    AwsTransitGatewayRouteTable(
                        id = x['TransitGatewayRouteTableId'],
                        name = name,
                        associated = associated,
                        association_id = association_id,
                        association_type = association_type,
                        association = association
                    )
                )
            for x in self.route_tables:
                local = ''
                if x.association_id != '':
                    local = x.association.id
                    for a in self.attachments:
                        if x.association.association_id == a.association_id:
                            a.association = x
                r = self.client.search_transit_gateway_routes(
                    TransitGatewayRouteTableId=x.id,
                    Filters=[{'Name': 'state','Values': ['active',]},]
                )
                for y in r['Routes']:
                    if local == y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId']:
                        x.local_routes.append(
                            AwsTransitGatewayRoute(
                                cidrblock = y['DestinationCidrBlock'],
                                attachments = y['TransitGatewayAttachments'],
                                # FIXME: assuming len(y['TransitGatewayAttachments']) == 1 here may not be thorough
                                resource_id = y['TransitGatewayAttachments'][0]['ResourceId'],
                                attachment_id = y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId'],
                                resource_type = y['TransitGatewayAttachments'][0]['ResourceType'],
                                route_type = y['Type'],
                                state = y['State']
                            )
                        )
                    else:
                        x.foreign_routes.append(
                            AwsTransitGatewayRoute(
                                cidrblock = y['DestinationCidrBlock'],
                                attachments = y['TransitGatewayAttachments'],
                                # FIXME: assuming len(y['TransitGatewayAttachments']) == 1 here may not be thorough
                                resource_id = y['TransitGatewayAttachments'][0]['ResourceId'],
                                attachment_id = y['TransitGatewayAttachments'][0]['TransitGatewayAttachmentId'],
                                resource_type = y['TransitGatewayAttachments'][0]['ResourceType'],
                                route_type = y['Type'],
                                state = y['State']
                            )
                        )
                    x.routes = x.local_routes + x.foreign_routes
            for a in self.attachments:
                r = self.client.get_transit_gateway_attachment_propagations(TransitGatewayAttachmentId=a.id)['TransitGatewayAttachmentPropagations']
                for p in r:
                    if a.association:
                        if p['TransitGatewayRouteTableId'] != a.association.id:
                            for t in self.route_tables:
                                if t.id == p['TransitGatewayRouteTableId']:
                                    table = t
                            a.propagations.append(t)
                
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
