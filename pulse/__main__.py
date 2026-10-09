"""``python -m pulse`` runs the service with uvicorn."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "pulse.app:app",
        host=os.environ.get("PULSE_HOST", "0.0.0.0"),  # noqa: S104 - container entrypoint
        port=int(os.environ.get("PULSE_PORT", "7860")),
        log_level=os.environ.get("PULSE_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
