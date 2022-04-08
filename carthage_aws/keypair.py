# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *
from carthage.dependency_injection import *
from carthage.network import TechnologySpecificNetwork, this_network
from carthage.config import ConfigLayout
from carthage.modeling import NetworkModel

from .connection import AwsConnection, AwsManaged, run_in_executor

import boto3
from botocore.exceptions import ClientError

import os
import base64
import struct

import boto3
import paramiko
from paramiko.util import deflate_long

__all__ = ['AwsKeyPair']

# Using 'keyfile' is probably the wrong idea.  We only need the pubkey
# and we want to be able to get it from a file, from a secret store,
# or generate on AWS and store to a file or secret store.  This will
# do for a start though.

class AwsKeyPair(AwsManaged):

    stamp_type = 'key_pair'
    resource_type = 'key_pair'

    def __init__(self, *, keyfile=None, **kwargs):

        super().__init__( **kwargs)

        c_aws = self.config_layout.aws

        if keyfile is not None:
            assert not hasattr(self, 'model')
            self.keyfile = keyfile
        else:
            self.keyfile = getattr(getattr(self, 'model', object()), 'keyfile', None)

        if self.keyfile is None:
            self.keyfile = self.injector.get_instance(InjectionKey('config/aws.keypair_keyfile'))

        if self.name is None:
            self.name = getattr(getattr(self, 'model', object()), 'name', None)

        if self.name is None:
            self.name = c_aws.keypair_name

        if self.id is None:
            self.id = getattr(getattr(self, 'model', object()), 'id', None)

        if self.id is None:
            self.id = c_aws.keypair_id

        if not (self.name or self.id):
              raise ValueError("must specify either a name or an id")

    def find_from_id(self):
        assert self.id
        resource_factory = getattr(self.service_resource, 'KeyPair')
        self.mob = resource_factory(self.name)
        self.mob.load()
        return self.mob

    def do_create(self):
        key = paramiko.RSAKey.from_private_key_file(self.keyfile)
        keydata = key.get_name().encode('ascii') + b' ' + base64.b64encode(key.asbytes()).replace(b'\n', b'')
        kp = self.connection.client.import_key_pair(KeyName=self.name,
                                                    TagSpecifications=[self.resource_tags],
                                                    PublicKeyMaterial=keydata)
        self.id = kp['KeyPairId']
