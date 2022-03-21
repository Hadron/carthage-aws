from botocore.exceptions import ClientError
from botocore import xform_name
import logging

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

async def delete_all_for(ec2, k):

    removed, errors = 0, 0

    nn = (xform_name(k) + 's')
    nn = nn.replace('sss', 'sses').replace('dhcp_optionss', 'dhcp_options_sets')

    for o in getattr(ec2, nn).all():
        try:
            if hasattr(o, 'state'):
                if isinstance(o.state, str): pass
                elif o.state['Name'] == 'terminated': continue
            action = True
            remap = dict(VpcAddress='release', Instance='terminate')
            d = remap.get(k, 'delete')
            logging.info(f'removing {o} with {d}')
            getattr(o, d)()
            removed += 1
            logging.info(f'removed {o}')

        except Exception as e:
            errors += 1
            logging.error(f'failed to delete {o}: {e}')
    
    return removed, errors

async def delete_helper(client, kt, ktid, kdel, kdesc, uselist=False):

    todel = []
    for o in getattr(client, kdesc)()[kt]:
        if not uselist:
            kwargs = { ktid: o[ktid] }
            getattr(client, kdel)(**kwargs)
        else:
            todel.append(o[ktid])
    if todel:
        kwargs = { ktid+'s': todel }
        getattr(client, kdel)(**kwargs)
        

async def remove_transit_gateways(client):

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

async def delete_all(connection):

    while True:

        errors, removed = 0, 0

        ec2 = connection.connection.resource('ec2')
        client = connection.connection.client('ec2')

        await delete_helper(client, 'NatGateways', 'NatGatewayId', 'delete_nat_gateway', 'describe_nat_gateways')
        await delete_helper(client, 'VpcEndpoints', 'VpcEndpointId', 'delete_vpc_endpoints', 'describe_vpc_endpoints', uselist=True)
        await delete_helper(client, 'Addresses', 'AllocationId', 'release_address', 'describe_addresses')

        for k in ec2.get_available_subresources():
            if k in ['VpcAddress', 'NetworkInterfaceAssociation', 'RouteTableAssociation',
                     'Snapshot', 'Tag', 'Route', 'Image']: continue
            c_errors, c_removed = await delete_all_for(ec2, k)
            errors += c_errors
            removed += c_removed

        for ig in ec2.internet_gateways.all():
            action = True
            for a in ig.attachments:
                ig.detach_from_vpc(VpcId=a['VpcId'])
            ig.delete()

        await remove_transit_gateways(client)

        if (errors + removed) == 0:
            break

    await connection.inventory()
