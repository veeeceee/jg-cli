from ch.commands.transition import _match_transition, _normalize


def _t(name: str, to_name: str | None = None, id_: str = "1") -> dict:
    return {"id": id_, "name": name, "to": {"name": to_name or name}}


def test_normalize():
    assert _normalize("In Review") == "inreview"
    assert _normalize("READY FOR REVIEW") == "readyforreview"
    assert _normalize("send-for_review") == "sendforreview"


def test_match_exact_to_name():
    transitions = [_t("Send for review", "READY FOR REVIEW", "3"), _t("Resolve", "Resolved", "9")]
    m = _match_transition(transitions, "ready for review")
    assert m["id"] == "3"


def test_match_exact_transition_name():
    transitions = [_t("Send for review", "READY FOR REVIEW", "3")]
    m = _match_transition(transitions, "send for review")
    assert m["id"] == "3"


def test_match_substring():
    transitions = [_t("Send for review", "READY FOR REVIEW", "3"), _t("Resolve", "Resolved", "9")]
    m = _match_transition(transitions, "review")
    assert m["id"] == "3"


def test_match_ambiguous_returns_none():
    transitions = [_t("In Review", "In Review", "3"), _t("Code review", "Code Review", "5")]
    assert _match_transition(transitions, "review") is None


def test_match_no_match():
    transitions = [_t("Resolve", "Resolved", "9")]
    assert _match_transition(transitions, "deploy") is None
