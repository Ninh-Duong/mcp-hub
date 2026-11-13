"""Playbook confirmation: the offer lists the playbooks, and a token is single use, task-bound, and expiring."""
from __future__ import annotations

import unittest

from mcp_hub import plan_confirm
from mcp_hub.playbooks import Playbook
from mcp_hub.policy import PolicyRefused


def playbook(pb_id: str, when: str) -> Playbook:
    return Playbook(pb_id, when, (), (), None, (), (), "")


PLAYBOOKS = {
    "rcid-bug": playbook("rcid-bug", "The user gives an rcid and wants to know why that request failed."),
    "source-search": playbook("source-search", "The user asks to search or count code in a source repository."),
}
TASK = "count the functions in the repository that split on a comma"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ChoicesTest(unittest.TestCase):
    def test_best_match_first_and_no_playbook_last(self) -> None:
        options = plan_confirm.choices(TASK, PLAYBOOKS)
        self.assertEqual(options[0][0], "source-search")
        self.assertEqual(options[-1][0], plan_confirm.NONE_CHOICE)
        self.assertEqual([option for option, _ in options], ["source-search", "rcid-bug", plan_confirm.NONE_CHOICE])

    def test_ties_keep_a_stable_order(self) -> None:
        options = plan_confirm.choices("zzzz", PLAYBOOKS)
        self.assertEqual([option for option, _ in options[:2]], ["rcid-bug", "source-search"])


class NeedsConfirmationTest(unittest.TestCase):
    def test_always_asks_for_every_task(self) -> None:
        self.assertTrue(plan_confirm.needs_confirmation("always", TASK, PLAYBOOKS))
        self.assertTrue(plan_confirm.needs_confirmation("always", "rcid abc", PLAYBOOKS))

    def test_off_never_asks(self) -> None:
        self.assertFalse(plan_confirm.needs_confirmation("off", "zzzz", PLAYBOOKS))

    def test_ambiguous_asks_unless_exactly_one_playbook_matches(self) -> None:
        self.assertFalse(plan_confirm.needs_confirmation("ambiguous", "search the repository for functions", PLAYBOOKS))
        self.assertTrue(plan_confirm.needs_confirmation("ambiguous", "zzzz", PLAYBOOKS))
        self.assertTrue(plan_confirm.needs_confirmation("ambiguous", "rcid and the repository code", PLAYBOOKS))


class ConfirmTokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.tokens = plan_confirm.ConfirmTokens(600, clock=self.clock)
        self.token = self.tokens.issue(TASK, ["source-search", plan_confirm.NONE_CHOICE])

    def test_a_token_redeems_once_for_the_offered_choice(self) -> None:
        self.tokens.redeem(self.token, TASK, "source-search")
        with self.assertRaisesRegex(PolicyRefused, "already used"):
            self.tokens.redeem(self.token, TASK, "source-search")

    def test_no_playbook_is_a_choice_when_offered(self) -> None:
        self.tokens.redeem(self.token, TASK, plan_confirm.NONE_CHOICE)

    def test_a_choice_that_was_not_offered_is_refused(self) -> None:
        with self.assertRaisesRegex(PolicyRefused, "offered choices"):
            self.tokens.redeem(self.token, TASK, "rcid-bug")

    def test_a_token_for_another_task_is_refused(self) -> None:
        with self.assertRaisesRegex(PolicyRefused, "another task"):
            self.tokens.redeem(self.token, "something else entirely", "source-search")

    def test_surrounding_whitespace_does_not_change_the_task(self) -> None:
        self.tokens.redeem(self.token, f"  {TASK}  ", "source-search")

    def test_an_expired_token_is_refused(self) -> None:
        self.clock.now = 601
        with self.assertRaisesRegex(PolicyRefused, "expired"):
            self.tokens.redeem(self.token, TASK, "source-search")

    def test_an_unknown_or_missing_token_is_refused(self) -> None:
        for bogus in ("nope", None, 42):
            with self.assertRaisesRegex(PolicyRefused, "unknown"):
                self.tokens.redeem(bogus, TASK, "source-search")

    def test_expired_tokens_are_pruned_on_issue(self) -> None:
        self.clock.now = 601
        self.tokens.issue(TASK, ["source-search"])
        self.assertEqual(len(self.tokens._issued), 1)


class OfferTextTest(unittest.TestCase):
    def test_the_offer_names_the_token_and_every_choice(self) -> None:
        text = plan_confirm.offer_text("tok123", plan_confirm.choices(TASK, PLAYBOOKS))
        self.assertIn("confirm_token: tok123", text)
        for choice in ("source-search", "rcid-bug", plan_confirm.NONE_CHOICE):
            self.assertIn(f"- {choice}:", text)


if __name__ == "__main__":
    unittest.main()
