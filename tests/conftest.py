pytest_plugins = ('carthage.pytest_plugin',)

import logging
import pytest
import carthage.ssh
from carthage import *
from carthage.modeling import *
from carthage.pytest import *



@pytest.fixture(scope='session')
def carthage_layout(loop):
    from layout import test_layout
    injector = base_injector.claim("AWS Layout")
    # The boto logging is way too verbose
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    from carthage_aws import AwsConnection
    ainjector = injector(AsyncInjector)
    ainjector.add_provider(InjectionKey(CarthageLayout), test_layout)
    layout = loop.run_until_complete(ainjector.get_instance_async(CarthageLayout))
    lainjector = layout.ainjector
    loop.run_until_complete(lainjector.get_instance_async(AwsConnection))
    loop.run_until_complete(lainjector.get_instance_async(carthage.ssh.SshKey))
    yield layout
    if getattr(layout, 'do_cleanup', True):
        loop.run_until_complete(lainjector(cleanup))
    loop.run_until_complete(shutdown_injector(ainjector))

@inject(ainjector=AsyncInjector)
async def cleanup(ainjector):
    from carthage_aws import AwsImage
    result = await ainjector(run_deployment_destroy)
    print(result)
    try:
        while True:
            aws_image = await ainjector(AwsImage, name='test-ami*')
            if not aws_image.mob:
                break
            await aws_image.delete()
    except (LookupError, NotImplementedError):
        pass
