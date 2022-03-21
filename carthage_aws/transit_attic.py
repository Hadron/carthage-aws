#from    def find_from_id(self):
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
                
