"""The golden plugins' backend: one tool named after the plugin.

Installed as ``backend.py`` for the source plugin and as the ``.pyd`` file of the compiled
ones (the test loads it with a source loader where the host uses ExtensionFileLoader).
"""


def register(api):
    pid = __name__.removeprefix("uefn_plugin_").split("_v")[0]

    @api.tool(name=f"{pid}_echo")
    def echo(text: str) -> dict:
        """Echo the text back."""
        return {"echo": text}
