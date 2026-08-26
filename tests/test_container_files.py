from pathlib import Path


def test_docker_image_includes_runtime_scripts() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts ./scripts" in dockerfile
    assert "FROM base AS test" in dockerfile
    assert "COPY tests ./tests" in dockerfile


def test_web_application_exposes_runtime_endpoints() -> None:
    main_module = Path("app/main.py").read_text(encoding="utf-8")
    assert 'app = FastAPI(' in main_module
    assert '@app.get("/health/ready")' in main_module
    assert '@app.post("/webhooks/telegram", include_in_schema=False)' in main_module
    assert '@app.post("/webhooks/mercado-pago", include_in_schema=False)' in main_module
