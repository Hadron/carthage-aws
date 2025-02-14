# Copyright (C) 2022-2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio


from carthage import *
from carthage import deployment
from carthage.modeling import *
from carthage.pytest import *
from carthage.network import this_network

from carthage_aws import *

@async_test
async def test_base_vm(carthage_layout):
    layout = carthage_layout
    try:
        await layout.generate()
        await layout.ainjector.get_instance_async(AwsConnection)
        await layout.test_vm.machine.async_become_ready()
        await layout.test_vm.machine.is_machine_running()
        await layout.test_vm.machine.ssh_online()
    finally:
        try:
            layout.test_vm.machine.mob.terminate()
        except Exception:
            pass

@async_test
async def test_record_private_zone(carthage_layout):
    layout = carthage_layout
    zone = await layout.ainjector.get_instance_async('autotest_photon_local')
    await layout.ainjector.get_instance_async(AwsConnection)
    try:
        await zone.update_records(
            *[('autotest.photon.local', 'TXT', '"This is a test"'),]
        )
    except Exception as e:
        logger.error(e)
        raise Exception("Failed to update update_records") from e # pylint: disable=broad-exception-raised

    instance =  layout.test_private_vm
    await instance.machine.start_machine()
    await instance.machine.ssh_online()
    await instance.machine.ssh("apt update && apt install -y dnsutils")
    with TestTiming(2000):
        timeout = 300
        interval = 5
        start_time = asyncio.get_event_loop().time()
        resp = await instance.machine.ssh("dig +short TXT autotest.photon.local")
        output = resp.stdout.decode()
        try:
            assert "This is a test" in output
        except AssertionError as e:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise e
            await asyncio.sleep(interval)


@async_test
async def test_read_only(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    await layout.does_not_exist.machine.async_become_ready()
    await layout.does_not_exist.machine.find()
    assert not layout.does_not_exist.machine.mob


@async_test
async def test_create_volume(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    vol = None
    try:
        vol = await layout.ainjector.get_instance_async('some_volume')
    finally:
        if vol:
            await vol.delete()

@async_test
async def test_attach_volume(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    vol = None
    try:
        instance =  layout.instance_for_volume
        await instance.machine.async_become_ready()
        instance.injector.add_provider(
            InjectionKey('aws_availability_zone'),
            instance.machine.mob.placement['AvailabilityZone']
        )
        vol = await instance.ainjector.get_instance_async('volume')
        await vol.attach(instance=instance, device="xvdi")
        vol = None
    finally:
        if vol:
            await vol.delete()
            with TestTiming(400):
                await instance.machine.delete()

@async_test
async def test_start_machine(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    try:
        instance =  layout.test_no_ready
        await instance.machine.start_machine()
    finally:
        await instance.machine.delete()

@async_test
async def test_image_building(carthage_layout, request):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    try:
        instance =  layout.image_builder
        with TestTiming(2000):
            await instance.async_become_ready()
        with TestTiming(2000):
            await instance.machine.async_become_ready()
        #instance.machine.ssh('-A', _fg=True)
        #breakpoint()
        with TestTiming(2000):
            await subtest_controller(
                request, instance.machine,
                ["--carthage-config=/carthage_aws/config.yml",
                 "/carthage_aws/tests/inner_image_builder.py"],
                python_path="/carthage:/carthage_aws",
                ssh_agent=True)

    finally:
        await instance.machine.delete()

@async_test
async def test_security_groups(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    await layout.all_access.async_become_ready()
    try:
        assert set(layout.all_access.ingress_rules) == layout.all_access.existing_ingress
    finally:
        await layout.all_access.delete()
    await layout.no_access.async_become_ready()
    try:
        assert layout.no_access.existing_egress == set()
    finally:
        await layout.no_access.delete()

@async_test
async def test_elastic_ip(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    await layout.ip_1.async_become_ready()
    try:
        assert layout.ip_1.ip_address
        await layout.ip_test.resolve_networking()
        await layout.ip_test.machine.async_become_ready()
        assert str(layout.ip_test.network_links['eth0'].merged_v4_config.public_address )== layout.ip_1.ip_address
    finally:
        if layout.ip_test.machine.mob:
            with TestTiming(2000):
                await layout.ip_test.machine.delete()
        await layout.ip_1.delete()

@async_test
async def test_aws_subnet_create(ainjector):
    '''
    Test creation of a VPC and a subnet
    '''
    class creation_vpc(AwsVirtualPrivateCloud, InjectableModel):
        name = 'creation_vpc'
        vpc_cidr = '10.1.0.0/16'
        dns_hostnames_enabled = True

        # The igw logic is kind of broken at the moment so comment out
        # the tests until it is fixed.  VPC creation always creates an
        # IGW, so explicit IGWs never are useful.

        # class route_table(AwsRouteTable):
        #     name = 'test_route_table'

        #     routes = [
        #         ('0.0.0.0/0', InjectionKey('test_igw')),
        #     ]

        # @provides("test_igw")
        # class igw(AwsInternetGateway):

        #     name = 'test_igw'


        @provides(InjectionKey(Network, role='public'))
        class created_subnet(NetworkModel):
            v4_config = V4Config(network='10.1.0.0/24')
            aws_availability_zone = 'us-east-1c'
            @propagate_key(InjectionKey("nat_gw", _globally_unique=True))
            class nat_gw(AwsNatGateway):
                name = "test_nat_gw"

                class net_config(NetworkConfigModel):
                    add('eth0', mac=None, net=this_network)
        class private_subnet(NetworkModel):
            v4_config = V4Config(network='10.1.1.0/24')
            aws_availability_zone = 'us-east-1d'
            class route_table(AwsRouteTable):
                name = 'test_private_route_table'
                routes = [
                    ('0.0.0.0/0', InjectionKey("nat_gw")),
                    ]
    try:
        ainjector.add_provider(creation_vpc)
        vpc = None
        try:
            vpc_to_cleanup = await ainjector(creation_vpc, readonly=deployment.DryRun)
            await vpc_to_cleanup.ainjector(run_deployment_destroy)
        except LookupError:
            pass
        vpc = await ainjector.get_instance_async(creation_vpc)
        with instantiation_not_ready():
            with TestTiming(2000):
                subnet = await vpc.created_subnet.access_by(AwsSubnet)
        await subnet.find()
        assert not subnet.mob
        await subnet.async_become_ready()
        assert subnet.mob
        assert subnet.mob.availability_zone == subnet._gfi("aws_availability_zone") # pylint: disable=protected-access
        result =  vpc.mob.describe_attribute(Attribute="enableDnsHostnames")
        assert result["EnableDnsHostnames"]["Value"] == True
        with TestTiming(2000):
            await vpc.private_subnet.access_by(AwsSubnet)
    finally:
        with TestTiming(2000):
            if vpc:
                print(await vpc.ainjector(run_deployment_destroy))

@async_test
async def test_network_without_config(carthage_layout):
    layout = carthage_layout
    ainjector = layout.ainjector
    await     layout.our_net.async_become_ready()
    await layout.our_net.access_by(AwsSubnet)
    vpc = await ainjector.get_instance_async(AwsVirtualPrivateCloud)
    await vpc.connection.inventory()
    class our_net(NetworkModel):
        name = 'our_net'

    # Create a version of our_net without a v4_config
    net = await ainjector(our_net)
    # and access by subnet
    await net.access_by(AwsSubnet)
    assert net.v4_config.network == layout.our_net.v4_config.network
