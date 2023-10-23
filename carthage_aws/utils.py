from botocore.exceptions import ClientError
from botocore import xform_name
import logging
import asyncio

class BaseMob:
    def __init__(self, *args, **kwargs):
        for a in args:  
            if type(a) is dict:
                self.__dict__ = a
        for k, v in kwargs.items():
            if k in allkw:
                self.__dict__.update({k:v})

    def __repr__(self):
        items = [f'{k}={v}' for k, v in self.__dict__.items()]
        return f"<{self.__class__.__name__}({', '.join(items)})>"

def unpack(i, c=0, rk=''):
    c += 1
    attrs = {}
    if type(i) is dict:
        for k, v in i.items():
            if type(v) is dict:
                attrs.update({k:unpack(v, c, k)})
            elif type(v) is list:
                attrs.update({k:unpack(v, c, k)})
            elif type(v) is (str or int):
                attrs.update({k:v})
    elif type(i) is list:
        attrs = []
        for li in i:
            if type(li) is dict:
                attrs.append(unpack(li, c, rk))
            elif type(li) is list:
                attrs.append(unpack(li, c, rk))
            elif type(li) is (str or int):
                attrs.append(li)
        return attrs
    elif type(i) is (str or int):
        return i
    # rk[:-1] if rk.endswith('s') else rk
    return type(rk, (BaseMob,), {})(attrs)

def find_ipv4_interfaces(machine):
    setattr(machine, 'interfaces', {})
    _ = [ machine.interfaces.update({f'{x[0]}':x[1].private_ip_address}) for x in zip(machine.network_links, machine.mob.network_interfaces) ]

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

async def remove_directory_service(client):
    for x in client.describe_directories()['DirectoryDescriptions']:
        if x['Stage'] not in ['Deleting', 'Deleted']:
            logging.info(f"Deleting {x['DirectoryId']}")
            client.delete_directory(DirectoryId=x['DirectoryId'])

    await asyncio.sleep(.25)

    done = False
    while not done:
        done = True
        for x in client.describe_directories()['DirectoryDescriptions']:
            if x['Stage'] not in ['Deleted']:
                logging.info(f"waiting for directory {x['DirectoryId']} to delete")
                done = False
        await asyncio.sleep(2)

async def remove_vpc_endpoint_service(client):
    async def remove_vpce_routes():
        vpces = client.describe_vpc_endpoints()['VpcEndpoints']
        rts = client.describe_route_tables()['RouteTables']
        for vpce in vpces:
            for rt in rts:
                for r in rt['Routes']:
                    for k, v in r.items():
                        if 'Gateway' in k:
                            if v == vpce['VpcEndpointId']:
                                logging.info(f"Deleting route for {r['DestinationCidrBlock']} -> {vpce['VpcEndpointId']}")
                                client.delete_route(**dict(
                                    DestinationCidrBlock=r['DestinationCidrBlock'],
                                    RouteTableId=rt['RouteTableId']
                                ))

    async def remove_vpce():
        vpces = client.describe_vpc_endpoints()['VpcEndpoints']
        if len(vpces) > 0:
            logging.info(f"Deleting endpoints {[x['VpcEndpointId'] for x in vpces]}")
            client.delete_vpc_endpoints(VpcEndpointIds=[x['VpcEndpointId'] for x in vpces])

    async def remove_vpcsvc():
        vpcsvccons = client.describe_vpc_endpoint_service_configurations()['ServiceConfigurations']
        if len(vpcsvccons) > 0:
            logging.info(f"Deleting endpoints {[x['ServiceId'] for x in vpcsvccons]}")
            client.delete_vpc_endpoint_service_configurations(ServiceIds=[x['ServiceId'] for x in vpcsvccons])

    tasks = []
    tasks.append(remove_vpce_routes())
    tasks.append(remove_vpce())
    tasks.append(remove_vpcsvc())
    await asyncio.gather(*tasks)
        
async def remove_load_balancers(client):

    async def remove_target_groups():
        allok = False
        while not allok:
            allok = True
            for tg in client.describe_target_groups()['TargetGroups']:
                try:
                    client.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                    logging.info(f"Deleting target group {tg['TargetGroupArn']}")
                    allok = False
                except Exception as e:
                    logging.error(f'{e}')
            await asyncio.sleep(5)

    async def remove_listeners(lb):
        tasks = []
        for li in client.describe_listeners(LoadBalancerArn=lb['LoadBalancerArn'])['Listeners']:
            logging.info(f"Deleting listener {li['ListenerArn']}")
            tasks.append(client.delete_listener(ListenerArn=li['ListenerArn']))
        await asyncio.gather(*tasks)

    async def remove_lb():
        allok = False
        while not allok:
            allok = True
            try:
                r = client.describe_load_balancers()['LoadBalancers']
                tasks = []
                for lb in r:
                    tasks.append(remove_listeners(lb))
                await asyncio.gather(*tasks)
                for lb in r:
                    if lb['State']['Code'] not in ['deleted', 'deleting']:
                        logging.info(f"Deleting load balancer {lb['LoadBalancerArn']}")
                        client.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
                        allok = False
                await asyncio.sleep(5)
            except ClientError as e:
                logging.error(f"{e}")
                await asyncio.sleep(5)

        alldel = False
        while not alldel:
            alldel = True
            try:
                r = client.describe_load_balancers()['LoadBalancers']
                for lb in r:
                    if r['State']['Code'] in ['deleting']:
                        logging.info(f'waiting for {lb["LoadBalancerArn"]} to delete')
                        alldel = False
                await asyncio.sleep(5)
            except ClientError as e:
                logging.error(f'{e}')
                await asyncio.sleep(5)

    tasks = []
    tasks.append(remove_target_groups())
    tasks.append(remove_lb())
    await asyncio.gather(*tasks)

    return

async def remove_vpns(client):
    async def remove_vpn(x):
        if x['State'] == 'deleted':
            return
        try:
            client.delete_vpn_connection(VpnConnectionId=x['VpnConnectionId'])
        except ClientError as e:
            logger.info(f'Ignoring {e}')

    async def remove_connection(x):
        if x['State'] == 'deleted':
            return
        try:
            client.delete_vpn_gateway(VpnGatewayId=x['VpnGatewayId'])
        except ClientError as e:
            logger.info(f'Ignoring {e}')

    async def remove_gateway(x):
        if x['State'] == 'deleted':
            return
        try:
            client.delete_customer_gateway(CustomerGatewayId=x['CustomerGatewayId'])
        except ClientError as e:
            logger.info(f'Ignoring {e}')

    vpns = [ x for x in client.describe_vpn_connections()['VpnConnections'] ]
    vgws = [ x for x in client.describe_vpn_gateways()['VpnGateways'] ]
    cgws = [ x for x in client.describe_customer_gateways()['CustomerGateways'] ]

    vpt = [ remove_vpn(v) for v in vpns ]
    vgt = [ remove_connection(v) for v in vgws ]
    cgt = [ remove_gateway(v) for v in cgws ]

    await asyncio.gather(*vpt, *vgt, *cgt)
    
async def remove_transit_gateways(client):

    async def delete_attachment(at):
        vpntasks = None
        if at['State'] == 'deleted':
            return
        if at['ResourceType'] == 'vpn':
            return

        try:
            client.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=at['TransitGatewayAttachmentId'])
        except ClientError as e:
            logging.info(f'Ignoring {e}')
        while 1:
            r = client.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=[at['TransitGatewayAttachmentId']])['TransitGatewayAttachments'][0]
            if r['State'] == 'deleted':
                break
            else:
                logging.info(f'waiting on {at["TransitGatewayAttachmentId"]} to delete')
                await asyncio.sleep(5)

    async def delete_association(rt, at):
        client.disassociate_transit_gateway_route_table(
            TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'],
            TransitGatewayAttachmentId=at['TransitGatewayAttachmentId']
        )
        
    async def delete_route_table(rt):
        if rt['State'] == 'deleted':
            return

        ascs = client.get_transit_gateway_route_table_associations(TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'])['Associations']

        atasks = [ delete_association(rt, at) for at in ascs ]

        await asyncio.gather(*atasks)

        while 1: 
            try:
                client.delete_transit_gateway_route_table(TransitGatewayRouteTableId=rt['TransitGatewayRouteTableId'])
            except ClientError as e:
                logging.info(f'Ignoring {e}')
                await asyncio.sleep(5)

        while 1:
            try:
                r = client.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[rt['TransitGatewayRouteTableId']])['TransitGatewayRouteTables'][0]
                logging.info(f'waiting on {rt["TransitGatewayRouteTableId"]} to delete')
                await asyncio.sleep(5)
            except ClientError as e:
                if e.response['Error']['Code'] == 'InvalidRouteTableId.NotFound':
                    break
                raise
        
    ats = client.describe_transit_gateway_attachments()['TransitGatewayAttachments']
    rts = client.describe_transit_gateway_route_tables()['TransitGatewayRouteTables']

    rtasks = [ delete_route_table(rt) for rt in rts ]
    atasks = [ delete_attachment(at) for at in ats ]

    await asyncio.gather(*atasks, *rtasks)

    for tgw in client.describe_transit_gateways()['TransitGateways']:
        if tgw['State'] not in ['deleted', 'deleting']:
            print(f'removing tgw {tgw["TransitGatewayId"]}')
            try:
                client.delete_transit_gateway(TransitGatewayId=tgw['TransitGatewayId'])
            except ClientError as e:
                # it is likely the tgw is shared with us so we can see it but not delete it
                if e.response['Error']['Code'] == 'InvalidTransitGatewayID.NotFound':
                    return
                raise

async def remove_ram_shares(connection):
    pass

async def delete_all(connection):

    while True:

        errors, removed = 0, 0

        ec2 = connection.connection.resource('ec2')
        client = connection.connection.client('ec2')
        elbv2 = connection.connection.client('elbv2')
        ds = connection.connection.client('ds')
        ram = connection.connection.client('ram')

        await asyncio.gather(
            # remove_vpns(client),
            # remove_transit_gateways(client),
            # remove_directory_service(ds)
            remove_load_balancers(elbv2),
            remove_vpc_endpoint_service(client),
        )

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
                try:
                    ig.detach_from_vpc(VpcId=a['VpcId'])
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation':
                        pass
            try:
                ig.delete()
            except ClientError as e:
                if e.response['Error']['Code'] == 'DependencyViolation':
                    pass

        if (errors + removed) == 0:
            break

    await connection.inventory()
