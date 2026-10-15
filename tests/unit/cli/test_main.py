from corki.cli.main import build_parser


def test_parser_supports_codex_style_resume_command() -> None:
    parsed = build_parser().parse_args(["resume", "thread-123"])

    assert parsed.command == "resume"
    assert parsed.thread_id == "thread-123"


def test_parser_supports_legacy_resume_flag() -> None:
    parsed = build_parser().parse_args(["--resume"])

    assert parsed.resume == "latest"
