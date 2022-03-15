# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio

import pytest

from carthage import *
from carthage_aws import *
from carthage.modeling import *
from carthage.pytest import *

from layout import test_layout
@pytest.fixture(scope='session')
def carthage_layout(loop):
    injector = base_injector.claim("AWS Layout")
    ainjector = injector(AsyncInjector)
    ainjector.add_provider(InjectionKey(CarthageLayout), test_layout)
    layout = loop.run_until_complete(ainjector.get_instance_async(CarthageLayout))
    yield layout
    loop.run_until_complete(shutdown_injector(ainjector))

@async_test
async def test_start_machine(carthage_layout):
    layout = carthage_layout
    await layout.generate()
    await layout.ainjector.get_instance_async(AwsConnection)
    await layout.test_vm.machine.async_become_ready()
    await layout.test_vm.machine.is_machine_running()
    layout.test_vm.machine.mob.terminate()
    
                           
@async_test
async def test_read_only(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    with pytest.raises(LookupError):
        await layout.does_not_exist.machine.async_become_ready()
        
