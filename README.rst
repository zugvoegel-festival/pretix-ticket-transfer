Ticket Transfer (Zugvögel)
==========================

This is a plugin for `pretix`_.

Allow transferring tickets to a new account, including an optional
payment-and-refund flow where the shop mediates settlement between the
old and new owner.

Based on
--------

This plugin is based on Kulturkosmos' open-source
`pretix-ticket-transfer`_ plugin (Apache License 2.0).

It is maintained as an independent Zugvögel Festival fork. We keep the
Kulturkosmos repository as a secondary git remote named ``upstream`` for
selective pulls; we do not track it as the product source of truth.

To add the secondary remote locally::

    git remote add upstream https://git.kulturkosmos.de/ticketing/pretix-ticket-transfer.git
    git fetch upstream

Development setup
-----------------

1. Make sure that you have a working `pretix development setup`_.

2. Clone this repository.

3. Activate the virtual environment you use for pretix development.

4. Execute ``python setup.py develop`` within this directory to register this application with pretix's plugin registry.

5. Execute ``make`` within this directory to compile translations.

6. Restart your local pretix server. You can now use the plugin from this repository for your events by enabling it in
   the 'plugins' tab in the settings.

This plugin has CI set up to enforce a few code style rules. To check locally, you need these packages installed::

    pip install flake8 isort black docformatter

To check your plugin for rule violations, run::

    docformatter --check -r .
    black --check .
    isort -c .
    flake8 .

You can auto-fix some of these issues by running::

    docformatter -r .
    isort .
    black .

To automatically check for these issues before you commit, you can run ``.install-hooks``.


License
-------

Copyright 2022 alice (Kulturkosmos)
Copyright 2026 Zugvögel Festival

Released under the terms of the Apache License 2.0



.. _pretix: https://github.com/pretix/pretix
.. _pretix development setup: https://docs.pretix.eu/en/latest/development/setup.html
.. _pretix-ticket-transfer: https://git.kulturkosmos.de/ticketing/pretix-ticket-transfer
