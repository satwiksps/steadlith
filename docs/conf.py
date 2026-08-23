"""Sphinx configuration for the Steadlith documentation."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

project = "Steadlith"
author = "Steadlith contributors"
copyright = "2026, Steadlith contributors"

try:
    release = version("steadlith")
except PackageNotFoundError:
    release = "1.0.0"
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx_immaterial",
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = {".md": "markdown"}
master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
nitpicky = True

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 4

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"
autosummary_generate = False

html_theme = "sphinx_immaterial"
html_title = "Steadlith documentation"
html_logo = "_static/steadlith-mark.svg"
html_favicon = "_static/steadlith-mark.svg"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
html_last_updated_fmt = "%Y-%m-%d"
html_show_sourcelink = True

html_theme_options = {
    "site_url": html_baseurl or "https://steadlith.readthedocs.io/en/latest/",
    "repo_url": "https://github.com/satwiksps/steadlith",
    "repo_name": "satwiksps/steadlith",
    "edit_uri": "edit/main/docs/",
    "font": False,
    "icon": {"repo": "fontawesome/brands/github"},
    "toc_title": "On this page",
    "features": [
        "content.action.edit",
        "content.code.copy",
        "navigation.sections",
        "navigation.expand",
        "navigation.top",
        "navigation.footer",
        "search.highlight",
        "search.share",
        "search.suggest",
        "toc.follow",
        "toc.sticky",
    ],
    "palette": [
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "primary": "custom",
            "accent": "custom",
            "toggle": {
                "icon": "material/weather-night",
                "name": "Use dark mode",
            },
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "primary": "custom",
            "accent": "custom",
            "toggle": {
                "icon": "material/weather-sunny",
                "name": "Use light mode",
            },
        },
    ],
}

html_context = {
    "display_github": True,
    "github_user": "satwiksps",
    "github_repo": "steadlith",
    "github_version": "main",
    "conf_py_path": "/docs/",
}
