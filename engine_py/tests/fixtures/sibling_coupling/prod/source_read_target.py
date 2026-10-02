"""GH1200 fixture (OFI 05747bb6, #1169) — production module whose SOURCE FORM
a sibling test asserts on.

The coupled test greps this file's text for `async for`: it names no symbol and
no constant value, so substring-driven audits cannot see it.
"""


async def stream_lines(source):
    collected = []
    async for line in source:
        collected.append(line)
    return collected
