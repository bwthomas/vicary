"""Single source of the package version.

Its own module so :mod:`vicary.assets` can read it without importing the
package's public surface, which would be a cycle.
"""

__version__ = "0.2.0"
