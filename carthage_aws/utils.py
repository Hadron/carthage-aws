#        except ClientError as e:
#            logger.error(f'Could not find TransitGateway for {self.name} by name because {e}.')
#        return

from botocore.exceptions import ClientError

def find_tags(tl, tn):
    tl = [ x for x in tl if x['Key'] == tn  ]
    values = [ x['Value'] for x in tl ]
    return values

def find_name_from_tags(tl):
    values = find_tags(tl)
    if len(values) != 1:
        raise ValueError(f'unable to find Name in {tl}: got {values}')
    return values[0]

def has_tag_matching(tl, k, v):
    for t in tl:
        if t['Key'] == k and t['Value'] == v:
            return True
    return False
    
async def delete_all(connection):

    from botocore import xform_name

    while True:

        action = False
        ec2 = connection.connection.resource('ec2')
        client = connection.connection.client('ec2')
        for ig in ec2.internet_gateways.all():
            action = True
            for a in ig.attachments:
                ig.detach_from_vpc(VpcId=a['VpcId'])
            ig.delete()

        for k in ec2.get_available_subresources():
            if k in ['NetworkInterfaceAssociation', 'RouteTableAssociation', 'Snapshot', 'Tag', 'Route', 'Image']: continue
            print(f'removing {k}')
            nn = (xform_name(k) + 's')
            nn = nn.replace('sss', 'sses').replace('dhcp_optionss', 'dhcp_options_sets')
            for o in getattr(ec2, nn).all():
                action = True
                try:
                    print(f'removing {o}')
                    o.delete()
                    print(f'removed {o}')
                except:
                    print(f'failed {o}')
                    pass

        if not action: break

        for at in client.describe_transit_gateway_attachments()['TransitGatewayAttachments']:
             for rt in client.describe_transit_gateway_route_tables()['TransitGatewayRouteTables']:
                 for pt in client.get_transit_gateway_attachment_propagations(TransitGatewayAttachmentId=at['TransitGatewayAttachmentId']):
                     print(f'removing at {at["TransitGatewayAttachmentId"]}')
                     print(f'removing rt {rt["TransitGatewayRouteTableId"]}')
                     print(f'removing pt {pt["TransitGatewayRouteTablePropagationId"]}')
                     try:
                         client.disable_transit_gateway_route_table_propagation(
                             TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'],
                             TransitGatewayAttachmentId=at['TransitGatewayAttachmentId']
                         )
                         client.disassociate_transit_gateway_route_table(
                             TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'],
                             TransitGatewayAttachmentId=at['TransitGatewayAttachmentId']
                         )
                         client.delete_transit_gateway_route_table(TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'])
                     except ClientError as e:
                         print(f'Ignoring {e}')
             client.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=at['TransitGatewayAttachmentId'])

        for tgw in client.describe_transit_gateways()['TransitGateways']:
             print(f'removing tgw {tgw["TransitGatewayId"]}')
             if tgw['State'] not in ['deleted', 'deleting']:
                 client.delete_transit_gateway(TransitGatewayId=tgw['TransitGatewayId'])
