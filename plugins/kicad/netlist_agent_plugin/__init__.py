try:
    import pcbnew

    from .plugin import NetlistAgentReportPlugin

    NetlistAgentReportPlugin().register()
except ImportError:
    pass  # outside KiCad
