"""Drive the real TUI through a scripted conversation, for recording demo.gif.

Runs `oi` exactly as `main()` would (so the terminal probe, model resolution
and streaming are the real thing) with `OiApp` swapped for a subclass whose
worker types each prompt into the compose box, submits it, and waits for the
turn to finish. Usage:  python demo/tui_demo.py [extra oi args…]
"""

from __future__ import annotations

import asyncio
import sys

PROMPTS = [
    "Explain the attention mechanism in transformers, with the key equations.",
    "Why divide by the square root of d_k?",
]
TYPING_DELAY = 0.05
PAUSE_BEFORE_TYPING = 1.5
PAUSE_AFTER_ANSWER = 3.0


def main() -> None:
    import oi.app
    import oi.tui.app as tui_app
    from oi.tui.app import ChatInput, OiApp

    class DemoApp(OiApp):
        def on_mount(self) -> None:
            # Textual calls OiApp.on_mount too (one handler per class); the turn
            # worker is exclusive in the default group, so use another.
            self.run_worker(self._script(), group="demo")

        async def _script(self) -> None:
            for prompt in PROMPTS:
                await asyncio.sleep(PAUSE_BEFORE_TYPING)
                for char in prompt:
                    self._input.insert(char)
                    await asyncio.sleep(TYPING_DELAY)
                await asyncio.sleep(0.4)
                self._input.post_message(ChatInput.Submitted(self._input, prompt))
                await asyncio.sleep(0.5)
                while self._turn_active:
                    await asyncio.sleep(0.1)
                await asyncio.sleep(PAUSE_AFTER_ANSWER)
            self.exit()

    setattr(tui_app, "OiApp", DemoApp)  # run_tui looks the name up at call time
    sys.argv = ["oi", "--tui", "--ephemeral", *sys.argv[1:]]
    oi.app.main()


if __name__ == "__main__":
    main()
