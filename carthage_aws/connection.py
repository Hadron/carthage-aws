import logging

from carthage import *
from carthage.modeling import *
from carthage.config import ConfigLayout
from carthage.dependency_injection import *

import boto3
from botocore.exceptions import ClientError


@inject(config = ConfigLayout, injector = Injector)
class AwsConnection(Injectable):

    def __init__(self, config, injector):
        self.config = config.aws
        self.injector = injector
        super().__init__()
        self.connection = None
        self.connection = boto3.Session(
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key
        )
        self.region = self.config.region
        self.client = self.connection.client('ec2', region_name=self.region)
        self.keys = []
        for key in self.client.describe_key_pairs()['KeyPairs']:
            self.keys.append(key['KeyName'])