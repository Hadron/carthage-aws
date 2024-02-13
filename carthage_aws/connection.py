# Copyright (C) 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import asyncio
import os
import typing

import carthage.network
from carthage import *
from carthage.config import ConfigLayout
from carthage.dependency_injection import *
from carthage.modeling import propagate_key, CarthageLayout
import boto3
from botocore.exceptions import ClientError

#: Mapping of resource_types to classes that implement them
aws_type_registry: dict[str,'AwsManaged'] = {}

__all__ = ['AwsConnection', 'AwsManaged']


async def run_in_executor(func, *args):
    return await asyncio.get_event_loop().run_in_executor(None, func, *args)

@inject_autokwargs(config_layout=ConfigLayout)
class AwsConnection(AsyncInjectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = self.config_layout.aws
        self.connection = None
        self.region = None
        self.client = None
        self.keys = []
        self.vpcs = []
        self.igs = []
        self.subnets = []
        self.names_by_resource_type = {}


    async def _tag_filter(self, strict):
        '''Produce tag filter; see :meth:`AwsTagProvider.tag_filter`
        :param strict: If true, then tag filters should be as strict
        as possible to prevent falso positives for orphans.  If not
        strict, tag filters may be permissive to adopt objects as
        tagging policy changes.

        '''
        providers_res = await self.ainjector.filter_instantiate_async(AwsTagProvider, ['name'])
        providers: list[AwsTagProvider] = [x[1] for x in providers_res]
        tags: dict[str, set[str]] = {}
        for provider in providers:
            for k, values in provider.tag_filter(strict).items():
                tags.setdefault(k, set())
                tags[k] |= set(values)
        result = []
        for k, values in tags.items():
            result.append({
                'Name': 'tag:'+k,
                'Values': list(values),
                })
        return result

    async def inventory(self):
        self.connection = boto3.Session(
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            profile_name=self.config.profile if self.config.profile else None
        )
        self.region = self.config.region
        self.client = self.connection.client('ec2', region_name=self.region)
        for key in self.client.describe_key_pairs()['KeyPairs']:
            self.keys.append(key['KeyName'])
        tag_filter = await self._tag_filter(False)
        self.names_by_resource_type = await run_in_executor(self._inventory, tag_filter)

    def _inventory(self, tag_filter):
        # Executor context
        nbrt = {}
        # describe_tags won't do a join for us so we need to do the
        # intersection ourselves.
        our_resources:set[str]|None = None
        for component in tag_filter:
            r = self.client.describe_tags(Filters=[component])
            if our_resources is None:
                # First pass, populate from the tag results.
                our_resources = set(resource['ResourceId'] for resource in r['Tags'])
            else:
                # subsiquent passes: intersect
                our_resources &= set(resource['ResourceId'] for resource in r['Tags'])

        r = self.client.describe_tags(
            Filters=[{'Name':'key', 'Values':['Name']},
                     ])
        for resource in r['Tags']:
            rt, name, rid = resource['ResourceType'], resource['Value'], resource['ResourceId']
            if our_resources is not None and rid not in our_resources:
                continue #Not a resource we manage
            rt = rt.replace('-','_')
            nbrt.setdefault(rt, {})
            nbrt[rt].setdefault(name, set())
            nbrt[rt][name].add(rid)

        r = self.client.describe_vpcs()
        for v in r['Vpcs']:
            vpc = {'id': v['VpcId']}
            if 'Tags' in v:
                for t in v['Tags']:
                    if t['Key'] == 'Name':
                        vpc['name'] = t['Value']
            else: vpc['name'] = ''
            self.vpcs.append(vpc)

        r = self.client.describe_internet_gateways()
        for ig in r['InternetGateways']:
            if len(ig['Attachments']) == 0:
                continue
            a = ig['Attachments'][0]
            if a['State'] == 'attached' or a['State'] == 'available':
                self.igs.append({'id': ig['InternetGatewayId'], 'vpc': a['VpcId']})


        r = self.client.describe_subnets()
        for s in r['Subnets']:
            subnet = {'CidrBlock': s['CidrBlock'], 'id': s['SubnetId'], 'vpc': s['VpcId']}
            self.subnets.append(subnet)
        return nbrt


    async def async_ready(self):
        await self.inventory()
        return await super().async_ready()

    def invalid_ec2_resource(self, resource_type, resource_id, *, name=None):
        '''
        Indicate that a given resource does not (and will not) exist.
        Clean it out of our caches and untag it.
        Run in executor context.
        '''
        if name:
            names = self.names_by_resource_type.get(resource_type)
            if names and name in names:
                names[name].remove(resource_id)

        self.client.delete_tags(Resources=[resource_id])


@inject_autokwargs(config_layout=ConfigLayout,
                   connection=InjectionKey(AwsConnection, _ready=True),
                                      readonly = InjectionKey("aws_readonly", _optional=NotPresent),
                   id=InjectionKey("aws_id", _optional=NotPresent),
                   )
class AwsManaged(SetupTaskMixin, AsyncInjectable):

    pass_name_to_super = False # True for machines
    name = None
    id = None
    readonly = None

    def __init__(self, *, name=None, **kwargs):
        if name and self.pass_name_to_super:
            kwargs['name'] = name
        if name:
            self.name = name
        super().__init__(**kwargs)
        if self.readonly is None: # pylint: disable=access-member-before-definition
            self.readonly = bool(self.id)
        self.mob = None


    @memoproperty
    def stamp_type(self):
        raise NotImplementedError(type(self))

    @property
    def resource_type(self):
        '''The resource type associated with tags'''
        raise NotImplementedError

    @memoproperty
    def service_resource(self):
        # override for non-ec2
        return self.connection.connection.resource('ec2', region_name=self.connection.region)

    @memoproperty
    def resource_types_to_tag(self)-> list:
        '''
        Which resource types to include in tag specifications.  Defaults to self.resource_type
        '''
        return [self.resource_type]

    def resource_tags(self):
        '''Return a TagSpecification.  If the object has a name, includes a Name tag in the specification.
        See :class:`AwsTagProvider` for other tags.

        Typically resources of types included in
        :attr:`resource_types_to_tag` are tagged. That defaults to
        :attr:`resource_type` but for example could be extended on a
        :class:`~.vm.AwsVm` to include *volume* as well as *instance*
        to tag the initially created volume.

        '''
        filter_res = self.injector.filter_instantiate(AwsTagProvider, ['name'])
        tag_providers: list[AwsTagProvider]  = [x[1] for x in filter_res]
        results = []
        for resource_type in self.resource_types_to_tag:
            tags = []
            if self.name:
                tags.append({"Key":"Name", "Value":self.name})
            for provider in tag_providers:
                for k, v in provider.resource_tags(self, resource_type).items():
                    tags.append({
                        'Key': k,
                        'Value': v})
            results.append( {
                "ResourceType":resource_type.replace('_','-'),
                "Tags":tags
        })
        return results

    def find_from_id(self):
        #called in executor context; create a mob from id
        assert self.id
        resource_factory = getattr(self.service_resource, self.resource_factory_method)
        self.mob = resource_factory(self.id)
        try:
            self.mob.load()
        except ClientError as e:
            if hasattr(self.mob, 'wait_until_exists'):
                logger.info('Waiting for %s to exist', repr(self.mob))
                self.mob.wait_until_exists()
                self.mob.load()
            else:
                logger.warning('Failed to load %s', self, exc_info=e)
                self.mob = None
                if not self.readonly:
                    self.connection.invalid_ec2_resource(self.resource_type, self.id, name=self.name)
        return self.mob

    #pylint: disable =redefined-builtin
    @classmethod
    @inject(injector=Injector)
    def from_id(cls, id, name, *, injector):
        '''Given an ID, construct a not-ready instance of *cls* that
        is readonly.. For most subclasses of :class:`AwsManaged` this
        class method is equivalent to::

            injector(cls, id=id, name=name, readonly=True)

        This method exists so that classes like
        :class:`~.network.AwsSubnet` which need special handling can
        properly be instantiated.

        This is typically called from
        :meth:`AwsDeployableFinder.find_orphans` and in that context
        name is typically available. Significantly better results are
        provided if name can be passed in; if *name* is unavailable,
        pass in ``None``.

        This method is an alternate constructor.  It is different from
        :meth:`find_from_id`, which is the part of :meth:`find` that
        runs in executor context.  That method is part of connecting
        an instance of :class:`AwsManaged` with the underlying boto3
        object representing a resource in AWS.

        '''
        with instantiation_not_ready():
            return injector(cls, id=id, name=name, readonly=True)

    async def find(self):

        '''
        Find ourself from a name or id
        '''
        if self.id:
            return await run_in_executor(self.find_from_id)
        if self.name:
            for resource_id in await self.possible_ids_for_name():
                # use the first viable
                self.id = resource_id
                await run_in_executor(self.find_from_id)
                if self.mob:
                    return
            self.id = None

    async def possible_ids_for_name(self):
        resource_type = self.resource_type
        try:
            names = self.connection.names_by_resource_type[resource_type]
        except KeyError:
            return []
        if self.name in names:
            return list(names[self.name])
        return []

    def __repr__(self):
        if self.name:
            return f'<{self.__class__.__name__} ({self.name}) at 0x{id(self):0x}>'
        return f'<anonymous {self.__class__.__name__} at 0x{id(self):0x}>'

    def __str__(self):
        try:
            result = self.resource_type+':'
            if self.name:
                result += self.name
            elif self.id:
                result += self.id
            return result
        except Exception: # pylint: disable=broad-except
            return super().__str__()

    @setup_task("construct", order=700)
    async def find_or_create(self): #type: ignore

        if self.mob:
            return

        # If we are called directly, rather than through setup_tasks,
        # then our check_completed will not have run, so we should
        # explicitly try find, because double creating is bad.

        await self.find()

        if self.mob:
            if not self.readonly:
                await self.ainjector(self.read_write_hook)
            await self.ainjector(self.post_find_hook)
            return

        if not self.name:
            raise RuntimeError(f'unable to create AWS resource for {self} without a name')
        if self.readonly:
            raise LookupError(f'unable to find AWS resource for {self} and creation was not enabled')

        await self.ainjector(self.pre_create_hook)
        await run_in_executor(self.do_create)

        if not (self.mob or self.id):
            raise RuntimeError(f'do_create failed to create AWS resource for {self}')

        if not self.mob:
            await self.find()
        elif not self.id:
            self.id = self.mob.id

        await self.ainjector(self.post_create_hook)
        await self.ainjector(self.read_write_hook)
        await self.ainjector(self.post_find_hook)
        return self.mob

    @find_or_create.check_completed()
    # pylint: disable=function-redefined
    async def find_or_create(self):
        await self.find()
        if self.mob:
            if not self.readonly:
                await self.ainjector(self.read_write_hook)
            await self.ainjector(self.post_find_hook)
            return True
        return False

    def _gfi(self, key, default="error"):
        '''
        get_from_injector.  Used to look up some configuration in the model or its enclosing injectors.
        '''
        k = InjectionKey(key, _optional=default != "error")
        res = self.injector.get_instance(k)
        if res is None and default != "error":
            res = default
        return res

    def do_create(self):
        '''
        Run in executor context.  Do the actual creation.  Cannot do async things.
        Do any necessary async work in pre_create_hook.
        '''
        raise NotImplementedError

    async def pre_create_hook(self):
        '''
        Any async tasks that need to be performed before do_create is called in executor context.
        May have injected dependencies.
        '''

    async def post_create_hook(self):
        '''
        Any tasks that should be performed in async context after creation.
        May have injected dependencies.
        '''

    async def post_find_hook(self):
        '''
        Any tasks performed in async context after an object is found or created.
        May have injected dependencies.  If you need to perform tasks before find,
        simply override :meth:`find`.
        This hook MUST NOT modify the object if self.readonly is True.

        In general, any tasks that may modify the object should be run in
        :meth:`read_write_hook` which runs before *post_find_hook*.
        '''

    async def read_write_hook(self):
        '''
        A hook for performing tasks that may modify the state of an
        object such as reconciling expected configuration with actual configuration.
        Called before :meth:`post_find_hook` when an object is found or created,
        but only when *readonly* is not true.
        '''


    @memoproperty
    def stamp_path(self):
        p = Path(self.config_layout.state_dir)
        p = p.joinpath("aws_stamps", self.stamp_type,str(self.id)+".stamps")
        os.makedirs(p, exist_ok=True)
        return p

    async def dynamic_dependencies(self):
        # for Deployable interface
        return []


    def aws_propagate_key(cls): #type: ignore pylint: disable=no-self-argument
        '''
        Returns an :class:`InjectionKey`  that will
        :func:`propagate up <carthage.modeling.propagate_key>` so that
        :mod:`deployment <carthage.deployment>` can find the deployable.

        Note that this method is sometimes called as if it were a class method although
        it must not be declared as such.  That is, sometimes *cls* is a class and
        sometimes an instance:

        * called  as ``cls.aws_propagate_key(cls)`` in :meth:`default_class_injection_key`
          and :meth:`__init_subclass__`.

        * called with an instance in :meth:`default_instance_injection_key`.

        This method can raise *AttributeError* if it is unable to determine the key to
        propagate, for example if ``cls.name`` is None.
        This method should be overridden in subclasses if the default is not correct.
        '''
        if cls.name is None:
            raise AttributeError('Name not yet set')
        constraint = {cls.resource_type+'_name':cls.name}
        target = aws_type_registry[cls.resource_type]
        return InjectionKey(target, **constraint)

    @classmethod
    def default_class_injection_key(cls):
        try:
            return cls.aws_propagate_key(cls)
        except AttributeError:
            return super().default_class_injection_key()

    def  default_instance_injection_key(self):
        return self.aws_propagate_key()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.resource_type not in aws_type_registry:
            aws_type_registry[cls.resource_type] = cls
        try:
            propagate_key(cls.aws_propagate_key(cls), cls)
        except AttributeError:
            pass



async def wait_for_state_change(obj, get_state_func, desired_state:str, wait_states: list[str]):
    '''
    Wait for a state transition, generally for objects without a boto3 resource implementation.
    So *get_state_func* typically decomposes whatever :meth:``find_from_id` puts in *mob*.

    :param obj: An :class:`AwsManaged` implementing *find_from_id* which we will use as a
    reload function.

    :param get_state_func: A function to get the current state from *mob*, possibly
    something like `lambda obj:obj.mob['State']`

    :param desired_state: The state that counts as success.

    :param wait_states:  If one of these states persists, then continue to wait.

    '''
    timeout = 300 # Turn this into a parameter if we need to adjust.
    state = get_state_func(obj)
    logged = False
    while timeout > 0:
        if state == desired_state:
            return
        if state not in wait_states:
            raise RuntimeError(f'Unexpected state for {obj}: {state}')
        if not logged:
            logger.info('Waiting for %s to enter %s state', obj, desired_state)
            logged=True
        await asyncio.sleep(5)
        timeout -= 5
        await run_in_executor(obj.find_from_id)
        state = get_state_func(obj)
    raise RuntimeError(f'{obj}: {state=} is not desired state {desired_state}')

class AwsDeployableFinder(DeployableFinder):
    '''
    Find any :class:`AwsManaged`.  Also, for any VPC, explicitly instantiate any networks
    contained within the VPC as an AwsSubnet.
    '''

    name = 'aws'

    async def find(self, ainjector):
        from .network import AwsVirtualPrivateCloud, AwsSubnet # pylint: disable=relative-beyond-top-level,import-outside-toplevel
        subnets = []
        # Instantiating allow_multiple keys like AwsSubnet with
        # filter_instantiate almost certainly leads to Deployable
        # duplication, so don't.
        results = await ainjector.filter_instantiate_async(
            None,
            lambda k:
                isinstance(k.target, type) and issubclass(k.target, AwsManaged) and not issubclass(k.target, AwsSubnet),
            ready=False,
            stop_at=ainjector
        )
        for _, obj in results:
            if isinstance(obj,AwsVirtualPrivateCloud):
                networks = await obj.ainjector.filter_instantiate_async(
                    None,
                    lambda k: isinstance(k.target, type) and issubclass(k.target, carthage.Network),
                    stop_at=obj.ainjector,
                    ready=False)
                for _, network in networks:
                    subnets.append(await network.access_by(AwsSubnet, ready=False))
        return subnets + [x[1] for x in results]

    async def find_orphans(self, deployables):
        '''
        This method looks at the inventory maintained by
        :class:`AwsConnection`. That inventory collects all the
        resource IDs for :class:`AwsManaged` objects previously
        produced by the current layout.  This method collects the
        resource IDs from the input deployables and subtracts them
        from the resource IDs found from the inventory. Any remaining
        resource IDs represent orphans. These objects are instantiated
        readonly and returned.

        '''
        deployed_ids = set()
        connection = await self.ainjector.get_instance_async(AwsConnection)
        await connection.async_become_ready()
        if not (tag_filter := await connection._tag_filter(True)): # pylint: disable=protected-access
            logger.info('AWS orphan detection unavailable because no tag filter set; consider setting layout_name')
            return []
        #pylint: disable=protected-access
        names_by_resource_type = await run_in_executor(connection._inventory, tag_filter)
        for d in deployables:
            if not isinstance(d, AwsManaged):
                continue
            assert d.id is not None, f'{d} reached find_orphans without and id'
            deployed_ids.add(d.id)
        results = []
        for rt, name_ids in names_by_resource_type.items():
            if not (cls := aws_type_registry.get(rt)):
                continue
            for name, ids in name_ids.items():
                with instantiation_not_ready():
                    ids = ids - deployed_ids
                    for x in ids:
                        results.append(self.injector(cls.from_id, x, name))
        return results

class AwsTagProvider(Injectable):

    '''
        An AwsTagProvider provides tags for resources to conform to some sort of schema.  Usage might include:

    * Tracking which objects are created by a given layout so that
      orphans can be detected; see :class:`LayoutTagProvider`.

    * Track billing and asset allocation according to local needs.

    * Track aspects of a model to know when reconfiguration is required.

    When a :class:`AwsManaged` is created,
    :meth:`AwsManaged.resource_tags` instatiates all tag providers in
    the local injector using
    :meth:`~carthage.Injector.filter_instantiate`.  Then it calls
    :meth:`resource_tags` on the providers and collects together
    appropriate tags.

    TagProviders are also instantiated by :class:`AwsConnection`
    during inventory.  Then :meth:`tag_filter` is called to narrow
    down the set of resources that may belong to the connection.  This
    limits resources that will be considered by
    :meth:`Awsconnaction.possible_ids_for_name`.

    Typical usage is to subclass this class and then::

        base_injector.add_provider(SomeTagProviderSubclass)

    However, note that where inn the injector hierarchy the
    *AwsTagProvider* is added affects which tag providers will be used
    in what circumstances as well as what dependencies can be provided
    by the tag provider's :class:`Injector`:

    * Since :class:`AwsConnection` is typically added to
      *base_injector*, in this configuration, only tag providers
      *registered at the base injector will have their
      *:meth:`tag_filter` methods called.  If tag providers are
      *registered lower in the hierarchy that need to contribute to
      *the tag filter, then :class:`AwsConnection` should also be
      *registered lower in the hierarchy.

    * If a tag provider is added to an injector with *allow_multiple*
      False, then that tag provider can only access dependencies
      provided by the injector where it is added. As an example, a tag
      provider added at the base injector could not access modeling
      keys propagated up to the injector of a
      :class:`~carthage.modeling.CarthageLayout`.  If *allow_multiple*
      is set to true, at least when :meth:`resource_tags` is called,
      the tag provider can access any dependencies available to the
      tagged resource.

    * Even if the tag provider is added with *allow_multiple* True,
      then :meth:`tag_filter` is limited in what dependencies it can
      access because again :class:`AwsConnection` tends to be high in
      the hierarchy.  Having too many AWS connections will impact
      performance: :class:`AwsConnection` is expensive to instantiate.

    *If tag providers are added with *allow_multiple* True, then there
      will be multiple instances of the provider.  This may impact
      performance.

    See :class:`LayoutTagProvider` for a practical discussion of these implications.

    '''

    # A name under which the tag provider is registered.
    name: typing.ClassVar[str]

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(AwsTagProvider, name=cls.name)

    # pylint: disable=unused-argument
    def tag_filter(self, strict:bool)-> dict[str, list[str]]:
        '''Returns a dictionary of key-value pairs.  Keys are tags,
        and values are lists of acceptable values.  Called by
        :class:`AwsConnection` to set up a filter that excludes
        objects that should not be considered for name-to-id mapping.
        See class documentation for caveats around which
        AwsTagProviders are used based on injector positioning between
        the tag provider and :class:`AwsConnection`.

        :param strict: If true, return a strict filter to avoid false
        positives for orphan detection.  If False, permissive tag
        filters may be used to pick up objects after tagging policy
        changes.

        '''
        return {}

    # pylint: disable=unused-argument
    def resource_tags(self, resource:AwsManaged, resource_type:str)-> dict[str,str]:
        '''The tags a given resource should have. Returns a dict mapping tag keys to their value.

        :param resource: the resource being tagged.

        :param resource_type: The resource type that is currently
        being tagged.  A create operation may tag multiple types of
        resources.  As an example, running an instance typically also
        creates EBS volumes and network interfaces.

        '''
        return {}

__all__ += ['AwsTagProvider']

@inject_autokwargs(injector=Injector)
class LayoutTagProvider(AwsTagProvider):

    '''Tags resources based on their membership in a :class:`~carthage.modeling.CarthageLayout`.

    This tag provider is added at the base injector as part of the
    initialization of the AWS plugin. As a result, for creating a tag
    filter, it will only find a layout that is available at the base
    injector.  Since ``carthage-runner`` also finds its layout at the
    base injector, it is typical for layouts to be registered there.

    In contrast, resource_tags looks for a layout on the object being tagged.

    Note that even if a layout can be found, the default behavior is
    not to create a tag filter but instead to **adopt** resources that
    have the same name as the current layout would produce but that
    are not tagged by the layout.  If a tag filter is desired, execute
    the following::

        base_injector.add_provider(carthage_aws_layout_adopt_resources, False)

    '''

    name = 'layout'

    def resource_tags(self, resource, resource_type):
        try:
            layout = resource.injector.get_instance(CarthageLayout)
        except (KeyError, AsyncRequired):
            return {}
        if not layout.layout_name:
            return {}
        return {
            'carthage:layout': layout.layout_name,
            }

    def tag_filter(self, strict):
        try:
            config = self.injector(ConfigLayout)
            if config.layout_name:
                layout = self.injector.get_instance(InjectionKey(
                    CarthageLayout, layout_name=config.layout_name,
                    _ready=True))
            else:
                layout = self.injector.get_instance(InjectionKey(CarthageLayout, _ready=False))
            adopt = self.injector.get_instance(InjectionKey(carthage_aws_layout_adopt_resources, _optional=True))
            if adopt is None:
                adopt = True
        except (KeyError, AsyncRequired):
            return {}
        if (adopt  and not strict) or not layout.layout_name:
            return {}
        return {
            'carthage:layout': [layout.layout_name],
            }

#: An injection key to configure whether the layout adopts resources
#that have a name produced by the layout but are not tagged as having
#a layout.  Layouts should adopt resources if they predate support for
#the layout filtag provider, or if they are moving from no layout_name to
#a layout_name.
carthage_aws_layout_adopt_resources = InjectionKey('carthage_aws.adopt_resources')

__all__ += ['carthage_aws_layout_adopt_resources']
