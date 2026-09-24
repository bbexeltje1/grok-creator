"""Entry point for the Grok / x.ai account creator desktop app."""

from pathlib import Path

from grok_creator.gui import GrokCreatorApp


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    app = GrokCreatorApp(base_dir)
    app.mainloop()


if __name__ == "__main__":
    main()