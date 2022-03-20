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

        for k in ec2.get_available_subresources():
            if k in ['VpcAddress', 'NetworkInterfaceAssociation', 'RouteTableAssociation',
                     'Snapshot', 'Tag', 'Route', 'Image']: continue
            # print(f'removing {k}')
            nn = (xform_name(k) + 's')
            nn = nn.replace('sss', 'sses').replace('dhcp_optionss', 'dhcp_options_sets')
            # print(f'removing {nn}')
            for o in getattr(ec2, nn).all():
                try:
                    if o.state['Name'] == 'terminated': continue
                    action = True
                    remap = dict(VpcAddress='release', Instance='terminate')
                    d = remap.get(k, 'delete')
                    print(f'removing {o} with {d}')
                    getattr(o, d)()
                    print(f'removed {o}')
                except Exception as e:
                    print(f'failed {o} {e}')
                    pass

        for ig in ec2.internet_gateways.all():
            action = True
            for a in ig.attachments:
                ig.detach_from_vpc(VpcId=a['VpcId'])
            ig.delete()

        for at in client.describe_transit_gateway_attachments()['TransitGatewayAttachments']:
            if at['State'] == 'deleted':
                continue
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
             if tgw['State'] not in ['deleted', 'deleting']:
                 print(f'removing tgw {tgw["TransitGatewayId"]}')
                 client.delete_transit_gateway(TransitGatewayId=tgw['TransitGatewayId'])

        if not action: break
