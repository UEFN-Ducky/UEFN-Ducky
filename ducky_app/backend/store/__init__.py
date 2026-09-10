"""The one owner of ``ducky.db`` (ADR 0003).

Only this package imports :mod:`sqlite3`; ``test_fitness.py`` enforces it.
Everything else goes through the repos in :mod:`backend.store.repos`.
"""
