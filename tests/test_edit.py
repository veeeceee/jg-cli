from ch.commands.edit import _normalize_priority, _split_addremove


def test_normalize_priority_exact():
    assert _normalize_priority("High") == "High"
    assert _normalize_priority("highest") == "Highest"


def test_normalize_priority_prefix():
    assert _normalize_priority("h") == "Highest"  # first match
    assert _normalize_priority("med") == "Medium"


def test_normalize_priority_unknown():
    assert _normalize_priority("urgent") is None


def test_split_addremove_default_add():
    add, remove = _split_addremove(("foo", "bar"))
    assert add == ["foo", "bar"]
    assert remove == []


def test_split_addremove_with_signs():
    add, remove = _split_addremove(("+foo", "-bar", "baz"))
    assert add == ["foo", "baz"]
    assert remove == ["bar"]
