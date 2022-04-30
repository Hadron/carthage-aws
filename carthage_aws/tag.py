from carthage.config import ConfigLayout, config_key
from carthage.dependency_injection import Injectable, InjectionKey, inject_autokwargs
from carthage.modeling import Enclave

from .network import AwsVirtualPrivateCloud, AwsSubnet

__all__ = ['AwsTagsProvider']

@inject_autokwargs(
    logical_id=InjectionKey(config_key('aws.tags.logical_id'), _optional=True),
    stack_id=InjectionKey(config_key('aws.tags.stack_id'), _optional=True),
    stack_name=InjectionKey(config_key('aws.tags.stack_name'), _optional=True)
)
class AwsTagsProvider(Injectable):

    cloudkw = ['logical_id', 'stack_id', 'stack_name']

    def __init__(self, **kwargs):
        self.tags = {}

        for kw in self.cloudkw:
            if kw in kwargs and kwargs[kw] is not None:
                k = kwargs.pop(kw)
                self._injection_autokwargs.remove(kw)
                assert type(k) is str,f'{kw}={k} for {self} must be str'
                self.tags[f'carthage:cloudformation:{kw}'] = k

        super().__init__(**kwargs)
