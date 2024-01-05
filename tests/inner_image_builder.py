# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import asyncio, logging


import pytest
from carthage import *
from carthage_aws import *
from carthage.modeling import *
from carthage.pytest import *

@async_test
async def test_actually_build_image(carthage_layout):
    layout = carthage_layout
    layout.do_cleanup = False
    instance = layout.image_builder.machine
    await instance.is_machine_running()
    with TestTiming(1300):
        await layout.ainjector(build_ami,
                               name="test-ami",
                               add_time_to_name=True)
    
