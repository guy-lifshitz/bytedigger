# bytedigger

This is a pointer package. ByteDigger itself is a Python project -- a verified agentic TDD engine: a frozen, event-sourced RED -> validate -> GREEN state machine with deterministic anti-reward-hacking gates.

**The real thing lives here: https://github.com/guy-lifshitz/bytedigger**

## Install

The engine is a Python package (3.9+, no runtime dependencies):

```bash
pip install git+https://github.com/guy-lifshitz/bytedigger.git#subdirectory=engine_py
```

Or from a clone:

```bash
git clone https://github.com/guy-lifshitz/bytedigger.git
cd bytedigger/engine_py
pip install -e .
```

Either way you get the `bytedigger-engine` CLI.

## What this npm package does

`npx bytedigger` checks that python3 is available, runs `bytedigger-engine` if it's installed, and prints the pip install instructions if it isn't. That's all -- it exists so people who find the name on npm end up in the right place.

## License

MIT © Guy Lifshitz
