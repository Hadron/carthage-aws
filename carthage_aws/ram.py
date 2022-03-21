import asyncio

from carthage import *
from carthage.dependency_injection import *
from carthage.utils import memoproperty
from carthage_aws.connection import AwsConnection, AwsManaged, run_in_executor
from carthage_aws.network import AwsVirtualPrivateCloud, AwsSubnet

from dataclasses import dataclass, field

from botocore.exceptions import ClientError

class AwsResourceAccessManager(AwsManaged):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # accept_resource_share_invitation()
    # associate_resource_share()
    # associate_resource_share_permission()
    # can_paginate()
    # create_resource_share()
    # delete_resource_share()
    # disassociate_resource_share()
    # disassociate_resource_share_permission()
    # enable_sharing_with_aws_organization()
    # get_paginator()
    # get_permission()
    # get_resource_policies()
    # get_resource_share_associations()
    # get_resource_share_invitations()
    # get_resource_shares()
    # get_waiter()
    # list_pending_invitation_resources()
    # list_permission_versions()
    # list_permissions()
    # list_principals()
    # list_resource_share_permissions()
    # list_resource_types()
    # list_resources()
    # promote_resource_share_created_from_policy()
    # reject_resource_share_invitation()
    # tag_resource()
    # untag_resource()
    # update_resource_share()
