import carthage
from carthage.dependency_injection import *
from carthage.config import ConfigSchema
from carthage.config.types import ConfigString

from .connection import AwsConnection


class AwsConfig(ConfigSchema, prefix = "aws"):
    #aws_access_key_id
    access_key_id: ConfigString

    #aws_secret_access_key
    secret_access_key: ConfigString

    #AWS region
    region: ConfigString


@inject(injector=Injector)
def enable_new_aws_connection(injector):
    conn = AwsConnection
    injector.add_provider(InjectionKey(AwsConnection), conn)

@inject(injector=Injector)
def carthage_plugin(injector):

    injector(enable_new_aws_connection)