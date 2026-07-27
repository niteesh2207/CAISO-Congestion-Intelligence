from __future__ import annotations

import os
import uvicorn


def main() -> None:
    uvicorn.run(
        "pwai.server:app",
        host=os.getenv("PWAI_HOST", "127.0.0.1"),
        port=int(os.getenv("PWAI_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
