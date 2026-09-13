"""Bundled suite packs shipped as wheel package data.

``agent_eval.packs.starter`` rides inside the wheel so ``eval-suite run demo``
works on a pure pip install with zero setup — no repo checkout, no network, no
credentials (spec: suite-distribution). The repo-root ``packs/<name>/``
directories are the authoring copies; a test pins both sides byte-identical so
they cannot drift apart.
"""
