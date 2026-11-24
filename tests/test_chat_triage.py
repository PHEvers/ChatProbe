import unittest
from unittest.mock import Mock

from src.chatprobe.browser.smoke import wait_for_triage_step


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.destination = Mock()
        self.visible = self.destination.filter.return_value
        self.other_subject = Mock()
        self.log = Mock()

    def test_selects_other_subject_before_waiting_for_the_next_step(self):
        self.visible.count.return_value = 0
        order = Mock()
        order.attach_mock(self.other_subject.last.click, "click")
        order.attach_mock(self.visible.wait_for, "wait_for_next_step")

        wait_for_triage_step(self.destination, self.other_subject, self.log)

        self.visible.or_.assert_called_once_with(self.other_subject)
        self.assertEqual([call[0] for call in order.mock_calls], ["click", "wait_for_next_step"])
        self.log.assert_called_once()

    def test_proceeds_directly_when_the_next_step_is_visible(self):
        self.visible.count.return_value = 1

        wait_for_triage_step(self.destination, self.other_subject, self.log)

        self.other_subject.last.click.assert_not_called()
        self.visible.wait_for.assert_not_called()
        self.log.assert_not_called()
