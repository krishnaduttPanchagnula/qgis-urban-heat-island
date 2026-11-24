def classFactory(iface):
    from .uhi_plugin import UhiPlugin
    return UhiPlugin(iface)
