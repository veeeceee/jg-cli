"""Pure Slack text-cleaning tests — no network."""

from __future__ import annotations

from jg.slack import SlackMsg, clean_text

USERS = {"U1": "jaimon", "U2": "vibhu"}


def test_user_mention_becomes_handle():
    assert clean_text("hey <@U1> can you look", USERS) == "hey @jaimon can you look"


def test_unknown_user_mention_is_someone():
    assert clean_text("ping <@U999>", USERS) == "ping @someone"


def test_channel_ref_becomes_hash_name():
    assert clean_text("see <#C123|engineering>", USERS) == "see #engineering"


def test_link_with_label_keeps_label():
    assert clean_text("docs <https://x.com/a|the doc>", USERS) == "docs the doc"


def test_bare_link_kept():
    assert clean_text("<https://x.com/a>", USERS) == "https://x.com/a"


def test_here_channel_broadcast():
    assert clean_text("<!here> standup", USERS) == "@here standup"


def test_entities_unescaped_and_whitespace_collapsed():
    assert clean_text("a &amp; b\n\n  c", USERS) == "a & b c"


def test_jira_keys_from_text():
    m = SlackMsg("C1", "#eng", "1.0", "vibhu", "look at CH-526 and CH-9", "channel")
    assert set(m.jira_keys) == {"CH-526", "CH-9"}
