# bytedigger

This is a pointer package. ByteDigger itself is a Python project -- a verified agentic TDD engine: a frozen, event-sourced RED -> validate -> GREEN state machine with deterministic anti-reward-hacking gates.

**The real thing lives here: https://github.com/guy-lifshitz/bytedigger**

## Install

The engine is a Python package (3.9+, no runtime dependencies; the editable install below needs pip 21.3+):

```bash
pipx install bytedigger-engine
```

Or into an active virtualenv:

```bash
python3 -m pip install bytedigger-engine
```

Or from a clone:

```bash
git clone https://github.com/guy-lifshitz/bytedigger.git
cd bytedigger/engine_py
python3 -m pip install -e .
```

Either way you get the `bytedigger-engine` CLI.

## What this npm package does

`npx bytedigger` checks that python3 is available, runs `bytedigger-engine` if it's installed, and otherwise it prints instructions for getting it installed. That's all -- it exists so people who find the name on npm end up in the right place.

## License

MIT © Guy Lifshitz
