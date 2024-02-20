# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio
import contextlib
import warnings

from ipaddress import IPv4Address
import yaml

from botocore.exceptions import ClientError

from carthage import *
from carthage.modeling import *
from carthage.dependency_injection import *
from carthage.machine import Machine, NetworkedModel
from carthage.network import NetworkLink
from carthage.local import LocalMachineMixin
from carthage.cloud_init import generate_cloud_init_cloud_config

from .connection import AwsConnection, AwsManaged, run_in_executor

__all__ = ['AwsVm']

@inject(
    connection=AwsConnection,
    ami=InjectionKey('aws_ami'),
    model=AbstractMachineModel,
    volume_type=InjectionKey('aws_volume_type', _optional=True),
    )
async def generate_block_device_mappings(connection, ami, model, volume_type):
    if volume_type is None:
        volume_type ='gp2'
    services =         connection .connection.resource('ec2', region_name=connection.region)
    ami_image = services.Image(ami)
    mappings = []
    disk_sizes = model.disk_sizes
    disk_name = '/dev/xvda'
    i = 0
    for i, mapping in enumerate(ami_image.block_device_mappings):
        if i >= len(disk_sizes):
            break
        if 'Ebs' not in mapping:
            continue
        if mapping['Ebs']['VolumeSize'] > disk_sizes[i]:
            warnings.warn(
                f'{model}: disk size entry {i} size {disk_sizes[i]} less than image size {mapping["Ebs"]["VolumeSize"]}'
            )
            continue
        disk_name=mapping['DeviceName']
        mappings.append({
            "DeviceName":mapping['DeviceName'],
            "Ebs":{"VolumeSize":disk_sizes[i]}
        })
    for i, _ in enumerate(disk_sizes):
        if i < len(mappings):
            continue
        disk_name = disk_name[:-1]+chr(ord(disk_name[-1])+1)
        mappings.append({
            "DeviceName":disk_name,
            "Ebs":{
                "VolumeSize":disk_sizes[i],
                "DeleteOnTermination":True,
                "VolumeType":volume_type
            }
        })
    return mappings


@inject(ainjector=AsyncInjector)
async def find_security_groups(l:NetworkLink,  *, ainjector):
    desired_groups = getattr(l, 'aws_security_groups', None)
    if desired_groups is None:
        desired_groups = getattr(l.net, 'aws_security_groups', None)
        if desired_groups is None:
            desired_groups = l.net.injector.get_instance(InjectionKey('aws_security_groups', _optional=True))
    if desired_groups is None:
        desired_groups = ['default']

    if isinstance(desired_groups, str):
        raise ValueError(
            f"`{desired_groups}` is not a valid value for desired_groups. "
            "It must be an iterable of strings and not of type str."
        )

    groups = {g['GroupName']:g['GroupId'] for g in l.net_instance.vpc.groups}
    results = []
    for g in desired_groups:
        assert isinstance(g, str), f"Items in desired_groups must be a string. Got {g!r} instead."
        g_obj = await l.net.ainjector.get_instance_async(InjectionKey(AwsSecurityGroup, name=g, _optional=True))
        if not g_obj:
            g_obj = await ainjector.get_instance_async(InjectionKey(AwsSecurityGroup, name=g, _optional=True))
        if g_obj:
            results.append(g_obj.id)
        elif g in groups:
            results.append(groups[g])
        else:
            logger.error('%s is an unknown security group for %s', g, l)
    return results



@inject_autokwargs(connection=InjectionKey(AwsConnection,_ready=True))
class AwsVm(AwsManaged, Machine):

    pass_name_to_super = True

    @memoproperty
    def aws_ip_address_is_private(self):
        '''
        If True, then ip_address will be populated with the private address of the
        instance if it is not otherwise set in a subclass or the model.

        By default, this is True if :meth:`ssh_jump_host` is set, otherwise False.
        '''
        return bool(self.ssh_jump_host)

    def __init__(self, name, **kwargs):
        self.name = ""
        super().__init__(name=name, **kwargs)
        self.running = False
        self.closed = False
        self._operation_lock = asyncio.Lock()
        self._clear_ip_address = True
        self._user_data = None
        self.image_id = None
        self.iam_profile = None
        self.block_device_mappings = None

    async def user_data(self):
        '''
        In the case where :attr:`cloud_init` is not True, generate the instance's user data.
        This function is not called if :attr:`cloud_init` is True.
        By default it tries to see if the model has an attribute aws_user_data.
        '''
        if getattr(self, 'model', None):
            await self.model.async_become_ready()
            return getattr(self.model, 'aws_user_data', "")
        return ""

    def _find_ip_address(self):
        def async_cb():
            for network_link in updated_private_links:
                self.injector.emit_event(
                    InjectionKey(NetworkLink),
                    "address", network_link,
                    addl_keys=[InjectionKey(NetworkLink, host=self.name)])
            for network_link in updated_public_links:
                self.injector.emit_event(
                    InjectionKey(NetworkLink),
                    "public_address", network_link,
                    adl_keys=[InjectionKey(NetworkLink, host=self.name)])

        updated_public_links = []
        updated_private_links = []
        update_ip_address = False
        if self.__class__.ip_address is Machine.ip_address:
            try:
                self.ip_address # pylint: disable=pointless-statement
                update_ip_address = False
            except NotImplementedError:
                update_ip_address = True

        self.mob.wait_until_running()
        self.mob.reload()
        local_network_links = filter(lambda l: not l.local_type, self.network_links.values())
        for network_link, interface in zip(local_network_links, self.mob.network_interfaces):
            if network_link.net_instance.mob != interface.subnet:
                logger.warning(
                    'Instance %s: network links do not match instance interface for %s',
                    self.id, network_link.interface
                )
                continue
            private_address = IPv4Address(interface.private_ip_address)
            if private_address != network_link.merged_v4_config.address:
                network_link.merged_v4_config.address = private_address
                if update_ip_address and self.aws_ip_address_is_private:
                    self.ip_address = str(private_address)
                    self._clear_ip_address = True
                updated_private_links.append(network_link)
            if not interface.association_attribute:
                continue
            association = interface.association_attribute
            if not ('PublicIp' in association and association['PublicIp']):
                continue
            address = IPv4Address(association['PublicIp'])
            if address != network_link.merged_v4_config.public_address:
                if getattr(network_link,'vpc_address_allocation', None):
                    # Configured to use a specific elastic address
                    logger.info(
                        '%s associating elastic IP for %s',
                        self.id, network_link.merged_v4_config.public_address
                    )
                    self.connection.client.associate_address(
                        AllocationId=network_link.vpc_address_allocation,
                        NetworkInterfaceId=interface.id)
                else:
                    # public_v4_address being updated from association
                    network_link.merged_v4_config.public_address = address
            updated_public_links.append(network_link)
            if update_ip_address and not self.aws_ip_address_is_private:
                self.ip_address = str(address)
                self._clear_ip_address = True
        if updated_public_links or updated_private_links:
            self.ainjector.loop.call_soon_threadsafe(async_cb)

    async def find(self):
        await self.resolve_networking()
        futures = []
        loop = asyncio.get_event_loop()
        res = await super().find()
        if not (self.network_links or self.mob or self.readonly):
            raise RuntimeError('AWS instances require a network link to create')
        for l in self.network_links.values():
            futures.append(loop.create_task(l.instantiate(AwsSubnet)))
        await asyncio.gather(*futures)
        return res

    async def pre_create_hook(self):
        # operation lock is held by overriding find_or_create
        _cloud_init = getattr(self.model, 'cloud_init', False)
        _win_init = getattr(self.model, 'win_init', False)
        cloud_config = await self.ainjector(generate_cloud_init_cloud_config, model=self.model)

        if _cloud_init and _win_init:
            raise ValueError("Can not win_init and cloud_init at the same time.")
        if not _cloud_init and not _win_init:
            self._user_data = await self.user_data()

        if _cloud_init:
            user_data = "#cloud-config\n"
            user_data += yaml.dump(cloud_config.user_data, default_flow_style=False)
            self._user_data = user_data
            if self.ssh_online_command == Machine.ssh_online_command:
                self.ssh_online_command = 'systemctl --wait is-system-running'

        if _win_init:
            script_content = []
            password = cloud_config.user_data.get('password', None)
            if password:
                script_content.append(f"net user Administrator {password}")
            enable_winrm = cloud_config.user_data.get('winrm', False)
            if enable_winrm:
                script_content = script_content + [
                    "winrm quickconfig -q",
                    "winrm set winrm/config/service '@{{AllowUnencrypted=\"true\"}}'",
                    "winrm set winrm/config/service/auth '@{{Basic=\"true\"}}'",
                ]
            user_data = {
                'version': 1.0,
                'tasks': [
                    {
                        'task': 'executeScript',
                        'inputs': [
                            {
                                'frequency': 'always',
                                'type': 'powershell',
                                'runAs': 'localSystem',
                                'content': "\n".join(script_content),
                            }
                        ]
                    }
                ]
            }
            self._user_data = yaml.dump(user_data)

        self.image_id = await self.ainjector.get_instance_async('aws_ami')
        self.iam_profile = await self.ainjector.get_instance_async(InjectionKey("aws_iam_profile", _optional=True))
        await self.start_dependencies()
        await super().start_machine()
        if hasattr(self.model, 'disk_sizes'):
            self.block_device_mappings = await self.ainjector(generate_block_device_mappings)
        else: self.block_device_mappings = None
        for l in self.network_links.values():
            if l.local_type:
                continue
            l.security_group_ids = await self.ainjector(find_security_groups, l)
            if l.merged_v4_config.public_address:
                from .network import aws_link_handle_eip
                await aws_link_handle_eip(self, l)


    @setup_task("Create VM", order=AwsManaged.find_or_create.order)
    async def find_or_create(self, already_locked=False):
        async with contextlib.AsyncExitStack() as stack:
            if not already_locked:
                await stack.enter_async_context(self._operation_lock)
            return await super().find_or_create()

    find_or_create.check_completed_func = AwsManaged.find_or_create.check_completed_func


    def do_create(self):
        network_interfaces = []
        device_index = 0
        for l in self.network_links.values():
            if l.local_type:
                continue
            d = {
                'DeviceIndex': device_index,
                'Description': l.interface,
                'SubnetId': l.net_instance.id,
            }
            if l.merged_v4_config.address:
                assert l.merged_v4_config.address in l.net.v4_config.network.hosts(), (
                    f"{l.merged_v4_config.address} is not a hostaddr in "
                    f"{l.net.v4_config.network} for host {self.name}"
                )
                d['PrivateIpAddress'] = l.merged_v4_config.address.compressed
            if len(self.network_links) == 1 or l.merged_v4_config.public_address:
                d['AssociatePublicIpAddress'] = not l.merged_v4_config.public_address is False
            if hasattr(l, 'security_group_ids'):
                d['Groups'] = l.security_group_ids
            network_interfaces.append(d)
            device_index += 1

        logger.info('Starting %s VM', self.name)

        try:
            extra = {}
            key_name = self._gfi('aws_key_name', default=None)
            if key_name:
                extra['KeyName'] = key_name
            if self.block_device_mappings:
                extra['BlockDeviceMappings'] = self.block_device_mappings
            if self.iam_profile:
                extra['IamInstanceProfile'] = {"Name":self.iam_profile}
            r = self.connection.client.run_instances(
                ImageId=self.image_id,
                MinCount=1,
                MaxCount=1,
                InstanceType=self._gfi('aws_instance_type'),
                UserData=self._user_data,
                NetworkInterfaces=network_interfaces,
                TagSpecifications=self.resource_tags(),
                **extra
            )
            self.id = r['Instances'][0]['InstanceId']
            return True
        except ClientError as e:
            logger.error('Could not create AWS VM for %s because %s.', self.model.name, e)
            return False

    def find_from_id(self):
        # terminated instances do not count
        super().find_from_id()
        if self.mob:
            try:
                if self.mob.state['Name'] == 'terminated':
                    self.mob = None
                    return
            except AttributeError:
                # Sometimes for terminated instances, boto3 fails such
                # that self.mob exists but self.mob.meta does not and
                # so we cannot access the state.
                self.mob = None
                return
    async def post_find_hook(self):
        await self.is_machine_running()
        return await super().post_find_hook()

    async def dynamic_dependencies(self):
        result =  await NetworkedModel.dynamic_dependencies(self)
        # In addition to the AwsSubnets, we need to depend on any
        # security group we use.  It turns out calculating that is
        # harder than I want to spend time on, so as a stop-gap depend
        # on all security groups.
        result.extend(self.injector.filter(AwsSecurityGroup, ['name']))
        return result



    async def start_machine(self):
        async with self._operation_lock:
            await self.is_machine_running()
            if self.running:
                return
            await self.start_dependencies()
            await super().start_machine()
            if not self.mob:
                #presumably create since is_machine_running calls find already
                await self.find_or_create(already_locked=True)
                await self.is_machine_running()
                if self.running:
                    return
                logger.info('Starting %s', self.name)
            await run_in_executor(self.mob.start)
            await run_in_executor(self.mob.wait_until_running)
            await self.is_machine_running()
            return True


    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            await run_in_executor(self.mob.stop)
            await run_in_executor(self.mob.wait_until_stopped)
            if self._clear_ip_address:
                try:
                    del self.ip_address
                except AttributeError:
                    pass
            self.running = False
            await super().stop_machine()

    async def is_machine_running(self):
        if not self.mob:
            await self.find()
        if not self.mob:
            self.running = False
            return False
        self.running = self.mob.state['Name'] in ('pending', 'running')
        if self.running:
            # This needs to happen in a thread which has an asyncio
            # loop rather than in a executor thread.
            self.ssh_jump_host # pylint: disable=pointless-statement
            self.aws_ip_address_is_private # pylint: disable=pointless-statement
            await run_in_executor(self._find_ip_address)
        return self.running

    async def delete(self):
        await run_in_executor(self.mob.terminate)
        await run_in_executor(self.mob.wait_until_terminated)

    async def root_device_and_volume(self):
        ''':returns: tuple of root device and volume'''
        from .ebs import AwsVolume
        if not self.mob:
            await self.find()
        rootdev = self.mob.root_device_name
        for mapping in self.mob.block_device_mappings:
            if mapping['DeviceName'] == rootdev:
                return rootdev, await self.ainjector(
                    AwsVolume, id=mapping['Ebs']['VolumeId'], readonly=True)
        raise ValueError('Root device mapping not found')

    stamp_type = 'vm'
    resource_type = 'instance'
    resource_factory_method = 'Instance'

@inject()
class  LocalAwsVm(LocalMachineMixin, AwsVm):
    pass

__all__ += ['LocalAwsVm']

try:
    import carthage_base.hosted
    class MaybeLocalAwsVm(carthage_base.hosted.BareOrLocal):
        implementation = LocalAwsVm
        remote_implementation = AwsVm

except ImportError:
    class MaybeLocalAwsVm(Injectable):
        def __new__(cls, *args, **kwargs):
            raise NotImplementedError('MaybeLocalAwsVm requires carthage_base available')

__all__ += ['MaybeLocalAwsVm']

# At the end so that network can inject an AwsVm
from .network import  AwsSubnet, AwsSecurityGroup
AwsVm.network_implementation_class = AwsSubnet
