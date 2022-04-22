pytest_plugins = ('carthage.pytest_plugin',)


import asyncio, logging


import pytest
import carthage.ssh
from carthage import *
from carthage_aws import *
from carthage.modeling import *
from carthage.pytest import *

# The boto logging is way too verbose
logging.getLogger('boto3').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)


@pytest.fixture(scope='session')
def carthage_layout(loop):
    from layout import test_layout
    injector = base_injector.claim("AWS Layout")
    ainjector = injector(AsyncInjector)
    ainjector.add_provider(InjectionKey(CarthageLayout), test_layout)
    layout = loop.run_until_complete(ainjector.get_instance_async(CarthageLayout))
    lainjector = layout.ainjector
    loop.run_until_complete(lainjector.get_instance_async(AwsConnection))
    loop.run_until_complete(lainjector.get_instance_async(carthage.ssh.SshKey))
    yield layout
    loop.run_until_complete(shutdown_injector(ainjector))

