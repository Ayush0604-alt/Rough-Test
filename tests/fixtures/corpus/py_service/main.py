"""Service entry point."""
from app.api import router


def build_app():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return app


if __name__ == "__main__":
    build_app()
