"""IMAP access layer.

The modules are layered so that everything below ``store`` is free of IMAP
state and can be tested directly:

``mime``        pure MIME/header parsing
``threads``     parsing of IMAP THREAD responses
``quoting``     building quoted thread blocks from parsed messages
``folders``     special-use folder discovery (needs a connection)
``connection``  connection lifecycle, locking, folder selection
``store``       the four mailbox operations, one instance per mailbox
"""
