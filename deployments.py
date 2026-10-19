"""Builds a ready SurveyChatbot from a subject folder.

Each host takes a --deployment name and calls build(name), which reads
deployments/<name>/config.json and content/course.json. Adding a subject
means adding a folder, with no change to any host.
"""
import json
from pathlib import Path

from core import SurveyChatbot

ROOT = Path(__file__).resolve().parent
DEPLOYMENTS_DIR = ROOT / "deployments"
SHARED_BANK = ROOT / "content" / "survey-bank.json"


def available():
    """Names of deployments that have both a config.json and a content/course.json."""
    names = []
    if not DEPLOYMENTS_DIR.is_dir():
        return names
    for child in sorted(DEPLOYMENTS_DIR.iterdir()):
        if (child / "config.json").is_file() and (child / "content" / "course.json").is_file():
            names.append(child.name)
    return names


def build(name, mode="offline", database=None):
    """Build a SurveyChatbot for deployments/<name>/.

    Each subject gets its own database, runtime/<name>.sqlite, unless another
    path is given.
    """
    path = DEPLOYMENTS_DIR / name
    config_file = path / "config.json"
    course_file = path / "content" / "course.json"
    if not config_file.is_file() or not course_file.is_file():
        known = ", ".join(available()) or "(none found)"
        raise ValueError(f"Unknown deployment {name!r}. Known deployments: {known}")
    config = json.loads(config_file.read_text())
    course = json.loads(course_file.read_text())
    bank = json.loads(SHARED_BANK.read_text())
    if database is None:
        database = str(ROOT / "runtime" / f"{name}.sqlite")
    bot = SurveyChatbot(
        content_pack=course,
        survey_bank=bank,
        chatbot_context=config["chatbot_context"],
        conversation_title=config["conversation_title"],
        database=database,
        mode=mode,
    )
    # Optional display settings, used only by the browser host. Other hosts
    # ignore bot.ui. Missing fields fall back to defaults from the title.
    bot.ui = {
        "deployment": name,
        "display_name": config.get("display_name", config["conversation_title"].lower()),
        "brand": config.get("brand", name[:4].upper()),
        "sample_questions": config.get("sample_questions", []),
        "placeholder_hint": config.get("placeholder_hint", "Ask a question about this topic..."),
    }
    return bot
