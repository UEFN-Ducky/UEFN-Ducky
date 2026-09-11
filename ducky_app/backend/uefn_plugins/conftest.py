"""test_store_sync.py and test_uefn_plugins.py deliberately leave plugin-host
threads hung forever (hung register()/unload fakes). Those threads keep
running into later tests and touch whatever AppData root is current, which
made unrelated tests flaky once every test got its own database. They run in
their own interpreter via test_isolated_suites.py; explicit paths still work:

    py -m pytest ducky_app/backend/uefn_plugins/test_store_sync.py
"""

collect_ignore = ["test_store_sync.py", "test_uefn_plugins.py"]
