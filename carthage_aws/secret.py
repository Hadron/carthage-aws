# Copyright (C) 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from botocore.exceptions import ClientError

from carthage import *

from .connection import run_in_executor, AwsConnection

__all__ = []

@inject(connection=AwsConnection)
def secret_ref(secret:str, *, connection=None)-> str|bytes:
    '''
    Returns either the bytes or string of the AwsCurrent version of the given secret.
    Usage::

        result = await ainjector(secret_ref, secret_name)

    Or with currying::

        add_provider(InjectionKey("some_secret"), secret_ref("some_secret"))
    Note that when not curried, this function should be treated as asynchronous.
    '''
    def callback():
        sm = connection.connection.client('secretsmanager')
        r = sm.get_secret_value(SecretId=secret)
        if 'SecretString' in r:
            return r['SecretString']
        return r['SecretBinary']
    if connection:
        return  run_in_executor(callback)
    return partial_with_dependencies(secret_ref, secret=secret)



__all__ += ['secret_ref']

@inject(connection=AwsConnection)
async def upsert_secret(
        secret:str,
        value:str|bytes,
        *,
        connection):
    '''
    Create or update an AWS secret
    '''
    vdict = {}
    if isinstance(value, str):
        vdict['SecretString'] = value
    elif isinstance(value, bytes):
        vdict['SecretBinary'] = value
    else: raise TypeError('Value is not a string or bytes')
    def callback():
        sm = connection.connection.client('secretsmanager')
        try:
            sm.put_secret_value(SecretId=secret, **vdict)
        except ClientError as e:
            try:
                sm.create_secret(Name=secret, **vdict)
            except Exception:
                raise e from None
    return await run_in_executor(callback)

__all__ += ['upsert_secret']
