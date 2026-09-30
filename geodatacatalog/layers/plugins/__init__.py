"""Built-in geodatacatalog plugins.

Plugins are plain modules exposing ``fetch(request, product_type, config)`` (see
:class:`geodatacatalog.layers.plugin.PythonPluginLayer`). They are loaded by file
name through ``search.type: <name>.py``. :func:`geodatacatalog.load_catalog` adds
this directory to its plugin search path, so a shipped plugin like ``opensky.py``
works out of the box — no need to copy it next to your ``catalog.yml``.
"""
