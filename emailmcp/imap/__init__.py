"""IMAP access layer.

The modules are layered so that everything below ``store`` is free of IMAP
state and can be tested directly:

``mime``        pure MIME/header parsing
``parts``       inventory and classification of a message's MIME parts
``images``      decoding, downscaling and re-encoding of mail images
``content``     the body as an ordered run of text and image segments
``threads``     parsing of IMAP THREAD responses
``quoting``     building quoted thread blocks from parsed messages
``folders``     special-use folder discovery (needs a connection)
``connection``  connection lifecycle, locking, folder selection
``store``       the mailbox operations, one instance per mailbox
"""
