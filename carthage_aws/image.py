# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import datetime
from carthage import *
from .connection import AwsConnection, run_in_executor

__all__ = []


def image_provider(
        owner, *,
        name,
        architecture="x86_64",
        ):
    def callback(connection):
        r = connection.client.describe_images(
            Owners=[owner],
            Filters=[dict(
                Name='name',
                Values=[name])])
        images = r['Images']
        for i in images:
            creation_date = i['CreationDate']
            #AWS uses trailing Z rather than offset; datetime.datetime cannot deal with that
            creation_date = creation_date[:-1]+'+00:00'
            i['CreationDate'] = datetime.datetime.fromisoformat(creation_date)
        images.sort(key=lambda i: i['CreationDate'], reverse=True)
        return images

    @inject(connection=AwsConnection)
    async def image_provider_inner(connection):
        images = await run_in_executor(callback, connection)
        return images[0]['ImageId']
    return image_provider_inner

__all__ += ['image_provider']

debian_ami_owner ='136693071363'

__all__ += ['debian_ami_owner']
