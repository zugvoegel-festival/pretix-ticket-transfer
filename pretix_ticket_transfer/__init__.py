from django.utils.translation import gettext_lazy

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")

__version__ = "0.0.1"


class PluginApp(PluginConfig):
    name = "pretix_ticket_transfer"
    verbose_name = "Ticket Transfer"

    class PretixPluginMeta:
        name = gettext_lazy("Ticket Transfer")
        author = "alice"
        description = gettext_lazy("Allow to transfert ticket to new account")
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2.7.0"

    def ready(self):
        from . import signals  # NOQA


default_app_config = "pretix_ticket_transfer.PluginApp"
