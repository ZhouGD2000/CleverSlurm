import pytest

from ai_slurm.ai.summarize import parse_summary_json


def test_ai_summary_parser_rejects_malformed_json():
    with pytest.raises(ValueError, match="valid JSON"):
        parse_summary_json("{not json")


def test_ai_summary_parser_requires_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_summary_json("[]")
