"""Running people's bots.

* ``child``     the small program every bot starts through (limits, readable logs, a clean stop),
* ``sandbox``   how a bot's process is set up: its own folder, a clean environment, its limits,
* ``manager``   starts, watches and stops the processes and keeps what they write,
* ``protocol``  the request that starts a bot, the errors, and the signature on calls to a remote runner,
* ``clients``   what the website uses to reach a runner (built in, remote, or none),
* ``app``       the web service a remote runner exposes,
* ``service``   the one runner the website talks to.
"""
