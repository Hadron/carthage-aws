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
async def test_base_vm(carthage_layout):
    layout = carthage_layout
    try:
        await layout.generate()
        await layout.ainjector.get_instance_async(AwsConnection)
        await layout.test_vm.machine.async_become_ready()
        await layout.test_vm.machine.is_machine_running()
        await layout.test_vm.machine.ssh_online()
    finally:
        try: layout.test_vm.machine.mob.terminate()
        except Exception: pass
    
                           
@async_test
async def test_read_only(carthage_layout):
    layout = carthage_layout
    await layout.ainjector.get_instance_async(AwsConnection)
    with pytest.raises(LookupError):
        await layout.does_not_exist.machine.async_become_ready()
        
@async_test
async def test_create_volume(carthage_layout):
    layout = carthage_layout
    con = await layout.ainjector.get_instance_async(AwsConnection)
    vol = None
    try:
        vol = await layout.ainjector.get_instance_async('some_volume')
    finally:
        if vol: await vol.delete()

@async_test
async def test_attach_volume(carthage_layout):
    layout = carthage_layout
    con = await layout.ainjector.get_instance_async(AwsConnection)
    vol = None
    try:
        instance =  layout.instance_for_volume
        await instance.machine.async_become_ready()
        instance.injector.add_provider(InjectionKey('aws_availability_zone'), instance.machine.mob.placement['AvailabilityZone'])
        vol = await instance.ainjector.get_instance_async('volume')
        await vol.attach(instance=instance, device="xvdi")
        vol = None
    finally:
        if vol: await vol.delete()
        await         instance.machine.delete()

@async_test
async def test_start_machine(carthage_layout):
    layout = carthage_layout
    con = await layout.ainjector.get_instance_async(AwsConnection)
    try:
        instance =  layout.test_no_ready
        await instance.machine.start_machine()
    finally:
        await instance.machine.delete()

@async_test
async def test_image_building(carthage_layout, request):
    layout = carthage_layout
    con = await layout.ainjector.get_instance_async(AwsConnection)
    try:
        instance =  layout.image_builder
        await instance.async_become_ready()
        await instance.machine.async_become_ready()
        #instance.machine.ssh('-A', _fg=True)
        #breakpoint()
        await subtest_controller(
            request, instance.machine,
            ["--carthage-config=/carthage_aws/config.yml",
             "/carthage_aws/tests/inner_image_builder.py"],
            python_path="/carthage:/carthage_aws",
            ssh_agent=True)
        
    finally:
        await instance.machine.delete()
