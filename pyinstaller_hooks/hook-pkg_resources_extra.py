# Custom hook: Ensure pkg_resources dependencies are bundled (setuptools 69+ externalized these)
# Fixes: ModuleNotFoundError: No module named 'jaraco', 'platformdirs', etc.
hiddenimports = [
    'jaraco',
    'jaraco.functools',
    'jaraco.context',
    'jaraco.text',
    'platformdirs',
]
