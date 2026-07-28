"""UEFN Ducky HTTP listener (split package).

Run from UEFN: ``ducky_app/uefn_listener/launch_listener.py`` (calls ``listener.bootstrap.run()``).

To start from Python after ensuring commands are registered::

    import listener.handlers  # noqa: F401
    from listener.runtime import start_listener

    start_listener()
"""

from listener.config import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION"]
