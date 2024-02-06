from carthage import Injector, inject, InjectionKey
from carthage.config import ConfigSchema
from carthage.modeling import CarthageLayout


class DevelopmentSchema(ConfigSchema, prefix="developer"):
    #: A route53 zone in which to register developer resources

    domain: str  #: Name of primary developer vm
    machine: str
    iam_profile: str = None
    carthage_viewer: bool = True


@inject(injector=Injector)
def carthage_plugin(injector):
    from layout import dev_layout

    injector.add_provider(InjectionKey(CarthageLayout, layout_name="aws_development"), dev_layout)
