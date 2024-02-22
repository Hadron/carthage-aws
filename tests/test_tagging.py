# Copyright (C) 2022-2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

#pylint: disable=redefined-outer-name

import pytest

from carthage import *
from carthage.modeling import *
from carthage.pytest import *

from carthage_aws import *
from carthage_aws import enable_new_aws_connection

class layout_1(CarthageLayout):
    layout_name = 'layout_1'

    # it is important that the layout 1 and layout 2 vpc names are the
    # same; we test that with the tag filter enabled, there is no
    # cross-adoption and with the tagfilter disabled, there is.
    class vpc(AwsVirtualPrivateCloud):
        name = 'tagfilter_vpc'
        vpc_cidr = '10.0.0.0/17'

class layout_2(CarthageLayout):
    layout_name = 'layout_2'

    class vpc(AwsVirtualPrivateCloud):
        name = 'tagfilter_vpc'
        vpc_cidr = '10.0.0.0/17'

@pytest.fixture()
def our_ainjector(ainjector):
    injector = ainjector.injector.claim("tagging tests")
    injector.add_provider(carthage_aws_layout_adopt_resources, False)
    # so the tag provider picks up the config setting even though not on base injector
    injector.add_provider(layout_1)
    injector.add_provider(layout_2)
    ainjector = injector(AsyncInjector)
    yield ainjector
    ainjector.loop.run_until_complete(shutdown_injector(injector))

async def setup_layout(layout):
    ainjector = layout.ainjector
    await ainjector(enable_new_aws_connection)
    layout.connection = await ainjector.get_instance_async(AwsConnection)

@async_test
async def test_tag_filter(our_ainjector):
    ainjector = our_ainjector
    l1 = await ainjector.get_instance_async(InjectionKey(CarthageLayout, layout_name='layout_1'))
    l2 = await ainjector.get_instance_async(InjectionKey(CarthageLayout, layout_name='layout_2'))
    await setup_layout(l1)
    await setup_layout(l2)
    print(f'Layout 1 tag filter: {await l1.connection._tag_filter(False)}') # pylint: disable=protected-access
    try:
        await l1.vpc.async_become_ready()
        await l2.vpc.async_become_ready()
        assert l1.vpc.id != l2.vpc.id
    finally:
        res =             await l1.ainjector(run_deployment_destroy)
        print("L1 cleanup report:\n"+str(res))
        res =             await l2.ainjector(run_deployment_destroy)
        print("L2 cleanup report:\n"+str(res))
