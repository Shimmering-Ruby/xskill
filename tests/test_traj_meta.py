"""tests/test_traj_meta.py -- trajectory header parsing"""

from __future__ import annotations

from xskill.pipeline.trajectory import parse_traj_header


class TestParseTrajHeader:
    def test_full_header(self):
        md = "<!-- xskill:skill=fix_django side=staging sha=a1b2c3d4 -->\n# Trajectory"
        result = parse_traj_header(md)
        assert result == {"skill": "fix_django", "side": "staging", "sha": "a1b2c3d4"}

    def test_partial_header_skill_only(self):
        md = "<!-- xskill:skill=my_skill -->\n# Traj"
        result = parse_traj_header(md)
        assert result == {"skill": "my_skill"}

    def test_no_header(self):
        md = "# Trajectory\n\nSome content here"
        assert parse_traj_header(md) is None

    def test_empty_string(self):
        assert parse_traj_header("") is None

    def test_header_not_in_first_500_chars(self):
        md = "x" * 600 + "\n<!-- xskill:skill=late -->"
        assert parse_traj_header(md) is None

    def test_header_at_start(self):
        md = "<!-- xskill:skill=first side=main sha=deadbeef -->\n# Title"
        result = parse_traj_header(md)
        assert result["skill"] == "first"
        assert result["side"] == "main"
        assert result["sha"] == "deadbeef"

    def test_header_with_extra_whitespace(self):
        md = "<!--  xskill:skill=ws   side=staging   sha=abc  -->"
        result = parse_traj_header(md)
        assert result == {"skill": "ws", "side": "staging", "sha": "abc"}

    def test_header_after_other_comments(self):
        md = "<!-- source: swe_smith -->\n<!-- xskill:skill=second side=main -->\n# Traj"
        result = parse_traj_header(md)
        assert result == {"skill": "second", "side": "main"}

    def test_arbitrary_keys(self):
        md = "<!-- xskill:skill=test agent=agent_007 custom=value -->"
        result = parse_traj_header(md)
        assert result == {"skill": "test", "agent": "agent_007", "custom": "value"}

    def test_regular_html_comment_ignored(self):
        md = "<!-- this is a normal comment -->\n# Traj"
        assert parse_traj_header(md) is None
