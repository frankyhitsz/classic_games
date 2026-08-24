"""Console entry points that keep optional dependencies optional."""

from __future__ import annotations


def api_main() -> None:
    try:
        from server.app import main
    except ImportError as exc:
        raise SystemExit(
            "API 调试功能未安装；请运行：pip install 'classic-games-hub[api]'") from exc
    main()
