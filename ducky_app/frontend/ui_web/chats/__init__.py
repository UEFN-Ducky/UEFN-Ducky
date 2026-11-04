"""Project chats, split by concern.

``store`` (paths, slugs, the row-store switch, locks), ``saves`` (the chat-writer and
message saves), ``conversations``, ``folders``, ``groups`` (group hubs) and ``lanes``.
Modules reach each other through the module object (``_store._repo()``), never a bound
copy, so a test that patches a name on its owner reaches every caller.
"""
